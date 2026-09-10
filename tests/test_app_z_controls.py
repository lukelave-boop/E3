"""Application lifecycle keeps the configured Ender ceiling across restart."""
import json

import pytest

from laser_aligner.app import AppContext
from laser_aligner.errors import MachineError
from laser_aligner.machine.z_limits import mainboard_limits_path
from tests.test_app_simulation import _settings


def test_context_uses_saved_limit_without_connecting(tmp_path):
    settings = _settings(tmp_path)
    path = mainboard_limits_path(settings.source_path)
    path.write_text(json.dumps({
        "schema_version": 1, "controller_port": settings.machine.air_assist.port,
        "max_z_mm": 55.0,
    }))
    context = AppContext(settings)
    assert context.machine.settings.mainboard_max_z_mm == 55
    assert context.machine._mainboard_z_limits.path == path
    assert not context.machine.status()["connected"]
    restarted = AppContext(_settings(tmp_path))
    assert restarted.machine.settings.mainboard_max_z_mm == 55


def test_bad_saved_limit_prevents_fallback_to_higher_default(tmp_path):
    settings = _settings(tmp_path)
    mainboard_limits_path(settings.source_path).write_text('{"max_z_mm": 55}')
    with pytest.raises(MachineError, match="Invalid saved"):
        AppContext(settings)
