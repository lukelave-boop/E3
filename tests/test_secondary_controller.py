from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import FrozenInstanceError

import pytest

from laser_aligner.air_assist import AirAssistMode, AirAssistSettings
from laser_aligner.errors import MachineError
from laser_aligner.machine.controller_dialects import resolve_air_assist_commands
from laser_aligner.machine.secondary_controller import (
    CrealityControllerOwner,
    SecondaryControllerError,
    SecondaryMarlinFanController,
)

_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"


class FakeSerial:
    def __init__(
        self,
        responses: list[str | None | BaseException] | None = None,
        *,
        on_write: Callable[[str], None] | None = None,
        on_read: Callable[[], str | None] | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.on_write = on_write
        self.on_read = on_read
        self.open_calls = 0
        self.close_calls = 0
        self.synchronize_calls = 0
        self.writes: list[str] = []
        self.passive_fault: BaseException | None = None

    def open(self) -> None:
        self.open_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def synchronize_input(self) -> None:
        self.synchronize_calls += 1

    def write_line(self, line: str) -> None:
        self.writes.append(line)
        if self.on_write is not None:
            self.on_write(line)

    def read_line(self, timeout: float = 1.0) -> str | None:
        assert timeout > 0.0
        if self.on_read is not None:
            return self.on_read()
        if not self.responses:
            return None
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    def raise_if_faulted(self) -> None:
        if self.passive_fault is not None:
            raise self.passive_fault


def _binding():
    binding = resolve_air_assist_commands(
        AirAssistSettings(
            mode=AirAssistMode.SECONDARY_MARLIN_FAN,
            port=_PORT,
            baudrate=115200,
        ),
        protocol="grbl",
    )
    assert binding is not None
    return binding


def _controller(
    serials: list[FakeSerial],
    *,
    startup_delay_seconds: float = 0.0,
    read_timeout_seconds: float = 0.02,
    sleep: Callable[[float], None] = lambda _delay: None,
) -> tuple[CrealityControllerOwner, SecondaryMarlinFanController]:
    def factory(path: str, baudrate: int) -> FakeSerial:
        assert (path, baudrate) == (_PORT, 115200)
        if not serials:
            raise AssertionError("unexpected secondary serial reopen")
        return serials.pop(0)

    owner = CrealityControllerOwner(
        _PORT,
        115200,
        serial_factory=factory,
        sleep=sleep,
        startup_delay_seconds=startup_delay_seconds,
        read_timeout_seconds=read_timeout_seconds,
    )
    return owner, SecondaryMarlinFanController(owner, _binding())


def test_exact_fan2_commands_are_acknowledged_and_redundant_state_is_suppressed() -> None:
    serial = FakeSerial(["start chatter", "ok", "ok P15 B3", "ok", "ok"])
    owner, fan = _controller([serial])

    fan.initialize_off()
    fan.set_enabled(True, mapping_digest=fan.binding.mapping_digest)
    fan.set_enabled(True, mapping_digest=fan.binding.mapping_digest)
    fan.set_enabled(False, mapping_digest=fan.binding.mapping_digest)
    fan.ensure_off()

    assert serial.open_calls == 1
    assert serial.synchronize_calls == 2
    assert serial.writes == [
        "M106 S0",
        "M106 S255",
        "M106 S0",
        "M106 S0",
    ]
    assert all(" P" not in command and command != "M107" for command in serial.writes)
    assert owner.ready is True
    assert fan.status.enabled is False


def test_mapping_digest_mismatch_is_rejected_without_a_write() -> None:
    serial = FakeSerial(["ok"])
    _owner, fan = _controller([serial])
    fan.initialize_off()

    with pytest.raises(SecondaryControllerError, match="mapping digest"):
        fan.set_enabled(True, mapping_digest="0" * 64)

    assert serial.writes == ["M106 S0"]


def test_initial_open_applies_startup_delay_and_synchronizes_before_exact_off() -> None:
    delays: list[float] = []
    serial = FakeSerial(["ok"])
    _owner, fan = _controller(
        [serial],
        startup_delay_seconds=2.0,
        sleep=delays.append,
    )

    fan.initialize_off()

    assert delays == [2.0]
    assert serial.synchronize_calls == 1
    assert serial.writes == ["M106 S0"]


def test_startup_framing_rejection_gets_exactly_one_fresh_session_retry() -> None:
    stale = FakeSerial(["echo:Unknown command: \x13�BADM106 S0"])
    fresh = FakeSerial(["ok"])
    owner, fan = _controller([stale, fresh])

    fan.initialize_off()

    assert stale.writes == ["M106 S0"]
    assert stale.close_calls == 1
    assert fresh.open_calls == 1
    assert fresh.synchronize_calls == 1
    assert fresh.writes == ["M106 S0"]
    assert owner.ready is True
    assert fan.status.enabled is False


def test_persistent_startup_framing_rejection_fails_after_one_retry() -> None:
    first = FakeSerial(["echo:Unknown command: BADM106 S0"])
    second = FakeSerial(["echo:Unknown command: BADM106 S0"])
    owner, fan = _controller([first, second])

    with pytest.raises(SecondaryControllerError, match="rejected"):
        fan.initialize_off()

    assert first.writes == ["M106 S0"]
    assert second.writes == ["M106 S0"]
    assert first.close_calls == 1
    assert second.close_calls == 1
    assert owner.ready is False


@pytest.mark.parametrize(
    ("serial", "match"),
    [
        (FakeSerial(["Error:Unknown command"]), "rejected"),
        (FakeSerial([OSError("read failed")]), "read failed"),
        (
            FakeSerial(
                ["ok"],
                on_write=lambda _line: (_ for _ in ()).throw(OSError("write failed")),
            ),
            "write failed",
        ),
        (FakeSerial([]), "timed out"),
    ],
)
def test_exchange_failure_closes_and_untrusts_session(
    serial: FakeSerial,
    match: str,
) -> None:
    serials = [serial]
    if match == "rejected":
        serials.append(FakeSerial(["Error:Unknown command"]))
    owner, fan = _controller(serials, read_timeout_seconds=0.005)

    with pytest.raises(SecondaryControllerError, match=match):
        fan.initialize_off()

    assert serial.close_calls == 1
    assert owner.ready is False
    assert owner.fault is not None
    with pytest.raises(SecondaryControllerError):
        owner.raise_if_faulted()


def test_owner_serializes_the_complete_write_and_ack_exchange() -> None:
    first_read_started = threading.Event()
    release_first_ack = threading.Event()
    writes: list[str] = []
    write_lock = threading.Lock()

    def on_write(command: str) -> None:
        with write_lock:
            writes.append(command)

    def on_read() -> str:
        with write_lock:
            current_count = len(writes)
        if current_count == 2 and not release_first_ack.is_set():
            first_read_started.set()
            assert release_first_ack.wait(timeout=2.0)
        return "ok"

    serial = FakeSerial(on_write=on_write, on_read=on_read)
    owner, first = _controller([serial], read_timeout_seconds=3.0)
    second = SecondaryMarlinFanController(owner, first.binding)
    first.initialize_off()
    failures: list[BaseException] = []

    def enable() -> None:
        try:
            first.set_enabled(True, mapping_digest=first.binding.mapping_digest)
        except BaseException as exc:
            failures.append(exc)

    def disable() -> None:
        try:
            second.set_enabled(False, mapping_digest=second.binding.mapping_digest)
        except BaseException as exc:
            failures.append(exc)

    first_worker = threading.Thread(target=enable)
    second_worker = threading.Thread(target=disable)
    first_worker.start()
    assert first_read_started.wait(timeout=1.0)
    second_worker.start()
    second_worker.join(timeout=0.05)
    assert second_worker.is_alive()
    assert writes == ["M106 S0", "M106 S255"]

    release_first_ack.set()
    first_worker.join(timeout=1.0)
    second_worker.join(timeout=1.0)

    assert not failures
    assert writes == ["M106 S0", "M106 S255", "M106 S0"]


def test_multiple_typed_fan_wrappers_share_one_known_physical_state() -> None:
    serial = FakeSerial(["ok", "ok", "ok", "ok"])
    owner, first = _controller([serial])
    second = SecondaryMarlinFanController(owner, first.binding)

    first.initialize_off()
    first.set_enabled(True, mapping_digest=first.binding.mapping_digest)
    second.set_enabled(False, mapping_digest=second.binding.mapping_digest)
    first.set_enabled(True, mapping_digest=first.binding.mapping_digest)

    assert serial.writes == [
        "M106 S0",
        "M106 S255",
        "M106 S0",
        "M106 S255",
    ]
    assert first.status.enabled is True
    assert second.status.enabled is True


def test_write_guard_is_released_before_waiting_for_ack() -> None:
    guard_held = False

    @contextmanager
    def write_guard():
        nonlocal guard_held
        assert guard_held is False
        guard_held = True
        try:
            yield
        finally:
            guard_held = False

    def on_read() -> str:
        assert guard_held is False
        return "ok"

    serial = FakeSerial(on_read=on_read)
    _owner, fan = _controller([serial])
    fan.initialize_off()

    fan.set_enabled(
        True,
        mapping_digest=fan.binding.mapping_digest,
        write_guard=write_guard,
    )

    assert serial.writes == ["M106 S0", "M106 S255"]


def test_stop_guard_rejection_before_write_keeps_session_available_for_off() -> None:
    @contextmanager
    def stopped_guard():
        raise MachineError("Job stopped")
        yield

    serial = FakeSerial(["ok", "ok"])
    owner, fan = _controller([serial])
    fan.initialize_off()

    with pytest.raises(MachineError, match="Job stopped"):
        fan.set_enabled(
            True,
            mapping_digest=fan.binding.mapping_digest,
            write_guard=stopped_guard,
        )

    assert owner.ready is True
    assert owner.fault is None
    assert fan.best_effort_off() is True
    assert serial.writes == ["M106 S0", "M106 S0"]


def test_failed_runtime_off_reopens_only_once_for_cleanup() -> None:
    first = FakeSerial(["ok", OSError("cable reset")])
    reopened = FakeSerial(["ok"])
    owner, fan = _controller([first, reopened])
    fan.initialize_off()

    assert fan.best_effort_off() is True
    assert first.writes == ["M106 S0", "M106 S0"]
    assert first.close_calls == 1
    assert reopened.writes == ["M106 S0"]
    assert reopened.open_calls == 1
    assert owner.ready is True
    assert owner.fault is None


def test_startup_failure_can_be_retried_with_fresh_acknowledged_off() -> None:
    failed = FakeSerial(["error: heater fault"])
    restarted = FakeSerial(["ok"])
    _owner, fan = _controller([failed, restarted])

    with pytest.raises(SecondaryControllerError):
        fan.initialize_off()
    fan.ensure_off()

    assert failed.close_calls == 1
    assert restarted.writes == ["M106 S0"]
    assert fan.ready is True
    assert fan.status.enabled is False


def test_passive_reader_fault_untrusts_active_session_and_fail_off_reopens_once() -> None:
    active = FakeSerial(["ok", "ok"])
    recovered = FakeSerial(["ok"])
    owner, fan = _controller([active, recovered])
    fan.initialize_off()
    fan.set_enabled(True, mapping_digest=fan.binding.mapping_digest)
    active.passive_fault = OSError("USB cable disconnected")

    with pytest.raises(SecondaryControllerError, match="USB cable disconnected"):
        fan.raise_if_faulted()

    assert active.close_calls == 1
    assert owner.ready is False
    assert fan.status.enabled is None
    assert fan.best_effort_off() is True
    assert recovered.writes == ["M106 S0"]


def test_session_and_binding_snapshots_are_immutable_and_construction_does_not_open() -> None:
    serial = FakeSerial(["ok"])
    owner, fan = _controller([serial])

    assert serial.open_calls == 0
    with pytest.raises(AttributeError):
        owner.session = owner.session  # type: ignore[misc]
    with pytest.raises(AttributeError):
        fan.binding = fan.binding  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        owner.session.port = "/dev/ttyUSB9"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        fan.binding.port = "/dev/ttyUSB9"  # type: ignore[misc]


def test_persistent_prestart_off_discards_idle_rx_and_requires_new_ack() -> None:
    class IdleSerial(FakeSerial):
        def synchronize_input(self):
            super().synchronize_input()
            self.responses.clear()

        def write_line(self, line):
            super().write_line(line)
            self.responses.append("ok")

    serial = IdleSerial()
    owner, fan = _controller([serial, FakeSerial([])])
    fan.initialize_off()
    serial.responses.extend(["Error: stale idle fragment", "ok"])
    fan.ensure_off()
    assert owner.ready
    assert serial.open_calls == 1
    assert serial.synchronize_calls == 2
    assert serial.writes == ["M106 S0", "M106 S0"]
    serial.write_line = lambda line: serial.writes.append(line)
    serial.responses.append("ok")
    with pytest.raises(SecondaryControllerError, match="timed out"):
        fan.ensure_off()
    assert not owner.ready


def test_prestart_framing_rejection_uses_same_bounded_reopen_as_restart() -> None:
    current = FakeSerial(["ok", "echo:Unknown command: corrupt M106 S0"])
    fresh = FakeSerial(["ok"])
    owner, fan = _controller([current, fresh])
    fan.initialize_off()
    fan.ensure_off()
    assert owner.ready
    assert current.writes == ["M106 S0", "M106 S0"]
    assert fresh.writes == ["M106 S0"]
    assert current.close_calls == 1


@pytest.mark.parametrize("failure", [None, OSError("read failed"), "echo:Unknown command: bad OFF"])
@pytest.mark.parametrize("retry_fails", [False, True])
def test_prestart_uncertain_off_has_one_fresh_attempt(failure, retry_fails, caplog):
    current = FakeSerial(["ok"])
    fresh = FakeSerial([] if retry_fails else ["ok"])
    owner, fan = _controller([current, fresh], read_timeout_seconds=0.005)
    fan.initialize_off()
    current.responses = [failure]
    with caplog.at_level("INFO"):
        if retry_fails:
            with pytest.raises(SecondaryControllerError, match="fresh-session OFF retry failed"):
                fan.ensure_off()
        else:
            fan.ensure_off()
    assert current.close_calls == 1
    assert current.writes == ["M106 S0", "M106 S0"]
    assert fresh.open_calls == fresh.synchronize_calls == 1
    assert fresh.writes == ["M106 S0"]
    assert fresh.close_calls == int(retry_fails)
    assert owner.ready is (not retry_fails)
    assert "persistent attempt failed" in caplog.text
    assert "fresh-session retry started" in caplog.text
    assert ("fresh-session retry failed" if retry_fails else "fresh-session retry acknowledged") in caplog.text


@pytest.mark.parametrize("failure", [None, OSError("ON read failed"), "echo:Unknown command: ON"])
def test_uncertain_on_never_reopens_or_replays(failure):
    current = FakeSerial(["ok", failure])
    unused = FakeSerial(["ok"])
    owner, fan = _controller([current, unused], read_timeout_seconds=0.005)
    fan.initialize_off()
    with pytest.raises(SecondaryControllerError):
        fan.set_enabled(True, mapping_digest=fan.binding.mapping_digest)
    assert current.writes == ["M106 S0", "M106 S255"]
    assert current.close_calls == 1
    assert unused.open_calls == 0
    assert not owner.ready
    assert fan.status.enabled is None


def test_prestart_rx_sync_fault_recovers_with_startup_settle():
    current = FakeSerial(["ok"])
    fresh = FakeSerial(["ok"])
    delays = []
    owner, fan = _controller([current, fresh], startup_delay_seconds=2, sleep=delays.append)
    fan.initialize_off()
    def fail_sync():
        raise OSError("RX synchronization failed")
    current.synchronize_input = fail_sync
    fan.ensure_off()
    assert current.close_calls == 1
    assert current.writes == ["M106 S0"]
    assert fresh.open_calls == fresh.synchronize_calls == 1
    assert fresh.writes == ["M106 S0"]
    assert delays == [2, 2]
    assert owner.ready


def test_fault_detected_after_rx_sync_cannot_add_an_implicit_reopen():
    current = FakeSerial(["ok"])
    fresh = FakeSerial([])
    owner, fan = _controller([current, fresh], read_timeout_seconds=0.005)
    fan.initialize_off()
    def fault_after_sync():
        current.passive_fault = OSError("fault after RX sync")
    current.synchronize_input = fault_after_sync
    with pytest.raises(SecondaryControllerError, match="fresh-session OFF retry failed"):
        fan.ensure_off()
    assert current.writes == ["M106 S0"]
    assert current.close_calls == 1
    assert fresh.open_calls == 1
    assert fresh.writes == ["M106 S0"]
    assert not owner.ready
