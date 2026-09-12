"""Offline compact installer assembly boundaries; no firmware or device writes."""

import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

from firmware.ender_aux_f401compact import host
from firmware.marlin_mainboard_compact.package import assemble

CAPABILITIES = (
    b"Cap:E3_MAINBOARD_V1:1",
    b"Cap:E3_MATERIAL_HEIGHT_V1:1",
    b"Cap:E3_USB_UPDATER_F401_V1:1",
    b"Cap:E3_Z_LIMIT_80_V1:1",
    b"Cap:E3_SURFACE_HEIGHT_V2:1",
    b"Cap:E3_RECOVERY_V1:1",
    b"Cap:E3_LIVE_Z_V1:1",
    b"E3SG:2 PROBE_Z:",
    b"E3HW:1 MCU:",
)


def test_image_packaging_imports_without_optional_build_dependencies():
    subprocess.run(
        [sys.executable, "-S", "-c", "from firmware.marlin_mainboard_compact.package import assemble; assert callable(assemble)"],
        cwd=Path(__file__).resolve().parents[1], check=True, capture_output=True,
    )


def application(length=1024, omit=None):
    body = bytearray(b"\xff" * length)
    struct.pack_into("<II", body, 0, 0x20010000, 0x08020395)
    identifiers = b"\0".join(cap for cap in CAPABILITIES if cap != omit) + b"\0"
    body[408:408 + len(identifiers)] = identifiers
    return host.pack_image(bytes(body))


def updater(length):
    # assemble() receives an ELF-audited updater from package.main(). These
    # placeholders exercise placement/size only; they are not flashable images.
    raw = bytearray(b"U" * length)
    if length >= 8:
        struct.pack_into("<II", raw, 0, 0x20010000, 0x08010195)
    return bytes(raw)


@pytest.mark.parametrize("cap", CAPABILITIES[:-1])
def test_capability_prefix_is_not_an_exact_capability(cap):
    payload = host.validate_image(application()).replace(cap + b"\0", cap + b"0\0")
    with pytest.raises(ValueError, match="Missing"):
        assemble(updater(408), host.pack_image(payload))


@pytest.mark.parametrize("updater_length", [408, 4096, 65536])
def test_updater_minimum_through_full_sector_and_exact_offsets(updater_length):
    raw = updater(updater_length)
    image = application()
    combined = assemble(raw, image)
    assert combined[:updater_length] == raw
    assert combined[updater_length:65536] == b"\xff" * (65536 - updater_length)
    assert combined[65536:] == image
    assert struct.unpack_from("<I", combined, 65536)[0] == 0x55413345
    assert struct.unpack_from("<II", combined, 65536 + 512) == (0x20010000, 0x08020395)
    assert combined[0x10200:] == host.validate_image(image)
    # Loading the combined file at 0x08010000 keeps the retained loader outside
    # the file and places application metadata/vectors at their fixed addresses.
    assert 0x08010000 + 65536 == 0x08020000
    assert 0x08010000 + 65536 + 512 == 0x08020200


@pytest.mark.parametrize("length", [0, 4, 407, 65537])
def test_updater_truncation_or_sector_overflow_rejected(length):
    with pytest.raises(ValueError, match="reserved sector"):
        assemble(updater(length), application())


def test_maximum_payload_ends_exactly_at_256_kib_boundary():
    image = application(130560)
    combined = assemble(updater(65536), image)
    assert len(image) == 131072
    assert len(combined) == 196608
    assert 0x08010000 + len(combined) == 0x08040000
    assert combined[0x10200:] == host.validate_image(image)


def test_oversized_application_rejected_before_a_combined_image_is_returned():
    with pytest.raises(host.FirmwareError, match="supported range"):
        assemble(updater(408), application(130560) + b"\xff" * 4)


@pytest.mark.parametrize("other_board", [0x0401E013, 0x0103E013])
def test_other_target_with_repaired_header_crc_is_rejected(other_board):
    image = bytearray(application())
    struct.pack_into("<I", image, 8, other_board)
    struct.pack_into("<I", image, 24, zlib.crc32(image[4:24]))
    with pytest.raises(host.FirmwareError, match="board"):
        assemble(updater(408), bytes(image))


@pytest.mark.parametrize("missing", CAPABILITIES)
def test_every_required_capability_and_hardware_identity_must_be_present(missing):
    with pytest.raises(ValueError, match="runtime feature/hardware identity"):
        assemble(updater(408), application(omit=missing))


def test_caps_in_header_or_updater_do_not_substitute_for_application_features():
    raw = updater(4096) + b"\0".join(CAPABILITIES)
    with pytest.raises(ValueError, match="runtime feature/hardware identity"):
        assemble(raw, application(omit=CAPABILITIES[2]))


@pytest.mark.parametrize("offset", [24, 511, 700])
def test_corrupt_header_or_application_is_rejected(offset):
    image = bytearray(application())
    image[offset] ^= 1
    with pytest.raises(host.FirmwareError):
        assemble(updater(408), bytes(image))
