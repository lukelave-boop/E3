from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("PySide6")

from laser_aligner.desktop.setup_guide import read_setup_status
from laser_aligner.desktop.setup_instructions import progress_text
from laser_aligner.setup_workflow import SETUP_STEPS, evaluate_setup_steps


def setup_with_snapshot(snapshot):
    machine = SimpleNamespace(refresh_status=Mock(), setup_evidence_snapshot=Mock(return_value=snapshot))
    context = SimpleNamespace(
        machine=machine, precision_setup_binding=lambda: {}, precision_assessment_binding=lambda: {},
        camera_calibration_readiness=lambda: {"state": "READY", "expected_resolution": [800, 600]},
        lens=SimpleNamespace(model=SimpleNamespace(quality={"gate": "pass"}, image_size=(800, 600))),
        bed_status=lambda: {"calibrated": True}, solve_surface_height_model=lambda: None,
    )
    return SimpleNamespace(context=context)


def evidence():
    return {"calibration_compatible": True, "reference": {"border_z_mm": 0},
            "reference_id": "saved-reference", "controller_session": 3,
            "probe_xy_offset_mm": [3, 38], "firmware_geometry": {}}


def test_check_progress_refreshes_cleared_cache_and_recognizes_saved_gauge():
    setup = setup_with_snapshot(None)
    machine = setup.context.machine
    machine.refresh_status.side_effect = lambda: setattr(machine.setup_evidence_snapshot, "return_value", evidence())
    statuses, _ = read_setup_status(setup)
    machine.refresh_status.assert_called_once_with()
    assert statuses["gauge"].state == "complete"
    assert statuses["heights"].next_action == "heights"


def test_failed_refresh_does_not_reuse_previous_compatible_snapshot():
    setup = setup_with_snapshot(evidence())
    setup.context.machine.refresh_status.side_effect = RuntimeError("Pi status unavailable")
    statuses, errors = read_setup_status(setup)
    setup.context.machine.setup_evidence_snapshot.assert_not_called()
    assert "Pi status unavailable" in errors
    assert statuses["gauge"].state != "complete"
    message = progress_text(SETUP_STEPS[6], statuses["heights"])
    assert "do not repeat gauge teaching" in message
    assert "Finish Step 5" not in message


def test_missing_snapshot_does_not_fall_back_to_stale_widget_or_request_teaching():
    setup = setup_with_snapshot(None)
    setup.focus_workspace = SimpleNamespace(panel=SimpleNamespace(fresh=lambda: True, _result={"calibration_compatible": True}))
    statuses, _ = read_setup_status(setup)
    assert statuses["gauge"].state != "complete"
    assert "Current gauge status is unavailable" in statuses["heights"].reason


def test_incompatible_snapshot_requires_inspection_not_blind_reteaching():
    setup = setup_with_snapshot(dict(evidence(), calibration_compatible=False))
    statuses, _ = read_setup_status(setup)
    assert statuses["gauge"].state != "complete"
    assert "compatibility problem" in progress_text(SETUP_STEPS[6], statuses["heights"])


def test_saved_compatible_gauge_does_not_depend_on_repeating_bed_survey():
    statuses = evaluate_setup_steps(readiness={"lens": True, "bed": True, "gauge": True}, evidence={})
    assert statuses["datum"].state != "complete"
    assert statuses["gauge"].state == "complete"
    assert statuses["heights"].next_action == "heights"


def test_cached_guide_read_does_not_start_network_io():
    setup = setup_with_snapshot(evidence())
    statuses, _ = read_setup_status(setup, refresh_machine=False)
    setup.context.machine.refresh_status.assert_not_called()
    assert statuses["gauge"].state == "complete"
