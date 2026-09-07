"""Offline image and maintenance-protocol tests; never access a controller."""

import struct
import sys
import zlib
from types import SimpleNamespace

import pytest

from firmware.ender_aux import host

UPDATER = "E3AUX1 UPDATER 0.1.0 BOARD=0401E013"
APPLICATION = "E3AUX1 APP 0.1.0 BOARD=0401E013"


def payload_bytes(length=452, *, stack=0x20018000, reset=0x08020395):
    return struct.pack("<II", stack, reset) + bytes(i % 256 for i in range(length - 8))


def image_bytes(payload=None, **fields):
    """Encode independently of the implementation's builder and constants."""
    if payload is None:
        payload = payload_bytes()
    values = {
        "magic": 0x55413345,
        "format": 1,
        "board": 0x0401E013,
        "length": len(payload),
        "checksum": zlib.crc32(payload),
        "version": 1,
    }
    values.update(fields)
    header = struct.pack("<6I", *values.values())
    header += struct.pack("<I", zlib.crc32(header[4:24]))
    return header.ljust(512, b"\xff") + payload


class ScriptedPort:
    """Only release an ACK after the expected command has been written."""

    def __init__(self, exchanges):
        self.exchanges = list(exchanges)
        self.incoming = bytearray()
        self.commands = []
        self.flushed = 0
        self.input_resets = 0
        self.closed = False

    def write(self, data):
        assert data.endswith(b"\n")
        command = data[:-1].decode("ascii")
        self.commands.append(command)
        assert self.exchanges, f"Unexpected command or automatic retry: {command}"
        expected, response = self.exchanges.pop(0)
        assert command == expected
        if response is not None:
            if isinstance(response, str):
                response = response.encode("ascii") + b"\n"
            self.incoming.extend(response)
        return len(data)

    def read(self, size):
        assert size == 1
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def flush(self):
        self.flushed += 1

    def reset_input_buffer(self):
        self.input_resets += 1
        self.incoming.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.closed = True


@pytest.fixture(autouse=True)
def offline_clock_and_port(monkeypatch):
    # Progress time deterministically, including empty reads, without sleeping.
    clock = SimpleNamespace(now=0.0, sleeps=[])

    def monotonic():
        clock.now += 0.001
        return clock.now

    def forbidden_port(_name):
        pytest.fail("This test must not open any hardware port")

    monkeypatch.setattr(host, "time", SimpleNamespace(monotonic=monotonic, sleep=clock.sleeps.append))
    monkeypatch.setattr(host, "open_port", forbidden_port)
    return clock


def updater_handshake():
    return [("INFO", UPDATER), ("HOLD", "OK HOLD"), ("INFO", UPDATER)]


def begin_command(payload):
    return f"BEGIN 0401E013 {len(payload):08X} {zlib.crc32(payload):08X}"


def data_exchanges(payload):
    return [
        (f"DATA {offset:08X} {payload[offset:offset + 64].hex().upper()}",
         f"OK DATA {min(offset + 64, len(payload)):08X}")
        for offset in range(0, len(payload), 64)
    ]


def test_validate_independently_encoded_image_and_known_crc():
    assert host.crc32(b"123456789") == 0xCBF43926
    assert host.validate_image(image_bytes()) == payload_bytes()


def test_pack_image_pads_payload_and_matches_wire_format():
    payload = payload_bytes(451)
    packed = host.pack_image(payload)
    assert packed == image_bytes(payload + b"\xff")
    assert host.validate_image(packed) == payload + b"\xff"


@pytest.mark.parametrize("payload", [b"", b"1234", b"1234567"])
def test_pack_rejects_invalid_vector_payload(payload):
    with pytest.raises(host.FirmwareError):
        host.pack_image(payload)


@pytest.mark.parametrize("fields", [
    {"magic": 0}, {"format": 2}, {"board": 0x0401E014}, {"version": 2},
    {"length": 0}, {"length": 128}, {"length": 136}, {"length": 0xFFFFFFFF},
    {"checksum": 0},
])
def test_valid_header_crc_does_not_allow_wrong_metadata(fields):
    with pytest.raises(host.FirmwareError):
        host.validate_image(image_bytes(**fields))


@pytest.mark.parametrize("offset", [4, 24, 28, 511, 520])
def test_image_integrity_rejects_corrupted_metadata_reserved_bytes_or_payload(offset):
    image = bytearray(image_bytes())
    image[offset] ^= 1
    with pytest.raises(host.FirmwareError):
        host.validate_image(bytes(image))


@pytest.mark.parametrize("image", [b"", b"\xff" * 512, image_bytes()[:-1], image_bytes() + b"\xff"])
def test_image_truncation_and_trailing_data_rejected(image):
    with pytest.raises(host.FirmwareError):
        host.validate_image(image)


def test_word_unaligned_payload_rejected_despite_correct_length_and_crc():
    with pytest.raises(host.FirmwareError, match="word aligned"):
        host.validate_image(image_bytes(payload_bytes(451)))


@pytest.mark.parametrize("length", [8, 132, 400, 404, 405, 407])
def test_payload_needs_full_vector_table_and_code(length):
    with pytest.raises(host.FirmwareError):
        host.validate_image(image_bytes(payload_bytes(length)))


def test_minimum_payload_has_complete_vector_table_and_reset_code():
    payload = payload_bytes(408)
    assert host.validate_image(image_bytes(payload)) == payload


@pytest.mark.parametrize("stack", [0, 0x20000000, 0x20000004, 0x20018004, 0x20018008, 0x08020000])
def test_stack_must_be_aligned_and_within_ram(stack):
    with pytest.raises(host.FirmwareError, match="stack"):
        host.validate_image(image_bytes(payload_bytes(stack=stack)))


@pytest.mark.parametrize("reset", [
    0, 0x08020394, 0x08010001, 0x080201FF, 0x08020209, 0x08020393, 0x080203C5, 0x20000001,
])
def test_reset_vector_requires_thumb_and_address_within_payload(reset):
    with pytest.raises(host.FirmwareError, match="Reset vector"):
        host.validate_image(image_bytes(payload_bytes(reset=reset)))


def test_largest_application_fits_but_cannot_enter_reserved_bootloader_region():
    capacity = 0x08080000 - 0x08020200
    payload = payload_bytes() + b"\xff" * (capacity - 452)
    assert host.validate_image(image_bytes(payload)) == payload
    with pytest.raises(host.FirmwareError):
        host.validate_image(image_bytes(payload + b"\xff" * 4))


@pytest.mark.parametrize("action", ["inspect", "boot", "upload", "interrupt-upload"])
def test_hardware_flag_required_before_any_port_open(action, tmp_path, capsys):
    args = [action, "--port", "COM4"]
    if action in ("upload", "interrupt-upload"):
        path = tmp_path / "test.e3a"
        path.write_bytes(image_bytes())
        args.append(str(path))
    assert host.main(args) == 1
    assert "--hardware-enabled" in capsys.readouterr().err


@pytest.mark.parametrize("action", ["inspect", "boot", "upload", "interrupt-upload"])
def test_no_implicit_com_port(action):
    args = [action, "--hardware-enabled"]
    if action in ("upload", "interrupt-upload"):
        args.append("unused.e3a")
    with pytest.raises(SystemExit) as exc:
        host.main(args)
    assert exc.value.code == 2


def test_offline_verify_never_needs_hardware(tmp_path, capsys):
    path = tmp_path / "test.e3a"
    path.write_bytes(image_bytes())
    assert host.main(["verify", str(path)]) == 0
    assert "452 payload bytes" in capsys.readouterr().out


@pytest.mark.parametrize("action", ["upload", "interrupt-upload"])
def test_invalid_upload_never_opens_port_even_with_hardware_flag(action, tmp_path, capsys):
    path = tmp_path / "bad.e3a"
    path.write_bytes(image_bytes(board=0))
    assert host.main([action, str(path), "--port", "COM4", "--hardware-enabled"]) == 1
    assert "Wrong image" in capsys.readouterr().err


def test_oversized_file_is_rejected_offline(tmp_path):
    path = tmp_path / "oversized.e3a"
    with path.open("wb") as file:
        file.seek(0x60000)
        file.write(b"\x00")
    with pytest.raises(host.FirmwareError, match="too large"):
        host.read_image(path)


def test_upload_validates_before_even_sending_identity():
    port = ScriptedPort([])
    with pytest.raises(host.FirmwareError):
        host.upload(host.Link(port), b"invalid")
    assert not port.commands


@pytest.mark.parametrize("identity", [
    "FIRMWARE_NAME:Marlin 2.0.8.26F4", "ok", "E3AUX1 UPDATER 0.1.1 BOARD=0401E013",
    "E3AUX1 UPDATER 0.1.0 BOARD=0401E014", "E3AUX1 UNKNOWN 0.1.0 BOARD=0401E013",
])
def test_upload_rejects_stock_or_unrecognized_identity_before_erase(identity):
    port = ScriptedPort([("INFO", identity)])
    with pytest.raises(host.FirmwareError, match="Unsupported firmware identity"):
        host.upload(host.Link(port), image_bytes())
    assert port.commands == ["INFO"]


@pytest.mark.parametrize("response", ["ERR UNSUPPORTED", "OK", None])
def test_hold_must_be_acknowledged_before_begin(response):
    port = ScriptedPort([("INFO", UPDATER), ("HOLD", response)])
    with pytest.raises(host.FirmwareError):
        host.upload(host.Link(port), image_bytes())
    assert port.commands == ["INFO", "HOLD"]


def test_second_identity_must_confirm_updater_before_begin():
    port = ScriptedPort([("INFO", UPDATER), ("HOLD", "OK HOLD"), ("INFO", APPLICATION)])
    with pytest.raises(host.FirmwareError, match="Expected the updater"):
        host.upload(host.Link(port), image_bytes())
    assert port.commands == ["INFO", "HOLD", "INFO"]


def test_application_update_entry_is_acknowledged_and_rechecked(offline_clock_and_port):
    port = ScriptedPort([
        ("INFO", APPLICATION), ("UPDATE", "OK UPDATE"),
        ("HOLD", f"{UPDATER}\nOK HOLD"), ("INFO", UPDATER),
    ])
    host.Link(port).hold_updater()
    assert port.commands == ["INFO", "UPDATE", "HOLD", "INFO"]
    assert port.input_resets == 1
    assert offline_clock_and_port.sleeps == [0.5]


@pytest.mark.parametrize("response", [None, "ERR UNSUPPORTED", "OK"])
def test_update_entry_failure_sends_no_hold_or_erase(response):
    port = ScriptedPort([("INFO", APPLICATION), ("UPDATE", response)])
    with pytest.raises(host.FirmwareError):
        host.upload(host.Link(port), image_bytes())
    assert port.commands == ["INFO", "UPDATE"]
    assert port.input_resets == 0


def test_upload_waits_for_each_exact_offset_ack_and_never_boots():
    payload = payload_bytes()
    exchanges = updater_handshake() + [(begin_command(payload), "OK BEGIN")]
    exchanges += data_exchanges(payload) + [("END", "OK END")]
    port = ScriptedPort(exchanges)
    host.upload(host.Link(port), image_bytes(payload))
    assert not port.exchanges
    assert port.commands[-1] == "END"
    assert "BOOT" not in port.commands
    assert len([c for c in port.commands if c.startswith("BEGIN ")]) == 1
    assert port.flushed == len(port.commands)


@pytest.mark.parametrize("response", [None, "ERR ERASE", "OK BEGIN extra", "OK DATA 00000040"])
def test_begin_failure_stops_without_data_end_boot_or_retry(response):
    payload = payload_bytes()
    port = ScriptedPort(updater_handshake() + [(begin_command(payload), response)])
    with pytest.raises(host.FirmwareError):
        host.upload(host.Link(port), image_bytes(payload))
    assert port.commands == ["INFO", "HOLD", "INFO", begin_command(payload)]


@pytest.mark.parametrize("response", [None, "OK DATA 00000000", "OK DATA 00000080", "OK", "ERR WRITE"])
def test_uncertain_or_wrong_data_ack_stops_without_retry_or_commit(response):
    payload = payload_bytes()
    first, _ = data_exchanges(payload)[0]
    exchanges = updater_handshake() + [(begin_command(payload), "OK BEGIN"), (first, response)]
    port = ScriptedPort(exchanges)
    with pytest.raises(host.FirmwareError):
        host.upload(host.Link(port), image_bytes(payload))
    assert port.commands[-1] == first
    assert port.commands.count(first) == 1
    assert "END" not in port.commands and "BOOT" not in port.commands


@pytest.mark.parametrize("response", [None, "ERR COMMIT", "OK END extra"])
def test_commit_failure_never_retries_or_boots(response):
    payload = payload_bytes()
    exchanges = updater_handshake() + [(begin_command(payload), "OK BEGIN")]
    exchanges += data_exchanges(payload) + [("END", response)]
    port = ScriptedPort(exchanges)
    with pytest.raises(host.FirmwareError):
        host.upload(host.Link(port), image_bytes(payload))
    assert port.commands[-1] == "END"
    assert port.commands.count("END") == 1 and "BOOT" not in port.commands


def test_partial_write_is_not_retried_or_flushed():
    writes = []
    port = SimpleNamespace(write=lambda data: writes.append(data) or len(data) - 1)
    with pytest.raises(host.FirmwareError, match="incomplete"):
        host.Link(port).send("INFO")
    assert writes == [b"INFO\n"]


@pytest.mark.parametrize("response", [b"\xff\n", b"x" * 257 + b"\n", b"unterminated", b""])
def test_malformed_or_incomplete_serial_response_rejected(response):
    port = ScriptedPort([])
    port.incoming.extend(response)
    with pytest.raises(host.FirmwareError):
        host.Link(port).receive()


def test_blank_lines_and_crlf_are_tolerated():
    port = ScriptedPort([])
    port.incoming.extend(b"\nOK HOLD\r\n")
    assert host.Link(port).receive() == "OK HOLD"


def test_cli_upload_closes_port_after_timeout_and_reports_stopped(tmp_path, monkeypatch, capsys):
    payload = payload_bytes()
    path = tmp_path / "test.e3a"
    path.write_bytes(image_bytes(payload))
    port = ScriptedPort(updater_handshake() + [(begin_command(payload), None)])
    names = []
    monkeypatch.setattr(host, "open_port", lambda name: names.append(name) or port)
    assert host.main(["upload", str(path), "--port", "COM4", "--hardware-enabled"]) == 1
    assert names == ["COM4"]
    assert port.closed
    assert "Stopped:" in capsys.readouterr().err
    assert port.commands[-1] == begin_command(payload)


def test_cli_boot_is_a_separate_explicit_command(monkeypatch, capsys):
    port = ScriptedPort(updater_handshake() + [("BOOT", "OK BOOT")])
    monkeypatch.setattr(host, "open_port", lambda _name: port)
    assert host.main(["boot", "--port", "COM4", "--hardware-enabled"]) == 0
    assert port.commands[-1] == "BOOT"
    assert port.closed
    assert "Boot requested" in capsys.readouterr().out


@pytest.mark.parametrize("role", ["APP", "UPDATER"])
def test_interrupt_upload_cli_sends_one_block_and_closes_without_commit_or_boot(
    role, tmp_path, monkeypatch, capsys, offline_clock_and_port,
):
    payload = payload_bytes()
    path = tmp_path / "recovery-test.e3fw"
    path.write_bytes(image_bytes(payload))
    if role == "APP":
        exchanges = [
            ("INFO", APPLICATION), ("UPDATE", "OK UPDATE"),
            ("HOLD", "OK HOLD"), ("INFO", UPDATER),
        ]
    else:
        exchanges = updater_handshake()
    exchanges += [(begin_command(payload), "OK BEGIN"), data_exchanges(payload)[0]]
    expected_commands = [command for command, _ in exchanges]

    class SlowErasePort(ScriptedPort):
        remaining_erase_reads = 5000

        def read(self, size):
            # Five simulated seconds before BEGIN ACK exercises the longer
            # erase allowance rather than the normal three-second reply limit.
            if self.commands[-1].startswith("BEGIN ") and self.remaining_erase_reads:
                self.remaining_erase_reads -= 1
                return b""
            return super().read(size)

    port = SlowErasePort(exchanges)
    opened = []
    monkeypatch.setattr(host, "open_port", lambda name: opened.append(name) or port)
    assert host.main(["interrupt-upload", str(path), "--port", "COM6", "--hardware-enabled"]) == 0
    assert opened == ["COM6"]
    assert port.closed and not port.exchanges
    assert port.commands == expected_commands
    assert port.commands[-1] == f"DATA 00000000 {payload[:64].hex().upper()}"
    assert "END" not in port.commands and "BOOT" not in port.commands
    assert offline_clock_and_port.sleeps == ([0.5] if role == "APP" else [])
    assert capsys.readouterr().out.strip() == (
        "Application deliberately left incomplete after 64 bytes. No commit or boot was sent."
    )


def test_interrupt_upload_validates_entire_image_before_sending_identity():
    # Damage bytes after the only block that the recovery test will transmit.
    image = bytearray(image_bytes())
    image[-1] ^= 1
    port = ScriptedPort([])
    with pytest.raises(host.FirmwareError, match="CRC"):
        host.interrupt_upload(host.Link(port), bytes(image))
    assert not port.commands


def test_interrupt_upload_corrupt_tail_rejected_before_port_open(tmp_path, capsys):
    image = bytearray(image_bytes())
    image[-1] ^= 1
    path = tmp_path / "corrupt-tail.e3fw"
    path.write_bytes(image)
    assert host.main(["interrupt-upload", str(path), "--port", "COM6", "--hardware-enabled"]) == 1
    assert "CRC" in capsys.readouterr().err


@pytest.mark.parametrize("exchanges", [
    [("INFO", "FIRMWARE_NAME:Marlin 2.0.8.26F4")],
    [("INFO", "E3AUX1 UPDATER 0.1.0 BOARD=0401E014")],
    [("INFO", None)],
    [("INFO", APPLICATION), ("UPDATE", None)],
    [("INFO", APPLICATION), ("UPDATE", "ERR UNSUPPORTED")],
    [("INFO", UPDATER), ("HOLD", "OK")],
    [("INFO", UPDATER), ("HOLD", None)],
    [("INFO", UPDATER), ("HOLD", "OK HOLD"), ("INFO", APPLICATION)],
    updater_handshake() + [(begin_command(payload_bytes()), None)],
    updater_handshake() + [(begin_command(payload_bytes()), "ERR ERASE")],
    updater_handshake() + [(begin_command(payload_bytes()), "OK BEGIN extra")],
    updater_handshake() + [(begin_command(payload_bytes()), "OK BEGIN"),
                           (data_exchanges(payload_bytes())[0][0], None)],
    updater_handshake() + [(begin_command(payload_bytes()), "OK BEGIN"),
                           (data_exchanges(payload_bytes())[0][0], "OK DATA 00000000")],
    updater_handshake() + [(begin_command(payload_bytes()), "OK BEGIN"),
                           (data_exchanges(payload_bytes())[0][0], "OK DATA 00000080")],
    updater_handshake() + [(begin_command(payload_bytes()), "OK BEGIN"),
                           (data_exchanges(payload_bytes())[0][0], "ERR WRITE")],
])
def test_interrupt_upload_failure_closes_port_without_retry_commit_or_boot(exchanges, tmp_path, monkeypatch, capsys):
    path = tmp_path / "recovery-test.e3fw"
    path.write_bytes(image_bytes())
    port = ScriptedPort(exchanges)
    opened = []
    monkeypatch.setattr(host, "open_port", lambda name: opened.append(name) or port)
    assert host.main(["interrupt-upload", str(path), "--port", "COM6", "--hardware-enabled"]) == 1
    assert opened == ["COM6"]
    assert port.closed and not port.exchanges
    assert port.commands == [command for command, _ in exchanges]
    assert "END" not in port.commands and "BOOT" not in port.commands
    output = capsys.readouterr()
    assert "Stopped:" in output.err
    assert "deliberately left incomplete" not in output.out


def test_open_port_configures_control_lines_before_open(monkeypatch):
    # Exercise the actual open_port against a fake imported serial module.
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location("ender_host_port_test", Path(host.__file__))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    events = []

    class FakeSerial:
        def __init__(self, **kwargs):
            events.append(("create", kwargs))

        def open(self):
            events.append(("open", self.port, self.dtr, self.rts))

        def reset_input_buffer(self):
            events.append(("reset_input",))

    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=FakeSerial))
    module.open_port("COM4")
    assert events == [
        ("create", {"port": None, "baudrate": 115200, "timeout": 0.1, "write_timeout": 3}),
        ("open", "COM4", False, False), ("reset_input",),
    ]
