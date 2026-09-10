"""Compact F401 image and maintenance host checks; no physical serial access."""

import struct
import subprocess
import sys
import zlib
from types import SimpleNamespace

import pytest

from firmware.ender_aux import host as old_f401
from firmware.ender_aux_f401compact import host
from tests.test_ender_aux_host import ScriptedPort

UPDATER = "E3AUX1 UPDATER 0.3.0 BOARD=0401C013"
MARLIN = (
    "FIRMWARE_NAME:Marlin 2.0.8.24F4\n"
    "E3HW:1 MCU:0423 FLASH_KIB:256\n"
    "Cap:E3_USB_UPDATER_F401_V1:1\nok"
)
REAL_OPEN_PORT = host.open_port


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    clock = SimpleNamespace(now=0.0, sleeps=[])

    def now():
        clock.now += 0.001
        return clock.now

    monkeypatch.setattr(host, "time", SimpleNamespace(monotonic=now, sleep=clock.sleeps.append))
    monkeypatch.setattr(host, "open_port", lambda _: pytest.fail("Unexpected hardware access"))
    return clock


def payload(length=452, stack=0x20010000, reset=0x08020395):
    return struct.pack("<II", stack, reset) + bytes(index % 256 for index in range(length - 8))


def image_bytes(body=None, **fields):
    if body is None:
        body = payload()
    values = dict(magic=0x55413345, format=1, board=0x0401C013,
                  length=len(body), crc=zlib.crc32(body), version=1)
    values.update(fields)
    header = struct.pack("<6I", *values.values())
    header += struct.pack("<I", zlib.crc32(header[4:24]))
    return header.ljust(512, b"\xff") + body


def handshake():
    return [("M115", UPDATER), ("HOLD", "OK HOLD"), ("M115", UPDATER)]


def begin(body):
    return f"BEGIN 0401C013 {len(body):08X} {zlib.crc32(body):08X}"


def blocks(body):
    return [
        (f"DATA {offset:08X} {body[offset:offset + 64].hex().upper()}",
         f"OK DATA {min(offset + 64, len(body)):08X}")
        for offset in range(0, len(body), 64)
    ]


def test_image_layout_roundtrip_and_known_crc():
    assert host.crc32(b"123456789") == 0xCBF43926
    assert host.pack_image(payload()) == image_bytes()
    assert host.validate_image(image_bytes()) == payload()
    assert host.MAX_PAYLOAD == 130560
    assert host.pack_image(payload(451)) == image_bytes(payload(451) + b"\xff")


@pytest.mark.parametrize("operation", [host.upload, host.interrupt_upload])
@pytest.mark.parametrize("other_image", [
    old_f401.pack_image(payload(stack=0x20018000)),
    image_bytes(payload(length=512, reset=0x080103E9), board=0x0103E013),
])
def test_cross_target_image_rejected_before_any_command(operation, other_image):
    port = ScriptedPort([])
    with pytest.raises(host.FirmwareError, match="board"):
        operation(host.Link(port), other_image)
    assert not port.commands


@pytest.mark.parametrize("other", [old_f401])
def test_other_host_rejects_compact_image(other):
    with pytest.raises(other.FirmwareError):
        other.validate_image(image_bytes())


@pytest.mark.parametrize("length", [408, 130560])
def test_minimum_and_maximum_valid_image_bounds(length):
    body = payload(length)
    assert host.validate_image(image_bytes(body)) == body


@pytest.mark.parametrize("length", [8, 400, 404, 407, 409, 130564])
def test_payload_requires_vectors_code_alignment_and_capacity(length):
    with pytest.raises(host.FirmwareError):
        host.validate_image(image_bytes(payload(length)))


@pytest.mark.parametrize("stack", [0x20000000, 0x20000004, 0x2000FFFF, 0x20010008, 0x20018000])
def test_stack_requires_64k_ram_and_eight_byte_alignment(stack):
    with pytest.raises(host.FirmwareError, match="stack"):
        host.validate_image(image_bytes(payload(stack=stack)))


@pytest.mark.parametrize("reset", [0, 0x08010001, 0x08020393, 0x08020394, 0x080203C5, 0x08040001])
def test_reset_requires_thumb_code_after_all_101_vectors(reset):
    with pytest.raises(host.FirmwareError, match="Reset vector"):
        host.validate_image(image_bytes(payload(reset=reset)))


@pytest.mark.parametrize("offset", [4, 24, 28, 511, 520, 963])
def test_corrupt_header_padding_or_payload_rejected_before_erase(offset):
    image = bytearray(image_bytes())
    image[offset] ^= 1
    port = ScriptedPort([])
    with pytest.raises(host.FirmwareError):
        host.upload(host.Link(port), bytes(image))
    assert not port.commands


@pytest.mark.parametrize("fields", [{"magic": 0}, {"format": 2}, {"version": 2},
                                    {"length": 448}, {"crc": 0}])
def test_valid_header_crc_does_not_allow_incorrect_metadata(fields):
    with pytest.raises(host.FirmwareError):
        host.validate_image(image_bytes(**fields))


@pytest.mark.parametrize("reply", [
    "ok", "FIRMWARE_NAME:Marlin 2.0.8.26F4\nok",
    "Cap:E3_USB_UPDATER_F401_V1:1\nok",
    "FIRMWARE_NAME:Marlin 2.0\nCap:E3_USB_UPDATER_F103_V1:1\nok",
    "E3AUX1 UPDATER 0.2.0 BOARD=0401E013",
    "E3AUX1 UPDATER 0.2.0 BOARD=0103E013",
    "E3AUX1 UPDATER 0.3.0 BOARD=0401E013", "ERR MCU_MISMATCH", None,
])
def test_wrong_missing_or_partial_identity_never_requests_update(reply):
    port = ScriptedPort([("M115", reply)])
    with pytest.raises(host.FirmwareError):
        host.upload(host.Link(port), image_bytes())
    assert port.commands == ["M115"]


def test_marlin_handoff_ack_hold_and_identity_required(offline):
    port = ScriptedPort([
        ("M115", MARLIN), ("M997", "E3USB:1 ENTERING_UPDATER"),
        ("HOLD", UPDATER + "\nOK HOLD"), ("M115", UPDATER),
    ])
    host.Link(port).hold_updater()
    assert not port.exchanges
    assert port.commands == ["M115", "M997", "HOLD", "M115"]
    assert port.input_resets == 1 and offline.sleeps == [0.5]


@pytest.mark.parametrize("response", ["Error:E3USB:1 PRECONDITION", "Error:E3USB:1 UPDATER_INVALID",
                                      "ok", None])
def test_refused_or_unconfirmed_entry_never_erases_or_retries(response):
    port = ScriptedPort([("M115", MARLIN), ("M997", response)])
    with pytest.raises(host.FirmwareError):
        host.upload(host.Link(port), image_bytes())
    assert port.commands == ["M115", "M997"] and port.input_resets == 0


@pytest.mark.parametrize("reply", ["OK", "ERR UNSUPPORTED", None])
def test_hold_failure_blocks_begin(reply):
    port = ScriptedPort([("M115", UPDATER), ("HOLD", reply)])
    with pytest.raises(host.FirmwareError):
        host.upload(host.Link(port), image_bytes())
    assert port.commands == ["M115", "HOLD"]


def test_identity_after_hold_must_still_be_matching_updater():
    port = ScriptedPort([("M115", UPDATER), ("HOLD", "OK HOLD"), ("M115", MARLIN)])
    with pytest.raises(host.FirmwareError, match="before erase"):
        host.upload(host.Link(port), image_bytes())
    assert port.commands == ["M115", "HOLD", "M115"]


def test_upload_commits_exact_blocks_and_never_boots():
    body = payload()
    exchanges = handshake() + [(begin(body), "OK BEGIN")] + blocks(body) + [("END", "OK END")]
    port = ScriptedPort(exchanges)
    host.upload(host.Link(port), image_bytes(body))
    assert not port.exchanges and port.commands == [command for command, _ in exchanges]
    assert "BOOT" not in port.commands


@pytest.mark.parametrize("stage,reply", [
    ("begin", "ERR ERASE"), ("begin", None),
    ("data", "ERR WRITE"), ("data", "OK DATA 00000080"), ("data", None),
    ("end", "ERR COMMIT"), ("end", "OK END extra"), ("end", None),
])
def test_uncertain_write_or_commit_stops_without_retry_or_boot(stage, reply):
    body = payload()
    exchanges = handshake() + [(begin(body), reply if stage == "begin" else "OK BEGIN")]
    if stage == "data":
        exchanges += [(blocks(body)[0][0], reply)]
    elif stage == "end":
        exchanges += blocks(body) + [("END", reply)]
    port = ScriptedPort(exchanges)
    with pytest.raises(host.FirmwareError):
        host.upload(host.Link(port), image_bytes(body))
    assert not port.exchanges and port.commands == [command for command, _ in exchanges]
    assert "BOOT" not in port.commands


def test_interrupted_upload_recovery_restarts_begin_and_requires_explicit_boot():
    body = payload()
    interrupted = handshake() + [(begin(body), "OK BEGIN"), blocks(body)[0]]
    recovered = handshake() + [(begin(body), "OK BEGIN")] + blocks(body) + [("END", "OK END")]
    port = ScriptedPort(interrupted + recovered)
    host.interrupt_upload(host.Link(port), image_bytes(body))
    assert port.commands == [command for command, _ in interrupted]
    assert "END" not in port.commands
    host.upload(host.Link(port), image_bytes(body))
    assert not port.exchanges and port.commands.count(begin(body)) == 2
    assert port.commands.count("END") == 1 and "BOOT" not in port.commands


def test_erase_uses_longer_deadline_without_retry(offline):
    body = payload()

    class SlowErase(ScriptedPort):
        waiting = 5000

        def read(self, size):
            if self.commands[-1].startswith("BEGIN ") and self.waiting:
                self.waiting -= 1
                return b""
            return super().read(size)

    port = SlowErase(handshake() + [(begin(body), "OK BEGIN"), blocks(body)[0]])
    host.interrupt_upload(host.Link(port), image_bytes(body))
    assert not port.exchanges and port.commands.count(begin(body)) == 1
    assert 5 < offline.now < 7


@pytest.mark.parametrize("response", [b"\xff\n", b"x" * 1025 + b"\n", b"unterminated", b""])
def test_non_ascii_oversized_or_unfinished_reply_rejected(response):
    port = ScriptedPort([])
    port.incoming.extend(response)
    with pytest.raises(host.FirmwareError):
        host.Link(port).receive()


def test_identity_has_one_overall_deadline(offline):
    port = ScriptedPort([("M115", "\n".join(["x" * 1000] * 4) + "\n" + MARLIN)])
    with pytest.raises(host.FirmwareError):
        host.Link(port).identity()
    assert port.commands == ["M115"] and offline.now < 3.02


def test_identity_has_line_count_bound_even_before_deadline():
    port = ScriptedPort([("M115", "echo:x\n" * 128 + MARLIN)])
    with pytest.raises(host.FirmwareError, match="No confirmed"):
        host.Link(port).identity()
    assert port.commands == ["M115"]


@pytest.mark.parametrize("marker", ["start", "echo:Marlin 2.0.8.24F4"])
def test_reset_between_identity_and_ack_prevents_maintenance_entry(marker):
    prefix = MARLIN.rsplit("\nok", 1)[0]
    port = ScriptedPort([("M115", prefix + "\n" + marker + "\nok")])
    with pytest.raises(host.FirmwareError):
        host.Link(port).hold_updater()
    assert port.commands == ["M115"]


def test_reset_then_fresh_identity_capability_and_ack_are_accepted():
    prefix = MARLIN.rsplit("\nok", 1)[0]
    port = ScriptedPort([("M115", prefix + "\nstart\n" + MARLIN)])
    assert host.Link(port).identity() == "MARLIN_F401"
    assert port.commands == ["M115"]


@pytest.mark.parametrize("new_identity", ["FIRMWARE_NAME:Marlin 2.0.8.26F4",
                                         "FIRMWARE_NAME:OtherFirmware 1.0"])
def test_new_identity_cannot_inherit_previous_capability(new_identity):
    prefix = MARLIN.rsplit("\nok", 1)[0]
    port = ScriptedPort([("M115", prefix + "\n" + new_identity + "\nok")])
    with pytest.raises(host.FirmwareError):
        host.Link(port).hold_updater()
    assert port.commands == ["M115"]


def test_partial_write_does_not_retry_or_flush():
    writes = []
    port = SimpleNamespace(write=lambda data: writes.append(data) or len(data) - 1)
    with pytest.raises(host.FirmwareError, match="incomplete"):
        host.Link(port).send("M115")
    assert writes == [b"M115\n"]


@pytest.mark.parametrize("action", ["inspect", "boot", "upload", "interrupt-upload"])
def test_cli_requires_hardware_enable_before_open(action, tmp_path, capsys):
    args = [action, "--port", "FAKE"]
    if action in {"upload", "interrupt-upload"}:
        path = tmp_path / "application.e3fw"
        path.write_bytes(image_bytes())
        args.append(str(path))
    assert host.main(args) == 1
    assert "--hardware-enabled" in capsys.readouterr().err


def test_cli_validates_other_target_before_open(tmp_path, capsys):
    path = tmp_path / "wrong-board.e3fw"
    path.write_bytes(image_bytes(board=0x0401E013))
    assert host.main(["upload", str(path), "--port", "FAKE", "--hardware-enabled"]) == 1
    assert "Wrong image" in capsys.readouterr().err


def test_offline_verify_and_file_size_bound(tmp_path):
    path = tmp_path / "application.e3fw"
    path.write_bytes(image_bytes())
    assert host.main(["verify", str(path)]) == 0
    path.write_bytes(bytes(131073))
    with pytest.raises(host.FirmwareError, match="too large"):
        host.read_image(path)


def test_cli_closes_port_on_failure_and_boot_is_separate(tmp_path, monkeypatch, capsys):
    path = tmp_path / "application.e3fw"
    path.write_bytes(image_bytes())
    failed = ScriptedPort(handshake() + [(begin(payload()), None)])
    monkeypatch.setattr(host, "open_port", lambda _: failed)
    assert host.main(["upload", str(path), "--port", "FAKE", "--hardware-enabled"]) == 1
    assert failed.closed and "BOOT" not in failed.commands
    assert "Stopped:" in capsys.readouterr().err
    boot = ScriptedPort(handshake() + [("BOOT", "OK BOOT")])
    monkeypatch.setattr(host, "open_port", lambda _: boot)
    assert host.main(["boot", "--port", "FAKE", "--hardware-enabled"]) == 0
    assert boot.closed and boot.commands[-1] == "BOOT"


def fake_linux_serial(monkeypatch, *, state="inactive\n", code=0, error=None, fail_open=False):
    events = []

    def run(args, **kwargs):
        assert args == ["systemctl", "show", "e3-hardware-node.service", "--property=ActiveState", "--value"]
        assert kwargs == dict(text=True, capture_output=True, timeout=5, check=False)
        events.append("service")
        if error is not None:
            raise error
        return SimpleNamespace(stdout=state, returncode=code)

    class Serial:
        def __init__(self, **options):
            events.append(("create", options))

        def open(self):
            events.append(("open", self.port, self.dtr, self.rts))
            if fail_open:
                raise OSError("busy")

        def reset_input_buffer(self):
            events.append("reset_input")

        def close(self):
            events.append("close")

    monkeypatch.setattr(host, "sys", SimpleNamespace(platform="linux", stderr=sys.stderr))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=Serial))
    return events


@pytest.mark.parametrize("state", ["inactive\n", "failed\n"])
def test_linux_stopped_service_required_and_port_exclusive(monkeypatch, state):
    events = fake_linux_serial(monkeypatch, state=state)
    port = REAL_OPEN_PORT("/fake/ender")
    port.close()
    assert events == [
        "service",
        ("create", dict(port=None, baudrate=115200, timeout=0.1, write_timeout=3, exclusive=True)),
        ("open", "/fake/ender", False, False), "reset_input", "close",
    ]


@pytest.mark.parametrize("state,code", [("active\n", 0), ("activating\n", 0),
                                       ("deactivating\n", 0), ("", 1)])
def test_linux_service_unconfirmed_does_not_construct_serial(monkeypatch, state, code):
    events = fake_linux_serial(monkeypatch, state=state, code=code)
    with pytest.raises(host.FirmwareError, match="no port was opened"):
        REAL_OPEN_PORT("/fake/ender")
    assert events == ["service"]


def test_linux_open_failure_closes_without_reset(monkeypatch):
    events = fake_linux_serial(monkeypatch, fail_open=True)
    with pytest.raises(OSError, match="busy"):
        REAL_OPEN_PORT("/fake/ender")
    assert events[-2:] == [("open", "/fake/ender", False, False), "close"]


def test_linux_service_timeout_fails_closed_and_reports_cleanly(monkeypatch, capsys):
    events = fake_linux_serial(monkeypatch, error=subprocess.TimeoutExpired("systemctl", 5))
    monkeypatch.setattr(host, "open_port", REAL_OPEN_PORT)
    assert host.main(["inspect", "--port", "/fake/ender", "--hardware-enabled"]) == 1
    assert events == ["service"]
    assert "Stopped:" in capsys.readouterr().err
