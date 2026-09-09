from types import SimpleNamespace

import pytest

from scripts.inspect_ender_usb import identify, inspect_adapter, main, select_new_port


def adapter(device, vid=0x1A86, pid=0x7523):
    return SimpleNamespace(device=device, vid=vid, pid=pid)


def test_select_only_new_ch340_leaves_existing_spare_excluded():
    new = adapter("COM9")
    assert select_new_port({"COM6"}, [adapter("COM6"), adapter("COM8", 123), new]) is new


@pytest.mark.parametrize("ports", [[], [adapter("COM6")], [adapter("COM8", 123)],
                                   [adapter("COM8"), adapter("COM9")]])
def test_ambiguous_absent_existing_or_wrong_adapter_rejects(ports):
    with pytest.raises(ValueError, match="No port was opened"):
        select_new_port({"COM6"}, ports)


class Serial:
    def __init__(self, replies=None, startup=b""):
        self.replies = replies or {}
        self.pending = startup
        self.writes = []
        self.now = 0

    def clock(self):
        self.now += 0.25
        return self.now

    def read(self, size):
        data, self.pending = self.pending[:size], self.pending[size:]
        return data

    def write(self, data):
        self.writes.append((self.now, data))
        self.pending += self.replies.get(data, b"")
        return len(data)


def test_marlin_identity_waits_and_sends_only_m115():
    port = Serial({b"M115\n": b"FIRMWARE_NAME:Marlin test\nCap:E3_MAINBOARD_V1:1\n"
                   b"Cap:E3_MATERIAL_HEIGHT_V1:1\nCap:EMERGENCY_PARSER:1\nok\n"}, b"start\n")
    result = identify(port, clock=port.clock)
    assert result["reported_role"] == "e3_mainboard_v1_reported"
    assert result["phases"][0]["text"] == "start\n"
    assert port.writes == [(port.writes[0][0], b"M115\n")]
    assert port.writes[0][0] >= 35
    assert result["physical_functions_verified"] is False


def test_updater_is_identified_without_hold_boot_or_flash():
    port = Serial({b"M115\n": b"ERR UNSUPPORTED\n",
                   b"INFO\n": b"E3AUX1 UPDATER 0.1.0 BOARD=0401E013\n"})
    assert identify(port, clock=port.clock)["reported_role"] == "e3_updater_0_1_0"
    assert [data for _, data in port.writes] == [b"M115\n", b"INFO\n"]


@pytest.mark.parametrize("reply, role", [
    (b"", "no_response"), (b"garbage\n", "unidentified"),
    (b"FIRMWARE_NAME:Marlin stock\nok\n", "marlin"),
    (b"FIRMWARE_NAME:Marlin missing_ack\n", "unidentified"),
])
def test_missing_unknown_and_stock_replies_do_not_retry(reply, role):
    port = Serial({b"M115\n": reply})
    assert identify(port, clock=port.clock)["reported_role"] == role
    assert [data for _, data in port.writes] == [b"M115\n"]


def test_startup_limit_prevents_any_query():
    port = Serial(startup=b"x" * 65537)
    with pytest.raises(ValueError, match="64 KiB"):
        identify(port, clock=port.clock)
    assert not port.writes


def test_short_write_does_not_retry():
    port = Serial()
    port.write = lambda data: 1
    with pytest.raises(OSError, match="Incomplete"):
        identify(port, clock=port.clock)


def test_hardware_flag_required_before_port_enumeration():
    with pytest.raises(SystemExit):
        main([])


@pytest.mark.parametrize("fail", [False, True])
def test_serial_open_is_explicit_and_close_runs_on_failure(fail):
    port = Serial()
    events = []

    def factory(**kwargs):
        assert kwargs == {"port": None, "baudrate": 115200, "timeout": 0.2, "write_timeout": 2}
        return port

    def open_port():
        assert port.port == "COM9" and port.dtr is False and port.rts is False
        events.append("open")
        if fail:
            raise OSError("unavailable")

    port.open = open_port
    port.close = lambda: events.append("close")
    if fail:
        with pytest.raises(OSError, match="unavailable"):
            inspect_adapter(factory, "COM9", clock=port.clock)
        assert port.writes == []
    else:
        assert inspect_adapter(factory, "COM9", clock=port.clock)["reported_role"] == "no_response"
    assert events == ["open", "close"]
