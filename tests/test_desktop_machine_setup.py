from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from laser_aligner.core import CoreRuntime
from laser_aligner.desktop.machine_setup import MachineSetupDialog
from laser_aligner.desktop.qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _runtime(tmp_path: Path) -> CoreRuntime:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "config" / "default.json").read_text(encoding="utf-8"))
    payload["app"]["data_dir"] = str(tmp_path / "data")
    payload["app"]["open_browser"] = False
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    runtime = CoreRuntime.from_config(path, hardware_enabled=False)
    runtime.start()
    return runtime


def test_machine_setup_exposes_native_camera_calibration_and_checks(
    qt_application: QtWidgets.QApplication, tmp_path: Path
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    try:
        assert [dialog.tabs.tabText(index) for index in range(dialog.tabs.count())] == [
            "1 · Camera",
            "2 · Lens",
            "3 · Bed mapping",
            "4 · Fine registration",
            "5 · Accuracy validation",
        ]
        assert dialog.synthetic_scene.isEnabled()
        assert dialog.runtime.hardware_enabled is False
        assert dialog.points.rowCount() >= 4
        assert "Solved" in dialog.bed_status.text()
        assert dialog.registration_results.horizontalHeaderItem(0).text() == "Use"
        button_text = {
            button.text() for button in dialog.findChildren(QtWidgets.QPushButton)
        }
        assert {
            "Prepare dry registration path",
            "Prepare powered mark job",
            "Home / park, precision capture",
            "Recapture without homing",
            "Apply reviewed translation",
            "Reset fine translation",
            "Apply reviewed full-bed map",
            "Reset full-bed refinement",
            "Prepare dry validation path",
            "Prepare powered validation job",
        }.issubset(button_text)
        assert not dialog.reverse_x.isChecked()
        assert not dialog.registration_recapture_button.isEnabled()
        assert not dialog.validation_recapture_button.isEnabled()
        dialog._set_photo_pose_confirmed(True)
        assert dialog.registration_recapture_button.isEnabled()
        assert dialog.validation_recapture_button.isEnabled()
        assert dialog.reverse_x.text() == "Reverse X mapping — OFF"
        assert "saved in the bed calibration" in dialog.axis_mapping_status.text()
        assert dialog.machine_connection_status.text().startswith("Machine connected")
        assert dialog.machine_connection_button.text() == "Disconnect machine"
    finally:
        dialog.close()
        runtime.stop()


def test_machine_setup_can_disconnect_and_reconnect_machine(
    qt_application: QtWidgets.QApplication, tmp_path: Path
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    try:
        dialog.toggle_machine_connection()
        assert not runtime.context.machine.status()["connected"]
        assert dialog.machine_connection_button.text() == "Connect machine"

        dialog.toggle_machine_connection()
        assert runtime.context.machine.status()["connected"]
        assert dialog.machine_connection_button.text() == "Disconnect machine"
    finally:
        dialog.close()
        runtime.stop()


def test_machine_setup_refreshes_raw_camera_and_lens_previews(
    qt_application: QtWidgets.QApplication, tmp_path: Path
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    try:
        dialog.refresh_camera()
        dialog.refresh_lens_preview()
        assert dialog.camera_preview._image is not None
        assert dialog.lens_preview._image is not None
    finally:
        dialog.close()
        runtime.stop()


def test_machine_setup_can_add_and_delete_manual_bed_point(
    qt_application: QtWidgets.QApplication, tmp_path: Path
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    try:
        original = dialog.points.rowCount()
        dialog.image_x.setValue(100)
        dialog.image_y.setValue(120)
        dialog.machine_x.setValue(20)
        dialog.machine_y.setValue(30)
        dialog.point_label.setText("manual")
        dialog.add_bed_point()
        assert dialog.points.rowCount() == original + 1
        dialog.points.selectRow(original)
        dialog.delete_bed_point()
        assert dialog.points.rowCount() == original
    finally:
        dialog.close()
        runtime.stop()


def test_machine_setup_prepares_dry_registration_through_main_job_signal(
    qt_application: QtWidgets.QApplication, tmp_path: Path
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    prepared = []
    dialog.registrationJobPrepared.connect(prepared.append)
    try:
        dialog.prepare_registration_job(False)
        assert len(prepared) == 1
        job = prepared[0]
        assert job.powered is False
        assert len(job.targets) == 8
        assert "M3 " not in job.program.text
        assert "M4 " not in job.program.text
        assert (runtime.settings.app.data_dir / "fine_registration.json").exists()
    finally:
        dialog.close()
        runtime.stop()


def test_machine_setup_prepares_dry_validation_through_main_job_signal(
    qt_application: QtWidgets.QApplication, tmp_path: Path
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    prepared = []
    dialog.validationJobPrepared.connect(prepared.append)
    try:
        dialog.prepare_accuracy_validation_job(False)
        assert len(prepared) == 1
        job = prepared[0]
        assert job.display_name == "Accuracy validation"
        assert job.powered is False
        assert len(job.targets) == 5
        assert "accuracy-validation-holdout-crosses" in job.program.text
        assert (
            runtime.settings.app.data_dir / "accuracy_validation.json"
        ).exists()
    finally:
        dialog.close()
        runtime.stop()


def test_machine_setup_shows_reviewed_registration_exclusion_checkbox(
    qt_application: QtWidgets.QApplication, tmp_path: Path
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    measurements = [
        {
            "id": index,
            "machine_x": float(index * 10),
            "machine_y": float(index * 12),
            "observed_x": float(index * 10 - 2),
            "observed_y": float(index * 12 - 1),
            "error_x_mm": -2.0,
            "error_y_mm": -1.0,
        }
        for index in range(1, 9)
    ]
    try:
        dialog._populate_registration_results(measurements, {7})
        assert dialog.registration_results.item(0, 0).checkState() == QtCore.Qt.CheckState.Checked
        assert dialog.registration_results.item(6, 0).checkState() == QtCore.Qt.CheckState.Unchecked
        assert "incorrectly detected cross" in dialog.registration_results.item(6, 0).toolTip()
    finally:
        dialog.close()
        runtime.stop()


def test_machine_setup_source_covers_browser_only_shared_operations() -> None:
    source = (Path(__file__).resolve().parents[1] / "laser_aligner" / "desktop" / "machine_setup.py").read_text(
        encoding="utf-8"
    )
    for operation in (
        "apply_configured_controls",
        "lens.capture",
        "lens.solve",
        "capture_bed_reference",
        "bed.add_point",
        "detect_bed_cross_grid",
        "prepare_fine_registration_job",
        "context.capture_fine_registration",
        "apply_fine_registration",
        "apply_fine_registration_homography",
        "prepare_accuracy_validation_job",
        "context.capture_accuracy_validation",
        "solve_bed",
        "detect_workpiece",
        "detect_fiducials",
        "synthetic_scene",
    ):
        assert operation in source


def test_machine_setup_restores_non_power_preferences(
    qt_application: QtWidgets.QApplication, tmp_path: Path
) -> None:
    runtime = _runtime(tmp_path)
    first = MachineSetupDialog(runtime)
    try:
        first.tabs.setCurrentIndex(4)
        first.registration_mark_size.setValue(7.0)
        first.registration_speed.setValue(777.0)
        first.registration_power.setValue(25.0)
        first.validation_mark_size.setValue(6.0)
        first.validation_speed.setValue(888.0)
        first.validation_power.setValue(30.0)
        first.close()

        second = MachineSetupDialog(runtime)
        try:
            assert second.tabs.currentIndex() == 4
            assert second.registration_mark_size.value() == 7.0
            assert second.registration_speed.value() == 777.0
            assert second.validation_mark_size.value() == 6.0
            assert second.validation_speed.value() == 888.0
            assert second.registration_power.value() == 0.0
            assert second.validation_power.value() == 0.0
        finally:
            second.close()
    finally:
        runtime.stop()


def test_machine_setup_reopens_with_saved_reversed_axis_highlighted(
    qt_application: QtWidgets.QApplication, tmp_path: Path
) -> None:
    runtime = _runtime(tmp_path)
    runtime.context.bed.set_machine_axis_reversed("x", True)
    first = MachineSetupDialog(runtime)
    try:
        assert first.reverse_x.isChecked()
        assert first.reverse_x.text() == "Reverse X mapping — ON"
        assert "saved in the bed calibration" in first.axis_mapping_status.text()
        first.close()

        reopened = MachineSetupDialog(runtime)
        try:
            assert reopened.reverse_x.isChecked()
            assert reopened.reverse_x.text() == "Reverse X mapping — ON"
        finally:
            reopened.close()
    finally:
        runtime.stop()
