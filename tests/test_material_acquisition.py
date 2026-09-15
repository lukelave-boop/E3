from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

from laser_aligner.errors import CalibrationError
from laser_aligner.material_workspace import MaterialWorkspaceMixin
from laser_aligner.storage import atomic_write_json


@pytest.fixture
def acquisition(tmp_path):
    class Context(MaterialWorkspaceMixin):
        pass
    context = Context()
    measurement = {
        "id": "plan-one", "measurement_id": "surface-one", "reusable": True,
        "contact_z_mm": 4., "surface_elevation_mm": 4., "honeycomb_height_mm": -1.5,
        "reference": {"border_z_mm": 0., "firmware": "fixed", "geometry": {}}, "session": [1, 2, 3],
    }
    context.machine = SimpleNamespace(material_surface_snapshot=lambda: copy.deepcopy(measurement))
    context.surface_calibration = SimpleNamespace(path=tmp_path / "surface_height_calibration.json")
    context.bed = SimpleNamespace(calibration=object())
    binding = {"datum_id": "border", "mount_revision": "mount-one"}
    context.production_surface_binding = lambda *args: copy.deepcopy(binding)
    support = {"honeycomb_execution_signature": ["support", 1, "corners", "bed"],
               "honeycomb_support_corners_machine_mm": [[0, 0], [100, 0], [100, 100], [0, 100]]}
    context._required_calibration_support = lambda **kwargs: object()
    context._calibration_support_fields = lambda _support: copy.deepcopy(support)
    context._require_session_bed_mapping = lambda *args: None
    context._require_session_execution = lambda *args: {"powered": True}
    session = {
        "schema_version": 1, "binding": binding, "targets": [{}] * 25, "powered": True,
        "height_mm": 4., "uncertainty_mm": .01,
        "measurement": context._surface_acquisition_measurement(4., .01), **support,
    }
    atomic_write_json(context.surface_capture_path, session)
    return context, measurement, support, session


def test_valid_measurement_uses_signed_top_including_supports(acquisition):
    context, measurement, _, session = acquisition
    before = copy.deepcopy(measurement)
    assert context._surface_capture_session() == session
    assert measurement == before
    context._surface_acquisition_measurement(4.0105, .01)
    with pytest.raises(CalibrationError, match="disagrees"):
        context._surface_acquisition_measurement(4.0106, .01)


@pytest.mark.parametrize("height,uncertainty", [(float("nan"), .01), (True, .01), (4., -1), (4., 1.01), (4., float("inf"))])
def test_invalid_entered_calibration_values_reject(acquisition, height, uncertainty):
    with pytest.raises(CalibrationError):
        acquisition[0]._surface_acquisition_measurement(height, uncertainty)


@pytest.mark.parametrize("change", ["missing", "measurement", "reference", "session", "honeycomb", "support", "height", "out_of_range"])
@pytest.mark.parametrize("require_executed", [False, True])
def test_prestart_capture_and_save_reject_changed_acquisition_context(acquisition, change, require_executed):
    context, measurement, support, session = acquisition
    if change == "missing":
        context.machine.material_surface_snapshot = lambda: None
    elif change == "measurement":
        measurement["measurement_id"] = "replacement"
    elif change == "reference":
        measurement["reference"]["firmware"] = "changed"
    elif change == "session":
        measurement["session"][2] += 1
    elif change == "honeycomb":
        measurement["honeycomb_height_mm"] = -1.
    elif change == "support":
        support["honeycomb_execution_signature"][2] = "moved corners"
    elif change == "height":
        session["height_mm"] = 6.
        atomic_write_json(context.surface_capture_path, session)
    else:
        measurement.update(contact_z_mm=19., surface_elevation_mm=19.)
    with pytest.raises(CalibrationError):
        context._surface_capture_session(require_executed=require_executed)


def test_ungauged_probe_and_above_limit_cannot_create_powered_acquisition(acquisition):
    context, measurement, _, _ = acquisition
    measurement["reusable"] = False
    with pytest.raises(CalibrationError, match="reusable"):
        context._surface_acquisition_measurement(4., .01)
    measurement.update(reusable=True, contact_z_mm=19., surface_elevation_mm=19.)
    with pytest.raises(CalibrationError, match="0 to 20"):
        context._surface_acquisition_measurement(19., .01)


def test_retained_focus_xy_changes_preserve_acquisition_identity(acquisition):
    context, measurement, _, session = acquisition
    measurement.update(xy=[12, 18], parked_at_clearance=True)
    assert context._surface_capture_session() == session
