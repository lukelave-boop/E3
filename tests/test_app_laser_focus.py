"""Application startup selects a machine-bound calibration store without hardware."""
import json

import pytest

from laser_aligner.app import AppContext
from laser_aligner.errors import MachineError
from laser_aligner.machine.laser_focus import focus_calibration_path
from tests.test_app_simulation import _settings


def test_context_uses_configuration_sidecar_without_connecting(tmp_path):
    settings = _settings(tmp_path)
    path = focus_calibration_path(settings.source_path)
    path.write_text(json.dumps({"schema_version": 1,
                                "controller_port": settings.machine.air_assist.port,
                                "calibration": None}))
    context = AppContext(settings)
    assert context.machine._laser_focus.path == path
    assert context.machine._laser_focus.calibration is None
    assert not context.machine.status()["connected"]


def test_bad_calibration_is_reported_instead_of_inventing_an_offset(tmp_path):
    settings = _settings(tmp_path)
    focus_calibration_path(settings.source_path).write_text('{"offset": 7}')
    with pytest.raises(MachineError, match="Invalid saved focus"):
        AppContext(settings)
