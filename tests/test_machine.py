import threading
import time
from collections.abc import Callable
from dataclasses import replace

import pytest

from laser_aligner.config import LaserSettings, MachineSettings, WorkArea
from laser_aligner.errors import (
    CameraError,
    MachineError,
    SafetyError,
    TransientConnectionError,
)
from laser_aligner.machine.service import MachineService, _ControllerCommandRejected
from tests.fakes.simulator_transport import SimulatedTransport


@pytest.fixture(autouse=True)
def _explicit_test_machine_runtime(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Keep controller emulation test-only after removing the product simulator."""

    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda *_args, **_kwargs: SimulatedTransport(),
    )
    original_init = MachineService.__init__
    preserve_hardware_gate = "hardware_gate" in request.node.name

    def initialize(
        service: MachineService,
        settings: MachineSettings,
        laser: LaserSettings,
        hardware_enabled: bool = False,
        laser_lockout: bool = False,
    ) -> None:
        if not settings.allow_motion:
            settings.allow_motion = True
        original_init(
            service,
            settings,
            laser,
            hardware_enabled=(hardware_enabled or not preserve_hardware_gate),
            laser_lockout=laser_lockout,
        )

    monkeypatch.setattr(MachineService, "__init__", initialize)
    original_connect = MachineService.connect
    auto_reference = not any(
        token in request.node.name
        for token in (
            "jog",
            "prepare_job_start",
            "prepare_photo_position",
            "serial_",
            "ack_timeout",
            "initial_connection",
        )
    )

    def connect(service: MachineService, *args: object, **kwargs: object) -> dict[str, object]:
        result = original_connect(service, *args, **kwargs)
        if auto_reference:
            service._coordinate_reference_ready = True
            service._coordinate_state_reference = {
                "active_workspace": "G54",
                "active_offset_mm": [0.0, 0.0, 0.0],
                "g92_offset_mm": [0.0, 0.0, 0.0],
            }
            service._jog_position_mm = (0.0, 0.0)
        return result

    monkeypatch.setattr(MachineService, "connect", connect)


def wait_for_job(machine: MachineService, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while machine.status()["job"]["running"] and time.monotonic() < deadline:
        time.sleep(0.01)


def test_connect_hardware_gate_rejects_internal_disabled_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda *_args, **_kwargs: pytest.fail(
            "disabled process attempted transport construction"
        ),
    )
    machine = MachineService(
        MachineSettings(backend="serial"),
        LaserSettings(),
        hardware_enabled=False,
    )

    with pytest.raises(SafetyError, match="not granted hardware authority") as error:
        machine.connect()

    assert "--hardware" not in str(error.value)
    assert machine.connected is False


def test_grbl_realtime_status_parses_and_derives_position_vectors() -> None:
    complete = MachineService._parse_grbl_realtime_status(
        "<Idle|MPos:10.000,20.000,3.000|WPos:9.000,18.000,3.000|WCO:1.000,2.000,0.000>"
    )
    derived_wpos = MachineService._parse_grbl_realtime_status(
        "<Idle|MPos:10,20,3|WCO:1,2,0>"
    )
    derived_wco = MachineService._parse_grbl_realtime_status(
        "<Idle|MPos:10,20,3|WPos:9,18,3>"
    )

    assert complete["mpos_mm"] == [10.0, 20.0, 3.0]
    assert complete["wpos_mm"] == [9.0, 18.0, 3.0]
    assert complete["wco_mm"] == [1.0, 2.0, 0.0]
    assert derived_wpos["wpos_mm"] == [9.0, 18.0, 3.0]
    assert derived_wpos["derived_fields"] == ["WPos"]
    assert derived_wco["wco_mm"] == [1.0, 2.0, 0.0]
    assert derived_wco["derived_fields"] == ["WCO"]


def test_realtime_position_sampling_sends_only_question_mark() -> None:
    class RealtimeTransport:
        def __init__(self) -> None:
            self.raw_writes: list[bytes] = []
            self.responses = ["<Idle|MPos:15,195,0|WPos:15,195,0|WCO:0,0,0>"]

        def write_raw(self, data: bytes) -> None:
            self.raw_writes.append(data)

        def read_line(self, timeout: float = 1.0) -> str | None:
            del timeout
            return self.responses.pop(0) if self.responses else None

    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl"),
        LaserSettings(),
        hardware_enabled=True,
    )
    transport = RealtimeTransport()
    machine._transport = transport  # type: ignore[assignment]
    machine._connected = True
    machine._protocol = "grbl"

    snapshot = machine.sample_realtime_position(timeout=0.1)

    assert transport.raw_writes == [b"?"]
    assert snapshot["xy_complete"] is True
    assert snapshot["wpos_mm"][:2] == [15.0, 195.0]


def test_realtime_position_sampling_rejects_running_job_without_state_changes() -> None:
    class RealtimeTransport:
        def __init__(self) -> None:
            self.raw_writes: list[bytes] = []
            self.read_calls = 0
            self.responses = ["<Idle|MPos:15,195,0|WPos:15,195,0|WCO:0,0,0>"]

        def write_raw(self, data: bytes) -> None:
            self.raw_writes.append(data)

        def read_line(self, timeout: float = 1.0) -> str | None:
            del timeout
            self.read_calls += 1
            return self.responses.pop(0) if self.responses else None

    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl"),
        LaserSettings(),
        hardware_enabled=True,
    )
    transport = RealtimeTransport()
    machine._transport = transport  # type: ignore[assignment]
    machine._connected = True
    machine._protocol = "grbl"
    coordinate_state_reference = {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }
    machine._coordinate_reference_ready = True
    machine._coordinate_state_reference = coordinate_state_reference
    machine._jog_position_mm = (15.0, 195.0)
    machine._controller_reconnect_required = False
    machine._trusted_controller_session_established = True
    machine._authorization_epoch = 7
    machine._armed_until = time.time() + 60.0
    machine._armed_until_monotonic = time.monotonic() + 60.0
    machine._armed_program_digest = "running-program"
    machine._job_laser_authorized = True
    machine._job.running = True
    machine._job.phase = "streaming"
    machine._job.name = "running-job.gcode"
    machine._job.total_lines = 4
    machine._job.completed_lines = 2
    machine._job.program_digest = "running-program"
    machine._job.powered = True

    job = machine._job
    expected_job_state = job.to_dict()
    expected_session_state = (
        machine._transport,
        machine._connected,
        machine._protocol,
        machine._trusted_controller_session_established,
    )
    expected_coordinate_state = (
        machine._coordinate_reference_ready,
        machine._coordinate_state_reference,
        machine._jog_position_mm,
    )
    expected_authorization_state = (
        machine._authorization_epoch,
        machine._armed_until,
        machine._armed_until_monotonic,
        machine._armed_program_digest,
        machine._job_laser_authorized,
    )
    expected_log = list(machine._log)

    with pytest.raises(MachineError, match="while a job is running"):
        machine.sample_realtime_position(timeout=0.1)

    assert transport.raw_writes == []
    assert transport.read_calls == 0
    assert transport.responses == [
        "<Idle|MPos:15,195,0|WPos:15,195,0|WCO:0,0,0>"
    ]
    assert machine._job is job
    assert machine._job.to_dict() == expected_job_state
    assert (
        machine._transport,
        machine._connected,
        machine._protocol,
        machine._trusted_controller_session_established,
    ) == expected_session_state
    assert machine._controller_reconnect_required is False
    assert (
        machine._coordinate_reference_ready,
        machine._coordinate_state_reference,
        machine._jog_position_mm,
    ) == expected_coordinate_state
    assert machine._coordinate_state_reference is coordinate_state_reference
    assert (
        machine._authorization_epoch,
        machine._armed_until,
        machine._armed_until_monotonic,
        machine._armed_program_digest,
        machine._job_laser_authorized,
    ) == expected_authorization_state
    assert list(machine._log) == expected_log


@pytest.mark.parametrize(
    "response",
    ["<Idle|MPos:bad,2,0>", "<Idle|FS:0,0>", "not-a-status-frame"],
)
def test_realtime_position_sampling_fails_diagnostic_only(response: str) -> None:
    class RealtimeTransport:
        def __init__(self) -> None:
            self.raw_writes: list[bytes] = []
            self.responses = [response]

        def write_raw(self, data: bytes) -> None:
            self.raw_writes.append(data)

        def read_line(self, timeout: float = 1.0) -> str | None:
            del timeout
            return self.responses.pop(0) if self.responses else None

    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl"),
        LaserSettings(),
        hardware_enabled=True,
    )
    transport = RealtimeTransport()
    machine._transport = transport  # type: ignore[assignment]
    machine._connected = True
    machine._protocol = "grbl"
    coordinate_state_reference = {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }
    expected_coordinate_state_reference = {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }
    machine._coordinate_reference_ready = True
    machine._coordinate_state_reference = coordinate_state_reference
    machine._controller_reconnect_required = False
    machine._trusted_controller_session_established = True
    machine.arm(machine.ARM_PHRASE)
    authorization_epoch = machine._authorization_epoch
    armed_until = machine._armed_until

    with pytest.raises(MachineError):
        machine.sample_realtime_position(timeout=0.01)

    status = machine.status()
    assert transport.raw_writes == [b"?"]
    assert status["coordinate_reference_ready"] is True
    assert status["coordinate_state_reference"] is coordinate_state_reference
    assert status["coordinate_state_reference"] == expected_coordinate_state_reference
    assert status["controller_reconnect_required"] is False
    assert status["armed"] is True
    assert status["armed_until"] == armed_until
    assert machine._authorization_epoch == authorization_epoch
    assert machine._trusted_controller_session_established is True


def test_manual_positive_laser_commands_are_always_blocked() -> None:
    machine = MachineService(MachineSettings(backend="serial"), LaserSettings(), hardware_enabled=False)
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
    machine = MachineService(MachineSettings(backend="serial"), LaserSettings(), hardware_enabled=False)
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
        MachineSettings(backend="serial", allow_motion=True),
        LaserSettings(),
        hardware_enabled=False,
    )

    result = machine.ensure_connected()

    assert machine.status()["connected"] is True
    assert result["connected"] is True
    machine.disconnect()


def test_ensure_connected_never_reconnects_an_uncertain_established_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="serial", allow_motion=True),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    machine.request_stop()
    monkeypatch.setattr(
        machine,
        "disconnect",
        lambda: pytest.fail("uncertain sessions must not be automatically disconnected"),
    )
    monkeypatch.setattr(
        machine,
        "connect",
        lambda: pytest.fail("uncertain sessions must not be automatically reconnected"),
    )

    with pytest.raises(MachineError, match="disconnect and reconnect manually"):
        machine.ensure_connected()

    assert machine.status()["connected"] is True
    assert machine.status()["controller_reconnect_required"] is True
    monkeypatch.undo()
    machine.disconnect()


def test_process_laser_lockout_allows_motion_and_rejects_laser_enable() -> None:
    machine = MachineService(
        MachineSettings(backend="serial"),
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
            MachineSettings(backend="serial"),
            LaserSettings(),
            laser_lockout=value,  # type: ignore[arg-type]
        )


def test_dry_program_does_not_require_arming() -> None:
    machine = MachineService(MachineSettings(backend="serial"), LaserSettings(), hardware_enabled=False)
    machine.connect()
    try:
        machine.start_job("G21\nG90\nM5\nG0X10Y10F1000\nG1X20Y20F500\nM5\n", "dry.gcode")
        wait_for_job(machine)
        assert machine.status()["job"]["error"] is None
    finally:
        machine.disconnect()


def test_powered_program_preflight_is_side_effect_free() -> None:
    machine = MachineService(
        MachineSettings(backend="serial"),
        LaserSettings(),
        hardware_enabled=False,
    )
    program = "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"

    assert machine.validate_program(program)[-1] == "M5"
    assert not machine.status()["armed"]
    with pytest.raises(SafetyError, match="not armed"):
        machine._check_line_safety("M4 S5")


def test_inline_g1_power_is_allowed_only_after_laser_mode_and_remains_bounded() -> None:
    machine = MachineService(
        MachineSettings(backend="serial"),
        LaserSettings(power_max=1000),
    )
    valid = (
        "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S500\n"
        "G1 X20 Y20 F500 S450\nG1 X30 Y20 F500 S550\nM5\n"
    )
    assert machine.validate_program(valid)[-1] == "M5"

    with pytest.raises(SafetyError, match="after M3/M4"):
        machine.validate_program(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nG1 X20 Y20 F500 S5\nM5\n"
        )
    with pytest.raises(SafetyError, match="bounded inline S on G1"):
        machine.validate_program(
            "G21\nG90\nM5\nG0 X10 Y10 F1000 S5\nM5\n"
        )
    with pytest.raises(SafetyError, match="between 0 and 1000"):
        machine.validate_program(
            "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S500\n"
            "G1 X20 Y20 F500 S1001\nM5\n"
        )


def test_program_requires_laser_off_xy_position_before_enable() -> None:
    machine = MachineService(
        MachineSettings(backend="serial"),
        LaserSettings(),
        hardware_enabled=False,
    )

    with pytest.raises(SafetyError, match="absolute XY move.*before laser enable"):
        machine.validate_program("G21\nG90\nM5\nM4 S5\nG0 X10 Y10 F1000\nM5\n")


def test_program_validates_controller_and_physical_spot_bounds() -> None:
    machine = MachineService(
        MachineSettings(backend="serial"),
        LaserSettings(spot_offset_x_mm=5.0),
        hardware_enabled=False,
    )

    with pytest.raises(SafetyError, match=r"physical laser spot X221\.000"):
        machine.validate_program("G21\nG90\nM5\nG0 X216 Y10 F1000\nM5\n")


def test_support_bound_preflight_uses_explicit_polygon_only_when_requested() -> None:
    polygon = (
        (18.218005, 29.679375),
        (228.217364, 30.198421),
        (227.698319, 240.197779),
        (17.698960, 239.678734),
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            work_area=WorkArea(10.0, 210.0, 10.0, 210.0),
        ),
        LaserSettings(
            boundary_margin_mm=5.0,
            guarded_output_polygon_mm=polygon,
        ),
        hardware_enabled=False,
    )
    program = "G21\nG90\nM5\nG0 X220 Y40 F1000\nM5\n"

    with pytest.raises(SafetyError, match="guarded output authority"):
        machine.preflight_program(program)

    preflight = machine.preflight_program(
        program,
        guarded_output_polygon_mm=polygon,
    )

    assert preflight.guarded_output_polygon_mm == polygon


def test_support_bound_preflight_rejects_controller_and_spot_polygon_escape() -> None:
    polygon = ((0.0, 0.0), (210.0, 0.0), (210.0, 210.0), (0.0, 210.0))
    machine = MachineService(
        MachineSettings(
            backend="serial",
            work_area=WorkArea(0.0, 200.0, 0.0, 200.0),
        ),
        LaserSettings(
            boundary_margin_mm=0.0,
            spot_offset_x_mm=5.0,
            guarded_output_polygon_mm=polygon,
        ),
        hardware_enabled=False,
    )

    with pytest.raises(SafetyError, match=r"G-code point X211\.000"):
        machine.preflight_program(
            "G21\nG90\nM5\nG0 X211 Y100 F1000\nM5\n",
            guarded_output_polygon_mm=polygon,
        )
    with pytest.raises(SafetyError, match=r"physical laser spot X213\.000"):
        machine.preflight_program(
            "G21\nG90\nM5\nG0 X208 Y100 F1000\nM5\n",
            guarded_output_polygon_mm=polygon,
        )


def test_support_bound_validated_program_rejects_polygon_change_before_start() -> None:
    polygon = ((0.0, 0.0), (210.0, 0.0), (210.0, 210.0), (0.0, 210.0))
    laser = LaserSettings(guarded_output_polygon_mm=polygon)
    machine = MachineService(
        MachineSettings(backend="serial"),
        laser,
        hardware_enabled=False,
    )
    preflight = machine.preflight_program(
        "G21\nG90\nM5\nG0 X100 Y100 F1000\nM5\n",
        guarded_output_polygon_mm=polygon,
    )

    laser.guarded_output_polygon_mm = (
        (0.0, 0.0),
        (211.0, 0.0),
        (211.0, 211.0),
        (0.0, 211.0),
    )

    with pytest.raises(SafetyError, match="configured authority"):
        machine.start_validated_program(preflight)


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
        MachineSettings(backend="serial"),
        LaserSettings(),
        hardware_enabled=False,
    )

    with pytest.raises(SafetyError, match=message):
        machine.validate_program(f"G21\nG90\nM5\n{line}\nM5\n")


def test_program_rejects_oversized_executable_line_before_streaming() -> None:
    machine = MachineService(
        MachineSettings(backend="serial"),
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
    settings = MachineSettings(backend="serial")
    setattr(settings, field, value)
    machine = MachineService(settings, LaserSettings(), hardware_enabled=False)

    with pytest.raises(SafetyError, match=message):
        machine.preflight_program(
            "G21\nG90\nM5\nG0 X10 Y10 F999999999\nM5\n"
        )


def test_validated_program_rejects_feed_ceiling_mutation_before_start() -> None:
    settings = MachineSettings(backend="serial")
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

    def unexpected_transport(
        _backend: str,
        _port: str,
        _baudrate: int,
    ) -> SimulatedTransport:
        nonlocal calls
        calls += 1
        return SimulatedTransport()

    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
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
    settings = MachineSettings(backend="serial")
    machine = MachineService(settings, LaserSettings(), hardware_enabled=False)
    program = machine.preflight_program(
        "G21\nG90\nM5\nG1 X10 Y10 F500\nM5\n"
    )
    machine.connect()
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
        MachineSettings(backend="serial"),
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
        MachineSettings(backend="serial"),
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
        MachineSettings(backend="serial"),
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
    settings = MachineSettings(backend="serial", grbl_step_idle_delay_ms=255)
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
        MachineSettings(backend="serial"),
        LaserSettings(),
        hardware_enabled=False,
    )
    transport = RecordingTransport()
    transport.open()
    machine._transport = transport
    machine._connected = True
    machine._coordinate_reference_ready = True
    machine._coordinate_state_reference = {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }
    machine._jog_position_mm = (0.0, 0.0)
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
        MachineSettings(backend="serial"),
        LaserSettings(),
        hardware_enabled=False,
    )
    transport = RecordingTransport()
    transport.open()
    machine._transport = transport
    machine._connected = True
    machine._coordinate_reference_ready = True
    machine._coordinate_state_reference = {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }
    machine._jog_position_mm = (0.0, 0.0)
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
        MachineSettings(backend="serial"),
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
        MachineSettings(backend="serial"),
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        MachineSettings(backend="serial"),
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        MachineSettings(backend="serial"),
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
        assert release_preflight.wait(timeout=5.0)
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
    worker.join(timeout=3.0)

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
        MachineSettings(backend="serial"),
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
        MachineSettings(backend="serial"),
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
        MachineSettings(backend="serial"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()

    machine.request_stop()

    assert machine.status()["controller_reconnect_required"]
    with pytest.raises(MachineError, match="disconnect and reconnect"):
        machine.prepare_photo_position()
    machine.disconnect()


def test_explicit_replacement_uses_post_disconnect_generation_and_stays_untrusted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(MachineSettings(backend="serial"), LaserSettings(), hardware_enabled=False)
    machine.connect()
    machine.request_stop()
    requested_generation = machine.operation_generation()
    connect_generations: list[int] = []
    original_connect = machine.connect

    def record_connect(*args: object, **kwargs: object) -> dict[str, object]:
        connect_generations.append(machine._operation_stop_epoch())
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(machine, "connect", record_connect)
    with machine.operation_scope(requested_generation):
        result = machine.replace_connection()

    assert connect_generations == [requested_generation + 1]
    assert result["connected"] is True
    assert result["controller_reconnect_required"] is False
    assert result["coordinate_reference_ready"] is False
    assert result["jog_ready"] is False
    assert result["armed"] is False
    assert result["job"]["running"] is False
    machine.disconnect()


def test_stop_during_replacement_connect_still_cancels(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(MachineSettings(backend="serial"), LaserSettings(), hardware_enabled=False)
    machine.connect()
    machine.request_stop()
    requested_generation = machine.operation_generation()
    original_connect = machine.connect

    def stop_then_connect(*args: object, **kwargs: object) -> dict[str, object]:
        machine.request_stop()
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(machine, "connect", stop_then_connect)
    with machine.operation_scope(requested_generation):
        with pytest.raises(MachineError, match="cancelled by software STOP"):
            machine.replace_connection()

    assert machine.status()["connected"] is False
    assert machine.status()["coordinate_reference_ready"] is False
    assert machine.status()["jog_ready"] is False


def test_stop_racing_between_disconnect_and_connect_is_not_absorbed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(MachineSettings(backend="serial"), LaserSettings(), hardware_enabled=False)
    machine.connect()
    machine.request_stop()
    requested_generation = machine.operation_generation()
    original_disconnect = machine.disconnect
    connect_called = False

    def disconnect_then_stop() -> None:
        original_disconnect()
        machine.request_stop()

    def unexpected_connect(*args: object, **kwargs: object) -> dict[str, object]:
        nonlocal connect_called
        connect_called = True
        return {}

    monkeypatch.setattr(machine, "disconnect", disconnect_then_stop)
    monkeypatch.setattr(machine, "connect", unexpected_connect)
    with machine.operation_scope(requested_generation):
        with pytest.raises(MachineError, match="cancelled by software STOP"):
            machine.replace_connection()

    assert not connect_called
    assert machine.status()["connected"] is False


def test_request_time_generation_cancels_composite_job_after_stop() -> None:
    machine = MachineService(
        MachineSettings(backend="serial"),
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
        MachineSettings(backend="serial"),
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
        MachineSettings(backend="serial"),
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
        MachineSettings(backend="serial"),
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
        MachineSettings(backend="serial"),
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


def test_job_runner_thread_start_failure_is_terminal_and_attempts_m5(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl"),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    transport = machine._transport
    assert transport is not None
    writes: list[str] = []
    original_write_line = transport.write_line

    def record_write(line: str) -> None:
        writes.append(line)
        original_write_line(line)

    monkeypatch.setattr(transport, "write_line", record_write)
    program = machine.preflight_program(
        "G21\nG90\nM5\nG0 X10 Y10 F500\nM4 S100\nG1 X20 Y20 F500\nM5\n"
    )
    machine.arm_program(machine.ARM_PHRASE, program)

    def fail_to_start(_thread: threading.Thread) -> None:
        raise RuntimeError("thread unavailable")

    monkeypatch.setattr(threading.Thread, "start", fail_to_start)
    with pytest.raises(RuntimeError, match="thread unavailable"):
        machine.start_validated_program(program, "thread-failure.gcode")

    status = machine.status()["job"]
    assert status["running"] is False
    assert status["phase"] == "failed"
    assert status["error"] == "Job runner could not start: thread unavailable"
    assert writes[-1] == "M5"
    assert machine.status()["controller_reconnect_required"] is True
    with pytest.raises(MachineError, match="Controller command state is untrusted"):
        machine.send_command("$I")
    assert machine.status()["last_successful_job"] is None
    machine.disconnect()


def test_start_preflighted_program_preserves_local_home_arm_start_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl"),
        LaserSettings(),
        hardware_enabled=True,
    )
    program = machine.preflight_program(
        "G21\nG90\nM5\nG0 X10 Y10 F500\nM4 S100\nG1 X20 Y20 F500\nM5\n"
    )
    calls: list[object] = []
    monkeypatch.setattr(
        machine,
        "prepare_job_start",
        lambda: calls.append("home"),
    )
    monkeypatch.setattr(
        machine,
        "arm_program",
        lambda phrase, exact: calls.append(("arm", phrase, exact.digest)),
    )
    monkeypatch.setattr(
        machine,
        "start_validated_program",
        lambda exact, name: calls.append(("start", name, exact.digest)) or {"running": True},
    )

    result = machine.start_preflighted_program(
        program,
        "ordered.gcode",
        authorization_phrase=machine.ARM_PHRASE,
    )

    assert result == {"running": True}
    assert calls == [
        "home",
        ("arm", machine.ARM_PHRASE, program.digest),
        ("start", "ordered.gcode", program.digest),
    ]


def test_preflight_is_invalidated_when_safety_profile_changes() -> None:
    machine = MachineService(
        MachineSettings(backend="serial"),
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
    machine = MachineService(MachineSettings(backend="serial"), LaserSettings(), hardware_enabled=False)
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
        "_execute_grbl_homing_locked",
        lambda **_kwargs: commands.append("$H") or ["ok"],
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
    settings = MachineSettings(backend="serial", photo_x=110, photo_y=105, home_before_photo=True)
    machine = MachineService(settings, LaserSettings(), hardware_enabled=False)
    machine.connect()
    try:
        result = machine.prepare_photo_position()
        assert result["position"] == {"x": 110.0, "y": 105.0, "z": None}
        assert result["homed"] is True
        assert result["parked"] is True
        assert result["home_position_snapshot"] is None
        assert machine._transport is not None
        assert machine._transport.x == pytest.approx(110)
        assert machine._transport.y == pytest.approx(105)
    finally:
        machine.disconnect()


def test_prepare_photo_position_can_capture_home_before_normal_simulated_park(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingPositionTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.raw_writes: list[bytes] = []
            self.sampled_positions: list[tuple[float, float]] = []

        def write_line(self, line: str) -> None:
            self.commands.append(line.strip().upper())
            super().write_line(line)

        def write_raw(self, data: bytes) -> None:
            self.raw_writes.append(data)
            if data == b"?":
                self.sampled_positions.append((self.x, self.y))
                self._queue.put(
                    f"<Idle|MPos:{self.x:.3f},{self.y:.3f},0.000|"
                    f"WPos:{self.x:.3f},{self.y:.3f},0.000|WCO:0.000,0.000,0.000>"
                )
                return
            super().write_raw(data)

    transport = RecordingPositionTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            photo_x=110.0,
            photo_y=105.0,
            home_before_photo=True,
        ),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    transport.x = 23.0
    transport.y = 47.0
    try:
        result = machine.prepare_photo_position(capture_home_position=True)

        home = result["home_position_snapshot"]
        assert result["homed"] is True
        assert result["parked"] is True
        assert home["available"] is True
        assert home["mpos_mm"][:2] == [0.0, 0.0]
        assert home["wpos_mm"][:2] == [0.0, 0.0]
        assert transport.sampled_positions == [(0.0, 0.0)]
        assert transport.raw_writes == [b"?"]
        assert result["position"] == {"x": 110.0, "y": 105.0, "z": None}
        assert (transport.x, transport.y) == pytest.approx((110.0, 105.0))
        assert "$H" in transport.commands
        assert not any(
            command.startswith(("M3", "M4")) for command in transport.commands
        )
        assert transport.laser_on is False
    finally:
        machine.disconnect()


def test_prepare_job_start_homes_without_parking_in_simulation(
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            photo_x=110,
            photo_y=105,
            home_before_photo=True,
        ),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    transport.commands.clear()
    try:
        result = machine.prepare_job_start()

        assert result["position"] is None
        assert result["homed"] is True
        assert result["parked"] is False
        assert transport.commands == ["M5", "$H", "$G", "$#", "G21", "G90"]
        assert machine.status()["coordinate_reference_ready"] is True
        assert machine.status()["jog_ready"] is False
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            allow_motion=True,
            photo_x=110.0,
            photo_y=105.0,
            max_travel_feed_mm_min=3000.0,
        ),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    transport.commands.clear()
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
        action_commands = [
            command for command in transport.commands if command not in {"$G", "$#"}
        ]
        assert action_commands == [
            "M5",
            "G21",
            "G90",
            "G1 X115.000 Y104.000 F700.000",
            "G4 P0.01",
            "M5",
            "G21",
            "G90",
            "G1 X114.900 Y104.200 F600.000",
            "G4 P0.01",
        ]
        assert not any(command.startswith(("M3", "M4")) for command in transport.commands)
        assert transport.laser_on is False
        assert machine.status()["jog_position_mm"] == {"x": 114.9, "y": 104.2}
    finally:
        machine.disconnect()


@pytest.mark.parametrize("feed", [20.0, 2000.0])
def test_jog_uses_requested_feed_on_feed_controlled_motion(
    monkeypatch: pytest.MonkeyPatch,
    feed: float,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
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
        transport.commands.clear()

        result = machine.jog(5.0, -2.0, feed)

        action_commands = [
            command for command in transport.commands if command not in {"$G", "$#"}
        ]
        assert action_commands[:4] == [
            "M5",
            "G21",
            "G90",
            f"G1 X115.000 Y103.000 F{feed:.3f}",
        ]
        assert not any(command.startswith("G0 ") for command in transport.commands)
        assert not any(command.startswith(("M3", "M4")) for command in transport.commands)
        assert transport.laser_on is False
        assert result["position"] == {"x": 115.0, "y": 103.0}
        assert result["feed_mm_min"] == feed
        assert machine.status()["jog_position_mm"] == {"x": 115.0, "y": 103.0}
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
            "G1 X111.000 Y105.000 F500.000",
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
            backend="serial",
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
            backend="serial",
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
            if command == "G1 X111.000 Y105.000 F500.000":
                self.jog_written.set()
                return
            super().write_line(line)

    transport = BlockedJogTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
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
    action_commands = [
        command for command in transport.commands if command not in {"$G", "$#"}
    ]
    assert action_commands == [
        "M5",
        "G21",
        "G90",
        "G1 X111.000 Y105.000 F500.000",
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
        backend="serial",
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
        backend="serial",
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
        if command == "$G":
            return ["[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]", "ok"]
        if command == "$#":
            return [
                "[G54:0.000,0.000,0.000]",
                "[G92:0.000,0.000,0.000]",
                "ok",
            ]
        return ["ok"]

    monkeypatch.setattr(machine, "send_command", record_command)
    monkeypatch.setattr(
        machine,
        "_execute_grbl_homing_locked",
        lambda *, timeout, expected_stop_epoch: recorded.append(
            ("$H", timeout, True)
        )
        or ["ok"],
    )
    try:
        machine.prepare_photo_position()
    finally:
        machine.disconnect()

    actions = [item for item in recorded if item[0] not in {"$G", "$#"}]
    assert actions[0] == ("M5", 6.0, True)
    assert actions[1] == ("$H", 120.0, True)
    assert actions[2] == ("G21", 6.0, True)
    assert actions[3] == ("G90", 6.0, True)
    assert actions[4][1:] == (6.0, True)
    assert actions[5] == ("G4 P0.01", 120.0, True)
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        assert transport.commands[:3] == ["$$", "M5", "$1=250"]
        assert "$MD" not in transport.commands
        assert "$SLP" not in transport.commands
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl"),
        LaserSettings(),
        hardware_enabled=True,
    )

    try:
        machine.connect()
        assert transport.commands[:2] == ["$$", "M5"]
        assert "$1=250" not in transport.commands
        assert "$MD" not in transport.commands
        assert "$SLP" not in transport.commands
        assert not machine.status()["coordinate_reference_ready"]
    finally:
        machine.disconnect()


def test_serial_connect_recovers_exact_grbl_alarm_lock_but_still_requires_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AlarmLockedTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.m5_count = 0

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "M5":
                self.m5_count += 1
                if self.m5_count == 1:
                    self._queue.put("error:9")
                    return
            if command == "$X":
                self._queue.put("ok")
                return
            super().write_line(line)

    transport = AlarmLockedTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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

    status = machine.connect()

    assert transport.commands[:4] == ["$$", "M5", "$X", "M5"]
    assert not any(
        command == "$H" or command.startswith(("G0 ", "G1 ", "G28"))
        for command in transport.commands
    )
    assert status["connected"] is True
    assert status["coordinate_reference_ready"] is False
    assert status["jog_position_mm"] is None
    assert status["jog_ready"] is False
    with pytest.raises(SafetyError, match="Home / park"):
        machine.jog(1.0, 0.0, 100.0)
    with pytest.raises(SafetyError, match="Home / park"):
        machine.arm(machine.ARM_PHRASE)
    motion = machine.preflight_program("G21\nG90\nM5\nG1 X1 Y1 F100\nM5\n")
    with pytest.raises(SafetyError, match="Home / park"):
        machine.start_validated_program(motion, "home-required.gcode")
    machine.disconnect()


@pytest.mark.parametrize(
    ("first_m5", "unlock", "second_m5", "home_before_photo", "message"),
    [
        ("error:9", "error:2", "ok", True, r"\$X.*error:2"),
        ("error:9", "ok", "error:8", True, "M5.*error:8"),
        ("error:8", "ok", "ok", True, "M5.*error:8"),
        ("error:90", "ok", "ok", True, "M5.*error:90"),
        ("ALARM:1", "ok", "ok", True, "ALARM:1"),
        ("error:9", "ok", "ok", False, "M5.*error:9"),
    ],
)
def test_serial_connect_alarm_unlock_rejects_every_non_exact_or_failed_exchange(
    monkeypatch: pytest.MonkeyPatch,
    first_m5: str,
    unlock: str,
    second_m5: str,
    home_before_photo: bool,
    message: str,
) -> None:
    class RejectedNormalizationTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.m5_count = 0

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "M5":
                self.m5_count += 1
                self._queue.put(first_m5 if self.m5_count == 1 else second_m5)
                return
            if command == "$X":
                self._queue.put(unlock)
                return
            super().write_line(line)

    transport = RejectedNormalizationTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            home_before_photo=home_before_photo,
            controller_startup_delay=0.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )

    with pytest.raises(MachineError, match=message):
        machine.connect()

    assert not machine.status()["connected"]
    assert not machine.status()["coordinate_reference_ready"]
    assert machine.status()["jog_position_mm"] is None
    assert "$H" not in transport.commands
    assert not any(command.startswith(("G0 ", "G1 ", "G28")) for command in transport.commands)
    if first_m5.lower() == "error:9" and home_before_photo:
        assert transport.commands[:3] == ["$$", "M5", "$X"]
    else:
        assert "$X" not in transport.commands


@pytest.mark.parametrize("failure", ["timeout", "disconnect"])
def test_serial_connect_alarm_unlock_rejects_ambiguous_first_m5_exchange(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    class AmbiguousNormalizationTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.commands: list[str] = []
            self.fail_read = False

        def write_line(self, line: str) -> None:
            command = line.strip().upper()
            self.commands.append(command)
            if command == "M5" and self.commands.count("M5") == 1:
                self.fail_read = failure == "disconnect"
                return
            super().write_line(line)

        def read_line(self, timeout: float = 1.0) -> str | None:
            if self.fail_read:
                self.fail_read = False
                raise OSError("controller disconnected")
            return super().read_line(timeout)

    transport = AmbiguousNormalizationTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
    )
    monkeypatch.setattr(
        "laser_aligner.machine.service._PHOTO_COMMAND_ACK_TIMEOUT_SECONDS",
        0.1,
    )
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            home_before_photo=True,
            controller_startup_delay=0.0,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )

    with pytest.raises(MachineError, match="M5"):
        machine.connect()

    assert not machine.status()["connected"]
    assert not machine.status()["coordinate_reference_ready"]
    assert machine.status()["jog_position_mm"] is None
    assert "$X" not in transport.commands
    assert "$H" not in transport.commands


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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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

    assert transport.commands[:3] == ["$$", "M5", "$1=250"]
    assert "$MD" not in transport.commands
    assert "$SLP" not in transport.commands
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
    assert transport.commands[:2] == ["$$", "M5"]
    assert "$MD" not in transport.commands
    assert "$SLP" not in transport.commands
    machine.disconnect()


def test_arm_waits_for_an_inflight_jog_command() -> None:
    machine = MachineService(
        MachineSettings(backend="serial", allow_motion=True),
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
        expected = f"G1 X{target_x:.3f} Y{target_y:.3f}"
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
        MachineSettings(backend="serial", allow_motion=True),
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
            rejection = _ControllerCommandRejected("error:9")
            raise MachineError("Command 'M5' failed: error:9") from rejection
        return ["ok"]

    monkeypatch.setattr(machine, "send_command", record_command)
    monkeypatch.setattr(
        machine,
        "_execute_grbl_homing_locked",
        lambda **_kwargs: commands.append("$H") or ["ok"],
    )
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
        MachineSettings(backend="serial"),
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: SimulatedTransport(),
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        MachineSettings(backend="serial"),
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: SimulatedTransport(),
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


class PostJobHomingTransport(SimulatedTransport):
    def __init__(
        self,
        *,
        homing_ack: str | None = None,
        statuses: list[str] | None = None,
        hold_statuses: bool = False,
    ) -> None:
        super().__init__()
        self.homing_ack = homing_ack
        self.statuses = list(statuses or [])
        self.commands: list[str] = []
        self.raw_writes: list[bytes] = []
        self.homing_started = threading.Event()
        self.status_queried = threading.Event()
        self.allow_statuses = threading.Event()
        if not hold_statuses:
            self.allow_statuses.set()

    def write_line(self, line: str) -> None:
        command = line.strip().upper()
        self.commands.append(command)
        if command == "$H":
            self.x = 0.0
            self.y = 0.0
            self.laser_on = False
            self.power = 0.0
            self.homing_started.set()
            if self.homing_ack is not None:
                self._queue.put(self.homing_ack)
            return
        super().write_line(line)

    def write_raw(self, data: bytes) -> None:
        self.raw_writes.append(data)
        if b"?" in data and self.homing_started.is_set():
            self.status_queried.set()
            if self.allow_statuses.is_set() and self.statuses:
                self._queue.put(self.statuses.pop(0))
            return
        super().write_raw(data)


def _powered_completion_machine(transport: SimulatedTransport) -> MachineService:
    transport.open()
    transport.drain()
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            read_timeout=0.01,
            allow_motion=True,
            home_before_photo=True,
            home_and_release_after_powered_job=True,
            photo_x=15,
            photo_y=195,
        ),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine._transport = transport
    machine._connected = True
    machine._protocol = "grbl"
    machine._coordinate_reference_ready = True
    machine._coordinate_state_reference = {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }
    machine._verify_grbl_coordinate_state = lambda: None  # type: ignore[method-assign]
    return machine


def _start_powered_completion_job(machine: MachineService) -> str:
    program = machine.preflight_program(
        "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S5\nG1 X20 Y20 F500\nM5\n"
    )
    machine.arm_program(machine.ARM_PHRASE, program)
    machine.start_validated_program(program, "powered-completion.gcode")
    return program.digest


def test_powered_post_job_grbl_homing_accepts_normal_ok() -> None:
    transport = PostJobHomingTransport(homing_ack="ok")
    machine = _powered_completion_machine(transport)
    try:
        digest = _start_powered_completion_job(machine)
        wait_for_job(machine)

        status = machine.status()
        assert status["job"]["phase"] == "complete"
        assert status["job"]["error"] is None
        assert status["job"]["finished_at"] is not None
        assert status["last_successful_job"]["program_digest"] == digest
        assert transport.commands.index("$H") < transport.commands.index(
            "G0 X15.000 Y195.000 F3000.000"
        )
        assert transport.commands.index("G0 X15.000 Y195.000 F3000.000") < transport.commands.index(
            "$MD"
        )
    finally:
        machine.disconnect()


@pytest.mark.parametrize("active_state", ["Run", "Home", "Homing"])
def test_powered_post_job_grbl_homing_accepts_active_then_idle_without_ok(
    monkeypatch: pytest.MonkeyPatch,
    active_state: str,
) -> None:
    transport = PostJobHomingTransport(
        statuses=[
            f"<{active_state}|MPos:1,1,0|FS:0,0>",
            "<Idle|MPos:0,0,0|FS:0,0>",
        ],
        hold_statuses=True,
    )
    machine = _powered_completion_machine(transport)
    monkeypatch.setattr(
        "laser_aligner.machine.service._GRBL_HOMING_STATUS_QUERY_INTERVAL_SECONDS",
        0.0,
    )
    try:
        digest = _start_powered_completion_job(machine)
        assert transport.homing_started.wait(timeout=1.0)
        assert transport.status_queried.wait(timeout=1.0)

        assert machine.status()["job"]["running"] is True
        assert machine.status()["job"]["phase"] == "homing"
        assert "G0 X15.000 Y195.000 F3000.000" not in transport.commands
        assert "$MD" not in transport.commands

        transport.allow_statuses.set()
        wait_for_job(machine)

        status = machine.status()
        assert status["job"]["running"] is False
        assert status["job"]["phase"] == "complete"
        assert status["job"]["error"] is None
        assert status["last_successful_job"]["program_digest"] == digest
        assert transport.x == pytest.approx(15)
        assert transport.y == pytest.approx(195)
        assert transport.commands.index("$H") < transport.commands.index(
            "G0 X15.000 Y195.000 F3000.000"
        )
        assert transport.commands.index("G4 P0.01", transport.commands.index("$H")) < (
            transport.commands.index("$MD")
        )
    finally:
        machine.disconnect()


@pytest.mark.parametrize(
    "statuses",
    [
        ["<Idle|MPos:0,0,0>"] * 3,
        ["<Run|MPos:1,1,0", "<Idle|MPos:0,0,0>"],
        [],
    ],
    ids=["idle-only", "malformed-active", "timeout"],
)
def test_powered_post_job_grbl_homing_ambiguous_or_timed_out_evidence_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    statuses: list[str],
) -> None:
    transport = PostJobHomingTransport(statuses=statuses)
    machine = _powered_completion_machine(transport)
    monkeypatch.setattr("laser_aligner.machine.service._GRBL_HOMING_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(
        "laser_aligner.machine.service._GRBL_HOMING_STATUS_QUERY_INTERVAL_SECONDS",
        0.0,
    )
    try:
        _start_powered_completion_job(machine)
        wait_for_job(machine)

        status = machine.status()
        assert status["job"]["running"] is False
        assert status["job"]["phase"] == "failed"
        assert "active-to-idle" in status["job"]["error"]
        assert status["last_successful_job"] is None
        assert status["controller_reconnect_required"] is True
        assert "G0 X15.000 Y195.000 F3000.000" not in transport.commands
    finally:
        machine.disconnect()


@pytest.mark.parametrize(
    "response",
    ["error:9", "ALARM:1", "<Alarm|MPos:0,0,0>"],
    ids=["error", "alarm", "alarm-status"],
)
def test_powered_post_job_grbl_homing_rejection_never_parks_or_publishes_success(
    monkeypatch: pytest.MonkeyPatch,
    response: str,
) -> None:
    transport = PostJobHomingTransport(statuses=[response])
    machine = _powered_completion_machine(transport)
    monkeypatch.setattr(
        "laser_aligner.machine.service._GRBL_HOMING_STATUS_QUERY_INTERVAL_SECONDS",
        0.0,
    )
    try:
        _start_powered_completion_job(machine)
        wait_for_job(machine)

        status = machine.status()
        assert status["job"]["phase"] == "failed"
        assert response.lower().split("|", 1)[0].lstrip("<") in status["job"]["error"].lower()
        assert status["last_successful_job"] is None
        assert "G0 X15.000 Y195.000 F3000.000" not in transport.commands
    finally:
        machine.disconnect()


def test_powered_post_job_grbl_homing_disconnect_fails_closed() -> None:
    class DisconnectingPostJobTransport(PostJobHomingTransport):
        def read_line(self, timeout: float = 1.0) -> str | None:
            if self.homing_started.is_set():
                raise MachineError("E3 bridge connection closed")
            return super().read_line(timeout)

    transport = DisconnectingPostJobTransport()
    machine = _powered_completion_machine(transport)
    try:
        _start_powered_completion_job(machine)
        wait_for_job(machine)

        status = machine.status()
        assert status["job"]["phase"] == "failed"
        assert "connection closed" in status["job"]["error"]
        assert status["last_successful_job"] is None
        assert status["controller_reconnect_required"] is True
        assert "G0 X15.000 Y195.000 F3000.000" not in transport.commands
    finally:
        machine.disconnect()


def test_stop_during_powered_post_job_missing_ack_homing_cancels_without_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = PostJobHomingTransport(hold_statuses=True)
    machine = _powered_completion_machine(transport)
    monkeypatch.setattr(
        "laser_aligner.machine.service._GRBL_HOMING_STATUS_QUERY_INTERVAL_SECONDS",
        0.0,
    )
    try:
        _start_powered_completion_job(machine)
        assert transport.homing_started.wait(timeout=1.0)
        assert transport.status_queried.wait(timeout=1.0)

        with pytest.raises(MachineError, match="while a job is running"):
            machine.prepare_photo_position()

        machine.stop_job()
        wait_for_job(machine)

        status = machine.status()
        assert status["job"]["running"] is False
        assert status["job"]["phase"] == "failed"
        assert status["job"]["error"] == "Job stopped"
        assert status["last_successful_job"] is None
        assert "G0 X15.000 Y195.000 F3000.000" not in transport.commands
    finally:
        machine.disconnect()


class CompletionPhaseGateTransport(SimulatedTransport):
    def __init__(self) -> None:
        super().__init__()
        self.commands: list[str] = []
        self.enabled = False
        self.barrier_count = 0
        self.entered = {
            phase: threading.Event()
            for phase in ("draining", "homing", "parking", "releasing")
        }

    def write_line(self, line: str) -> None:
        command = line.strip().upper()
        self.commands.append(command)
        phase: str | None = None
        if self.enabled and command == "G4 P0.01":
            self.barrier_count += 1
            phase = "draining" if self.barrier_count == 1 else "parking"
        elif self.enabled and command == "$H":
            self.x = 0.0
            self.y = 0.0
            phase = "homing"
        elif self.enabled and command == "$MD":
            phase = "releasing"
        if phase is not None:
            self.entered[phase].set()
            return
        super().write_line(line)

    def release(self, phase: str) -> None:
        assert self.entered[phase].is_set()
        self._queue.put("ok")


def test_powered_job_owns_machine_through_drain_home_park_and_release() -> None:
    transport = CompletionPhaseGateTransport()
    machine = _powered_completion_machine(transport)
    transport.enabled = True
    try:
        digest = _start_powered_completion_job(machine)
        for phase in ("draining", "homing", "parking", "releasing"):
            assert transport.entered[phase].wait(timeout=1.0)
            status = machine.status()
            assert status["job"]["running"] is True
            assert status["job"]["phase"] == phase
            assert status["job"]["finished_at"] is None
            assert status["last_successful_job"] is None
            if phase == "parking":
                assert "$MD" not in transport.commands
            transport.release(phase)

        wait_for_job(machine)
        status = machine.status()
        assert status["job"]["running"] is False
        assert status["job"]["phase"] == "complete"
        assert status["job"]["finished_at"] is not None
        assert status["last_successful_job"]["program_digest"] == digest
        park_index = transport.commands.index("G0 X15.000 Y195.000 F3000.000")
        park_barrier_index = transport.commands.index("G4 P0.01", park_index)
        assert park_index < park_barrier_index < transport.commands.index("$MD")
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(backend="serial"),
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        assert transport.commands[:2] == ["$$", "M5"]
        assert "$SLP" not in transport.commands
        assert transport.raw_writes == []
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: SimulatedTransport(),
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(backend="serial"),
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
            self.delay_first_m5 = False
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
        "laser_aligner.machine.service.create_machine_transport",
        lambda _backend, _port, _baudrate: transport,
    )
    machine = MachineService(
        MachineSettings(backend="serial"),
        LaserSettings(),
        hardware_enabled=False,
    )
    machine.connect()
    transport.delay_first_m5 = True

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
    machine = MachineService(MachineSettings(backend="serial"), LaserSettings(), hardware_enabled=False)
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


class HomingStatusTransport(SimulatedTransport):
    def __init__(self, statuses: list[str]) -> None:
        super().__init__()
        self.statuses = list(statuses)
        self.commands: list[str] = []

    def write_line(self, line: str) -> None:
        command = line.strip().upper()
        self.commands.append(command)
        if command == "$H":
            return
        super().write_line(line)

    def write_raw(self, data: bytes) -> None:
        if b"?" in data and self.statuses:
            self._queue.put(self.statuses.pop(0))
            return
        super().write_raw(data)


def _connected_homing_machine(transport: SimulatedTransport) -> MachineService:
    transport.open()
    transport.drain()
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
    machine._transport = transport
    machine._connected = True
    machine._protocol = "grbl"
    return machine


def test_grbl_homing_without_ok_requires_active_then_idle_before_park(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = HomingStatusTransport(
        ["<Home|MPos:1,1,0|FS:0,0>", "<Idle|MPos:0,0,0|FS:0,0>"]
    )
    machine = _connected_homing_machine(transport)
    monkeypatch.setattr(
        machine,
        "_read_grbl_coordinate_state",
        lambda: {
            "active_workspace": "G54",
            "active_offset_mm": [0.0, 0.0, 0.0],
            "g92_offset_mm": [0.0, 0.0, 0.0],
        },
    )
    monkeypatch.setattr(machine, "_wait_for_motion_complete", lambda **_kwargs: ["ok"])
    monkeypatch.setattr(
        "laser_aligner.machine.service._GRBL_HOMING_STATUS_QUERY_INTERVAL_SECONDS",
        0.0,
    )

    result = machine.prepare_photo_position()

    assert result["position"] == {"x": 110.0, "y": 105.0, "z": None}
    assert transport.commands[:4] == ["M5", "$H", "G21", "G90"]
    assert transport.commands[4].startswith("G0 X110.000 Y105.000")
    assert not any(command.startswith(("M3", "M4")) for command in transport.commands)
    assert machine.status()["coordinate_reference_ready"]
    assert machine._jog_position_mm == (110.0, 105.0)


@pytest.mark.parametrize(
    ("statuses", "message"),
    [
        (["error:9"], "error:9"),
        (["<Home|MPos:1,1,0>", "<Alarm|MPos:0,0,0>"], "Alarm"),
    ],
)
def test_grbl_homing_rejection_or_alarm_never_parks(
    monkeypatch: pytest.MonkeyPatch,
    statuses: list[str],
    message: str,
) -> None:
    transport = HomingStatusTransport(statuses)
    machine = _connected_homing_machine(transport)
    monkeypatch.setattr(
        "laser_aligner.machine.service._GRBL_HOMING_STATUS_QUERY_INTERVAL_SECONDS",
        0.0,
    )

    with pytest.raises(MachineError, match=message):
        machine.prepare_photo_position()

    assert not any(command.startswith("G0 ") for command in transport.commands)
    assert not machine.status()["coordinate_reference_ready"]


def test_grbl_homing_immediate_idle_is_ambiguous_and_requires_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = HomingStatusTransport(["<Idle|MPos:0,0,0>"] * 10)
    machine = _connected_homing_machine(transport)
    monkeypatch.setattr("laser_aligner.machine.service._GRBL_HOMING_TIMEOUT_SECONDS", 0.03)
    monkeypatch.setattr(
        "laser_aligner.machine.service._GRBL_HOMING_STATUS_QUERY_INTERVAL_SECONDS",
        0.0,
    )

    with pytest.raises(MachineError, match="active-to-idle"):
        machine.prepare_photo_position()

    assert not any(command.startswith("G0 ") for command in transport.commands)
    assert machine.status()["controller_reconnect_required"]
    assert not machine.status()["coordinate_reference_ready"]


def test_grbl_homing_disconnect_never_parks_and_requires_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class DisconnectingHomingTransport(HomingStatusTransport):
        def read_line(self, timeout: float = 1.0) -> str | None:
            if self.commands and self.commands[-1] == "$H":
                raise MachineError("E3 bridge connection closed")
            return super().read_line(timeout)

    transport = DisconnectingHomingTransport([])
    machine = _connected_homing_machine(transport)

    with pytest.raises(MachineError, match="connection closed"):
        machine.prepare_photo_position()

    assert not any(command.startswith("G0 ") for command in transport.commands)
    assert machine.status()["controller_reconnect_required"]
    assert not machine.status()["coordinate_reference_ready"]


def test_initial_transient_connect_failure_retries_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = SimulatedTransport()
    second = SimulatedTransport()
    close_calls = 0

    def fail_open() -> None:
        raise TransientConnectionError("temporary network failure")

    def record_close() -> None:
        nonlocal close_calls
        close_calls += 1

    first.open = fail_open  # type: ignore[method-assign]
    first.close = record_close  # type: ignore[method-assign]
    transports = iter([first, second])
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda *_args: next(transports),
    )
    monkeypatch.setattr(
        "laser_aligner.machine.service._INITIAL_CONNECT_RETRY_DELAY_SECONDS", 0.0
    )
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", controller_startup_delay=0.0),
        LaserSettings(),
        hardware_enabled=True,
    )

    machine.connect()

    assert close_calls >= 1
    assert machine.connected


def test_initial_connection_to_disconnected_controller_reports_real_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    class FailingTransport(SimulatedTransport):
        def open(self) -> None:
            nonlocal attempts
            attempts += 1
            raise TransientConnectionError("temporary network failure")

    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda *_args: FailingTransport(),
    )
    monkeypatch.setattr(
        "laser_aligner.machine.service._INITIAL_CONNECT_RETRY_DELAY_SECONDS", 0.0
    )
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", controller_startup_delay=0.0),
        LaserSettings(),
        hardware_enabled=True,
    )
    with pytest.raises(
        TransientConnectionError,
        match="temporary network failure",
    ):
        machine.connect()
    assert attempts == 2

    attempts = 0
    with pytest.raises(MachineError, match="Protocol"):
        machine.connect(protocol="invalid")
    assert attempts == 0


def test_initial_bridge_authentication_failure_is_not_retried(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0

    class AuthenticationFailureTransport(SimulatedTransport):
        def open(self) -> None:
            nonlocal attempts
            attempts += 1
            raise MachineError("E3 bridge rejected the connection: authentication_failed")

    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda *_args: AuthenticationFailureTransport(),
    )
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", controller_startup_delay=0.0),
        LaserSettings(),
        hardware_enabled=True,
    )

    with pytest.raises(MachineError, match="authentication_failed"):
        machine.connect()

    assert attempts == 1


def test_later_manual_reconnect_does_not_receive_initial_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    successful = SimulatedTransport()
    failing = SimulatedTransport()
    failure_attempts = 0

    def fail_open() -> None:
        nonlocal failure_attempts
        failure_attempts += 1
        raise TransientConnectionError("later connection lost")

    failing.open = fail_open  # type: ignore[method-assign]
    transports = iter([successful, failing])
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda *_args: next(transports),
    )
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl", controller_startup_delay=0.0),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    machine.disconnect()

    with pytest.raises(TransientConnectionError):
        machine.connect()

    assert failure_attempts == 1
