from types import SimpleNamespace

import pytest

from scripts import diagnose_ender_startup as diagnostic


class Serial:
    def __init__(self, replies=None, startup=b""):
        self.replies = replies or {}
        self.pending = startup
        self.writes = []
        self.now = 0

    def clock(self):
        self.now += 0.25
        return self.now

    def read(self, count):
        data, self.pending = self.pending[:count], self.pending[count:]
        return data

    def write(self, data):
        self.writes.append((self.now, data))
        self.pending += self.replies.get(data, b"")
        return len(data)


@pytest.mark.parametrize("reply,role", [
    (b"FIRMWARE_NAME:Marlin 2.0.8.26F4\nok\n", "marlin"),
    (b"FIRMWARE_NAME:Marlin custom\nCap:E3_MAINBOARD_V1:1\n"
     b"Cap:E3_MATERIAL_HEIGHT_V1:1\nCap:EMERGENCY_PARSER:1\nok\n", "e3_marlin"),
    (b"FIRMWARE_NAME:Marlin incomplete\n", "unidentified_bytes"),
    (b"ok\n", "unidentified_bytes"),
    (b"", "no_response"),
    (b"\xff\x01", "unidentified_bytes"),
    (b"E3AUX1 UPDATER 0.2.0 BOARD=0401E013\n", "f401_updater_0.2.0"),
    (b"E3AUX1 UPDATER 0.2.0 BOARD=0103E013\n", "f103_updater_0.2.0"),
])
def test_identity_is_bounded_and_read_only(reply, role):
    port = Serial({b"M115\n": reply})
    result = diagnostic.capture(port, clock=port.clock)
    assert result["reported_role"] == role
    assert [data for _, data in port.writes] == [b"M115\n"]
    assert 35 <= port.writes[0][0] < 37
    assert port.now < 46
    assert result["phases"][1]["hex"] == reply.hex()
    assert result["physical_functions_verified"] is False


def test_startup_error_is_preserved_even_when_query_is_silent():
    port = Serial(startup=b"ERR MCU_MISMATCH\n")
    assert diagnostic.capture(port, clock=port.clock)["reported_role"] == "mcu_mismatch_reported"


def test_old_updater_info_fallback_only_after_explicit_unsupported():
    port = Serial({b"M115\n": b"ERR UNSUPPORTED\n",
                   b"INFO\n": b"E3AUX1 UPDATER 0.1.0 BOARD=0401E013\n"})
    assert diagnostic.capture(port, clock=port.clock)["reported_role"] == "f401_updater_0.1.0"
    assert [data for _, data in port.writes] == [b"M115\n", b"INFO\n"]


def test_does_not_combine_old_identity_with_new_ack():
    port = Serial({b"M115\n": b"ok\n"}, startup=b"FIRMWARE_NAME:Marlin old\n")
    assert diagnostic.capture(port, clock=port.clock)["reported_role"] == "unidentified_bytes"


def test_receive_limit_stops_without_query():
    port = Serial(startup=b"x" * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        diagnostic.capture(port, clock=port.clock)
    assert not port.writes


def test_short_write_stops_without_retry():
    port = Serial()
    port.write = lambda data: 1
    with pytest.raises(OSError, match="Incomplete"):
        diagnostic.capture(port, clock=port.clock)


@pytest.mark.parametrize("state,code,allowed", [
    ("inactive\n", 0, True), ("failed\n", 0, True), ("active\n", 0, False),
    ("activating\n", 0, False), ("deactivating\n", 0, False), ("", 1, False),
])
def test_service_gate(monkeypatch, state, code, allowed):
    def run(args, **kwargs):
        assert args == ["systemctl", "show", "e3-hardware-node.service",
                        "--property=ActiveState", "--value"]
        assert kwargs["timeout"] == 5
        return SimpleNamespace(stdout=state, returncode=code)
    monkeypatch.setattr(diagnostic.subprocess, "run", run)
    if allowed:
        diagnostic.require_stopped_service()
    else:
        with pytest.raises(ValueError, match="no port was opened"):
            diagnostic.require_stopped_service()


@pytest.mark.parametrize("fail", [False, True])
def test_exclusive_open_no_deliberate_reset_and_always_close(fail):
    port = Serial()
    events = []

    def factory(**kwargs):
        assert kwargs == dict(port=None, baudrate=115200, timeout=0.2,
                              write_timeout=2, exclusive=True)
        return port

    def open_port():
        assert port.port == "/test/ender" and port.dtr is False and port.rts is False
        events.append("open")
        if fail:
            raise OSError("busy")
    port.open = open_port
    port.close = lambda: events.append("close")
    if fail:
        with pytest.raises(OSError):
            diagnostic.inspect_adapter(factory, "/test/ender", clock=port.clock)
    else:
        diagnostic.inspect_adapter(factory, "/test/ender", clock=port.clock)
    assert events == ["open", "close"]


def test_hardware_flag_required():
    with pytest.raises(SystemExit):
        diagnostic.main(["--port", "/test/ender"])


def test_linux_only_operation_but_portable_import(monkeypatch):
    monkeypatch.setattr(diagnostic.sys, "platform", "win32")
    with pytest.raises(SystemExit):
        diagnostic.main(["--port", "COM6", "--hardware-enabled"])
