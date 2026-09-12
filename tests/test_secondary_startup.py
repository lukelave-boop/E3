from __future__ import annotations

import threading
import time
from contextlib import contextmanager

import pytest

from laser_aligner.machine import secondary_controller as controller
from laser_aligner.machine.secondary_startup import wait_for_marlin
from tests.test_secondary_controller import _binding


class Peer:
    def __init__(self, ready_at=0.0, reply=None, off_reply="ok", board="0401E013"):
        self.now = 0.0
        self.ready_at = ready_at
        self.reply = reply
        self.off_reply = off_reply
        self.board = board
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
                version = "0.3.0" if self.board == "0401C013" else "0.2.0"
                self.pending.append(f"E3AUX1 UPDATER {version} BOARD={self.board}")
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
                        lambda transport, **kwargs: wait_for_marlin(transport, clock=lambda: peer.now, **kwargs))
    owner = controller.CrealityControllerOwner(
        _binding().port, 115200, serial_factory=lambda *_: peer,
        sleep=lambda _: None, startup_delay_seconds=0, read_timeout_seconds=0.01,
    )
    return owner, controller.SecondaryMarlinFanController(owner, _binding())


@pytest.mark.parametrize("ready_at", [0, 7, 20, 35, 42])
@pytest.mark.parametrize("board", ["0401E013", "0103E013", "0401C013"])
def test_cold_boot_waits_for_identity_then_off_on_one_connection(ready_at, board, monkeypatch):
    peer = Peer(ready_at, board=board)
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
    (["E3AUX1 UPDATER 0.2.0 BOARD=0103E013"], "readiness timed out"),
    (["E3AUX1 UPDATER 0.3.0 BOARD=0401C013"], "readiness timed out"),
    (["E3AUX1 UPDATER 0.2.0 BOARD=DEADBEEF"], "startup rejected"),
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


HALTED = "E3RECOVERY:1 STATE:HALTED BOARD:0401C013 TOKEN:00000001"


class HaltedPeer(Peer):
    def __init__(self, *, reply=None, reset_reply="E3RECOVERY:1 RESETTING", capability=True):
        super().__init__(reply=reply)
        self.halted = True
        self.reset_reply, self.capability = reset_reply, capability

    def write_line(self, line):
        self.writes.append(line)
        if line == "M115":
            if self.halted:
                self.pending.extend(self.reply if self.reply is not None else [HALTED, "ok"])
            else:
                self.pending.append("FIRMWARE_NAME:Marlin 2.0.8.24F4")
                if self.capability:
                    self.pending.append("Cap:E3_RECOVERY_V1:1")
                self.pending.append("ok")
        elif line == "E3RECOVER 00000001":
            if self.reset_reply is not None:
                self.pending.append(self.reset_reply)
            self.halted = False
        else:
            raise AssertionError(line)


def test_normal_startup_reports_halted_without_sending_recovery():
    peer = HaltedPeer()
    with pytest.raises(controller.MachineError, match="explicit Ender recovery"):
        wait_for_marlin(peer, clock=lambda: peer.now)
    assert peer.writes == ["M115"]


def test_explicit_halt_recovery_requires_fresh_identity_after_one_token_command():
    peer = HaltedPeer()
    result = wait_for_marlin(peer, clock=lambda: peer.now, recover_halted=True)
    assert peer.writes == ["M115", "E3RECOVER 00000001", "M115"]
    assert "Cap:E3_RECOVERY_V1:1" in result


@pytest.mark.parametrize("reply", [
    [HALTED], ["ok", HALTED], [HALTED, "start", "ok"],
    [HALTED, HALTED, "ok"], [HALTED, "FIRMWARE_NAME:Marlin 2.0", "ok"],
    ["FIRMWARE_NAME:Marlin 2.0", HALTED, "ok"], [HALTED, "unexpected", "ok"],
    [HALTED, "ok extra"], [HALTED.replace("0401C013", "0401E013"), "ok"],
    [HALTED.lower(), "ok"], [HALTED.replace("00000001", "AABBCCDD extra"), "ok"],
])
def test_partial_mixed_or_wrong_halt_response_never_authorizes_reset(reply):
    peer = HaltedPeer(reply=reply)
    with pytest.raises(controller.MachineError):
        wait_for_marlin(peer, clock=lambda: peer.now, recover_halted=True)
    assert set(peer.writes) == {"M115"}


@pytest.mark.parametrize("reply", [None, "Error:E3RECOVERY:1 REJECTED", "ok", "start"])
def test_recovery_ack_failure_does_not_retry_reset(reply):
    peer = HaltedPeer(reset_reply=reply)
    with pytest.raises(controller.MachineError, match="recovery"):
        wait_for_marlin(peer, clock=lambda: peer.now, recover_halted=True)
    assert peer.writes == ["M115", "E3RECOVER 00000001"]
    assert peer.now < 4


def test_stale_halted_banner_is_discarded_before_fresh_query():
    peer = HaltedPeer()
    peer.halted = False
    peer.pending.extend([HALTED, "ok"])
    wait_for_marlin(peer, clock=lambda: peer.now, recover_halted=True)
    assert peer.writes == ["M115"]


def test_recovery_rejects_application_missing_recovery_capability():
    peer = HaltedPeer(capability=False)
    with pytest.raises(controller.MachineError, match="expected recovery firmware"):
        wait_for_marlin(peer, clock=lambda: peer.now, recover_halted=True)
    assert peer.writes.count("E3RECOVER 00000001") == 1


def test_stop_after_halt_identity_prevents_reset_write():
    peer = HaltedPeer()
    @contextmanager
    def guard():
        if peer.now >= .4:
            raise controller.MachineError("Stopped")
        yield
    with pytest.raises(controller.MachineError, match="Stopped"):
        wait_for_marlin(peer, clock=lambda: peer.now, guard=guard, recover_halted=True)
    assert peer.writes == ["M115"]


@pytest.mark.parametrize("during_delay", [False, True])
def test_close_cancels_silent_startup_without_waiting_45_seconds(during_delay):
    peer = Peer(ready_at=100)
    entered = threading.Event()
    def read(timeout=1):
        entered.set()
        time.sleep(min(timeout, .02))
        return None
    peer.read_line = read
    def sleep(delay):
        entered.set()
        time.sleep(delay)
    owner = controller.CrealityControllerOwner("fake-ender", 115200, serial_factory=lambda *_: peer,
                                              startup_delay_seconds=5 if during_delay else 0, sleep=sleep)
    errors = []
    def initialize():
        try:
            with owner._lock:
                owner._open_locked()
        except controller.MachineError as exc:
            errors.append(str(exc))
    worker = threading.Thread(target=initialize)
    worker.start()
    assert entered.wait(2)
    started = time.monotonic()
    owner.close()
    worker.join(2)
    assert not worker.is_alive() and errors and time.monotonic() - started < 1
    assert peer.opens == peer.closes == 1
    assert set(peer.writes) == (set() if during_delay else {"M115"})
