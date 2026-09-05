from __future__ import annotations

import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Literal

import pytest

from laser_aligner.air_assist import AirAssistMode, AirAssistSettings
from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import MachineError, TransientConnectionError
from laser_aligner.machine import service as service_module
from laser_aligner.machine.service import MachineService

Channel = Literal["line", "raw"]
Payload = str | bytes


@dataclass(frozen=True, slots=True)
class ExpectedWrite:
    channel: Channel
    payload: Payload
    responses: tuple[str, ...] = ()
    error: BaseException | None = None


def line(
    payload: str,
    *responses: str,
    error: BaseException | None = None,
) -> ExpectedWrite:
    return ExpectedWrite("line", payload, tuple(responses), error)


def raw(payload: bytes, *responses: str) -> ExpectedWrite:
    return ExpectedWrite("raw", payload, tuple(responses))


class ScriptedTransport:
    """Strict deterministic MachineTransport used only by transcript tests."""

    test_only_allow_legacy_input_synchronization = True

    def __init__(
        self,
        steps: list[ExpectedWrite],
        *,
        startup: tuple[str, ...] = ("controller startup",),
        open_error: BaseException | None = None,
    ) -> None:
        self.is_open = False
        self.open_calls = 0
        self.close_calls = 0
        self.events: list[tuple[str, object]] = []
        self._writes: list[tuple[Channel, Payload]] = []
        self._steps = deque(steps)
        self._startup = startup
        self._open_error = open_error
        self._responses: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._writes_changed = threading.Condition(self._lock)
        self._failures: list[str] = []

    @property
    def writes(self) -> list[tuple[Channel, Payload]]:
        with self._lock:
            return list(self._writes)

    def open(self) -> None:
        with self._lock:
            self.open_calls += 1
            self.events.append(("open", None))
            error = self._open_error
        if error is not None:
            raise error
        self.is_open = True
        for response in self._startup:
            self._responses.put(response)

    def close(self) -> None:
        with self._lock:
            self.close_calls += 1
            self.events.append(("close", None))
        self.is_open = False

    def write_raw(self, data: bytes) -> None:
        self._consume("raw", data)

    def write_line(self, line_value: str) -> None:
        self._consume("line", line_value)

    def wait_for_write_count(self, count: int, timeout: float = 3.0) -> bool:
        with self._writes_changed:
            return self._writes_changed.wait_for(
                lambda: len(self._writes) >= count,
                timeout=timeout,
            )

    def _consume(self, channel: Channel, payload: Payload) -> None:
        with self._lock:
            self.events.append((channel, payload))
            self._writes.append((channel, payload))
            self._writes_changed.notify_all()
            if not self._steps:
                message = f"Unexpected {channel} write: {payload!r}"
                self._failures.append(message)
                raise AssertionError(message)
            expected = self._steps.popleft()
            if (expected.channel, expected.payload) != (channel, payload):
                message = (
                    f"Expected {expected.channel} write {expected.payload!r}, "
                    f"got {channel} write {payload!r}"
                )
                self._failures.append(message)
                raise AssertionError(message)
        if expected.error is not None:
            raise expected.error
        for response in expected.responses:
            self._responses.put(response)

    def read_line(self, timeout: float = 1.0) -> str | None:
        try:
            response = self._responses.get(timeout=min(max(0.0, timeout), 0.01))
        except queue.Empty:
            return None
        with self._lock:
            self.events.append(("rx", response))
        return response

    def drain(self) -> list[str]:
        output: list[str] = []
        while True:
            try:
                output.append(self._responses.get_nowait())
            except queue.Empty:
                break
        with self._lock:
            self.events.append(("drain", tuple(output)))
        return output

    def assert_complete(self, *, closes: int = 1) -> None:
        with self._lock:
            assert self._failures == []
            assert list(self._steps) == []
            assert self._responses.empty()
            assert self.open_calls == 1
            assert self.close_calls == closes


GRBL_IDENTITY = "[VER:1.1h.test:SCRIPTED]"
GRBL_MODAL = "[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]"
GRBL_SETTINGS = ("$1=250", "$30=1000", "$32=1", "ok")
GRBL_HELD_SETTINGS = ("$1=255", "$30=1000", "$32=1", "ok")
GRBL_OFFSETS = (
    "[G54:0.000,0.000,0.000]",
    "[G55:0.000,0.000,0.000]",
    "[G56:0.000,0.000,0.000]",
    "[G57:0.000,0.000,0.000]",
    "[G58:0.000,0.000,0.000]",
    "[G59:0.000,0.000,0.000]",
    "[G92:0.000,0.000,0.000]",
    "ok",
)
GRBL_REALTIME = "<Idle|MPos:0.000,0.000,0.000|WPos:0.000,0.000,0.000|WCO:0.000,0.000,0.000>"


def grbl_connect_steps() -> list[ExpectedWrite]:
    return [
        line("$I", GRBL_IDENTITY, "ok"),
        line("M5", "ok"),
        line("$$", *GRBL_SETTINGS),
        *coordinate_query_steps(),
        raw(b"?", GRBL_REALTIME),
    ]


def marlin_connect_steps() -> list[ExpectedWrite]:
    return [line("M115", "FIRMWARE_NAME:Marlin 2.1.2", "ok"), line("M5", "ok")]


def coordinate_query_steps() -> list[ExpectedWrite]:
    return [line("$G", GRBL_MODAL, "ok"), line("$#", *GRBL_OFFSETS)]


def grbl_hold_steps() -> list[ExpectedWrite]:
    return [
        line("$$", *GRBL_SETTINGS),
        line("$1=255", "ok"),
        line("$$", *GRBL_HELD_SETTINGS),
    ]


def disconnect_steps(protocol: str = "marlin") -> list[ExpectedWrite]:
    # GRBL aborts planner work before exact-session fail-off and close.
    return [*([raw(b"!\x18")] if protocol == "grbl" else []), line("M5")]


def air_disconnect_steps(off_command: str) -> list[ExpectedWrite]:
    return [raw(b"!\x18"), line("M5"), line(off_command)]


def machine_settings(
    protocol: str,
    *,
    complete_powered_job: bool = False,
) -> MachineSettings:
    return MachineSettings(
        backend="serial",
        protocol=protocol,
        port="scripted-controller",
        baudrate=115200,
        controller_startup_delay=0.0,
        read_timeout=0.05,
        allow_motion=True,
        home_before_photo=True,
        home_and_release_after_powered_job=complete_powered_job,
        photo_x=15.0,
        photo_y=195.0,
    )


def install_transports(
    monkeypatch: pytest.MonkeyPatch,
    *transports: ScriptedTransport,
) -> list[tuple[str, str, int]]:
    pending = deque(transports)
    calls: list[tuple[str, str, int]] = []

    def factory(backend: str, port: str, baudrate: int) -> ScriptedTransport:
        calls.append((backend, port, baudrate))
        if not pending:
            raise AssertionError("MachineService requested an unexpected transport")
        return pending.popleft()

    monkeypatch.setattr(service_module, "create_machine_transport", factory)
    return calls


def wait_for_job(machine: MachineService) -> dict[str, object]:
    deadline = time.monotonic() + 3.0
    status = machine.status()
    while status["job"]["running"] and time.monotonic() < deadline:  # type: ignore[index]
        time.sleep(0.001)
        status = machine.status()
    assert status["job"]["running"] is False  # type: ignore[index]
    return status


@pytest.mark.parametrize(
    "settings",
    [
        MachineSettings(backend="serial", protocol="auto"),
        MachineSettings(backend="serial", protocol="auto"),
    ],
    ids=["simulator", "serial-auto"],
)
def test_identity_query_before_protocol_resolution_keeps_connection_error(
    settings: MachineSettings,
) -> None:
    machine = MachineService(settings, LaserSettings())

    with pytest.raises(MachineError, match="^Controller is not connected$"):
        machine.query_identity()


@pytest.mark.parametrize(
    ("protocol", "connect_steps", "expected_connect_writes"),
    [
        (
            "grbl",
            grbl_connect_steps(),
            [
                ("line", "$I"),
                ("line", "M5"),
                ("line", "$$"),
                ("line", "$G"),
                ("line", "$#"),
                ("raw", b"?"),
            ],
        ),
        (
            "marlin",
            marlin_connect_steps(),
            [("line", "M115"), ("line", "M5")],
        ),
    ],
)
def test_explicit_connect_and_disconnect_have_exact_laser_off_transcript(
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    connect_steps: list[ExpectedWrite],
    expected_connect_writes: list[tuple[Channel, Payload]],
) -> None:
    transport = ScriptedTransport([*connect_steps, *disconnect_steps(protocol)])
    factory_calls = install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings(protocol),
        LaserSettings(),
        hardware_enabled=True,
    )

    connected = machine.connect()

    assert factory_calls == [("serial", "scripted-controller", 115200)]
    assert connected["connected"] is True
    assert connected["protocol"] == protocol
    assert connected["controller_reconnect_required"] is False
    assert connected["coordinate_reference_ready"] is False
    assert connected["jog_ready"] is False
    assert transport.writes == expected_connect_writes

    machine.disconnect()

    assert transport.writes == [
        *expected_connect_writes,
        *([("raw", b"!\x18")] if protocol == "grbl" else []),
        ("line", "M5"),
    ]
    assert machine.status()["connected"] is False
    assert machine.status()["coordinate_reference_ready"] is False
    transport.assert_complete()


@pytest.mark.parametrize(
    ("steps", "expected_protocol", "expected_writes"),
    [
        (
            [
                *grbl_connect_steps(),
                *disconnect_steps("grbl"),
            ],
            "grbl",
            ["$I", "M5", "$$", "$G", "$#", "M5"],
        ),
        (
            [
                line("$I", "error:Unknown command"),
                line("M115", "FIRMWARE_NAME:Marlin 2.1.2", "ok"),
                line("M5", "ok"),
                *disconnect_steps(),
            ],
            "marlin",
            ["$I", "M115", "M5", "M5"],
        ),
    ],
)
def test_auto_connect_identifies_each_controller_before_normalization(
    monkeypatch: pytest.MonkeyPatch,
    steps: list[ExpectedWrite],
    expected_protocol: str,
    expected_writes: list[str],
) -> None:
    transport = ScriptedTransport(steps, startup=("start",))
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings("auto"),
        LaserSettings(),
        hardware_enabled=True,
    )

    status = machine.connect()
    assert status["protocol"] == expected_protocol
    assert [payload for channel, payload in transport.writes if channel == "line"] == expected_writes[:-1]

    machine.disconnect()

    assert [payload for channel, payload in transport.writes if channel == "line"] == expected_writes
    transport.assert_complete()


def test_auto_grbl_banner_still_requires_identity_probe_and_full_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = ScriptedTransport(
        [*grbl_connect_steps(), *disconnect_steps("grbl")],
        startup=("Grbl 1.1h ['$' for help]",),
    )
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings("auto"),
        LaserSettings(),
        hardware_enabled=True,
    )

    status = machine.connect()

    assert status["protocol"] == "grbl"
    assert transport.writes == [
        ("line", "$I"),
        ("line", "M5"),
        ("line", "$$"),
        ("line", "$G"),
        ("line", "$#"),
        ("raw", b"?"),
    ]

    machine.disconnect()
    transport.assert_complete()


def test_identity_and_realtime_status_queries_use_the_existing_grbl_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    realtime = "<Idle|MPos:15,195,0|WPos:15,195,0|WCO:0,0,0>"
    steps = [
        *grbl_connect_steps(),
        line("$I", GRBL_IDENTITY, "ok"),
        raw(b"?", realtime),
        *disconnect_steps("grbl"),
    ]
    transport = ScriptedTransport(steps, startup=("start",))
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings("auto"),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()

    identity = machine.send_command("$I")
    snapshot = machine.sample_realtime_position(timeout=0.1)
    status = machine.status()

    assert identity == [GRBL_IDENTITY, "ok"]
    assert snapshot["state"] == "Idle"
    assert snapshot["mpos_mm"][:2] == [15.0, 195.0]
    assert snapshot["wpos_mm"][:2] == [15.0, 195.0]
    assert snapshot["wco_mm"][:2] == [0.0, 0.0]
    assert status["connected"] is True
    assert status["protocol"] == "grbl"
    assert status["port"] == "scripted-controller"
    assert status["baudrate"] == 115200
    assert status["controller_reconnect_required"] is False
    assert transport.writes[-2:] == [("line", "$I"), ("raw", b"?")]

    machine.disconnect()
    transport.assert_complete()


def test_marlin_identity_and_status_query_use_m115_and_m114(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity_line = "FIRMWARE_NAME:Marlin 2.1.2 SOURCE_CODE_URL:example.invalid"
    position_line = "X:15.00 Y:195.00 Z:0.00 E:0.00"
    transport = ScriptedTransport(
        [
            *marlin_connect_steps(),
            line("M115", identity_line, "ok"),
            line("M114", position_line, "ok"),
            *disconnect_steps(),
        ]
    )
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings("marlin"),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()

    identity = machine.query_identity()
    position = machine.send_command("M114")
    status = machine.status()

    assert identity == [identity_line, "ok"]
    assert position == [position_line, "ok"]
    assert status["connected"] is True
    assert status["protocol"] == "marlin"
    assert status["controller_reconnect_required"] is False
    assert status["coordinate_reference_ready"] is False
    assert transport.writes[-2:] == [("line", "M115"), ("line", "M114")]
    assert not any(channel == "raw" for channel, _payload in transport.writes)

    machine.disconnect()
    transport.assert_complete()


def test_grbl_home_park_uses_acknowledged_home_and_two_coordinate_snapshots(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_steps = [
        line("M5", "ok"),
        *grbl_hold_steps(),
        line("$H", "ok"),
        *coordinate_query_steps(),
        line("G21", "ok"),
        line("G90", "ok"),
        line("G0 X15.000 Y195.000 F3000.000", "ok"),
        line("G4 P0.01", "ok"),
        *coordinate_query_steps(),
    ]
    transport = ScriptedTransport(
        [*grbl_connect_steps(), *operation_steps, *disconnect_steps("grbl")]
    )
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings("grbl"),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()

    result = machine.prepare_photo_position()

    assert result["position"] == {"x": 15.0, "y": 195.0, "z": None}
    assert result["homed"] is True
    assert result["parked"] is True
    assert result["coordinate_state"]["active_workspace"] == "G54"
    assert machine.status()["coordinate_reference_ready"] is True
    assert machine.status()["jog_position_mm"] == {"x": 15.0, "y": 195.0}
    assert transport.writes == [
        *[(step.channel, step.payload) for step in grbl_connect_steps()],
        *[(step.channel, step.payload) for step in operation_steps],
    ]

    machine.disconnect()
    transport.assert_complete()


def test_grbl_home_without_ok_requires_active_then_idle_before_parking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    operation_steps = [
        line("M5", "ok"),
        *grbl_hold_steps(),
        line("$H"),
        raw(b"?", "<Home|MPos:1,1,0|FS:0,0>"),
        raw(b"?", "<Idle|MPos:0,0,0|FS:0,0>"),
        *coordinate_query_steps(),
        line("G21", "ok"),
        line("G90", "ok"),
        line("G0 X15.000 Y195.000 F3000.000", "ok"),
        line("G4 P0.01", "ok"),
        *coordinate_query_steps(),
    ]
    transport = ScriptedTransport(
        [*grbl_connect_steps(), *operation_steps, *disconnect_steps("grbl")]
    )
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings("grbl"),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()

    result = machine.prepare_photo_position()

    assert result["parked"] is True
    assert result["transcript"][4]["command"] == "$H"
    assert result["transcript"][4]["responses"] == [
        "<Home|MPos:1,1,0|FS:0,0>",
        "<Idle|MPos:0,0,0|FS:0,0>",
    ]
    assert machine.status()["coordinate_reference_ready"] is True
    assert [payload for channel, payload in transport.writes if channel == "raw"] == [
        b"?",
        b"?",
        b"?",
    ]

    machine.disconnect()
    transport.assert_complete()


@pytest.mark.parametrize(
    ("protocol", "home_steps", "expected_writes"),
    [
        (
            "grbl",
            [
                line("M5", "ok"),
                *grbl_hold_steps(),
                line("$H", "ok"),
                *coordinate_query_steps(),
                line("G21", "ok"),
                line("G90", "ok"),
            ],
            ["M5", "$$", "$1=255", "$$", "$H", "$G", "$#", "G21", "G90"],
        ),
        (
            "marlin",
            [
                line("M5", "ok"),
                line("G28", "ok"),
                line("G21", "ok"),
                line("G90", "ok"),
            ],
            ["M5", "G28", "G21", "G90"],
        ),
    ],
)
def test_job_start_home_establishes_coordinates_without_photo_parking(
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    home_steps: list[ExpectedWrite],
    expected_writes: list[str],
) -> None:
    connect = grbl_connect_steps() if protocol == "grbl" else marlin_connect_steps()
    transport = ScriptedTransport(
        [*connect, *home_steps, *disconnect_steps(protocol)]
    )
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings(protocol),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    before = len(transport.writes)

    result = machine.prepare_job_start()

    operation_writes = transport.writes[before:]
    assert [payload for channel, payload in operation_writes if channel == "line"] == expected_writes
    assert result["position"] is None
    assert result["homed"] is True
    assert result["parked"] is False
    assert machine.status()["coordinate_reference_ready"] is True
    assert machine.status()["jog_position_mm"] is None
    assert not any(
        channel == "line" and str(payload).startswith("G0 ")
        for channel, payload in operation_writes
    )

    machine.disconnect()
    transport.assert_complete()


def test_dry_marlin_stream_waits_for_m400_before_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program_lines = [
        "G21",
        "G90",
        "M5",
        "G0 X10 Y10 F1000",
        "G1 X20 Y20 F500",
        "M5",
    ]
    setup_steps = [
        line("M5", "ok"),
        line("G28", "ok"),
        line("G21", "ok"),
        line("G90", "ok"),
    ]
    stream_steps = [
        line("M5", "ok"),
        *(line(command, "ok") for command in program_lines),
        line("M5", "ok"),
        line("M400", "ok"),
    ]
    transport = ScriptedTransport(
        [
            *marlin_connect_steps(),
            *setup_steps,
            *stream_steps,
            *disconnect_steps(),
        ]
    )
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings("marlin"),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    machine.prepare_job_start()
    before = len(transport.writes)
    program = machine.preflight_program("\n".join(program_lines) + "\n")

    machine.start_validated_program(program, "dry-marlin.gcode")
    status = wait_for_job(machine)

    assert transport.writes[before:] == [
        (step.channel, step.payload) for step in stream_steps
    ]
    assert status["job"]["phase"] == "complete"  # type: ignore[index]
    assert status["job"]["error"] is None  # type: ignore[index]
    assert status["job"]["completed_lines"] == len(program_lines)  # type: ignore[index]
    assert status["last_successful_job"]["program_digest"] == program.digest  # type: ignore[index]
    assert status["last_successful_job"]["protocol"] == "marlin"  # type: ignore[index]
    assert status["coordinate_reference_ready"] is True

    machine.disconnect()
    transport.assert_complete()


def test_powered_grbl_stream_has_complete_home_park_hold_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program_lines = [
        "G21",
        "G90",
        "M5",
        "G0 X10 Y10 F1000",
        "M4 S5",
        "G1 X20 Y20 F500",
        "M5",
    ]
    job_start_steps = [
        line("M5", "ok"),
        *grbl_hold_steps(),
        line("$H", "ok"),
        *coordinate_query_steps(),
        line("G21", "ok"),
        line("G90", "ok"),
    ]
    start_verification = coordinate_query_steps()
    stream_steps = [
        line("M5", "ok"),
        *(line(command, "ok") for command in program_lines),
        line("M5", "ok"),
        line("G4 P0.01", "ok"),
        line("$H", "ok"),
        line("G21", "ok"),
        line("G90", "ok"),
        line("G0 X15.000 Y195.000 F3000.000", "ok"),
        line("G4 P0.01", "ok"),
        line("$$", *GRBL_HELD_SETTINGS),
        *coordinate_query_steps(),
    ]
    transport = ScriptedTransport(
        [
            *grbl_connect_steps(),
            *job_start_steps,
            *start_verification,
            *stream_steps,
            *disconnect_steps("grbl"),
        ]
    )
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings("grbl", complete_powered_job=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    machine.prepare_job_start()
    before_start = len(transport.writes)
    program = machine.preflight_program("\n".join(program_lines) + "\n")
    machine.arm_program(machine.ARM_PHRASE, program)

    machine.start_validated_program(program, "powered-grbl.gcode")
    status = wait_for_job(machine)

    assert transport.writes[before_start:] == [
        *[(step.channel, step.payload) for step in start_verification],
        *[(step.channel, step.payload) for step in stream_steps],
    ]
    assert status["job"]["phase"] == "complete"  # type: ignore[index]
    assert status["job"]["error"] is None  # type: ignore[index]
    assert status["job"]["running"] is False  # type: ignore[index]
    assert status["last_successful_job"]["program_digest"] == program.digest  # type: ignore[index]
    assert status["last_successful_job"]["powered"] is True  # type: ignore[index]
    assert status["armed"] is False
    assert status["controller_state"] == "READY_MOTION"
    assert status["coordinate_reference_ready"] is True
    assert status["jog_ready"] is True

    machine.disconnect()
    transport.assert_complete()


def test_powered_grbl_air_assist_is_off_before_home_park_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program_lines = [
        "G21",
        "G90",
        "M5",
        "M9",
        "G0 X10 Y10 F1000",
        "M8",
        "M4 S5",
        "G1 X20 Y20 F500",
        "M5",
        "M9",
        "M5",
    ]
    job_start_steps = [
        line("M5", "ok"),
        *grbl_hold_steps(),
        line("$H", "ok"),
        *coordinate_query_steps(),
        line("G21", "ok"),
        line("G90", "ok"),
    ]
    start_verification = coordinate_query_steps()
    stream_steps = [
        line("M5", "ok"),
        # Service-level fail-off covers powered setup/calibration programs that
        # intentionally contain no layer Air Assist literals.
        line("M9", "ok"),
        *(line(command, "ok") for command in program_lines),
        # MachineService owns a second acknowledged fail-off immediately
        # before any completion motion, even though the immutable program has
        # already ended in its standalone M5.
        line("M5", "ok"),
        line("M9", "ok"),
        line("G4 P0.01", "ok"),
        line("$H", "ok"),
        line("G21", "ok"),
        line("G90", "ok"),
        line("G0 X15.000 Y195.000 F3000.000", "ok"),
        line("G4 P0.01", "ok"),
        line("$$", *GRBL_HELD_SETTINGS),
        *coordinate_query_steps(),
    ]
    transport = ScriptedTransport(
        [
            *grbl_connect_steps(),
            line("M9", "ok"),
            *job_start_steps,
            *start_verification,
            *stream_steps,
            *air_disconnect_steps("M9"),
        ]
    )
    install_transports(monkeypatch, transport)
    settings = machine_settings("grbl", complete_powered_job=True)
    settings.air_assist = AirAssistSettings(mode=AirAssistMode.GRBL_COOLANT)
    machine = MachineService(settings, LaserSettings(), hardware_enabled=True)
    machine.connect()
    machine.prepare_job_start()
    before_start = len(transport.writes)
    program = machine.preflight_program("\n".join(program_lines) + "\n")
    machine.arm_program(machine.ARM_PHRASE, program)

    machine.start_validated_program(program, "powered-grbl-air.gcode")
    status = wait_for_job(machine)

    assert transport.writes[before_start:] == [
        *[(step.channel, step.payload) for step in start_verification],
        *[(step.channel, step.payload) for step in stream_steps],
    ]
    assert status["job"]["phase"] == "complete"  # type: ignore[index]
    assert status["job"]["error"] is None  # type: ignore[index]

    machine.disconnect()
    transport.assert_complete()


def test_powered_marlin_stream_has_complete_home_park_release_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program_lines = [
        "G21",
        "G90",
        "M5",
        "G0 X10 Y10 F1000",
        "M4 S5",
        "G1 X20 Y20 F500",
        "M5",
    ]
    job_start_steps = [
        line("M5", "ok"),
        line("G28", "ok"),
        line("G21", "ok"),
        line("G90", "ok"),
    ]
    stream_steps = [
        line("M5", "ok"),
        *(line(command, "ok") for command in program_lines),
        line("M5", "ok"),
        line("M400", "ok"),
        line("G28", "ok"),
        line("G21", "ok"),
        line("G90", "ok"),
        line("G0 X15.000 Y195.000 F3000.000", "ok"),
        line("M400", "ok"),
        line("M84", "ok"),
    ]
    transport = ScriptedTransport(
        [
            *marlin_connect_steps(),
            *job_start_steps,
            *stream_steps,
            *disconnect_steps(),
        ]
    )
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings("marlin", complete_powered_job=True),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    machine.prepare_job_start()
    before_start = len(transport.writes)
    program = machine.preflight_program("\n".join(program_lines) + "\n")
    machine.arm_program(machine.ARM_PHRASE, program)

    machine.start_validated_program(program, "powered-marlin.gcode")
    status = wait_for_job(machine)

    assert transport.writes[before_start:] == [
        (step.channel, step.payload) for step in stream_steps
    ]
    assert not any(channel == "raw" for channel, _payload in transport.writes)
    assert status["job"]["phase"] == "complete"  # type: ignore[index]
    assert status["job"]["error"] is None  # type: ignore[index]
    assert status["last_successful_job"]["program_digest"] == program.digest  # type: ignore[index]
    assert status["last_successful_job"]["protocol"] == "marlin"  # type: ignore[index]
    assert status["last_successful_job"]["powered"] is True  # type: ignore[index]
    assert status["armed"] is False
    assert status["coordinate_reference_ready"] is False
    assert status["jog_ready"] is False

    machine.disconnect()
    transport.assert_complete()


@pytest.mark.parametrize(
    ("protocol", "connect", "emergency", "expected_stop"),
    [
        (
            "grbl",
            grbl_connect_steps(),
            [raw(b"!\x18"), line("M5")],
            [("raw", b"!\x18"), ("line", "M5")],
        ),
        (
            "marlin",
            marlin_connect_steps(),
            [line("M112"), line("M5")],
            [("line", "M112"), ("line", "M5")],
        ),
    ],
)
def test_emergency_stop_is_protocol_specific_and_m5_is_last(
    monkeypatch: pytest.MonkeyPatch,
    protocol: str,
    connect: list[ExpectedWrite],
    emergency: list[ExpectedWrite],
    expected_stop: list[tuple[Channel, Payload]],
) -> None:
    transport = ScriptedTransport([*connect, *emergency])
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings(protocol),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    before_stop = len(transport.writes)

    machine.request_stop(emergency=True)

    assert transport.writes[before_stop:] == expected_stop
    status = machine.status()
    assert status["connected"] is False
    assert status["controller_reconnect_required"] is True
    assert status["coordinate_reference_ready"] is False
    assert status["armed"] is False
    assert expected_stop[-1] == ("line", "M5")

    machine.disconnect()
    assert machine.status()["connected"] is False
    transport.assert_complete()


def test_stop_during_active_job_cancels_stream_without_receipt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    program_lines = [
        "G21",
        "G90",
        "M5",
        "G0 X10 Y10 F1000",
        "M5",
    ]
    transport = ScriptedTransport(
        [
            *marlin_connect_steps(),
            line("M5", "ok"),
            line("G28", "ok"),
            line("G21", "ok"),
            line("G90", "ok"),
            # The worker blocks awaiting this injected leading M5. STOP then
            # owns the next M5; worker unwind must not send another fail-off.
            line("M5"),
            line("M5"),
        ]
    )
    install_transports(monkeypatch, transport)
    machine = MachineService(
        machine_settings("marlin"),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine.connect()
    machine.prepare_job_start()
    writes_before_job = len(transport.writes)
    program = machine.preflight_program("\n".join(program_lines) + "\n")

    machine.start_validated_program(program, "stopped-active-job.gcode")
    assert transport.wait_for_write_count(writes_before_job + 1)
    assert machine.status()["job"]["running"] is True  # type: ignore[index]

    machine.request_stop(emergency=False)
    status = wait_for_job(machine)

    assert transport.writes[writes_before_job:] == [
        ("line", "M5"),
        ("line", "M5"),
    ]
    assert status["job"]["phase"] == "failed"  # type: ignore[index]
    assert status["job"]["error"] == "Job stopped"  # type: ignore[index]
    assert status["last_successful_job"] is None
    assert status["controller_reconnect_required"] is True
    assert status["coordinate_reference_ready"] is False
    assert status["armed"] is False

    machine.disconnect()
    transport.assert_complete()


def test_connection_write_failure_attempts_m5_then_closes_without_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transports = tuple(
        ScriptedTransport(
            [
                line("M115", error=OSError("controller write failed")),
                line("M5"),
            ]
        )
        for _attempt in range(3)
    )
    factory_calls = install_transports(monkeypatch, *transports)
    machine = MachineService(
        machine_settings("marlin"),
        LaserSettings(),
        hardware_enabled=True,
    )

    with pytest.raises(MachineError, match="failed while writing"):
        machine.connect()

    assert factory_calls == [("serial", "scripted-controller", 115200)] * 3
    for transport in transports:
        assert transport.writes == [("line", "M115"), ("line", "M5")]
        assert transport.events[-1] == ("close", None)
    status = machine.status()
    assert status["connected"] is False
    assert status["controller_reconnect_required"] is False
    assert status["coordinate_reference_ready"] is False
    assert status["armed"] is False
    for transport in transports:
        transport.assert_complete()


def test_initial_transient_open_failure_gets_one_fresh_transport_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = ScriptedTransport(
        [line("M5")],
        open_error=TransientConnectionError("temporary bridge failure"),
    )
    second = ScriptedTransport([*marlin_connect_steps(), *disconnect_steps()])
    factory_calls = install_transports(monkeypatch, first, second)
    machine = MachineService(
        machine_settings("marlin"),
        LaserSettings(),
        hardware_enabled=True,
    )
    monkeypatch.setattr(service_module, "_INITIAL_CONNECT_RETRY_DELAY_SECONDS", 0.0)

    status = machine.connect()

    assert status["connected"] is True
    assert factory_calls == [
        ("serial", "scripted-controller", 115200),
        ("serial", "scripted-controller", 115200),
    ]
    first.assert_complete()

    machine.disconnect()
    second.assert_complete()
