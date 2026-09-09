from __future__ import annotations

import pytest

from laser_aligner.machine import secondary_controller as controller
from laser_aligner.machine.secondary_startup import wait_for_marlin
from tests.test_secondary_controller import _binding


class Peer:
    def __init__(self, ready_at=0.0, reply=None, off_reply="ok"):
        self.now = 0.0
        self.ready_at = ready_at
        self.reply = reply
        self.off_reply = off_reply
        self.pending = []
        self.writes = []
        self.opens = self.closes = self.syncs = 0

    def open(self):
        self.opens += 1

    def close(self):
        self.closes += 1

    def synchronize_input(self):
        self.pending.clear()
        self.syncs += 1

    def write_line(self, line):
        self.writes.append(line)
        if line == "M115":
            if self.reply is not None:
                self.pending.extend(self.reply)
            elif self.now >= self.ready_at:
                self.pending.extend(["FIRMWARE_NAME:Marlin 2.0.8.24F4 MACHINE_TYPE:Ender-3 S1 Pro",
                                     "Cap:E3_MAINBOARD_V1:1", "ok", "ok"])
            elif self.now < 5:
                self.pending.append("E3AUX1 UPDATER 0.2.0 BOARD=0401E013")
        elif line == "M106 S0" and self.off_reply is not None:
            self.pending.append(self.off_reply)
        else:
            assert line == "M106 S0", f"Unexpected command: {line}"

    def read_line(self, timeout=1.0):
        assert 0 < timeout <= 0.2
        self.now += timeout
        return self.pending.pop(0) if self.pending else None


def connect(peer, monkeypatch):
    monkeypatch.setattr(controller, "wait_for_marlin",
                        lambda transport: wait_for_marlin(transport, clock=lambda: peer.now))
    owner = controller.CrealityControllerOwner(
        _binding().port, 115200, serial_factory=lambda *_: peer,
        sleep=lambda _: None, startup_delay_seconds=0, read_timeout_seconds=0.01,
    )
    return owner, controller.SecondaryMarlinFanController(owner, _binding())


@pytest.mark.parametrize("ready_at", [0, 7, 20, 35, 42])
def test_cold_boot_waits_for_identity_then_off_on_one_connection(ready_at, monkeypatch):
    peer = Peer(ready_at)
    owner, fan = connect(peer, monkeypatch)
    fan.initialize_off()
    assert owner.ready and fan.status.enabled is False
    assert peer.opens == 1 and peer.closes == 0
    assert peer.writes[-1] == "M106 S0"
    assert set(peer.writes[:-1]) == {"M115"}
    assert peer.now >= ready_at
    assert peer.now <= 45


@pytest.mark.parametrize("reply,match", [
    (["ok"], "readiness timed out"),
    (["FIRMWARE_NAME:Marlin 2.0"], "readiness timed out"),
    (["E3AUX1 UPDATER 0.2.0 BOARD=0401E013"], "readiness timed out"),
    (["ERR UNSUPPORTED"], "readiness timed out"),
    (["FIRMWARE_NAME:Marlin 2.0", "start", "ok"], "readiness timed out"),
    (["E3AUX1 UPDATER 0.1.0 BOARD=0401E013"], "updater 0.1.0"),
    (["FIRMWARE_NAME:Other"], "Unexpected secondary firmware"),
    (["Grbl 1.1h"], "startup rejected"),
    (["Error:Printer halted"], "startup rejected"),
    (["Resend:1"], "startup rejected"),
    (["echo:Unknown command: M115"], "startup rejected"),
    (["E3AUX1 APP 0.2.0 BOARD=0401E013 MODE=BENCH OUTPUTS=DISABLED"], "startup rejected"),
    (["x" * 32769], "bounded limit"),
])
def test_not_ready_never_sends_control_boot_or_update_commands(reply, match, monkeypatch):
    peer = Peer(reply=reply)
    owner, fan = connect(peer, monkeypatch)
    with pytest.raises(controller.SecondaryControllerError, match=match):
        fan.initialize_off()
    assert not owner.ready and peer.closes == 1
    assert set(peer.writes) == {"M115"}
    assert len(peer.writes) <= 15 and peer.now <= 45.01


def test_silent_board_fails_with_bounded_diagnostic(monkeypatch):
    peer = Peer(ready_at=100)
    owner, fan = connect(peer, monkeypatch)
    with pytest.raises(controller.SecondaryControllerError, match="readiness timed out"):
        fan.initialize_off()
    assert not owner.ready and peer.opens == peer.closes == 1
    assert peer.now <= 45.01


def test_stale_readiness_ack_cannot_acknowledge_fan_off(monkeypatch):
    peer = Peer(off_reply=None)
    owner, fan = connect(peer, monkeypatch)
    with pytest.raises(controller.SecondaryControllerError, match="M106 S0: timed out"):
        fan.initialize_off()
    assert not owner.ready
    assert peer.writes == ["M115", "M106 S0"]


def test_transport_disconnect_during_readiness_closes_without_retry(monkeypatch):
    peer = Peer()
    peer.read_line = lambda **_: (_ for _ in ()).throw(OSError("USB disconnected"))
    owner, fan = connect(peer, monkeypatch)
    with pytest.raises(controller.SecondaryControllerError, match="USB disconnected"):
        fan.initialize_off()
    assert not owner.ready and peer.opens == peer.closes == 1
    assert peer.writes == ["M115"]
