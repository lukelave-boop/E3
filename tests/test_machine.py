import time

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import SafetyError
from laser_aligner.machine.service import MachineService


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
