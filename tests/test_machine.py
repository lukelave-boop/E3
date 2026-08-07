import time

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import MachineError, SafetyError
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
        machine.arm(machine.ARM_PHRASE)
        machine.start_job("G21\nG90\nM5\nG0 X10 Y10\nM4 S5\nG1 X20 Y20\nM5\n", "test.gcode")
        wait_for_job(machine)
        status = machine.status()["job"]
        assert not status["running"]
        assert status["error"] is None
        assert status["completed_lines"] == status["total_lines"]
        assert not machine.status()["armed"]
    finally:
        machine.disconnect()


def test_dry_program_does_not_require_arming() -> None:
    machine = MachineService(MachineSettings(backend="simulator"), LaserSettings(), hardware_enabled=False)
    machine.connect()
    try:
        machine.start_job("G21\nG90\nM5\nG0X10Y10F1000\nG1X20Y20F500\nM5\n", "dry.gcode")
        wait_for_job(machine)
        assert machine.status()["job"]["error"] is None
    finally:
        machine.disconnect()


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
    ) -> list[str]:
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
        lambda timeout: (_ for _ in ()).throw(
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


def test_disarm_stops_an_active_laser_job() -> None:
    machine = MachineService(MachineSettings(backend="simulator"), LaserSettings(), hardware_enabled=False)
    machine.connect()
    try:
        lines = ["G21", "G90", "M5", "G0 X10 Y10 F1000", "M4 S5"]
        for index in range(2000):
            coordinate = 20 if index % 2 else 10
            lines.append(f"G1 X{coordinate} Y{coordinate} F500")
        lines.append("M5")
        machine.arm(machine.ARM_PHRASE)
        machine.start_job("\n".join(lines), "long.gcode")
        time.sleep(0.01)
        machine.disarm()
        wait_for_job(machine)
        status = machine.status()
        assert not status["armed"]
        assert not status["job"]["running"]
        assert status["job"]["error"] == "Job stopped"
    finally:
        machine.disconnect()
