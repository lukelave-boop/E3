"""F103 image separation and explicit USB maintenance; no physical serial I/O."""
import struct
from types import SimpleNamespace

import pytest

from firmware.ender_aux import host as f401
from firmware.ender_aux_f103 import host
from tests.test_ender_aux_host import ScriptedPort

UPDATER = "E3AUX1 UPDATER 0.2.0 BOARD=0103E013"
MARLIN = "FIRMWARE_NAME:Marlin 2.0.8.24F1\nCap:E3_USB_UPDATER_F103_V1:1\nok"


@pytest.fixture(autouse=True)
def no_hardware(monkeypatch):
    clock = SimpleNamespace(now=0.0)

    def now():
        clock.now += 0.001
        return clock.now

    monkeypatch.setattr(host, "time", SimpleNamespace(monotonic=now, sleep=lambda _: None))
    monkeypatch.setattr(host, "open_port", lambda _: pytest.fail("Unexpected hardware access"))


def payload(stack=0x20010000, reset=0x080103E9, length=512):
    return struct.pack("<II", stack, reset) + bytes(length - 8)


def test_image_roundtrip_and_cross_target_rejection():
    image = host.pack_image(payload())
    assert host.validate_image(image) == payload()
    assert struct.unpack_from("<I", image, 8)[0] == 0x0103E013
    with pytest.raises(f401.FirmwareError):
        f401.validate_image(image)
    old = f401.pack_image(struct.pack("<II", 0x20018000, 0x08020395) + bytes(444))
    with pytest.raises(host.FirmwareError):
        host.validate_image(old)


@pytest.mark.parametrize("stack,reset", [
    (0x20000000, 0x080103E9), (0x20010008, 0x080103E9),
    (0x2000FFFF, 0x080103E9), (0x20010000, 0x080103E8),
    (0x20010000, 0x080103E3), (0x20010000, 0x08007001),
    (0x20010000, 0x08020001),
])
def test_invalid_stack_or_reset_rejected(stack, reset):
    with pytest.raises(host.FirmwareError):
        host.pack_image(payload(stack, reset))


@pytest.mark.parametrize("reply", [
    "ok", "FIRMWARE_NAME:Marlin 2.0\nok",
    "Cap:E3_USB_UPDATER_F103_V1:1\nok",
    "E3AUX1 UPDATER 0.2.0 BOARD=0401E013", "ERR MCU_MISMATCH",
])
def test_wrong_or_incomplete_identity_never_requests_update(reply):
    port = ScriptedPort([("M115", reply)])
    with pytest.raises(host.FirmwareError):
        host.Link(port).hold_updater()
    assert port.commands == ["M115"]


def test_marlin_maintenance_entry_then_hold():
    port = ScriptedPort([
        ("M115", MARLIN), ("M997", "E3USB:1 ENTERING_UPDATER"),
        ("HOLD", UPDATER + "\nOK HOLD"), ("M115", UPDATER),
    ])
    host.Link(port).hold_updater()
    assert not port.exchanges
    assert port.commands == ["M115", "M997", "HOLD", "M115"]


def test_rejected_maintenance_does_not_erase_or_retry():
    port = ScriptedPort([("M115", MARLIN), ("M997", "Error:E3USB:1 PRECONDITION")])
    with pytest.raises(host.FirmwareError):
        host.Link(port).hold_updater()
    assert port.commands == ["M115", "M997"]


def test_upload_to_recovery_commits_but_does_not_boot():
    image = host.pack_image(payload())
    body = host.validate_image(image)
    exchanges = [("M115", UPDATER), ("HOLD", "OK HOLD"), ("M115", UPDATER),
                 (f"BEGIN 0103E013 {len(body):08X} {host.crc32(body):08X}", "OK BEGIN")]
    for offset in range(0, len(body), 64):
        block = body[offset:offset + 64]
        exchanges.append((f"DATA {offset:08X} {block.hex().upper()}", f"OK DATA {offset + len(block):08X}"))
    exchanges.append(("END", "OK END"))
    port = ScriptedPort(exchanges)
    host.upload(host.Link(port), image)
    assert not port.exchanges and "BOOT" not in port.commands


def test_hardware_flag_required_before_port_access():
    assert host.main(["inspect", "--port", "not-a-real-port"]) == 1
