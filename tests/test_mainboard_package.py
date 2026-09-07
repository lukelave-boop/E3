from __future__ import annotations

import struct

import pytest

from firmware.ender_aux.host import pack_image
from firmware.marlin_mainboard import package


@pytest.fixture
def images(monkeypatch):
    updater = struct.pack("<II", 0x20010000, 0x08010101).ljust(65536, b"\xff")
    monkeypatch.setattr(package, "UPDATER_SHA256", package.sha(updater))
    payload = struct.pack("<II", 0x20010000, 0x08020395) + b"\x00\xbf" * 8
    payload += b"Cap:E3_MAINBOARD_V1:1\n\0Cap:E3_MATERIAL_HEIGHT_V1:1\n\0"
    payload = payload.ljust(408, b"\xff")
    return updater, pack_image(payload)


def test_sd_image_contains_exact_updater_and_application_at_correct_addresses(images):
    updater, app = images
    sd = package.assemble(updater, app)
    assert sd[:65536] == updater
    assert sd[65536:] == app
    assert sd[0x10200:0x10208] == struct.pack("<II", 0x20010000, 0x08020395)


@pytest.mark.parametrize("change", ["updater", "short_updater", "app", "capability"])
def test_corruption_and_wrong_application_reject(images, change):
    updater, app = images
    if change == "updater":
        updater = updater[:100] + b"\x00" + updater[101:]
    elif change == "short_updater":
        updater = updater[:-1]
    elif change == "app":
        app = app[:-1] + bytes([app[-1] ^ 1])
    else:
        payload = package.validate_image(app).replace(b"MAINBOARD_V1", b"MAINBOARD_V2")
        app = pack_image(payload)
    with pytest.raises((ValueError, RuntimeError)):
        package.assemble(updater, app)
