import threading
import time
from collections.abc import Callable
from dataclasses import replace

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import CameraError, MachineError, SafetyError
from laser_aligner.machine.service import MachineService
from laser_aligner.machine.simulator import SimulatedTransport


def wait_for_job(machine: MachineService, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while machine.status()["job"]["running"] and time.monotonic() < deadline:
        time.sleep(0.01)


def test_manual_positive_laser_commands_are_always_blocked() -> None:
    machine = MachineService(MachineSettings(backend="simulator"), LaserSettings(), hardware_enabled=False)
    machine.connect()
    try:
        with pytest.raises(SafetyError):
            machine.send_command("M4 S10")
        with pytest.raises(SafetyError):
            machine.send_command("M4S10")
        machine.arm(machine.ARM_PHRASE)
        with pytest.raises(SafetyError):
            machine.send_command("M4S10")
        assert machine.send_command("M5")[-1].lower() == "ok"
        machine.disarm()
        assert not machine.status()["armed"]
    finally:
        machine.disconnect()


def test_simulated_program_stream() -> None:
    machine = MachineService(MachineSettings(backend="simulator"), LaserSettings(), hardware_enabled=False)
    machine.connect()
    try:
        program_text = "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
        program = machine.preflight_program(program_text)
        machine.arm_program(machine.ARM_PHRASE, program)
        machine.start_validated_program(program, "test.gcode")
        wait_for_job(machine)
        status = machine.status()["job"]
        assert not status["running"]
        assert status["error"] is None
        assert status["completed_lines"] == status["total_lines"]
        assert not machine.status()["armed"]
        receipt = machine.status()["last_successful_job"]
        assert receipt["program_digest"] == program.digest
        assert receipt["powered"] is True
        assert machine.successful_job_receipt(
            program.digest,
            not_before=float(receipt["started_at"]),
        ) == receipt
        assert machine.successful_job_receipt(
            "0" * 64,
            not_before=0.0,
        ) is None
        assert machine.successful_job_receipt(
            program.digest,
            not_before=float("nan"),
        ) is None
        assert machine.successful_job_receipt(
            program.digest,
            not_before=float("-inf"),
        ) is None
        assert machine.successful_job_receipt(  # type: ignore[arg-type]
            object(),
            not_before=0.0,
        ) is None
    finally:
        machine.disconnect()


def test_ensure_connected_opens_a_disconnected_machine() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator", allow_motion=True),
        LaserSettings(),
        hardware_enabled=False,
    )

    result = machine.ensure_connected()

    assert machine.status()["connected"] is True
    assert result["connected"] is True
    machine.disconnect()


def test_concurrent_ensure_connected_calls_share_one_reconnect_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="simulator", allow_motion=True),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    machine.request_stop()
    original_disconnect = machine.disconnect
    disconnect_entered = threading.Event()
    release_disconnect = threading.Event()
    disconnect_calls = 0
    calls_lock = threading.Lock()

    def paused_disconnect() -> None:
        nonlocal disconnect_calls
        with calls_lock:
            disconnect_calls += 1
            call_number = disconnect_calls
        if call_number == 1:
            disconnect_entered.set()
            assert release_disconnect.wait(timeout=2.0)
        original_disconnect()

    monkeypatch.setattr(machine, "disconnect", paused_disconnect)
    results: list[dict[str, object]] = []
    errors: list[Exception] = []

    def reconnect() -> None:
        try:
            results.append(machine.ensure_connected())
        except Exception as exc:
            errors.append(exc)

    first = threading.Thread(target=reconnect, daemon=True)
    second = threading.Thread(target=reconnect, daemon=True)
    first.start()
    assert disconnect_entered.wait(timeout=1.0)
    second.start()
    time.sleep(0.05)

    with calls_lock:
        assert disconnect_calls == 1

    release_disconnect.set()
    first.join(timeout=2.0)
    second.join(timeout=2.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert len(results) == 2
    assert all(result["connected"] is True for result in results)
    assert machine.status()["connected"] is True
    assert machine.status()["controller_reconnect_required"] is False
    machine.disconnect()


def test_process_laser_lockout_allows_motion_and_rejects_laser_enable() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=True,
        laser_lockout=True,
    )
    machine.connect()
    try:
        motion = machine.preflight_program(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nG1 X20 Y20 F500\nM5\n"
        )
        machine.start_validated_program(motion, "motion-only.gcode")
        wait_for_job(machine)
        assert machine.status()["job"]["error"] is None
        assert machine.status()["laser_lockout"] is True
        with pytest.raises(SafetyError, match="laser-enable.*lockout"):
            machine.preflight_program(
                "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
            )
        with pytest.raises(SafetyError, match="Laser output is locked out"):
            machine.arm(machine.ARM_PHRASE)
        assert machine.send_command("M5")[-1].lower() == "ok"
    finally:
        machine.disconnect()


@pytest.mark.parametrize("value", ["false", 0, 1])
def test_machine_rejects_non_boolean_laser_lockout(value: object) -> None:
    with pytest.raises(TypeError, match="laser_lockout must be an exact boolean"):
        MachineService(
            MachineSettings(backend="simulator"),
            LaserSettings(),
            laser_lockout=value,  # type: ignore[arg-type]
        )


def test_dry_program_does_not_require_arming() -> None:
    machine = MachineService(MachineSettings(backend="simulator"), LaserSettings(), hardware_enabled=False)
    machine.connect()
    try:
        machine.start_job("G21\nG90\nM5\nG0X10Y10F1000\nG1X20Y20F500\nM5\n", "dry.gcode")
        wait_for_job(machine)
        assert machine.status()["job"]["error"] is None
    finally:
        machine.disconnect()


def test_powered_program_preflight_is_side_effect_free() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    program = "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"

    assert machine.validate_program(program)[-1] == "M5"
    assert not machine.status()["armed"]
    with pytest.raises(SafetyError, match="not armed"):
        machine._check_line_safety("M4 S5")


def test_program_requires_laser_off_xy_position_before_enable() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )

    with pytest.raises(SafetyError, match="absolute XY move.*before laser enable"):
        machine.validate_program("G21\nG90\nM5\nM4 S5\nG0 X10 Y10 F1000\nM5\n")


def test_program_validates_controller_and_physical_spot_bounds() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(spot_offset_x_mm=5.0),
        hardware_enabled=False,
    )

    with pytest.raises(SafetyError, match=r"physical laser spot X221\.000"):
        machine.validate_program("G21\nG90\nM5\nG0 X216 Y10 F1000\nM5\n")


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("G0 X10 Y10", "explicit F feed rate"),
        ("G1 X10 Y10", "explicit F feed rate"),
        ("G0 X10 Y10 F6001", "travel ceiling of 6000"),
        ("G1 X10 Y10 F6001", "work ceiling of 6000"),
    ],
)
def test_program_requires_explicit_bounded_feed(line: str, message: str) -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )

    with pytest.raises(SafetyError, match=message):
        machine.validate_program(f"G21\nG90\nM5\n{line}\nM5\n")


def test_program_rejects_oversized_executable_line_before_streaming() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    oversized_m5 = "M" + (" " * 300) + "5"

    with pytest.raises(SafetyError, match="exceeds 256 characters"):
        machine.preflight_program(f"G21\nG90\n{oversized_m5}\nM5\n")


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("max_travel_feed_mm_min", float("nan"), "finite"),
        ("max_travel_feed_mm_min", float("inf"), "finite"),
        ("max_travel_feed_mm_min", 0.0, "positive"),
        ("max_travel_feed_mm_min", -1.0, "positive"),
        ("max_work_feed_mm_min", float("nan"), "finite"),
        ("max_work_feed_mm_min", float("inf"), "finite"),
        ("max_work_feed_mm_min", 0.0, "positive"),
        ("max_work_feed_mm_min", -1.0, "positive"),
    ],
)
def test_preflight_rejects_invalid_programmatic_feed_ceilings(
    field: str,
    value: float,
    message: str,
) -> None:
    settings = MachineSettings(backend="simulator")
    setattr(settings, field, value)
    machine = MachineService(settings, LaserSettings(), hardware_enabled=False)

    with pytest.raises(SafetyError, match=message):
        machine.preflight_program(
            "G21\nG90\nM5\nG0 X10 Y10 F999999999\nM5\n"
        )


def test_validated_program_rejects_feed_ceiling_mutation_before_start() -> None:
    settings = MachineSettings(backend="simulator")
    machine = MachineService(settings, LaserSettings(), hardware_enabled=False)
    program = machine.preflight_program(
        "G21\nG90\nM5\nG1 X10 Y10 F500\nM5\n"
    )
    machine.connect()
    settings.max_work_feed_mm_min = float("inf")
    try:
        with pytest.raises(SafetyError, match="finite"):
            machine.start_validated_program(program)
    finally:
        machine.disconnect()


@pytest.mark.parametrize("value", ["false", 0, 1])
@pytest.mark.parametrize("gate", ["allow_motion", "hardware_enabled"])
def test_preflight_rejects_non_boolean_hardware_gates(
    gate: str,
    value: object,
) -> None:
    settings = MachineSettings(
        backend="serial",
        protocol="grbl",
        allow_motion=True,
    )
    machine = MachineService(settings, LaserSettings(), hardware_enabled=True)
    if gate == "allow_motion":
        settings.allow_motion = value  # type: ignore[assignment]
    else:
        machine.hardware_enabled = value  # type: ignore[assignment]

    with pytest.raises(SafetyError, match="exact booleans"):
        machine.preflight_program("G21\nG90\nM5\nG1 X1 Y1 F100\nM5\n")


def test_malformed_backend_is_rejected_before_serial_transport_creation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def unexpected_transport(_port: str, _baudrate: int) -> SimulatedTransport:
        nonlocal calls
        calls += 1
        return SimulatedTransport()

    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        unexpected_transport,
    )
    machine = MachineService(
        MachineSettings(backend="serial "),
        LaserSettings(),
        hardware_enabled=False,
    )

    with pytest.raises(SafetyError, match="backend must be exactly"):
        machine.connect()
    assert calls == 0


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("allow_motion", 0),
        ("hardware_enabled", 0),
        ("backend", "simulator "),
    ],
)
def test_validated_program_rejects_exact_gate_mutation_before_start(
    target: str,
    value: object,
) -> None:
    settings = MachineSettings(backend="simulator")
    machine = MachineService(settings, LaserSettings(), hardware_enabled=False)
    program = machine.preflight_program(
        "G21\nG90\nM5\nG1 X10 Y10 F500\nM5\n"
    )
    if target == "hardware_enabled":
        machine.hardware_enabled = value  # type: ignore[assignment]
    else:
        setattr(settings, target, value)

    with pytest.raises(SafetyError):
        machine.start_validated_program(program)


@pytest.mark.parametrize(
    "value",
    [float("nan"), float("inf"), 0, -1, 601, 60.0, "60"],
)
def test_arm_rejects_invalid_programmatic_timeout_without_grant(value: object) -> None:
    laser = LaserSettings()
    machine = MachineService(
        MachineSettings(backend="simulator"),
        laser,
        hardware_enabled=False,
    )
    machine.connect()
    laser.arm_timeout_seconds = value  # type: ignore[assignment]
    try:
        with pytest.raises(SafetyError, match="arm timeout"):
            machine.arm(machine.ARM_PHRASE)
        assert not machine.armed
        assert machine._armed_program_digest is None
    finally:
        machine.disconnect()


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (float("nan"), "finite"),
        (float("inf"), "finite"),
        ("3000", "finite"),
        (0.0, "travel feed must be positive"),
        (-1.0, "travel feed must be positive"),
        (6001.0, "exceeds the configured machine travel ceiling"),
    ],
)
def test_photo_position_rejects_invalid_travel_feed_before_motion(
    value: object,
    message: str,
) -> None:
    laser = LaserSettings()
    machine = MachineService(
        MachineSettings(backend="simulator"),
        laser,
        hardware_enabled=False,
    )
    machine.connect()
    transport = machine._transport
    assert isinstance(transport, SimulatedTransport)
    laser.travel_feed_mm_min = value  # type: ignore[assignment]
    try:
        with pytest.raises(SafetyError, match=message):
            machine.prepare_photo_position()
        assert (transport.x, transport.y) == (0.0, 0.0)
    finally:
        machine.disconnect()


def test_internal_motion_feed_is_bounded_immediately_before_write() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    transport = machine._transport
    assert isinstance(transport, SimulatedTransport)
    try:
        with pytest.raises(SafetyError, match="travel ceiling"):
            machine.send_command(
                "G0 X1 Y1 F999999",
                _internal_motion=True,
            )
        assert (transport.x, transport.y) == (0.0, 0.0)
    finally:
        machine.disconnect()


def test_invalid_normal_idle_delay_cannot_start_temporary_hold() -> None:
    settings = MachineSettings(backend="simulator", grbl_step_idle_delay_ms=255)
    machine = MachineService(settings, LaserSettings(), hardware_enabled=False)

    with pytest.raises(SafetyError, match="0 through 254"):
        with machine.temporary_stepper_hold():
            pytest.fail("invalid normal idle delay entered the hold scope")


def test_invalid_gate_mutation_cannot_suppress_disconnect_m5() -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    transport = RecordingTransport()
    transport.open()
    machine._transport = transport
    machine._connected = True
    machine.settings.allow_motion = "false"  # type: ignore[assignment]

    machine.disconnect()

    assert transport.commands == ["M5", "M5"]


@pytest.mark.parametrize(
    ("target", "value"),
    [
        ("hardware_enabled", False),
        ("hardware_enabled", "false"),
        ("read_timeout", "bad"),
        ("backend", "serial "),
    ],
)
def test_invalid_settings_cannot_suppress_disarm_m5(
    target: str,
    value: object,
) -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    transport = RecordingTransport()
    transport.open()
    machine._transport = transport
    machine._connected = True
    if target == "hardware_enabled":
        machine.hardware_enabled = value  # type: ignore[assignment]
    else:
        setattr(machine.settings, target, value)

    machine.disarm()

    assert transport.commands == ["M5"]
    assert not machine.armed
    transport.close()


def test_untrusted_connection_disarm_still_attempts_m5() -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    transport = RecordingTransport()
    transport.open()
    machine._transport = transport
    machine._connected = True
    machine._controller_reconnect_required = True

    machine.disarm()

    assert transport.commands == ["M5"]
    assert machine._controller_reconnect_required
    transport.close()


def test_motor_release_attempts_m5_before_using_mutated_settings() -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    transport = RecordingTransport()
    transport.open()
    machine._transport = transport
    machine._connected = True
    machine.settings.read_timeout = "bad"  # type: ignore[assignment]

    machine._release_grbl_motors(
        restore_idle_delay=250,
        job_execution=False,
        context="test cleanup",
    )

    assert transport.commands[0] == "M5"
    transport.close()


def test_forged_validated_program_is_reanalyzed_before_arm_and_start() -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    transport = RecordingTransport()
    transport.open()
    machine._transport = transport
    machine._connected = True
    valid = machine.preflight_program(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
    )
    forged_programs = [
        replace(
            valid,
            lines=(
                "G21",
                "G90",
                "M5",
                "G0 X999 Y999 F1000",
                "M4 S5",
                "G0 X1000 Y1000 F1000",
                "M5",
            ),
        ),
        replace(valid, digest="0" * 64),
        replace(valid, requires_laser_authorization=False),
        replace(valid, requires_motion=False),
        replace(valid, safety_profile=(*valid.safety_profile, "forged")),
    ]

    machine.arm_program(machine.ARM_PHRASE, valid)
    with pytest.raises(SafetyError):
        machine.start_validated_program(forged_programs[0])
    assert not machine.armed
    assert transport.commands == []

    for forged in forged_programs:
        with pytest.raises(SafetyError):
            machine.arm_program(machine.ARM_PHRASE, forged)
        assert not machine.armed

        machine.arm(machine.ARM_PHRASE, program_digest=forged.digest)
        assert machine.armed
        with pytest.raises(SafetyError):
            machine.start_validated_program(forged)
        assert not machine.armed
        assert not machine.status()["job"]["running"]

    assert transport.commands == []
    machine.disconnect()


def test_job_execution_authorization_outlives_arm_countdown_only_for_job() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine._job_laser_authorized = True

    machine._check_line_safety("M4 S5", job_execution=True)
    with pytest.raises(SafetyError, match="not armed"):
        machine._check_line_safety("M4 S5")


def test_stop_orders_m5_after_an_inflight_powered_job_write() -> None:
    class BlockingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.powered_write_entered = threading.Event()
            self.release_powered_write = threading.Event()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            if command == "M4 S5":
                self.powered_write_entered.set()
                assert self.release_powered_write.wait(timeout=2.0)
            self.commands.append(command)
            super().write_line(line)

    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    transport = BlockingTransport()
    transport.open()
    machine._transport = transport
    machine._connected = True
    machine._job_laser_authorized = True
    machine._job_stop.clear()
    errors: list[Exception] = []

    def write_powered_line() -> None:
        try:
            machine._write_running_job_line("M4 S5")
        except Exception as exc:
            errors.append(exc)

    writer = threading.Thread(
        target=write_powered_line,
        daemon=True,
    )
    writer.start()
    assert transport.powered_write_entered.wait(timeout=1.0)

    stopper = threading.Thread(target=machine.request_stop, daemon=True)
    stopper.start()
    time.sleep(0.02)
    assert stopper.is_alive()
    transport.release_powered_write.set()
    writer.join(timeout=1.0)
    stopper.join(timeout=1.0)

    assert errors == []
    assert transport.commands == ["M4 S5", "M5"]
    assert not machine._job_laser_authorized


def test_emergency_stop_attempts_m5_when_realtime_write_fails() -> None:
    class FailingRealtimeTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_raw(self, data: bytes) -> None:
            del data
            raise OSError("realtime channel failed")

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    transport = FailingRealtimeTransport()
    transport.open()
    machine._transport = transport
    machine._connected = True
    machine._protocol = "grbl"

    machine.request_stop(emergency=True)

    assert transport.commands == ["M5"]


def test_emergency_stop_attempts_m5_when_realtime_write_stalls() -> None:
    class StalledRealtimeTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.release = threading.Event()
            self.commands: list[str] = []

        def write_raw(self, data: bytes) -> None:
            if data == b"!\x18":
                self.release.wait(timeout=2.0)
                return
            super().write_raw(data)

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    transport = StalledRealtimeTransport()
    transport.open()
    machine._transport = transport
    machine._connected = True
    machine._protocol = "grbl"

    started = time.monotonic()
    machine.request_stop(emergency=True)
    elapsed = time.monotonic() - started
    transport.release.set()

    assert elapsed < 0.8
    assert transport.commands == ["M5"]


def test_emergency_stop_attempts_m5_when_realtime_worker_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    class UnstartableThread:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread capacity exhausted")

    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    transport = RecordingTransport()
    transport.open()
    machine._transport = transport
    machine._connected = True
    machine._protocol = "grbl"
    monkeypatch.setattr("laser_aligner.machine.service.threading.Thread", UnstartableThread)

    machine.request_stop(emergency=True)

    assert transport.commands == ["M5"]


def test_stop_during_connection_cannot_be_overwritten_by_successful_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    transport = RecordingTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="auto",
            allow_motion=True,
            controller_startup_delay=0.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    identify_entered = threading.Event()
    release_identify = threading.Event()

    def blocked_identify(*, expected_stop_epoch: int) -> str:
        del expected_stop_epoch
        identify_entered.set()
        assert release_identify.wait(timeout=2.0)
        return "marlin"

    monkeypatch.setattr(machine, "_identify_protocol", blocked_identify)
    errors: list[Exception] = []

    def connect() -> None:
        try:
            machine.connect()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=connect, daemon=True)
    worker.start()
    assert identify_entered.wait(timeout=1.0)
    machine.request_stop()
    release_identify.set()
    worker.join(timeout=1.0)

    assert len(errors) == 1
    assert "cancelled by software STOP" in str(errors[0])
    assert transport.commands == ["M5", "M5"]
    assert not machine.status()["connected"]
    assert not machine.status()["coordinate_reference_ready"]


def test_stop_during_transport_open_gets_connection_cleanup_m5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class PausedOpenTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.opened = threading.Event()
            self.release_open = threading.Event()
            self.commands: list[str] = []

        def open(self) -> None:
            super().open()
            self.opened.set()
            assert self.release_open.wait(timeout=2.0)

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    transport = PausedOpenTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(backend="serial", protocol="marlin", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    errors: list[Exception] = []

    def connect() -> None:
        try:
            machine.connect()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=connect, daemon=True)
    worker.start()
    assert transport.opened.wait(timeout=1.0)
    machine.request_stop()
    assert transport.commands == []
    transport.release_open.set()
    worker.join(timeout=1.0)

    assert len(errors) == 1
    assert "cancelled by software STOP" in str(errors[0])
    assert transport.commands == ["M5"]
    assert not machine.status()["connected"]


def test_successful_marlin_connection_starts_with_acknowledged_m5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    transport = RecordingTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(backend="serial", protocol="marlin"),
        LaserSettings(),
        hardware_enabled=True,
    )

    machine.connect()
    try:
        assert transport.commands == ["M5"]
        assert transport.read_line(timeout=0.0) is None
    finally:
        machine.disconnect()


@pytest.mark.parametrize("powered", [False, True])
def test_stop_cancels_a_start_already_in_progress(
    powered: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    text = (
        "G21\nG90\nM5\nG0 X10 Y10 F1000\n"
        + ("M4 S5\nG1 X20 Y20 F500\n" if powered else "G1 X20 Y20 F500\n")
        + "M5\n"
    )
    program = machine.preflight_program(text)
    if powered:
        machine.arm_program(machine.ARM_PHRASE, program)
    transport = machine._transport
    assert transport is not None
    commands: list[str] = []
    original_write = transport.write_line

    def record_write(line: str) -> None:
        commands.append(line.strip().upper())
        original_write(line)

    monkeypatch.setattr(transport, "write_line", record_write)
    validation_entered = threading.Event()
    release_validation = threading.Event()
    original_require = machine._require_current_safety_profile

    def paused_validation(candidate: object) -> None:
        validation_entered.set()
        assert release_validation.wait(timeout=2.0)
        original_require(candidate)  # type: ignore[arg-type]

    monkeypatch.setattr(machine, "_require_current_safety_profile", paused_validation)
    errors: list[Exception] = []

    def start() -> None:
        try:
            machine.start_validated_program(program, "pending.gcode")
        except Exception as exc:
            errors.append(exc)

    starter = threading.Thread(target=start, daemon=True)
    starter.start()
    assert validation_entered.wait(timeout=1.0)
    machine.request_stop()
    assert commands == ["M5"]
    release_validation.set()
    starter.join(timeout=1.0)

    assert len(errors) == 1
    assert "cancelled by software STOP" in str(errors[0])
    assert commands == ["M5"]
    assert not machine.status()["job"]["running"]
    assert not machine.status()["armed"]
    machine.disconnect()


def test_auto_protocol_probe_falls_back_to_marlin_after_consumed_grbl_rejection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MarlinProbeTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def open(self) -> None:
            self.is_open = True
            self._queue.put("start")

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "$I":
                self._queue.put("error:Unknown command")
                return
            if command == "M115":
                self._queue.put("FIRMWARE_NAME:Marlin 2.1.2")
                self._queue.put("ok")
                return
            if command == "M5":
                self._queue.put("ok")
                return
            raise AssertionError(f"Unexpected command: {command}")

    transport = MarlinProbeTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="auto",
            controller_startup_delay=0.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )

    status = machine.connect()

    assert status["connected"] is True
    assert status["protocol"] == "marlin"
    assert status["controller_reconnect_required"] is False
    assert transport.commands == ["$I", "M115", "M5"]
    machine.disconnect()


def test_stop_during_start_job_preflight_cannot_adopt_new_session_grant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    text = (
        "G21\nG90\nM5\nG0 X10 Y10 F1000\n"
        "M4 S5\nG1 X20 Y20 F500\nM5\n"
    )
    original_preflight = machine.preflight_program
    preflight_entered = threading.Event()
    release_preflight = threading.Event()

    def paused_preflight(candidate: str):
        program = original_preflight(candidate)
        preflight_entered.set()
        assert release_preflight.wait(timeout=2.0)
        return program

    monkeypatch.setattr(machine, "preflight_program", paused_preflight)
    errors: list[Exception] = []

    def stale_start() -> None:
        try:
            machine.start_job(text, "stale-request.gcode")
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=stale_start, daemon=True)
    worker.start()
    assert preflight_entered.wait(timeout=1.0)
    machine.request_stop()
    machine.disconnect()
    machine.connect()
    new_transport = machine._transport
    assert new_transport is not None
    commands: list[str] = []
    original_write = new_transport.write_line

    def record_write(line: str) -> None:
        commands.append(line.strip().upper())
        original_write(line)

    monkeypatch.setattr(new_transport, "write_line", record_write)
    fresh_program = original_preflight(text)
    machine.arm_program(machine.ARM_PHRASE, fresh_program)
    release_preflight.set()
    worker.join(timeout=1.0)

    assert len(errors) == 1
    assert "cancelled by software STOP" in str(errors[0])
    assert not any(command.startswith(("M3", "M4")) for command in commands)
    assert machine.status()["armed"]

    machine.start_validated_program(fresh_program, "fresh-request.gcode")
    wait_for_job(machine)
    assert any(command.startswith("M4 S5") for command in commands)
    assert machine.status()["job"]["error"] is None
    machine.disconnect()


def test_stale_arm_scope_cannot_clear_new_session_grant() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    text = (
        "G21\nG90\nM5\nG0 X10 Y10 F1000\n"
        "M4 S5\nG1 X20 Y20 F500\nM5\n"
    )
    stale_generation = machine.operation_generation()
    machine.request_stop()
    machine.disconnect()
    machine.connect()
    program = machine.preflight_program(text)
    machine.arm_program(machine.ARM_PHRASE, program)

    with machine.operation_scope(stale_generation):
        with pytest.raises(MachineError, match="cancelled by software STOP"):
            machine.arm_program(machine.ARM_PHRASE, program)

    assert machine.status()["armed"]
    machine.disconnect()


def test_request_time_generation_cancels_queued_home_park(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    transport = machine._transport
    assert transport is not None
    commands: list[str] = []
    original_write = transport.write_line

    def record_write(line: str) -> None:
        commands.append(line.strip().upper())
        original_write(line)

    monkeypatch.setattr(transport, "write_line", record_write)
    generation = machine.operation_generation()
    machine.request_stop()

    with machine.operation_scope(generation):
        with pytest.raises(MachineError, match="cancelled by software STOP"):
            machine.prepare_photo_position()

    assert commands == ["M5"]
    machine.disconnect()


def test_simulator_stop_requires_reconnect_before_new_controller_commands() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()

    machine.request_stop()

    assert machine.status()["controller_reconnect_required"]
    with pytest.raises(MachineError, match="disconnect and reconnect"):
        machine.prepare_photo_position()
    machine.disconnect()


def test_request_time_generation_cancels_composite_job_after_stop() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    program = machine.preflight_program(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\n"
        "M4 S5\nG1 X20 Y20 F500\nM5\n"
    )
    generation = machine.operation_generation()

    with machine.operation_scope(generation):
        machine.prepare_photo_position()
        machine.request_stop()
        with pytest.raises(MachineError, match="cancelled by software STOP"):
            machine.arm_program(machine.ARM_PHRASE, program)

    assert not machine.status()["armed"]
    assert not machine.status()["job"]["running"]
    transport = machine._transport
    assert transport is not None
    assert not transport.laser_on
    machine.disconnect()


def test_disarm_cancels_an_arm_already_in_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    program = machine.preflight_program(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\n"
        "M4 S5\nG1 X20 Y20 F500\nM5\n"
    )
    validation_entered = threading.Event()
    release_validation = threading.Event()
    original_require = machine._require_current_safety_profile

    def blocked_validation(candidate: object) -> None:
        validation_entered.set()
        assert release_validation.wait(timeout=2.0)
        original_require(candidate)  # type: ignore[arg-type]

    monkeypatch.setattr(machine, "_require_current_safety_profile", blocked_validation)
    errors: list[Exception] = []

    def arm() -> None:
        try:
            machine.arm_program(machine.ARM_PHRASE, program)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=arm, daemon=True)
    worker.start()
    assert validation_entered.wait(timeout=1.0)
    machine.disarm()
    release_validation.set()
    worker.join(timeout=1.0)

    assert len(errors) == 1
    assert "cancelled by disarm" in str(errors[0])
    assert not machine.status()["armed"]
    machine.disconnect()


def test_disarm_cancels_powered_start_after_grant_was_checked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    program = machine.preflight_program(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\n"
        "M4 S5\nG1 X20 Y20 F500\nM5\n"
    )
    machine.arm_program(machine.ARM_PHRASE, program)
    transport = machine._transport
    assert transport is not None
    commands: list[str] = []
    original_write = transport.write_line

    def record_write(line: str) -> None:
        commands.append(line.strip().upper())
        original_write(line)

    monkeypatch.setattr(transport, "write_line", record_write)
    connection_check_entered = threading.Event()
    release_connection_check = threading.Event()
    original_require_connection = machine._require_connection

    def paused_connection_check() -> object:
        if threading.current_thread().name == "pending-powered-start":
            connection_check_entered.set()
            assert release_connection_check.wait(timeout=2.0)
        return original_require_connection()

    monkeypatch.setattr(machine, "_require_connection", paused_connection_check)
    start_errors: list[Exception] = []

    def start() -> None:
        try:
            machine.start_validated_program(program, "pending-powered.gcode")
        except Exception as exc:
            start_errors.append(exc)

    starter = threading.Thread(target=start, name="pending-powered-start", daemon=True)
    starter.start()
    assert connection_check_entered.wait(timeout=1.0)
    authorization_epoch = machine._authorization_epoch
    disarmer = threading.Thread(target=machine.disarm, daemon=True)
    disarmer.start()
    deadline = time.monotonic() + 1.0
    while machine._authorization_epoch == authorization_epoch and time.monotonic() < deadline:
        time.sleep(0.001)
    assert machine._authorization_epoch > authorization_epoch

    release_connection_check.set()
    starter.join(timeout=1.0)
    disarmer.join(timeout=1.0)

    assert len(start_errors) == 1
    assert "cancelled by disarm" in str(start_errors[0])
    assert "M3" not in commands
    assert not any(command.startswith("M4") for command in commands)
    assert not machine.status()["armed"]
    assert not machine.status()["job"]["running"]
    machine.disconnect()


def test_idle_disarm_consumes_its_m5_acknowledgement() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    transport = machine._transport
    assert transport is not None

    machine.disarm()

    assert transport.read_line(timeout=0.0) is None
    machine.disconnect()


def test_powered_arm_is_hash_bound_and_consumed() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    first = machine.preflight_program(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
    )
    second = machine.preflight_program(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S6\nG1 X20 Y20 F500\nM5\n"
    )
    machine.connect()
    try:
        machine.arm_program(machine.ARM_PHRASE, first)
        with pytest.raises(SafetyError, match="exact preflight"):
            machine.start_validated_program(second, "wrong.gcode")
        assert not machine.status()["armed"]

        machine.arm_program(machine.ARM_PHRASE, first)
        machine.start_validated_program(first, "right.gcode")
        assert not machine.status()["armed"]
        wait_for_job(machine)
        assert not machine._job_laser_authorized
    finally:
        machine.disconnect()


def test_preflight_is_invalidated_when_safety_profile_changes() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    program = machine.preflight_program(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\nM5\n"
    )
    machine.settings.work_area.x_min = 5.0

    with pytest.raises(SafetyError, match="changed after program preflight"):
        machine.start_validated_program(program, "stale.gcode")


def test_unsafe_modal_and_out_of_bounds_programs_are_blocked() -> None:
    machine = MachineService(MachineSettings(backend="simulator"), LaserSettings(), hardware_enabled=False)
    machine.connect()
    try:
        with pytest.raises(SafetyError):
            machine.start_job("G21\nG90\nM5\nG92X0Y0\nM5\n", "offset.gcode")
        with pytest.raises(SafetyError):
            machine.start_job("G21\nG90\nM5\nG0X999Y10\nM5\n", "outside.gcode")
    finally:
        machine.disconnect()


def test_grbl_home_park_rejects_nonzero_xy_offsets() -> None:
    state = {
        "active_workspace": "G54",
        "active_offset_mm": [2.5, 0.0, 0.0],
        "g92_offset_mm": [0.0, -1.0, 0.0],
    }

    with pytest.raises(SafetyError, match=r"G54 XY=\[2\.5,0\].*G92 XY=\[0,-1\]"):
        MachineService._require_zero_xy_coordinate_offsets(state)

    MachineService._require_zero_xy_coordinate_offsets(
        {
            "active_workspace": "G54",
            "active_offset_mm": [0.0, 0.0, 4.0],
            "g92_offset_mm": [0.0, 0.0, -2.0],
        }
    )


def test_home_park_rejects_grbl_offsets_before_issuing_park_move(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine._transport = object()  # type: ignore[assignment]
    machine._connected = True
    machine._protocol = "grbl"
    commands: list[str] = []
    monkeypatch.setattr(
        machine,
        "send_command",
        lambda command, **_kwargs: commands.append(command) or ["ok"],
    )
    monkeypatch.setattr(
        machine,
        "_read_grbl_coordinate_state",
        lambda: {
            "active_workspace": "G54",
            "active_offset_mm": [50.0, 0.0, 0.0],
            "g92_offset_mm": [0.0, 0.0, 0.0],
        },
    )

    with pytest.raises(SafetyError, match="G54 XY"):
        machine.prepare_photo_position()

    assert commands == ["M5", "$H"]
    assert not any(command.startswith("G0 ") for command in commands)


def test_stop_cancels_home_park_waiting_for_homing_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockedHomeTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.home_written = threading.Event()

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "$H":
                self.home_written.set()
                return
            super().write_line(line)

    transport = BlockedHomeTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            read_timeout=2.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    transport.commands.clear()
    errors: list[Exception] = []

    def park() -> None:
        try:
            machine.prepare_photo_position()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=park, daemon=True)
    worker.start()
    assert transport.home_written.wait(timeout=1.0)

    machine.request_stop()
    commands_when_stop_returned = list(transport.commands)
    worker.join(timeout=1.0)

    assert commands_when_stop_returned == ["M5", "$H", "M5"]
    assert transport.commands == commands_when_stop_returned
    assert len(errors) == 1
    assert "cancelled by software STOP" in str(errors[0])
    assert not machine.status()["coordinate_reference_ready"]
    assert machine.status()["controller_reconnect_required"]
    before_retry = list(transport.commands)
    with pytest.raises(MachineError, match="disconnect and reconnect"):
        machine.prepare_photo_position()
    assert transport.commands == before_retry
    machine.disconnect()


def test_stop_in_final_home_park_commit_window_cannot_restore_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = SimulatedTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    summary_entered = threading.Event()
    release_summary = threading.Event()
    original_summary = machine._coordinate_state_summary

    def blocked_summary(state: dict[str, object]) -> str:
        summary_entered.set()
        assert release_summary.wait(timeout=2.0)
        return original_summary(state)

    monkeypatch.setattr(machine, "_coordinate_state_summary", blocked_summary)
    errors: list[Exception] = []

    def park() -> None:
        try:
            machine.prepare_photo_position()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=park, daemon=True)
    worker.start()
    assert summary_entered.wait(timeout=1.0)
    machine.request_stop()
    release_summary.set()
    worker.join(timeout=1.0)

    assert len(errors) == 1
    assert "cancelled by software STOP" in str(errors[0])
    assert not machine.status()["coordinate_reference_ready"]
    assert machine.status()["controller_reconnect_required"]
    machine.disconnect()


def test_stop_during_arm_validation_cannot_rearm_serial_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = SimulatedTransport()
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    transport.open()
    machine._transport = transport
    machine._connected = True
    machine._coordinate_reference_ready = True
    program = machine.preflight_program(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
    )
    validation_entered = threading.Event()
    release_validation = threading.Event()
    original_require = machine._require_current_safety_profile

    def blocked_validation(candidate: object) -> None:
        validation_entered.set()
        assert release_validation.wait(timeout=2.0)
        original_require(candidate)  # type: ignore[arg-type]

    monkeypatch.setattr(machine, "_require_current_safety_profile", blocked_validation)
    errors: list[Exception] = []

    def arm() -> None:
        try:
            machine.arm_program(machine.ARM_PHRASE, program)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=arm, daemon=True)
    worker.start()
    assert validation_entered.wait(timeout=1.0)
    machine.request_stop()
    release_validation.set()
    worker.join(timeout=1.0)

    assert len(errors) == 1
    assert "cancelled by software STOP" in str(errors[0])
    assert not machine.status()["armed"]
    assert machine.status()["controller_reconnect_required"]
    machine.disconnect()


def test_prepare_photo_position_in_simulation() -> None:
    settings = MachineSettings(backend="simulator", photo_x=110, photo_y=105, home_before_photo=True)
    machine = MachineService(settings, LaserSettings(), hardware_enabled=False)
    machine.connect()
    try:
        result = machine.prepare_photo_position()
        assert result["position"] == {"x": 110.0, "y": 105.0, "z": None}
        assert machine._transport is not None
        assert machine._transport.x == pytest.approx(110)
        assert machine._transport.y == pytest.approx(105)
    finally:
        machine.disconnect()


def test_jog_uses_bounded_absolute_laser_off_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    transport = RecordingTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.SimulatedTransport",
        lambda: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="simulator",
            allow_motion=True,
            photo_x=110.0,
            photo_y=105.0,
            max_travel_feed_mm_min=3000.0,
        ),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    try:
        assert not machine.status()["jog_ready"]
        with pytest.raises(SafetyError, match="Home / park"):
            machine.jog(1.0, 0.0, 500.0)

        machine.prepare_photo_position()
        assert machine.status()["jog_ready"]
        transport.commands.clear()

        first = machine.jog(5.0, -1.0, 700.0)
        second = machine.jog(-0.1, 0.2, 600.0)

        assert first["position"] == {"x": 115.0, "y": 104.0}
        assert second["position"] == {"x": 114.9, "y": 104.2}
        assert transport.commands == [
            "M5",
            "G21",
            "G90",
            "G0 X115.000 Y104.000 F700.000",
            "G4 P0.01",
            "M5",
            "G21",
            "G90",
            "G0 X114.900 Y104.200 F600.000",
            "G4 P0.01",
        ]
        assert not any(command.startswith(("M3", "M4")) for command in transport.commands)
        assert transport.laser_on is False
        assert machine.status()["jog_position_mm"] == {"x": 114.9, "y": 104.2}
    finally:
        machine.disconnect()


def test_serial_grbl_jog_rechecks_homed_coordinate_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    transport = RecordingTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            photo_x=110.0,
            photo_y=105.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()
        transport.commands.clear()

        machine.jog(1.0, 0.0, 500.0)

        assert transport.commands == [
            "$G",
            "$#",
            "M5",
            "G21",
            "G90",
            "G0 X111.000 Y105.000 F500.000",
            "G4 P0.01",
        ]
        assert machine.status()["jog_position_mm"] == {"x": 111.0, "y": 105.0}
    finally:
        machine.disconnect()


@pytest.mark.parametrize(
    ("dx", "dy", "feed", "message"),
    [
        (0.0, 0.0, 500.0, "must move"),
        (float("nan"), 0.0, 500.0, "finite"),
        (1.0, 0.0, 0.0, "positive"),
        (1.0, 0.0, 3001.0, "travel ceiling"),
    ],
)
def test_jog_rejects_invalid_requests_before_motion(
    dx: float,
    dy: float,
    feed: float,
    message: str,
) -> None:
    machine = MachineService(
        MachineSettings(
            backend="simulator",
            allow_motion=True,
            photo_x=110.0,
            photo_y=105.0,
            max_travel_feed_mm_min=3000.0,
        ),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()
        assert machine._transport is not None
        before = (machine._transport.x, machine._transport.y)
        with pytest.raises(SafetyError, match=message):
            machine.jog(dx, dy, feed)
        assert (machine._transport.x, machine._transport.y) == before
        assert machine.status()["jog_ready"]
    finally:
        machine.disconnect()


def test_jog_can_move_beyond_configured_work_area_for_limit_measurement() -> None:
    machine = MachineService(
        MachineSettings(
            backend="simulator",
            allow_motion=True,
            photo_x=210.0,
            photo_y=105.0,
        ),
        LaserSettings(boundary_margin_mm=5.0),
        hardware_enabled=False,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()

        result = machine.jog(10.0, 0.0, 500.0)

        assert result["position"] == {"x": 220.0, "y": 105.0}
        assert machine._transport is not None
        assert machine._transport.x == pytest.approx(220.0)
    finally:
        machine.disconnect()


def test_stop_during_jog_invalidates_position_and_prevents_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockedJogTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.jog_written = threading.Event()

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "G0 X111.000 Y105.000 F500.000":
                self.jog_written.set()
                return
            super().write_line(line)

    transport = BlockedJogTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.SimulatedTransport",
        lambda: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="simulator",
            allow_motion=True,
            photo_x=110.0,
            photo_y=105.0,
            read_timeout=2.0,
        ),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    machine.prepare_photo_position()
    transport.commands.clear()
    errors: list[Exception] = []

    def run_jog() -> None:
        try:
            machine.jog(1.0, 0.0, 500.0)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_jog, daemon=True)
    worker.start()
    assert transport.jog_written.wait(timeout=1.0)
    machine.request_stop()
    worker.join(timeout=1.0)

    assert len(errors) == 1
    assert "cancelled by software STOP" in str(errors[0])
    assert transport.commands == [
        "M5",
        "G21",
        "G90",
        "G0 X111.000 Y105.000 F500.000",
        "M5",
    ]
    status = machine.status()
    assert status["jog_position_mm"] is None
    assert not status["jog_ready"]
    assert not status["coordinate_reference_ready"]
    assert status["controller_reconnect_required"]
    machine.disconnect()


def test_jog_rejects_armed_or_motion_disabled_state() -> None:
    settings = MachineSettings(
        backend="simulator",
        allow_motion=True,
        photo_x=110.0,
        photo_y=105.0,
    )
    machine = MachineService(settings, LaserSettings(), hardware_enabled=False)
    machine.connect()
    try:
        machine.prepare_photo_position()
        machine.arm(machine.ARM_PHRASE)
        with pytest.raises(SafetyError, match="Disarm"):
            machine.jog(1.0, 0.0, 500.0)
        machine.disarm()
        settings.allow_motion = False
        with pytest.raises(SafetyError, match="machine.allow_motion"):
            machine.jog(1.0, 0.0, 500.0)
    finally:
        machine.disconnect()


def test_prepare_photo_position_allows_six_seconds_for_setup_acknowledgements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = MachineSettings(
        backend="simulator",
        photo_x=110,
        photo_y=105,
        home_before_photo=True,
        read_timeout=1.0,
    )
    machine = MachineService(settings, LaserSettings(), hardware_enabled=False)
    machine.connect()
    recorded: list[tuple[str, float | None, bool]] = []

    def record_command(
        command: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
        _expected_stop_epoch: int | None = None,
    ) -> list[str]:
        del _expected_stop_epoch
        recorded.append((command, timeout, _internal_motion))
        return ["ok"]

    monkeypatch.setattr(machine, "send_command", record_command)
    try:
        machine.prepare_photo_position()
    finally:
        machine.disconnect()

    assert recorded[0] == ("M5", 6.0, True)
    assert recorded[1] == ("$H", 120.0, True)
    assert recorded[2] == ("G21", 6.0, True)
    assert recorded[3] == ("G90", 6.0, True)
    assert recorded[4][1:] == (6.0, True)
    assert recorded[5] == ("G4 P0.01", 120.0, True)
    assert settings.read_timeout == 1.0


def test_temporary_stepper_hold_restores_grbl_idle_delay_after_capture_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine._transport = object()  # type: ignore[assignment]
    commands: list[str] = []

    def record_command(
        command: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
    ) -> list[str]:
        del timeout, _internal_motion
        commands.append(command)
        return ["$0=10", "$1=250", "ok"] if command == "$$" else ["ok"]

    monkeypatch.setattr(machine, "send_command", record_command)

    with pytest.raises(CameraError, match="capture failed"):
        with machine.temporary_stepper_hold():
            commands.append("capture")
            raise CameraError("capture failed")

    assert commands == ["$$", "$1=255", "capture", "M5", "$1=250", "$MD"]


def test_serial_connect_repairs_camera_hold_persisted_across_power_cycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StaleHoldTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "$$":
                self._queue.put("$1=255")
                self._queue.put("ok")
                return
            super().write_line(line)

    transport = StaleHoldTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            grbl_step_idle_delay_ms=250,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )

    try:
        machine.connect()
        assert transport.commands[:4] == ["$$", "M5", "$1=250", "$MD"]
        assert any("Recovered stale camera motor hold" in line for line in machine.status()["log"])
    finally:
        machine.disconnect()


def test_serial_connect_explicitly_releases_motors_with_normal_idle_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NormalIdleTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "$$":
                self._queue.put("$1=250")
                self._queue.put("ok")
                return
            super().write_line(line)

    transport = NormalIdleTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl"),
        LaserSettings(),
        hardware_enabled=True,
    )

    try:
        machine.connect()
        assert transport.commands[:3] == ["$$", "M5", "$MD"]
        assert "$1=250" not in transport.commands
        assert not machine.status()["coordinate_reference_ready"]
    finally:
        machine.disconnect()


@pytest.mark.parametrize(
    ("responses", "expected"),
    [
        (["$0=10", "$1=250", "$2=0", "ok"], 250),
        (["$1 = 250 (step idle delay, msec)", "ok"], 250),
        (["$1=250.000", "ok"], 250),
        (["$10=1", "$11=0.010", "ok"], None),
        (["$1=250.5", "ok"], None),
    ],
)
def test_grbl_step_idle_delay_parser_accepts_report_variants_only(
    responses: list[str],
    expected: int | None,
) -> None:
    assert MachineService._reported_grbl_step_idle_delay(responses) == expected


def test_home_park_waits_for_serial_connection_initialization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockingSettingsTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.settings_write_started = threading.Event()
            self.release_settings_write = threading.Event()
            self.overlapping_write = threading.Event()

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "$$":
                self.settings_write_started.set()
                if not self.release_settings_write.wait(timeout=2.0):
                    raise RuntimeError("test did not release the blocked settings report")
            elif not self.release_settings_write.is_set():
                self.overlapping_write.set()
            super().write_line(line)

    transport = BlockingSettingsTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
                allow_motion=True,
                home_before_photo=True,
                photo_x=110,
                photo_y=105,
                controller_startup_delay=0.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    errors: list[BaseException] = []
    parked: list[dict[str, object]] = []

    def connect() -> None:
        try:
            machine.connect()
        except BaseException as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    def home_and_park() -> None:
        try:
            parked.append(machine.prepare_photo_position())
        except BaseException as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    connect_thread = threading.Thread(target=connect, name="test-connect")
    home_thread = threading.Thread(target=home_and_park, name="test-home-park")
    connect_thread.start()
    assert transport.settings_write_started.wait(timeout=1.0)
    assert machine.status()["connecting"]
    assert not machine.status()["connected"]
    home_thread.start()
    try:
        assert not transport.overlapping_write.wait(timeout=0.1), (
            "Home / park wrote to the controller while Connect was still reading $$"
        )
    finally:
        transport.release_settings_write.set()
        connect_thread.join(timeout=3.0)
        home_thread.join(timeout=3.0)

    assert not connect_thread.is_alive()
    assert not home_thread.is_alive()
    assert errors == []
    assert parked[0]["position"] == {"x": 110.0, "y": 105.0, "z": None}
    assert transport.commands.index("M5") > transport.commands.index("$$")


def test_queued_home_park_does_not_move_when_connection_initialization_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingSettingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.settings_write_started = threading.Event()
            self.release_settings_write = threading.Event()

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "$$":
                self.settings_write_started.set()
                if not self.release_settings_write.wait(timeout=2.0):
                    raise RuntimeError("test did not release the blocked settings report")
                self._queue.put("$30=1000")
                self._queue.put("ok")
                return
            super().write_line(line)

    transport = MissingSettingTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
                protocol="grbl",
                allow_motion=True,
                home_before_photo=True,
                controller_startup_delay=0.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    errors: list[BaseException] = []

    def run(operation: Callable[[], object]) -> None:
        try:
            operation()
        except BaseException as exc:  # pragma: no cover - assertion reports details
            errors.append(exc)

    connect_thread = threading.Thread(target=run, args=(machine.connect,))
    home_thread = threading.Thread(target=run, args=(machine.prepare_photo_position,))
    connect_thread.start()
    assert transport.settings_write_started.wait(timeout=1.0)
    home_thread.start()
    transport.release_settings_write.set()
    connect_thread.join(timeout=3.0)
    home_thread.join(timeout=3.0)

    assert len(errors) == 2
    assert any("settings could not be read" in str(error) for error in errors)
    assert any("not connected" in str(error) for error in errors)
    assert "$H" not in transport.commands
    assert "G21" not in transport.commands
    assert not machine.status()["connected"]


def test_serial_connect_forces_finite_idle_delay_when_grbl_omits_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class MissingIdleSettingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "$$":
                self._queue.put("$30=1000")
                self._queue.put("ok")
                return
            super().write_line(line)

    transport = MissingIdleSettingTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            grbl_step_idle_delay_ms=250,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )

    with pytest.raises(MachineError, match=r"GRBL settings could not be read"):
        machine.connect()

    assert transport.commands[:4] == ["$$", "M5", "$1=250", "$MD"]
    assert not machine.connected
    assert not transport.is_open


def test_explicit_grbl_connect_waits_for_controller_startup_before_querying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class StartupSensitiveTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.ready = False
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if not self.ready:
                return
            super().write_line(line)

    transport = StartupSensitiveTransport()
    sleeps: list[float] = []

    def finish_startup(delay: float) -> None:
        sleeps.append(delay)
        transport.ready = True

    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    monkeypatch.setattr("laser_aligner.machine.service.time.sleep", finish_startup)
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            controller_startup_delay=2.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )

    status = machine.connect()

    assert status["connected"] is True
    assert sleeps[0] == 2.0
    assert transport.commands[:3] == ["$$", "M5", "$MD"]
    machine.disconnect()


def test_arm_waits_for_an_inflight_jog_command() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator", allow_motion=True),
        LaserSettings(arm_timeout_seconds=10),
        hardware_enabled=False,
    )
    machine.connect()
    machine.prepare_photo_position()
    start_position = machine.status()["jog_position_mm"]
    assert start_position is not None
    target_x = float(start_position["x"]) + 10.0
    target_y = float(start_position["y"])
    transport = machine._transport
    assert isinstance(transport, SimulatedTransport)
    original_write_line = transport.write_line
    jog_write_entered = threading.Event()
    release_jog_write = threading.Event()

    def blocked_write_line(line: str) -> None:
        expected = f"G0 X{target_x:.3f} Y{target_y:.3f}"
        if line.strip().upper().startswith(expected):
            jog_write_entered.set()
            assert release_jog_write.wait(timeout=2.0)
        original_write_line(line)

    transport.write_line = blocked_write_line  # type: ignore[method-assign]
    jog_errors: list[Exception] = []
    arm_errors: list[Exception] = []
    armed = threading.Event()

    def jog() -> None:
        try:
            machine.jog(10.0, 0.0, 1000.0)
        except Exception as exc:
            jog_errors.append(exc)

    def arm() -> None:
        try:
            machine.arm(machine.ARM_PHRASE)
            armed.set()
        except Exception as exc:
            arm_errors.append(exc)

    jog_worker = threading.Thread(target=jog, daemon=True)
    arm_worker = threading.Thread(target=arm, daemon=True)
    jog_worker.start()
    assert jog_write_entered.wait(timeout=1.0)
    arm_worker.start()
    time.sleep(0.05)

    assert not armed.is_set()
    assert arm_worker.is_alive()

    release_jog_write.set()
    jog_worker.join(timeout=2.0)
    arm_worker.join(timeout=2.0)

    assert not jog_worker.is_alive()
    assert not arm_worker.is_alive()
    assert jog_errors == []
    assert arm_errors == []
    assert armed.is_set()
    assert machine.status()["jog_position_mm"] == {"x": target_x, "y": target_y}
    machine.disconnect()


def test_stop_cancels_an_arm_queued_behind_controller_ownership() -> None:
    machine = MachineService(
        MachineSettings(backend="simulator", allow_motion=True),
        LaserSettings(arm_timeout_seconds=10),
        hardware_enabled=False,
    )
    machine.connect()
    errors: list[Exception] = []
    arm_waiting = threading.Event()
    release_arm = threading.Event()
    command_lock = machine._command_lock

    class PausedCommandLock:
        def __enter__(self) -> "PausedCommandLock":
            arm_waiting.set()
            assert release_arm.wait(timeout=2.0)
            command_lock.acquire()
            return self

        def __exit__(self, *_args: object) -> None:
            command_lock.release()

    machine._command_lock = PausedCommandLock()  # type: ignore[assignment]

    def arm() -> None:
        try:
            machine.arm(machine.ARM_PHRASE)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=arm, daemon=True)
    worker.start()
    assert arm_waiting.wait(timeout=1.0)
    machine.request_stop()
    release_arm.set()
    worker.join(timeout=2.0)
    machine._command_lock = command_lock

    assert not worker.is_alive()
    assert len(errors) == 1
    assert "cancelled by software STOP" in str(errors[0])
    assert machine.status()["armed"] is False
    machine.disconnect()


def test_temporary_stepper_hold_repairs_existing_stale_continuous_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine._transport = object()  # type: ignore[assignment]
    commands: list[str] = []

    def record_command(
        command: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
    ) -> list[str]:
        del timeout, _internal_motion
        commands.append(command)
        return ["$1=255", "ok"]

    monkeypatch.setattr(machine, "send_command", record_command)

    with machine.temporary_stepper_hold():
        commands.append("capture")

    assert commands == ["$$", "capture", "M5", "$1=250", "$MD"]


def test_temporary_stepper_hold_falls_back_to_sleep_and_invalidates_position(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ResetTransport:
        def __init__(self) -> None:
            self.raw: list[bytes] = []

        def write_raw(self, data: bytes) -> None:
            self.raw.append(data)

        def drain(self) -> list[str]:
            return ["Grbl reset"]

    transport = ResetTransport()
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            controller_startup_delay=0.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine._transport = transport  # type: ignore[assignment]
    machine._connected = True
    machine._coordinate_reference_ready = True
    commands: list[str] = []

    def record_command(
        command: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
    ) -> list[str]:
        del timeout, _internal_motion
        commands.append(command)
        if command == "$$":
            return ["$1=250", "ok"]
        if command == "$MD":
            raise MachineError("error:20")
        return ["ok"]

    monkeypatch.setattr(machine, "send_command", record_command)

    with machine.temporary_stepper_hold():
        pass

    assert commands == ["$$", "$1=255", "M5", "$1=250", "$MD", "$SLP", "$X"]
    assert transport.raw == [b"\x18"]
    assert not machine.status()["coordinate_reference_ready"]


def test_temporary_stepper_hold_cleanup_reaches_disable_after_prior_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine._transport = object()  # type: ignore[assignment]
    machine._connected = True
    machine._coordinate_reference_ready = True
    commands: list[str] = []

    def record_command(
        command: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
    ) -> list[str]:
        del timeout, _internal_motion
        commands.append(command)
        if command == "$$":
            return ["$1=250", "ok"]
        if command in {"M5", "$1=250"}:
            raise MachineError(f"forced {command} failure")
        return ["ok"]

    monkeypatch.setattr(machine, "send_command", record_command)

    with pytest.raises(MachineError, match="cleanup incomplete") as error:
        with machine.temporary_stepper_hold():
            pass

    assert commands == ["$$", "$1=255", "M5", "$1=250", "$MD"]
    assert "M5 failed" in str(error.value)
    assert "idle-delay restore failed" in str(error.value)
    assert not machine.status()["coordinate_reference_ready"]


def test_temporary_stepper_hold_attaches_cleanup_failure_to_capture_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine._transport = object()  # type: ignore[assignment]
    machine._connected = True
    commands: list[str] = []

    def record_command(
        command: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
    ) -> list[str]:
        del timeout, _internal_motion
        commands.append(command)
        if command == "$$":
            return ["$1=250", "ok"]
        if command == "M5":
            raise MachineError("forced M5 failure")
        return ["ok"]

    monkeypatch.setattr(machine, "send_command", record_command)

    with pytest.raises(CameraError, match="camera burst failed") as error:
        with machine.temporary_stepper_hold():
            raise CameraError("camera burst failed")

    notes = list(getattr(error.value, "__notes__", ()))
    assert len(notes) == 1
    assert "cleanup incomplete" in notes[0]
    assert "M5 failed" in notes[0]
    assert commands == ["$$", "$1=255", "M5", "$1=250", "$MD"]


def test_home_park_recovers_only_post_sleep_m5_alarm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            photo_x=110,
            photo_y=105,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine._transport = object()  # type: ignore[assignment]
    machine._connected = True
    commands: list[str] = []
    first_m5 = True

    def record_command(
        command: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
        _expected_stop_epoch: int | None = None,
    ) -> list[str]:
        nonlocal first_m5
        del timeout, _internal_motion, _expected_stop_epoch
        commands.append(command)
        if command == "M5" and first_m5:
            first_m5 = False
            raise MachineError("Command 'M5' failed: error:9")
        return ["ok"]

    monkeypatch.setattr(machine, "send_command", record_command)
    monkeypatch.setattr(
        machine,
        "_wait_for_motion_complete",
        lambda timeout, **_kwargs: ["ok"],
    )
    monkeypatch.setattr(
        machine,
        "_read_grbl_coordinate_state",
        lambda: {
            "active_workspace": "G54",
            "active_offset_mm": [0.0, 0.0, 0.0],
            "g92_offset_mm": [0.0, 0.0, 0.0],
        },
    )

    machine.prepare_photo_position()

    assert commands[:4] == ["M5", "$X", "M5", "$H"]
    assert machine.status()["coordinate_reference_ready"]


def test_temporary_stepper_hold_refuses_to_guess_missing_idle_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine._transport = object()  # type: ignore[assignment]
    monkeypatch.setattr(machine, "send_command", lambda *args, **kwargs: ["$0=10", "ok"])

    with pytest.raises(MachineError, match=r"did not report \$1"):
        with machine.temporary_stepper_hold():
            pytest.fail("Capture must not start without a known restore value")


def test_temporary_stepper_hold_is_noop_in_simulation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    monkeypatch.setattr(
        machine,
        "send_command",
        lambda *args, **kwargs: pytest.fail("Simulator hold must not send controller settings"),
    )

    with machine.temporary_stepper_hold():
        pass


def test_grbl_home_park_uses_planner_barrier_not_realtime_idle_polling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: SimulatedTransport(),
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            photo_x=110,
            photo_y=105,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    monkeypatch.setattr(
        machine,
        "_wait_until_idle",
        lambda timeout: pytest.fail("Home / park must not depend on realtime status"),
    )
    try:
        result = machine.prepare_photo_position()
    finally:
        machine.disconnect()

    commands = [item["command"] for item in result["transcript"]]
    assert commands == [
        "M5",
        "$H",
        "G21",
        "G90",
        "G0 X110.000 Y105.000 F3000.000",
    ]
    assert result["idle_responses"] == ["ok"]
    assert result["coordinate_state"] == {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }


def test_serial_job_rejects_work_offset_changed_after_home_park(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OffsetTransport(SimulatedTransport):
        g54_x = 0.0

        def write_line(self, line: str) -> None:
            if line.strip().upper() == "$#":
                for code in range(54, 60):
                    x = self.g54_x if code == 54 else 0.0
                    self._queue.put(f"[G{code}:{x:.3f},0.000,0.000]")
                self._queue.put("[G92:0.000,0.000,0.000]")
                self._queue.put("ok")
                return
            super().write_line(line)

    transport = OffsetTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            photo_x=110,
            photo_y=105,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()
        transport.g54_x = 2.5
        with pytest.raises(SafetyError, match=r"G54 changed.*2\.5"):
            machine.start_job(
                "G21\nG90\nM5\nG0 X10 Y10 F1000\nM5\n",
                "shifted.gcode",
            )
        assert not machine.status()["coordinate_reference_ready"]
    finally:
        machine.disconnect()


def test_command_timeout_identifies_the_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    monkeypatch.setattr(
        machine,
        "_wait_for_ack",
        lambda timeout, **_kwargs: (_ for _ in ()).throw(
            MachineError(f"Controller did not acknowledge command within {timeout:g} seconds")
        ),
    )
    try:
        with pytest.raises(MachineError, match=r"Command 'M5' failed.*6 seconds"):
            machine.prepare_photo_position()
    finally:
        machine.disconnect()


def test_serial_motion_and_arming_require_home_park_in_each_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: SimulatedTransport(),
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            photo_x=110,
            photo_y=105,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        assert not machine.status()["coordinate_reference_ready"]
        with pytest.raises(SafetyError, match="Home / park"):
            machine.start_job(
                "G21\nG90\nM5\nG0 X10 Y10 F1000\nM5\n",
                "unreferenced.gcode",
            )
        with pytest.raises(SafetyError, match="Home / park"):
            machine.arm(machine.ARM_PHRASE)

        machine.prepare_photo_position()
        assert machine.status()["coordinate_reference_ready"]
        machine.start_job(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nM5\n",
            "referenced.gcode",
        )
        wait_for_job(machine)
        assert machine.status()["job"]["error"] is None

        machine.stop_job(emergency=True)
        assert not machine.status()["coordinate_reference_ready"]
    finally:
        machine.disconnect()


def test_successful_powered_serial_job_homes_parks_and_releases_motors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.planner_busy = False

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command.startswith(("G0 ", "G1 ")):
                self.planner_busy = True
            elif command in {"G4 P0.01", "M400"}:
                self.planner_busy = False
            elif command == "$H" and self.planner_busy:
                self._queue.put("error:8")
                return
            super().write_line(line)

    transport = RecordingTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            home_and_release_after_powered_job=True,
            photo_x=15,
            photo_y=195,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()
        transport.commands.clear()
        program = machine.preflight_program(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
        )
        machine.arm_program(machine.ARM_PHRASE, program)
        machine.start_validated_program(program, "powered.gcode")
        wait_for_job(machine)

        status = machine.status()
        assert status["job"]["error"] is None
        assert status["job"]["phase"] == "complete"
        assert not status["coordinate_reference_ready"]
        assert transport.x == pytest.approx(15)
        assert transport.y == pytest.approx(195)
        assert transport.commands[-10:] == [
            "M5",
            "G4 P0.01",
            "$H",
            "G21",
            "G90",
            "G0 X15.000 Y195.000 F3000.000",
            "G4 P0.01",
            "$$",
            "M5",
            "$MD",
        ]
        assert "M9" not in transport.commands
    finally:
        machine.disconnect()


def test_powered_job_waits_past_interactive_timeout_for_final_m5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DelayedLaserOffTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.saw_powered_command = False
            self.delayed_laser_off = False

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command.startswith(("M3 ", "M4 ")):
                self.saw_powered_command = True
            if (
                command == "M5"
                and self.saw_powered_command
                and not self.delayed_laser_off
            ):
                self.delayed_laser_off = True
                self.laser_on = False
                self.power = 0.0
                timer = threading.Timer(0.05, lambda: self._queue.put("ok"))
                timer.daemon = True
                timer.start()
                return
            super().write_line(line)

    transport = DelayedLaserOffTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    monkeypatch.setattr(
        "laser_aligner.machine.service._JOB_COMMAND_ACK_TIMEOUT_SECONDS",
        0.2,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            read_timeout=0.01,
            allow_motion=True,
            home_before_photo=True,
            home_and_release_after_powered_job=True,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()
        transport.commands.clear()
        program = machine.preflight_program(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
        )
        machine.arm_program(machine.ARM_PHRASE, program)
        machine.start_validated_program(program, "delayed-m5.gcode")
        wait_for_job(machine)

        assert transport.delayed_laser_off
        assert machine.status()["job"]["error"] is None
        assert "$H" in transport.commands
    finally:
        machine.disconnect()


def test_stop_interrupts_extended_job_ack_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BlockedMoveTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.block_job_move = False
            self.move_written = threading.Event()

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            if command == "G1 X20 Y20 F500" and self.block_job_move:
                self.move_written.set()
                return
            super().write_line(line)

    transport = BlockedMoveTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()
        transport.block_job_move = True
        machine.start_job(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nG1 X20 Y20 F500\nM5\n",
            "blocked-ack.gcode",
        )
        assert transport.move_written.wait(timeout=1.0)

        machine.stop_job()
        wait_for_job(machine)

        status = machine.status()
        assert not status["job"]["running"]
        assert status["job"]["error"] == "Job stopped"
    finally:
        machine.disconnect()


def test_stop_during_final_ack_cannot_publish_success_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class GatedFinalAckTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.m5_count = 0
            self.block_next_read = False
            self.final_ack_waiting = threading.Event()
            self.release_final_ack = threading.Event()

        def write_line(self, line: str) -> None:
            if line.strip().upper() == "M5":
                self.m5_count += 1
                if self.m5_count == 4:
                    self.block_next_read = True
            super().write_line(line)

        def read_line(self, timeout: float) -> str | None:
            if self.block_next_read:
                self.block_next_read = False
                self.final_ack_waiting.set()
                assert self.release_final_ack.wait(timeout=2.0)
            return super().read_line(timeout)

    transport = GatedFinalAckTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.SimulatedTransport",
        lambda: transport,
    )
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    machine.start_job(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\nG1 X20 Y20 F500\nM5\n",
        "stop-at-final-ack.gcode",
    )
    assert transport.final_ack_waiting.wait(timeout=1.0)

    machine.request_stop()
    transport.release_final_ack.set()
    wait_for_job(machine)

    status = machine.status()
    assert status["job"]["error"] == "Job stopped"
    assert status["job"]["phase"] == "failed"
    assert status["last_successful_job"] is None
    machine.disconnect()


def test_powered_job_release_falls_back_to_sleep_reset_without_fan_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectMotorDisableTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.raw_writes: list[bytes] = []

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "$MD":
                self._queue.put("error:20")
                return
            super().write_line(line)

        def write_raw(self, data: bytes) -> None:
            self.raw_writes.append(data)
            super().write_raw(data)

    transport = RejectMotorDisableTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            home_and_release_after_powered_job=True,
            controller_startup_delay=0.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )

    machine.connect()
    try:
        assert transport.commands[:5] == ["$$", "M5", "$MD", "$SLP", "$X"]
        machine.prepare_photo_position()
        transport.commands.clear()
        transport.raw_writes.clear()
        program = machine.preflight_program(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
        )
        machine.arm_program(machine.ARM_PHRASE, program)
        machine.start_validated_program(program, "powered-fallback.gcode")
        wait_for_job(machine)

        assert machine.status()["job"]["error"] is None
        assert transport.commands[-12:] == [
            "M5",
            "G4 P0.01",
            "$H",
            "G21",
            "G90",
            "G0 X110.000 Y110.000 F3000.000",
            "G4 P0.01",
            "$$",
            "M5",
            "$MD",
            "$SLP",
            "$X",
        ]
        assert transport.raw_writes == [b"\x18"]
        assert "M8" not in transport.commands
        assert "M9" not in transport.commands
        assert not machine.status()["coordinate_reference_ready"]
    finally:
        machine.disconnect()


def test_dry_serial_job_does_not_move_or_release_after_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

    transport = RecordingTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            home_and_release_after_powered_job=True,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()
        transport.commands.clear()
        machine.start_job(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nM5\n",
            "dry.gcode",
        )
        wait_for_job(machine)

        assert machine.status()["job"]["error"] is None
        assert "$H" not in transport.commands
        assert "$MD" not in transport.commands
        assert "M9" not in transport.commands
    finally:
        machine.disconnect()


def test_serial_motion_job_does_not_publish_success_before_planner_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DelayedBarrierTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.barrier_entered = threading.Event()
            self.release_barrier = threading.Event()
            self.delay_barrier = False

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            if command == "G4 P0.01" and self.delay_barrier:
                self.barrier_entered.set()
                assert self.release_barrier.wait(timeout=2.0)
            super().write_line(line)

    transport = DelayedBarrierTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            home_and_release_after_powered_job=True,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()
        transport.delay_barrier = True
        machine.start_job(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nM5\n",
            "motion.gcode",
        )
        assert transport.barrier_entered.wait(timeout=1.0)

        status = machine.status()["job"]
        assert status["running"] is True
        assert status["phase"] == "draining"
        assert status["finished_at"] is None

        transport.release_barrier.set()
        wait_for_job(machine)
        status = machine.status()["job"]
        assert status["running"] is False
        assert status["phase"] == "complete"
        assert status["error"] is None
    finally:
        transport.release_barrier.set()
        machine.disconnect()


def test_post_job_positioning_failure_still_attempts_motor_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingHomeTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.fail_homing = False

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "$H" and self.fail_homing:
                self._queue.put("error:8")
                return
            super().write_line(line)

    transport = FailingHomeTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            home_and_release_after_powered_job=True,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()
        transport.commands.clear()
        transport.fail_homing = True
        program = machine.preflight_program(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
        )
        machine.arm_program(machine.ARM_PHRASE, program)
        machine.start_validated_program(program, "powered.gcode")
        wait_for_job(machine)

        status = machine.status()
        assert "COMMAND '$H' FAILED: ERROR:8" in status["job"]["error"].upper()
        assert status["job"]["phase"] == "failed"
        assert any("ERROR Controller job failed" in line for line in status["log"])
        assert transport.commands.index("G4 P0.01") < transport.commands.index("$H")
        assert "$MD" in transport.commands
        assert not any(command.startswith("G0 ") for command in transport.commands[-5:])
    finally:
        machine.disconnect()


def test_post_job_park_failure_still_attempts_motor_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingParkTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.fail_parking = False

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if (
                command == "G0 X15.000 Y195.000 F3000.000"
                and self.fail_parking
            ):
                self._queue.put("error:15")
                return
            super().write_line(line)

    transport = FailingParkTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            home_and_release_after_powered_job=True,
            photo_x=15,
            photo_y=195,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()
        transport.commands.clear()
        transport.fail_parking = True
        program = machine.preflight_program(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
        )
        machine.arm_program(machine.ARM_PHRASE, program)
        machine.start_validated_program(program, "powered.gcode")
        wait_for_job(machine)

        error = machine.status()["job"]["error"]
        assert error is not None
        assert "G0 X15.000 Y195.000" in error
        assert "$H" in transport.commands
        assert "$MD" in transport.commands
        assert transport.commands.count("G4 P0.01") == 1
        assert transport.commands.index("G4 P0.01") < transport.commands.index("$H")
        assert transport.x == pytest.approx(0)
        assert transport.y == pytest.approx(0)
    finally:
        machine.disconnect()


def test_failed_powered_serial_job_does_not_home_park_or_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingJobTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.fail_job_move = False

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "G1 X20 Y20 F500" and self.fail_job_move:
                self._queue.put("error:33")
                return
            super().write_line(line)

    transport = FailingJobTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=True,
            home_and_release_after_powered_job=True,
            photo_x=15,
            photo_y=195,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        machine.prepare_photo_position()
        transport.commands.clear()
        transport.fail_job_move = True
        program = machine.preflight_program(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
        )
        machine.arm_program(machine.ARM_PHRASE, program)
        machine.start_validated_program(program, "failed-powered.gcode")
        wait_for_job(machine)

        assert "ERROR:33" in machine.status()["job"]["error"].upper()
        assert "$H" not in transport.commands
        assert "$MD" not in transport.commands
        assert "G0 X15.000 Y195.000 F3000.000" not in transport.commands
        assert transport.commands[-1] == "M5"
    finally:
        machine.disconnect()


def test_serial_home_park_rejects_motion_without_homing_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_serial_transport",
        lambda port, baudrate: SimulatedTransport(),
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            allow_motion=True,
            home_before_photo=False,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    try:
        with pytest.raises(SafetyError, match="home_before_photo=true"):
            machine.prepare_photo_position()
    finally:
        machine.disconnect()


def test_failed_serial_job_poisoning_blocks_stale_cleanup_acknowledgement() -> None:
    class ErrorTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "G21" and not self.failed:
                self.failed = True
                self._queue.put("error:33")
                return
            super().write_line(line)

    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    transport = ErrorTransport()
    transport.open()
    transport.drain()
    machine._transport = transport
    machine._connected = True
    machine._coordinate_reference_ready = True
    machine._coordinate_state_reference = {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }

    machine.start_job(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\nM5\n",
        "failure.gcode",
    )
    wait_for_job(machine)

    assert "ERROR:33" in str(machine.status()["job"]["error"]).upper()
    assert machine.status()["controller_reconnect_required"]
    commands_before_retry = list(transport.commands)
    with pytest.raises(MachineError, match="disconnect and reconnect"):
        machine.prepare_photo_position()
    assert transport.commands == commands_before_retry
    machine.disconnect()


def test_failed_simulator_job_requires_reconnect_before_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ErrorTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.failed = False
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "G21" and not self.failed:
                self.failed = True
                self._queue.put("error:33")
                return
            super().write_line(line)

    transport = ErrorTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.SimulatedTransport",
        lambda: transport,
    )
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    machine.start_job(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\nM5\n",
        "simulator-failure.gcode",
    )
    wait_for_job(machine)

    assert "ERROR:33" in str(machine.status()["job"]["error"]).upper()
    assert machine.status()["controller_reconnect_required"]
    commands_before_retry = list(transport.commands)
    with pytest.raises(MachineError, match="disconnect and reconnect"):
        machine.prepare_photo_position()
    assert transport.commands == commands_before_retry
    machine.disconnect()


def test_ack_timeout_requires_reconnect_before_any_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DelayedAckTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.delay_first_m5 = True
            self.commands: list[str] = []

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "M5" and self.delay_first_m5:
                self.delay_first_m5 = False
                return
            super().write_line(line)

    transport = DelayedAckTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.SimulatedTransport",
        lambda: transport,
    )
    machine = MachineService(
        MachineSettings(backend="simulator"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()

    with pytest.raises(MachineError, match="did not acknowledge"):
        machine.send_command("M5", timeout=0.01)
    assert machine.status()["controller_reconnect_required"]
    transport._queue.put("ok")
    commands_before_retry = list(transport.commands)
    with pytest.raises(MachineError, match="disconnect and reconnect"):
        machine.prepare_photo_position()
    assert transport.commands == commands_before_retry
    machine.disconnect()


@pytest.mark.parametrize(
    ("protocol", "response", "reconnect_required"),
    [
        ("grbl", "error:33", False),
        ("grbl", "ALARM:1", True),
        ("marlin", "Error:Printer halted", True),
    ],
)
def test_only_grbl_error_is_treated_as_a_consumed_terminal_response(
    protocol: str,
    response: str,
    reconnect_required: bool,
) -> None:
    class RejectingTransport(SimulatedTransport):
        def write_line(self, line: str) -> None:
            del line
            self._queue.put(response)

    transport = RejectingTransport()
    transport.open()
    machine = MachineService(
        MachineSettings(backend="serial", protocol=protocol),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine._transport = transport
    machine._connected = True
    machine._protocol = protocol

    with pytest.raises(MachineError, match=response.split(":", 1)[0]):
        machine.send_command("M5", timeout=0.1)

    assert machine.status()["controller_reconnect_required"] is reconnect_required
    machine.disconnect()


def test_disarm_stops_an_active_laser_job() -> None:
    machine = MachineService(MachineSettings(backend="simulator"), LaserSettings(), hardware_enabled=False)
    machine.connect()
    try:
        lines = ["G21", "G90", "M5", "G0 X10 Y10 F1000", "M4 S5"]
        for index in range(2000):
            coordinate = 20 if index % 2 else 10
            lines.append(f"G1 X{coordinate} Y{coordinate} F500")
        lines.append("M5")
        program = machine.preflight_program("\n".join(lines))
        machine.arm_program(machine.ARM_PHRASE, program)
        machine.start_validated_program(program, "long.gcode")
        time.sleep(0.01)
        machine.disarm()
        wait_for_job(machine)
        status = machine.status()
        assert not status["armed"]
        assert not status["job"]["running"]
        assert status["job"]["error"] == "Job stopped"
    finally:
        machine.disconnect()
