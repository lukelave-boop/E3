from __future__ import annotations

import os
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from laser_aligner.config import WorkArea
from laser_aligner.desktop.laser_focus import LaserFocusPanel
from laser_aligner.desktop.machine_setup import ImagePicker
from laser_aligner.desktop.precision_setup import BedSurveyDialog, PrecisionSetupDialog
from laser_aligner.desktop.qt import require_qt
from laser_aligner.desktop.setup_guide import SetupGuideDialog
from laser_aligner.desktop.surface_height import SurfaceHeightDialog
from laser_aligner.setup_workflow import PrecisionEvidenceStore, PrecisionQualification
from tests.test_desktop_laser_focus import result, status

QtCore, _, QtWidgets = require_qt()


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


class FakeSetup(QtWidgets.QWidget):
    def __init__(self, directory):
        super().__init__()
        self.binding = {"camera": "camera-1", "model": "model-1"}
        self.surface = "surface-1"
        self.calls = []
        self._photo_pose_confirmed = False
        self.context = SimpleNamespace(
            surface_calibration=SimpleNamespace(path=directory / "surface.json"),
            settings=SimpleNamespace(machine=SimpleNamespace(work_area=WorkArea(10, 210, 10, 210))),
            precision_setup_binding=lambda: self.binding,
            precision_assessment_binding=lambda: dict(self.binding, surface=self.surface),
            capture_accuracy_validation=self.capture,
        )
        self.tabs = QtWidgets.QTabWidget()
        for _ in range(7):
            self.tabs.addTab(QtWidgets.QWidget(), "step")

    def capture(self, *, home_first, material_plane):
        self.calls.append((home_first, material_plane))
        return np.zeros((8, 8, 3), dtype=np.uint8), {
            "detected": True, "confidence": "high", "analysis": {
                "measurements": [{"id": index, "observed_x": float(index), "observed_y": 2.}
                                 for index in range(5)],
            },
        }

    def _invalidate_for_home_park(self):
        self._photo_pose_confirmed = False

    def _accuracy_validation_captured(self, result, *, home_first):
        if home_first:
            self._photo_pose_confirmed = True

    def _start_operation(self, name, operation, success, **options):
        if options.get("invalidate"):
            options["invalidate"]()
        success(operation())
        return True

    def navigate_setup_step(self, action):
        self.calls.append(action)


def test_assessment_requires_twenty_explicit_captures_and_rejects_surface_change(app, tmp_path):
    setup = FakeSetup(tmp_path)
    dialog = PrecisionSetupDialog(setup)
    dialog.start_assessment.click()
    assert not setup.calls
    assert not dialog.still_capture.isEnabled()
    dialog.prime_capture.click()
    assert setup.calls == [(True, True)]
    assert not dialog.assessment.still
    for _ in range(10):
        dialog.still_capture.click()
    assert len(dialog.assessment.still) == 10
    assert len(setup.calls) == 11
    setup.surface = "surface-2"
    dialog.park_capture.click()
    assert "identity changed" in dialog.status.text()
    assert len(setup.calls) == 11
    setup.surface = "surface-1"
    for _ in range(10):
        dialog.park_capture.click()
    assert dialog.assessment.summary()["complete"]
    assert len(setup.calls) == 21
    assert not dialog.park_capture.isEnabled()
    dialog.close()
    setup.close()


def test_physical_form_does_not_turn_blank_cells_or_stale_rows_into_a_pass(app, tmp_path):
    setup = FakeSetup(tmp_path)
    dialog = PrecisionSetupDialog(setup)
    dialog.method.setText("Independent optical scale measurement on the stated target")
    dialog.save_qualification.click()
    assert "Confirm" in dialog.status.text()
    dialog.confirm_physical.setChecked(True)
    dialog.save_qualification.click()
    assert "NOT QUALIFIED" in dialog.status.text()
    for row in range(9):
        x = (10, 110, 210)[row % 3]
        y = (10, 110, 210)[row // 3]
        for column, value in enumerate((str(x), str(y), "0.030", "0.040", "0.020"), 2):
            dialog.qual_table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
    dialog.save_qualification.click()
    assert "QUALIFIED:" in dialog.status.text()
    assert "0.070" in dialog.status.text()
    saved = PrecisionQualification.from_dict(dialog.store.load()["qualification"])
    assert saved.review(setup.binding)["passed"]
    assert not saved.review(setup.binding, height_range=(0, 1))["passed"]
    original_time = saved.observations[0].observed_at
    dialog.save_qualification.click()
    assert dialog.store.load()["qualification"]["observations"][0]["observed_at"] == original_time
    dialog.upper.setValue(1)
    dialog.save_qualification.click()
    assert "instead of relabeling" in dialog.status.text()
    dialog.upper.setValue(0)
    setup.binding = dict(setup.binding, model="replaced")
    dialog.save_qualification.click()
    assert "another setup" in dialog.status.text()
    dialog._new_qualification()
    assert dialog.qual_table.item(0, 2) is None
    assert not dialog.confirm_physical.isChecked()
    assert not setup.calls
    dialog.close()
    setup.close()


def test_guide_opens_real_steps_without_running_motion(app, tmp_path):
    setup = FakeSetup(tmp_path)
    guide = SetupGuideDialog(setup)
    guide.step_buttons["datum"].click()
    assert setup.calls == ["datum"]
    guide.step_checks["mechanics"].setChecked(True)
    assert guide._store.load()["checklist"] == ["mechanics"]
    guide.close()
    setup.close()


def test_survey_only_copies_complete_current_results_into_editor(app, tmp_path):
    panel = LaserFocusPanel(calibration_mode=True)
    panel.precision_evidence_store = PrecisionEvidenceStore(tmp_path)
    panel._status = status()
    payload = result(reference={"border_z_mm": 0.}, reference_id="border-reference-1")
    payload["surface"] = dict(payload["surface"], carriage_xy_mm=[0., 0.])
    panel.set_result(payload)
    survey = BedSurveyDialog(panel)
    panel._bed_survey_dialog = survey
    events = []
    panel.actionRequested.connect(lambda *args: events.append(args))
    survey.thickness.setValue(5.)
    survey.record.click()
    assert not survey.use_mean.isEnabled()
    assert not events
    for index in range(1, 5):
        payload = result(reference={"border_z_mm": 0.}, reference_id="border-reference-1")
        xy = ((0, 0), (100, 0), (100, 100), (0, 100), (50, 50))[index]
        payload["surface"] = dict(payload["surface"], id=f"surface-{index + 1}", elevation_mm=3.5,
                                   carriage_xy_mm=list(xy))
        panel.set_result(payload)
        survey.record.click()
    assert survey.use_mean.isEnabled()
    survey.use_mean.click()
    assert not events
    assert "No command was sent" in survey.summary.text()
    assert panel.honeycomb_height.value() == pytest.approx(-.98)
    assert panel.precision_evidence_store.load()["bed_survey"]["saved_datum"] is None
    panel.set_result(dict(payload, action="set_honeycomb_height", honeycomb_height_mm=-.98))
    saved = panel.precision_evidence_store.load()["bed_survey"]
    assert saved["saved_datum"]["value_mm"] == -.98
    assert saved["observations"][0]["carriage_xy_mm"] == [0., 0.]
    payload["reference_id"] = "border-reference-2"
    panel.set_result(payload)
    survey.use_mean.click()
    assert "Reference changed" in survey.summary.text()
    survey.close()
    panel.close()


def test_height_capture_recovers_exact_prepared_metadata_and_saves_without_base_map(app, tmp_path):
    calls = []
    context = SimpleNamespace(
        surface_calibration=SimpleNamespace(path=tmp_path / "height.json", load=lambda: {}),
        prepare_surface_height_capture=lambda *args, **kwargs: None,
        surface_calibration_binding=lambda: {"optics": "same"},
        save_surface_height_evidence=lambda *args: calls.append(("save", args)),
        enable_material_height_correction=lambda enabled: calls.append(("enable", enabled)),
        begin_surface_height_calibration=lambda reference: calls.append(("begin", reference)),
    )
    dialog = SurfaceHeightDialog(context, ImagePicker(), None)
    detection = {"detected": True, "points": [], "acquisition": {
        "slot": "upper", "height_mm": 18.5, "uncertainty_mm": .04, "reference": "fixed border",
    }}
    dialog._captured((np.zeros((1080, 1920, 3), dtype=np.uint8), detection))
    assert dialog.slot.currentData() == "upper"
    assert dialog.height.value() == 18.5
    assert dialog.uncertainty.value() == .04
    dialog.save_capture_button.click()
    assert calls == [("save", ("upper", 18.5, "fixed border", .04, detection))]
    assert not dialog.save_capture_button.isEnabled()
    assert "without replacing the support map" in dialog.result_status.text()
    dialog.enable_button.click()
    assert calls[-1] == ("enable", True)
    dialog.disable_button.click()
    assert calls[-1] == ("enable", False)
    dialog.close()


def test_failed_height_capture_does_not_offer_save(app, tmp_path):
    context = SimpleNamespace(surface_calibration=SimpleNamespace(load=lambda: {}))
    dialog = SurfaceHeightDialog(context, ImagePicker(), None)
    dialog._captured((np.zeros((8, 8, 3), dtype=np.uint8),
                      {"detected": False, "reason": "No independently identified target"}))
    assert not dialog.save_capture_button.isEnabled()
    assert "No independently identified" in dialog.result_status.text()
    dialog.close()


def test_checklist_uses_validated_setup_snapshot_after_navigation_and_retained_home(app, tmp_path):
    from laser_aligner.setup_workflow import SURVEY_REGIONS, BedSurvey, SurveyObservation, binding_json

    setup = FakeSetup(tmp_path)
    setup.binding["honeycomb_height_mm"] = -1.5
    panel = LaserFocusPanel(calibration_mode=True)
    panel._status = status()
    panel.set_result(result(reference={"border_z_mm": 0.}, reference_id="reference-1"))
    setup.focus_workspace = SimpleNamespace(panel=panel)
    snapshot = {
        "reference": {"border_z_mm": 0.}, "reference_id": "reference-1", "controller_session": 8,
        "probe_xy_offset_mm": [3., 38.], "firmware_geometry": {"probe_z_mm": -2.},
        "calibration_compatible": True,
    }
    setup.context.machine = SimpleNamespace(setup_evidence_snapshot=lambda: snapshot)
    setup.context.camera_calibration_readiness = lambda: {"state": "READY", "expected_resolution": [1920, 1080]}
    setup.context.lens = SimpleNamespace(model=SimpleNamespace(quality={"gate": "pass"}, image_size=(1920, 1080)))
    setup.context.bed_status = lambda: {"calibrated": True}
    survey_identity = binding_json({
        "reference": snapshot["reference"], "reference_id": snapshot["reference_id"],
        "controller_session": snapshot["controller_session"], "probe_offset": snapshot["probe_xy_offset_mm"],
        "firmware_geometry": snapshot["firmware_geometry"],
    })
    survey = BedSurvey(survey_identity)
    for index, region in enumerate(SURVEY_REGIONS):
        xy = ((0, 0), (100, 0), (100, 100), (0, 100), (50, 50))[index]
        survey.record(SurveyObservation(region, f"probe-{index}", 3.5, 5., xy, 100.), survey_identity)
    survey.associate_saved_datum(-1.5, 200., survey_identity)
    PrecisionEvidenceStore(tmp_path).save(bed_survey=survey)
    guide = SetupGuideDialog(setup)
    assert guide.step_statuses["datum"].state == "complete"
    panel.invalidate("Z controls paused while another setup tab is active")
    assert not panel.fresh()
    guide.refresh_status()
    assert guide.step_statuses["datum"].state == guide.step_statuses["gauge"].state == "complete"
    # Successful Home clears immediate probe controls but keeps validated datum evidence.
    panel.set_result(result(reference=None, reference_id=None, reference_ready=False))
    guide.refresh_status()
    assert guide.step_statuses["datum"].state == "complete"
    snapshot["controller_session"] = 9
    guide.refresh_status()
    assert guide.step_statuses["datum"].state == "stale"
    setup.context.machine.setup_evidence_snapshot = lambda: None
    guide.refresh_status()
    assert guide.step_statuses["datum"].state == "review"
    assert guide.step_statuses["gauge"].state == "blocked"
    assert not setup.calls
    guide.close()
    panel.close()
    setup.close()
