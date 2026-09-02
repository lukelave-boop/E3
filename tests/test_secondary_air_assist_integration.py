from __future__ import annotations

import re
import threading
import time
from collections.abc import Callable

import pytest

from laser_aligner.air_assist import (
    AirAssistCommands,
    AirAssistMode,
    AirAssistSettings,
    AirAssistTarget,
)
from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import SafetyError
from laser_aligner.machine.controller_dialects import resolve_air_assist_commands
from laser_aligner.machine.secondary_controller import (
    CrealityControllerOwner,
    SecondaryMarlinFanController,
)
from laser_aligner.machine.service import MachineService
from laser_aligner.project import (
    Bounds,
    ProjectDocument,
    SceneObject,
    generate_project_gcode,
)
from tests.fakes.simulator_transport import SimulatedTransport

_SECONDARY_PORT = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
_PRIMARY_PORT = "/dev/serial/by-id/primary-grbl"
_DIRECTIVE_PATTERN = re.compile(r"^E3AIRASSIST [0-9a-f]{64} (?:ON|OFF)$")


class _FakeSecondarySerial:
    def __init__(
        self,
        responses: list[str | None | BaseException] | None = None,
        *,
        default_response: str | None = "ok",
        open_error: BaseException | None = None,
        on_write: Callable[[str], None] | None = None,
        on_read: Callable[[], str | None] | None = None,
    ) -> None:
        self.responses = list(responses or [])
        self.default_response = default_response
        self.open_error = open_error
        self.on_write = on_write
        self.on_read = on_read
        self.open_calls = 0
        self.close_calls = 0
        self.synchronize_calls = 0
        self.writes: list[str] = []
        self._fault_lock = threading.Lock()
        self._passive_fault: BaseException | None = None

    def open(self) -> None:
        self.open_calls += 1
        if self.open_error is not None:
            raise self.open_error

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
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            return response
        return self.default_response

    def latch_fault(self, fault: BaseException) -> None:
        with self._fault_lock:
            self._passive_fault = fault

    def raise_if_faulted(self) -> None:
        with self._fault_lock:
            fault = self._passive_fault
        if fault is not None:
            raise fault


class _RecordingPrimaryTransport(SimulatedTransport):
    def __init__(
        self,
        *,
        on_write: Callable[[str], None] | None = None,
    ) -> None:
        super().__init__()
        self.commands: list[str] = []
        self.on_write = on_write

    def write_line(self, line: str) -> None:
        command = " ".join(line.strip().upper().split())
        self.commands.append(command)
        if self.on_write is not None:
            self.on_write(command)
        super().write_line(line)


def _air_settings(
    *,
    port: str = _SECONDARY_PORT,
    baudrate: int = 115200,
) -> AirAssistSettings:
    return AirAssistSettings(
        mode=AirAssistMode.SECONDARY_MARLIN_FAN,
        fan_index=0,
        port=port,
        baudrate=baudrate,
    )


def _machine_settings(
    *,
    port: str = _SECONDARY_PORT,
    baudrate: int = 115200,
) -> MachineSettings:
    return MachineSettings(
        backend="serial",
        protocol="grbl",
        port=_PRIMARY_PORT,
        baudrate=115200,
        read_timeout=0.05,
        allow_motion=True,
        controller_startup_delay=0.0,
        home_and_release_after_powered_job=False,
        air_assist=_air_settings(port=port, baudrate=baudrate),
    )


def _binding(
    *,
    port: str = _SECONDARY_PORT,
    baudrate: int = 115200,
) -> AirAssistCommands:
    commands = resolve_air_assist_commands(
        _air_settings(port=port, baudrate=baudrate),
        protocol="grbl",
    )
    assert commands is not None
    return commands


def _fan_controller(
    binding: AirAssistCommands,
    serials: list[_FakeSecondarySerial],
    *,
    read_timeout_seconds: float = 0.02,
) -> SecondaryMarlinFanController:
    port = binding.port
    baudrate = binding.baudrate
    assert port is not None and baudrate is not None

    def factory(requested_port: str, requested_baudrate: int) -> _FakeSecondarySerial:
        assert (requested_port, requested_baudrate) == (port, baudrate)
        if not serials:
            raise AssertionError("unexpected secondary serial reopen")
        return serials.pop(0)

    owner = CrealityControllerOwner(
        port,
        baudrate,
        serial_factory=factory,
        sleep=lambda _delay: None,
        startup_delay_seconds=0.0,
        read_timeout_seconds=read_timeout_seconds,
    )
    return SecondaryMarlinFanController(owner, binding)


def _document(
    *,
    first_air: bool = True,
    first_passes: int = 2,
    second_air: bool | None = True,
) -> ProjectDocument:
    document = ProjectDocument.new(
        "Secondary Air Assist integration",
        Bounds(15.0, 15.0, 205.0, 205.0),
    )
    first = document.layers[0]
    first.name = "First"
    first.speed_mm_min = 1200.0
    first.power_percent = 10.0
    first.passes = first_passes
    first.air_assist = first_air
    document.add_object(
        SceneObject.line(
            first.id,
            name="First A",
            center=(40.0, 40.0),
            length_mm=10.0,
        )
    )
    document.add_object(
        SceneObject.line(
            first.id,
            name="First B",
            center=(60.0, 40.0),
            length_mm=10.0,
        )
    )
    if second_air is not None:
        second = document.add_layer(name="Second")
        second.speed_mm_min = 1200.0
        second.power_percent = 15.0
        second.air_assist = second_air
        document.add_object(
            SceneObject.line(
                second.id,
                name="Second A",
                center=(80.0, 60.0),
                length_mm=10.0,
            )
        )
    return document


def _generated_job(
    commands: AirAssistCommands,
    *,
    first_air: bool = True,
    first_passes: int = 2,
    second_air: bool | None = True,
):
    return generate_project_gcode(
        _document(
            first_air=first_air,
            first_passes=first_passes,
            second_air=second_air,
        ),
        LaserSettings(power_max=1000),
        air_assist_commands=commands,
    )


def _connected_machine(
    monkeypatch: pytest.MonkeyPatch,
    *,
    settings: MachineSettings,
    secondary: SecondaryMarlinFanController | None,
    primary: _RecordingPrimaryTransport | None = None,
) -> tuple[MachineService, _RecordingPrimaryTransport]:
    transport = primary or _RecordingPrimaryTransport()
    monkeypatch.setattr(
        "laser_aligner.machine.service.create_machine_transport",
        lambda *_args, **_kwargs: transport,
    )
    machine = MachineService(
        settings,
        LaserSettings(power_max=1000, arm_timeout_seconds=10),
        hardware_enabled=True,
        secondary_air_assist=secondary,
    )
    machine.connect()
    machine._coordinate_reference_ready = True
    machine._coordinate_state_reference = {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }
    machine._jog_position_mm = (0.0, 0.0)
    return machine, transport


def _wait_for_job(machine: MachineService, timeout: float = 2.0) -> dict[str, object]:
    deadline = time.monotonic() + timeout
    status = machine.status()["job"]
    while status["running"] and time.monotonic() < deadline:
        time.sleep(0.005)
        status = machine.status()["job"]
    assert status["running"] is False
    return status


def _arm_and_start(machine: MachineService, text: str) -> None:
    program = machine.preflight_program(text)
    machine.arm_program(machine.ARM_PHRASE, program)
    machine.start_validated_program(program, "secondary-air.gcode")


def test_primary_grbl_accepts_exact_pi_owned_secondary_mapping() -> None:
    settings = _machine_settings()
    commands = _binding()

    assert settings.protocol == "grbl"
    assert settings.port == _PRIMARY_PORT
    assert settings.air_assist == _air_settings()
    assert commands.mode is AirAssistMode.SECONDARY_MARLIN_FAN
    assert commands.target is AirAssistTarget.PI_SECONDARY
    assert commands.protocol == "marlin"
    assert commands.fan_index is None
    assert commands.port == _SECONDARY_PORT
    assert commands.baudrate == 115200
    assert commands.on_commands == ("M106 S255",)
    assert commands.off_commands == ("M106 S0",)


def test_generation_preview_and_adjacent_layers_share_one_strict_schedule() -> None:
    commands = _binding()
    job = _generated_job(commands, first_passes=3, second_air=True)
    lines = [line.partition(";")[0].strip() for line in job.text.splitlines()]
    executable = [line for line in lines if line]
    directives = [line for line in executable if line.startswith("E3AIRASSIST")]
    primary_program = [line for line in executable if line not in directives]

    assert directives == [
        commands.program_lines(False)[0],
        commands.program_lines(True)[0],
        commands.program_lines(False)[0],
    ]
    assert all(_DIRECTIVE_PATTERN.fullmatch(line) for line in directives)
    assert [event.command for event in job.plan.air_assist_events] == [
        "M106 S0",
        "M106 S255",
        "M106 S0",
    ]
    assert [event.enabled for event in job.plan.air_assist_events] == [
        False,
        True,
        False,
    ]
    assert all("M106" not in line and "M107" not in line for line in primary_program)
    assert all(re.search(r"(?:^|\s)P[-+]?\d", line) is None for line in primary_program)

    first_position = next(
        index for index, line in enumerate(executable) if line.startswith("G0 ")
    )
    first_power = next(
        index for index, line in enumerate(executable) if re.match(r"M[34] S", line)
    )
    on_index = executable.index(commands.program_lines(True)[0])
    assert first_position < on_index < first_power
    assert directives.count(commands.program_lines(True)[0]) == 1
    assert directives[-1] == commands.program_lines(False)[0]
    assert executable[-2:] == [commands.program_lines(False)[0], "M5"]


def test_enabled_to_disabled_layer_turns_off_before_work_and_finishes_off() -> None:
    commands = _binding()
    job = _generated_job(commands, first_passes=2, second_air=False)
    lines = [line.strip() for line in job.text.splitlines()]
    off = commands.program_lines(False)[0]
    on = commands.program_lines(True)[0]
    directive_states = [
        line.rsplit(" ", 1)[-1]
        for line in lines
        if line.startswith("E3AIRASSIST")
    ]

    assert directive_states == ["OFF", "ON", "OFF", "OFF"]
    on_index = lines.index(on)
    transition_off_index = lines.index(off, on_index + 1)
    second_layer_index = next(
        index for index, line in enumerate(lines) if line.startswith("; Layer Second")
    )
    assert lines[transition_off_index - 1] == "M5"
    assert transition_off_index < second_layer_index
    assert [event.command for event in job.plan.air_assist_events] == [
        "M106 S0",
        "M106 S255",
        "M106 S0",
        "M106 S0",
    ]
    executable = [line.partition(";")[0].strip() for line in lines]
    executable = [line for line in executable if line]
    assert executable[-3:] == ["M5", off, "M5"]


@pytest.mark.parametrize(
    "replacement",
    [
        lambda commands: f"E3AIRASSIST {'0' * 64} OFF",
        lambda commands: f"E3AIRASSIST {commands.mapping_digest} MAYBE",
        lambda commands: f"E3AIRASSIST {commands.mapping_digest} OFF EXTRA",
    ],
    ids=["hash-mismatch", "invalid-state", "extra-token"],
)
def test_preflight_rejects_hash_mismatch_and_malformed_directives(
    replacement: Callable[[AirAssistCommands], str],
) -> None:
    settings = _machine_settings()
    commands = _binding()
    text = _generated_job(commands, second_air=None).text
    corrupted = text.replace(
        commands.program_lines(False)[0],
        replacement(commands),
        1,
    )
    machine = MachineService(
        settings,
        LaserSettings(power_max=1000),
        hardware_enabled=True,
    )

    with pytest.raises(SafetyError, match="invalid E3 air-assist instruction"):
        machine.preflight_program(corrupted)


def test_secondary_program_fails_closed_without_the_e3_mapping() -> None:
    commands = _binding()
    text = _generated_job(commands, second_air=None).text
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            port=_PRIMARY_PORT,
            allow_motion=True,
        ),
        LaserSettings(power_max=1000),
        hardware_enabled=True,
    )

    with pytest.raises(SafetyError, match="no resolved machine mapping"):
        machine.preflight_program(text)


def test_program_digest_changes_when_secondary_schedule_changes() -> None:
    commands = _binding()
    machine = MachineService(
        _machine_settings(),
        LaserSettings(power_max=1000),
        hardware_enabled=True,
    )
    with_assist = machine.preflight_program(
        _generated_job(commands, first_air=True, second_air=None).text
    )
    without_assist = machine.preflight_program(
        _generated_job(commands, first_air=False, second_air=None).text
    )

    assert with_assist.digest != without_assist.digest
    assert commands.program_lines(True)[0] in with_assist.lines
    assert commands.program_lines(True)[0] not in without_assist.lines


def test_program_and_mapping_digests_change_with_endpoint_and_baudrate() -> None:
    variants = [
        (_SECONDARY_PORT, 115200),
        (f"{_SECONDARY_PORT}-replacement", 115200),
        (_SECONDARY_PORT, 250000),
    ]
    mapping_digests: list[str] = []
    program_digests: list[str] = []

    for port, baudrate in variants:
        commands = _binding(port=port, baudrate=baudrate)
        machine = MachineService(
            _machine_settings(port=port, baudrate=baudrate),
            LaserSettings(power_max=1000),
            hardware_enabled=True,
        )
        program = machine.preflight_program(
            _generated_job(commands, second_air=None).text
        )
        mapping_digests.append(commands.mapping_digest)
        program_digests.append(program.digest)

    assert len(set(mapping_digests)) == len(variants)
    assert len(set(program_digests)) == len(variants)


def test_machine_service_routes_directives_only_to_the_secondary_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _binding()
    serial = _FakeSecondarySerial()
    fan = _fan_controller(commands, [serial])
    machine, primary = _connected_machine(
        monkeypatch,
        settings=_machine_settings(),
        secondary=fan,
    )
    primary.commands.clear()

    try:
        _arm_and_start(machine, _generated_job(commands, second_air=None).text)
        status = _wait_for_job(machine)

        assert status["error"] is None
        assert status["phase"] == "complete"
        assert serial.writes == ["M106 S0", "M106 S255", "M106 S0"]
        assert all(" P" not in f" {command}" and command != "M107" for command in serial.writes)
        assert all(not command.startswith("E3AIRASSIST") for command in primary.commands)
        assert all("M106" not in command and "M107" not in command for command in primary.commands)
        assert any(command.startswith("M4 S") for command in primary.commands)
    finally:
        machine.disconnect()


def test_start_rejects_an_unavailable_secondary_before_primary_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _binding()
    machine, primary = _connected_machine(
        monkeypatch,
        settings=_machine_settings(),
        secondary=None,
    )
    program = machine.preflight_program(_generated_job(commands, second_air=None).text)
    machine.arm_program(machine.ARM_PHRASE, program)
    primary.commands.clear()

    try:
        with pytest.raises(SafetyError, match="controller is unavailable"):
            machine.start_validated_program(program)
        assert primary.commands == []
        assert machine.status()["job"]["running"] is False
    finally:
        machine.disconnect()


def test_start_rejects_secondary_off_without_ack_before_primary_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _binding()
    serial = _FakeSecondarySerial(default_response=None)
    fan = _fan_controller(commands, [serial], read_timeout_seconds=0.005)
    machine, primary = _connected_machine(
        monkeypatch,
        settings=_machine_settings(),
        secondary=fan,
    )
    program = machine.preflight_program(_generated_job(commands, second_air=None).text)
    machine.arm_program(machine.ARM_PHRASE, program)
    primary.commands.clear()

    try:
        with pytest.raises(SafetyError, match="could not establish acknowledged OFF"):
            machine.start_validated_program(program)
        assert serial.writes == ["M106 S0"]
        assert primary.commands == []
        assert machine.status()["job"]["running"] is False
    finally:
        machine.disconnect()


def test_start_rejects_secondary_port_open_failure_before_primary_streaming(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _binding()
    serial = _FakeSecondarySerial(open_error=OSError("secondary port unavailable"))
    fan = _fan_controller(commands, [serial])
    machine, primary = _connected_machine(
        monkeypatch,
        settings=_machine_settings(),
        secondary=fan,
    )
    program = machine.preflight_program(_generated_job(commands, second_air=None).text)
    machine.arm_program(machine.ARM_PHRASE, program)
    primary.commands.clear()

    try:
        with pytest.raises(SafetyError, match="could not establish acknowledged OFF"):
            machine.start_validated_program(program)
        assert serial.open_calls == 1
        assert serial.writes == []
        assert primary.commands == []
        assert machine.status()["job"]["running"] is False
    finally:
        machine.disconnect()


def test_stop_sends_primary_m5_before_nonblocking_secondary_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _binding()
    secondary_waiting = threading.Event()
    release_secondary_ack = threading.Event()
    stop_returned = threading.Event()
    sequence: list[tuple[str, str]] = []

    serial: _FakeSecondarySerial

    def read_secondary() -> str:
        if serial.writes[-1] == "M106 S255" and not release_secondary_ack.is_set():
            secondary_waiting.set()
            assert release_secondary_ack.wait(timeout=2.0)
        return "ok"

    serial = _FakeSecondarySerial(
        on_write=lambda command: sequence.append(("secondary", command)),
        on_read=read_secondary,
    )
    fan = _fan_controller(commands, [serial], read_timeout_seconds=3.0)
    primary = _RecordingPrimaryTransport(
        on_write=lambda command: sequence.append(("primary", command))
    )
    machine, primary = _connected_machine(
        monkeypatch,
        settings=_machine_settings(),
        secondary=fan,
        primary=primary,
    )
    primary.commands.clear()
    sequence.clear()

    try:
        _arm_and_start(machine, _generated_job(commands, second_air=None).text)
        assert secondary_waiting.wait(timeout=1.0)
        primary.commands.clear()
        sequence.clear()

        stopper = threading.Thread(
            target=lambda: (machine.request_stop(), stop_returned.set()),
            daemon=True,
        )
        stopper.start()
        try:
            assert stop_returned.wait(timeout=0.5)
            assert primary.commands == ["M5"]
            assert sequence == [("primary", "M5")]
        finally:
            release_secondary_ack.set()
            stopper.join(timeout=1.0)

        status = _wait_for_job(machine)
        cleanup = machine._secondary_cleanup_thread
        if cleanup is not None:
            cleanup.join(timeout=1.0)
        assert status["phase"] == "failed"
        assert status["error"] == "Job stopped"
        assert sequence[0] == ("primary", "M5")
        assert ("secondary", "M106 S0") in sequence[1:]
    finally:
        release_secondary_ack.set()
        machine.disconnect()


def test_secondary_rejection_fails_job_and_attempts_primary_m5_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _binding()
    failed = _FakeSecondarySerial(["ok", "error: fan fault"])
    recovery = _FakeSecondarySerial()
    fan = _fan_controller(commands, [failed, recovery])
    machine, primary = _connected_machine(
        monkeypatch,
        settings=_machine_settings(),
        secondary=fan,
    )
    primary.commands.clear()

    try:
        _arm_and_start(machine, _generated_job(commands, second_air=None).text)
        status = _wait_for_job(machine)

        assert status["phase"] == "failed"
        assert "Secondary Creality controller session failed" in str(status["error"])
        assert primary.commands[-1] == "M5"
        assert primary.commands.count("M5") >= 3
        assert all(not command.startswith("E3AIRASSIST") for command in primary.commands)
        assert failed.writes == ["M106 S0", "M106 S255"]
        assert recovery.writes == ["M106 S0"]
    finally:
        machine.disconnect()


def test_secondary_cable_loss_after_on_fails_job_with_primary_m5_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands = _binding()
    disconnected = _FakeSecondarySerial()
    recovery = _FakeSecondarySerial()
    fan = _fan_controller(commands, [disconnected, recovery])
    job = _generated_job(commands, second_air=None)
    work_lines = [
        " ".join(line.strip().upper().split())
        for line in job.text.splitlines()
        if line.strip().upper().startswith("G1 ")
    ]
    assert len(work_lines) >= 2

    def latch_disconnect_after_first_work_line(command: str) -> None:
        if command == work_lines[0]:
            disconnected.latch_fault(OSError("secondary cable disconnected"))

    primary = _RecordingPrimaryTransport(
        on_write=latch_disconnect_after_first_work_line
    )
    machine, primary = _connected_machine(
        monkeypatch,
        settings=_machine_settings(),
        secondary=fan,
        primary=primary,
    )
    primary.commands.clear()

    try:
        _arm_and_start(machine, job.text)
        status = _wait_for_job(machine)

        assert status["phase"] == "failed"
        assert "secondary cable disconnected" in str(status["error"])
        assert work_lines[0] in primary.commands
        assert work_lines[1] not in primary.commands
        assert primary.commands[-1] == "M5"
        assert all(not command.startswith("E3AIRASSIST") for command in primary.commands)
        assert disconnected.writes == ["M106 S0", "M106 S255"]
        assert disconnected.close_calls == 1
        assert recovery.writes == ["M106 S0"]
    finally:
        machine.disconnect()
