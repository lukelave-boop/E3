"""Resource-bound acceptance and rejection without controller access."""

import cv2
import numpy as np
import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import SafetyError
from laser_aligner.gcode.limits import MAX_STREAM_COMMANDS
from laser_aligner.machine.service import MachineService
from laser_aligner.project import Bounds, generate_project_gcode
from tests.test_toolpath import make_image_document


def _machine():
    return MachineService(
        MachineSettings(allow_motion=True), LaserSettings(power_max=1000),
        # Preflight only; this service is never connected to a transport.
        hardware_enabled=True,
    )


def test_detailed_raster_above_old_limit_generates_indexes_and_validates(tmp_path):
    path = tmp_path / "detailed.png"
    pixels = (np.indices((512, 512)).sum(axis=0) % 2 * 255).astype(np.uint8)
    assert cv2.imwrite(str(path), pixels)
    document = make_image_document(
        path, width_mm=51.2, height_mm=51.2, line_interval_mm=0.1,
        work_area=Bounds(0, 0, 100, 100),
    )
    job = generate_project_gcode(document, LaserSettings(power_max=1000))
    validated = _machine().preflight_program(job.text)
    assert 250_000 < len(validated.lines) <= MAX_STREAM_COMMANDS
    assert validated.requires_motion and validated.requires_laser_authorization
    assert validated.lines[-1] == "M5"
    assert job.plan is not None and job.plan.powered
    assert all(not (move.rapid and move.laser_on) for move in job.plan.moves)
    assert all(0 <= move.end_x <= 100 and 0 <= move.end_y <= 100 for move in job.plan.moves)


def test_machine_accepts_exact_command_ceiling_without_output():
    text = "G21\nG90\n" + "M5\n" * (MAX_STREAM_COMMANDS - 2)
    machine = _machine()
    validated = machine.preflight_program(text)
    assert len(validated.lines) == MAX_STREAM_COMMANDS
    assert not validated.requires_motion
    assert not validated.requires_laser_authorization
    assert not machine.connected


def test_machine_rejects_one_command_above_ceiling():
    text = "G21\nG90\n" + "M5\n" * (MAX_STREAM_COMMANDS - 1)
    with pytest.raises(SafetyError, match="1,000,000-line limit"):
        _machine().preflight_program(text)


def test_machine_still_checks_unsafe_tail_beyond_old_ceiling():
    text = "G21\nG90\n" + "M5\n" * 250_000 + "G91\nM5\n"
    with pytest.raises(SafetyError):
        _machine().preflight_program(text)
