from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import json
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from laser_aligner.calibration.support import HoneycombSupportReference
from laser_aligner.config import WorkArea
from laser_aligner.core import CoreRuntime
from laser_aligner.desktop.controller import (
    _honeycomb_support_metadata,
    _usable_template_detections,
)
from laser_aligner.desktop.machine_setup import (
    ImagePicker,
    MachineSetupDialog,
    _work_area_reference_overlay,
)
from laser_aligner.desktop.qt import require_qt
from laser_aligner.desktop.setup_guide import SetupGuideDialog
from laser_aligner.desktop.tasks import FunctionTask
from laser_aligner.vision.ruler import HoneycombRulerDetection, RulerAxisDetection

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


def _wait_until(
    application: QtWidgets.QApplication,
    predicate,
    *,
    timeout: float = 3.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for Qt background operation")
        time.sleep(0.005)


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
        assert dialog.work_area_reference_button.text() == "Capture view"
        assert "Home and park" in dialog.work_area_reference_button.toolTip()
        assert "Camera/work: X0..220, Y0..220 mm" in (
            dialog.work_area_reference_status.text()
        )
        assert "Guarded laser output after 0 mm margin: X0..220, Y0..220 mm" in (
            dialog.work_area_reference_status.text()
        )
        label_text = {
            label.text() for label in dialog.findChildren(QtWidgets.QLabel)
        }
        assert {"Camera / work boundary", "Guarded laser output"}.issubset(
            label_text
        )
        assert "Detected honeycomb rulers" in label_text
        assert dialog.honeycomb_support_auto_button.text() == (
            "Detect honeycomb automatically"
        )
        assert dialog.honeycomb_support_record_button.text() == (
            "Fallback: detect with 3 hints"
        )
        assert "not recorded" in dialog.honeycomb_support_status.text()
        assert dialog.points.rowCount() >= 4
        assert "Solved" in dialog.bed_status.text()
        assert dialog.registration_results.horizontalHeaderItem(0).text() == "Use"
        button_text = {
            button.text() for button in dialog.findChildren(QtWidgets.QPushButton)
        }
        assert {
            "Setup guide",
            "Prepare powered base-map job",
            "Home / park, capture and detect base grid",
            "Prepare powered mark job",
            "Home / park, precision capture",
            "Recapture without homing",
            "Apply reviewed translation",
            "Reset fine translation",
            "Apply reviewed full-bed map",
            "Reset full-bed refinement",
            "Prepare powered validation job",
        }.issubset(button_text)
        assert not any("dry" in text.lower() for text in button_text)
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
        camera = runtime.context.camera.status()
        assert f"{camera.negotiated_fps:.1f} fps negotiated" in dialog.camera_status.text()
        assert dialog.base_grid_power.value() == 0
        assert dialog.base_grid_mark_size.maximum() == 5
    finally:
        dialog.close()
        runtime.stop()


def test_work_area_reference_overlay_projects_grid_and_both_boundaries() -> None:
    image = np.zeros((240, 240, 3), dtype=np.uint8)

    class IdentityBed:
        @staticmethod
        def mm_to_image(machine_x: float, machine_y: float) -> tuple[float, float]:
            return machine_x * 4.0 + 20.0, 220.0 - machine_y * 4.0

    preview = _work_area_reference_overlay(
        image,
        IdentityBed(),
        WorkArea(10.0, 40.0, 10.0, 40.0),
        5.0,
    )

    assert preview.shape == image.shape
    assert np.any(preview != image)
    # At machine Y25, the configured X-min and guarded X-min boundaries are
    # projected to image X60 and X80 respectively, away from coordinate labels.
    assert tuple(int(value) for value in preview[120, 60]) == (0, 165, 255)
    assert tuple(int(value) for value in preview[120, 80]) == (70, 220, 90)

    offset_preview = _work_area_reference_overlay(
        image,
        IdentityBed(),
        WorkArea(10.0, 40.0, 10.0, 40.0),
        5.0,
        spot_offset_x_mm=-2.0,
        spot_offset_y_mm=3.0,
    )
    # The offset-aware guarded X maximum is machine X33 (image X152), not the
    # margin-only X35 boundary (image X160).
    assert tuple(int(value) for value in offset_preview[120, 152]) == (70, 220, 90)
    assert tuple(int(value) for value in offset_preview[120, 160]) != (70, 220, 90)


def test_work_area_reference_overlay_draws_support_and_picked_points() -> None:
    image = np.zeros((240, 240, 3), dtype=np.uint8)

    class IdentityBed:
        @staticmethod
        def mm_to_image(machine_x: float, machine_y: float) -> tuple[float, float]:
            return machine_x * 4.0 + 20.0, 220.0 - machine_y * 4.0

    support = HoneycombSupportReference.from_observations(
        ruler_origin_machine_mm=(12.0, 12.0),
        ruler_x_mark_machine_mm=(32.0, 12.0),
        ruler_xy_mark_machine_mm=(32.0, 32.0),
        ruler_mark_mm=20.0,
        support_width_mm=20.0,
        support_height_mm=20.0,
        created_at=1.0,
        bed_calibration_created_at=1.0,
    )
    preview = _work_area_reference_overlay(
        image,
        IdentityBed(),
        WorkArea(10.0, 40.0, 10.0, 40.0),
        5.0,
        support_reference=support,
        picked_image_points=((120.0, 120.0),),
    )

    assert tuple(int(value) for value in preview[132, 68]) == (220, 95, 205)
    assert tuple(int(value) for value in preview[120, 120]) == (0, 225, 255)


def test_image_picker_zoom_keeps_source_pixel_mapping(
    qt_application: QtWidgets.QApplication,
) -> None:
    picker = ImagePicker()
    picker.resize(600, 400)
    picker.show()
    picker.set_image(np.zeros((100, 200, 3), dtype=np.uint8))
    qt_application.processEvents()
    cursor = QtCore.QPointF(450.0, 230.0)
    wheel = QtGui.QWheelEvent(
        cursor,
        cursor,
        QtCore.QPoint(),
        QtCore.QPoint(0, 120),
        QtCore.Qt.MouseButton.NoButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        QtCore.Qt.ScrollPhase.ScrollUpdate,
        False,
    )
    picker.wheelEvent(wheel)
    assert picker.zoom_factor == pytest.approx(1.25)

    picked: list[tuple[float, float]] = []
    picker.pointPicked.connect(lambda x, y: picked.append((x, y)))
    click = QtGui.QMouseEvent(
        QtCore.QEvent.Type.MouseButtonPress,
        cursor,
        cursor,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
    )
    picker.mousePressEvent(click)
    expected_x = (
        (cursor.x() - picker._display_rect.x())
        * 200.0
        / picker._display_rect.width()
    )
    expected_y = (
        (cursor.y() - picker._display_rect.y())
        * 100.0
        / picker._display_rect.height()
    )
    assert picked == [pytest.approx((expected_x, expected_y))]
    picker.close()


def test_image_picker_clockwise_view_rotation_preserves_source_click_coordinates(
    qt_application: QtWidgets.QApplication,
) -> None:
    picker = ImagePicker(rotation_degrees=90)
    picker.resize(400, 600)
    picker.show()
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    image[20, 30] = (10, 20, 30)
    picker.set_image(image)
    qt_application.processEvents()

    assert picker._image is not None
    assert (picker._image.width(), picker._image.height()) == (100, 200)
    display_x = 99.0 - 20.0
    display_y = 30.0
    cursor = QtCore.QPointF(
        picker._display_rect.x()
        + display_x * picker._display_rect.width() / picker._image.width(),
        picker._display_rect.y()
        + display_y * picker._display_rect.height() / picker._image.height(),
    )
    picked: list[tuple[float, float]] = []
    picker.pointPicked.connect(lambda x, y: picked.append((x, y)))
    picker.mousePressEvent(
        QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            cursor,
            cursor,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
    )

    assert picked == [pytest.approx((30.0, 20.0))]
    picker.close()


@pytest.mark.parametrize(
    "button",
    [QtCore.Qt.MouseButton.MiddleButton, QtCore.Qt.MouseButton.RightButton],
)
def test_image_picker_pan_buttons_never_place_a_point(
    qt_application: QtWidgets.QApplication,
    button: QtCore.Qt.MouseButton,
) -> None:
    picker = ImagePicker()
    picker.resize(600, 400)
    picker.show()
    picker.set_image(np.zeros((100, 200, 3), dtype=np.uint8))
    qt_application.processEvents()
    picked: list[tuple[float, float]] = []
    picker.pointPicked.connect(lambda x, y: picked.append((x, y)))
    start = QtCore.QPointF(300.0, 200.0)
    end = QtCore.QPointF(340.0, 225.0)

    picker.mousePressEvent(
        QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonPress,
            start,
            start,
            button,
            button,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
    )
    picker.mouseMoveEvent(
        QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseMove,
            end,
            end,
            QtCore.Qt.MouseButton.NoButton,
            button,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
    )
    picker.mouseReleaseEvent(
        QtGui.QMouseEvent(
            QtCore.QEvent.Type.MouseButtonRelease,
            end,
            end,
            button,
            QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
        )
    )

    assert picked == []
    assert picker._pan != QtCore.QPointF()
    picker.close()


def test_honeycomb_hint_mode_hides_diagnostic_overlay_and_preserves_zoom(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    try:
        calibration = runtime.context.bed.calibration
        assert calibration is not None
        raw = np.full(
            (calibration.image_height, calibration.image_width, 3),
            (17, 31, 49),
            dtype=np.uint8,
        )
        dialog._bed_image = raw
        dialog._work_area_reference_calibration = calibration
        monkeypatch.setattr(
            "laser_aligner.desktop.machine_setup._work_area_reference_overlay",
            lambda image, *_args, **_kwargs: np.full_like(image, 240),
        )
        dialog._render_work_area_reference_preview()
        assert dialog.bed_preview._image.pixelColor(10, 10).red() == 240
        dialog.bed_preview._zoom = 2.0

        dialog.toggle_honeycomb_support_picking()

        pixel = dialog.bed_preview._image.pixelColor(10, 10)
        assert (pixel.red(), pixel.green(), pixel.blue()) == (49, 31, 17)
        assert dialog.bed_preview.zoom_factor == pytest.approx(2.0)
    finally:
        dialog.close()
        runtime.stop()


def test_machine_setup_uses_hints_only_to_detect_honeycomb_support(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    try:
        calibration = runtime.context.bed.calibration
        assert calibration is not None
        calibration_before = calibration.to_dict()
        work_area_before = runtime.settings.machine.work_area
        dialog._bed_image = np.zeros(
            (calibration.image_height, calibration.image_width, 3), dtype=np.uint8
        )
        dialog._work_area_reference_calibration = calibration
        dialog._refresh_work_area_reference_status()
        assert dialog.honeycomb_support_record_button.isEnabled()

        detected_image_points = tuple(
            runtime.context.bed.mm_to_image(*machine_point)
            for machine_point in ((10.0, 10.0), (200.0, 10.0), (200.0, 200.0))
        )
        detection = HoneycombRulerDetection(
            ruler_origin_image_px=detected_image_points[0],
            ruler_x_mark_image_px=detected_image_points[1],
            ruler_xy_mark_image_px=detected_image_points[2],
            axis_x=RulerAxisDetection(
                detected_image_points[0],
                detected_image_points[1],
                4.0,
                191,
                0.94,
                12.0,
                9.0,
            ),
            axis_y=RulerAxisDetection(
                detected_image_points[1],
                detected_image_points[2],
                4.0,
                191,
                0.92,
                9.0,
                11.0,
            ),
            corner_error_px=9.0,
            axis_angle_deg=89.8,
        )
        candidate = HoneycombSupportReference.from_observations(
            ruler_origin_machine_mm=(10.0, 10.0),
            ruler_x_mark_machine_mm=(200.0, 10.0),
            ruler_xy_mark_machine_mm=(200.0, 200.0),
            ruler_mark_mm=190.0,
            support_width_mm=190.0,
            support_height_mm=190.0,
            bed_calibration_created_at=calibration.created_at,
        )
        received: dict[str, object] = {}

        def detect_reference(image, hints, *, ruler_mark_mm):
            received["image"] = image.copy()
            received["hints"] = hints
            received["mark"] = ruler_mark_mm
            return candidate, detection

        monkeypatch.setattr(
            runtime.context,
            "detect_honeycomb_support_reference",
            detect_reference,
        )
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "warning",
            lambda *_args, **_kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
        )
        dialog.toggle_honeycomb_support_picking()
        assert dialog.honeycomb_support_record_button.text() == (
            "Cancel support picking"
        )
        assert "Hint 1" in dialog.honeycomb_support_status.text()

        rough_hints = ((30.0, 40.0), (70.0, 180.0), (205.0, 215.0))
        for point in rough_hints:
            dialog._honeycomb_support_point_picked(*point)
        _wait_until(qt_application, lambda: not dialog.operation_busy)

        reference = runtime.context.honeycomb_support.reference
        assert reference is not None
        assert received["hints"] == rough_hints
        assert received["mark"] == 190.0
        assert np.array_equal(received["image"], dialog._bed_image)
        assert reference.ruler_origin_machine_mm == pytest.approx((10.0, 10.0))
        assert reference.measured_ruler_span_mm == pytest.approx((190.0, 190.0))
        assert np.asarray(reference.support_corners_machine_mm) == pytest.approx(
            np.asarray(((10.0, 10.0), (200.0, 10.0), (200.0, 200.0), (10.0, 200.0)))
        )
        assert dialog.honeycomb_support_record_button.text() == (
            "Fallback: detect with 3 hints"
        )
        assert "ruler 0/0 maps to X10.0/Y10.0" in (
            dialog.honeycomb_support_status.text()
        )
        assert (
            runtime.context.calibration_profiles.active_dir
            / "honeycomb_support.json"
        ).exists()
        assert runtime.context.bed.calibration is calibration
        assert runtime.context.bed.calibration.to_dict() == calibration_before
        assert runtime.settings.machine.work_area == work_area_before

        detection = {
            "selected_default": True,
            "diagnostics": {
                "within_work_area": True,
                "touches_image_edge": False,
            },
        }
        metadata = _honeycomb_support_metadata(runtime)
        usable, indices, evidence = _usable_template_detections([detection])
        assert metadata is not None and metadata["reference_only"] is True
        assert "corners_local_mm" not in metadata
        assert metadata["corners_machine_mm"][0] == pytest.approx([10.0, 10.0])
        assert "visual comparison only" in metadata["message"]
        assert usable == [detection]
        assert indices == [0]
        assert evidence["excluded_detection_count"] == 0
        assert detection["selected_default"] is True
        assert detection["diagnostics"] == {
            "within_work_area": True,
            "touches_image_edge": False,
        }
    finally:
        dialog.close()
        runtime.stop()


def test_honeycomb_hint_detection_failure_saves_nothing(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    try:
        calibration = runtime.context.bed.calibration
        assert calibration is not None
        dialog._bed_image = np.zeros(
            (calibration.image_height, calibration.image_width, 3), dtype=np.uint8
        )
        dialog._work_area_reference_calibration = calibration
        monkeypatch.setattr(
            runtime.context,
            "detect_honeycomb_support_reference",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                ValueError("Repeated ruler ticks were not verified")
            ),
        )
        monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *_a, **_k: None)

        dialog.toggle_honeycomb_support_picking()
        for point in ((20.0, 20.0), (20.0, 200.0), (200.0, 200.0)):
            dialog._honeycomb_support_point_picked(*point)
        _wait_until(qt_application, lambda: not dialog.operation_busy)

        assert runtime.context.honeycomb_support.reference is None
        assert not (tmp_path / "data" / "honeycomb_support.json").exists()
        assert "failed" in dialog.operation_status.text().lower()
    finally:
        dialog.close()
        runtime.stop()


def test_work_area_reference_overlay_uses_large_noncolliding_origin_label(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, float]] = []
    original_put_text = cv2.putText

    def record_put_text(
        image: np.ndarray,
        text: str,
        origin: tuple[int, int],
        font_face: int,
        font_scale: float,
        color: tuple[int, int, int],
        thickness: int,
        line_type: int,
    ) -> np.ndarray:
        calls.append((text, font_scale))
        return original_put_text(
            image,
            text,
            origin,
            font_face,
            font_scale,
            color,
            thickness,
            line_type,
        )

    monkeypatch.setattr(cv2, "putText", record_put_text)

    class Bed:
        @staticmethod
        def mm_to_image(machine_x: float, machine_y: float) -> tuple[float, float]:
            return 100.0 + machine_x * 4.0, 980.0 - machine_y * 4.0

    _work_area_reference_overlay(
        np.zeros((1080, 1080, 3), dtype=np.uint8),
        Bed(),
        WorkArea(10.0, 210.0, 10.0, 210.0),
        5.0,
    )

    texts = [text for text, _scale in calls]
    assert "X/Y 10" in texts
    assert "X10" not in texts
    assert "Y10" not in texts
    assert next(scale for text, scale in calls if text == "X/Y 10") >= 1.25
    assert min(scale for text, scale in calls if text != "X/Y 10") >= 1.6


def test_machine_setup_guide_opens_at_the_current_numbered_step(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    try:
        for index, heading in enumerate(
            (
                "1. Camera",
                "2. Lens",
                "3. Bed Mapping",
                "4. Fine Registration",
                "5. Accuracy Validation",
            )
        ):
            dialog.tabs.setCurrentIndex(index)
            dialog.setup_guide_button.click()
            qt_application.processEvents()
            guide = dialog._setup_guide_dialog
            assert isinstance(guide, SetupGuideDialog)
            assert guide.isVisible()
            assert heading in guide.browser.textCursor().selectedText()
            assert guide.windowModality() == QtCore.Qt.WindowModality.NonModal
        dialog.tabs.setEnabled(False)
        dialog.close_button.setEnabled(False)
        assert dialog.setup_guide_button.isEnabled()
        guide.close()
    finally:
        dialog.close()
        runtime.stop()


def test_machine_setup_surfaces_controller_reconnect_requirement(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    try:
        runtime.context.machine._controller_reconnect_required = True
        dialog.refresh_all()

        assert dialog.machine_connection_status.text().startswith(
            "RECONNECT REQUIRED"
        )
        assert "untrusted" in dialog.machine_connection_status.text().lower()
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


def test_machine_setup_home_park_keeps_stop_live_and_blocks_close_until_cleanup(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    started = threading.Event()
    release = threading.Event()
    worker_threads: list[int] = []
    stop_calls: list[tuple[bool, int]] = []

    def park() -> dict[str, object]:
        worker_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3.0)
        return {"position": {"x": 100.0, "y": 100.0}}

    monkeypatch.setattr(runtime.context.machine, "prepare_photo_position", park)
    monkeypatch.setattr(
        runtime.context.machine,
        "request_stop",
        lambda emergency=False: stop_calls.append((emergency, threading.get_ident())),
    )
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args, **kwargs: None)
    try:
        dialog.show()
        dialog.park()
        _wait_until(qt_application, started.is_set)

        assert dialog.operation_busy
        dialog.park()
        qt_application.processEvents()
        assert len(worker_threads) == 1
        assert worker_threads[0] != threading.get_ident()
        assert not dialog.tabs.isEnabled()
        assert not dialog.close_button.isEnabled()
        assert dialog.machine_stop_button.isEnabled()
        assert dialog.operation_progress.isVisible()

        dialog.close()
        qt_application.processEvents()
        assert dialog.isVisible()
        assert dialog.operation_busy

        dialog.machine_stop_button.click()
        qt_application.processEvents()
        assert stop_calls == [(True, threading.get_ident())]
        assert "Software STOP requested" in dialog.operation_status.text()

        release.set()
        _wait_until(qt_application, lambda: not dialog.operation_busy)
        assert not dialog._photo_pose_confirmed
        assert "result was discarded" in dialog.operation_status.text()
        assert dialog.close_button.isEnabled()
        dialog.close()
        qt_application.processEvents()
        assert not dialog.isVisible()
    finally:
        release.set()
        _wait_until(qt_application, lambda: not dialog.operation_busy)
        dialog.close()
        runtime.stop()


def test_function_task_emits_exception_notes_once(
    qt_application: QtWidgets.QApplication,
) -> None:
    messages: list[str] = []

    def fail_with_cleanup_note() -> None:
        error = RuntimeError("camera burst failed")
        error.add_note("Temporary camera motor-release cleanup also failed: M5 failed")
        error.add_note("Temporary camera motor-release cleanup also failed: M5 failed")
        raise error

    task = FunctionTask(fail_with_cleanup_note)
    task.signals.failed.connect(messages.append)

    task.run()
    qt_application.processEvents()

    assert messages == [
        "camera burst failed\n"
        "Temporary camera motor-release cleanup also failed: M5 failed"
    ]


def test_machine_setup_stop_rejects_home_park_queued_before_worker_start(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    pool = QtCore.QThreadPool()
    pool.setMaxThreadCount(1)
    blocker_started = threading.Event()
    release_blocker = threading.Event()

    def block_pool() -> None:
        blocker_started.set()
        assert release_blocker.wait(3.0)

    blocker = FunctionTask(block_pool)
    pool.start(blocker)
    _wait_until(qt_application, blocker_started.is_set)
    dialog._thread_pool = pool
    initial_log = list(runtime.context.machine.status()["log"])
    try:
        dialog.park()
        assert dialog.operation_busy
        dialog.machine_stop_button.click()
        release_blocker.set()
        _wait_until(qt_application, lambda: not dialog.operation_busy)

        new_log = runtime.context.machine.status()["log"][len(initial_log) :]
        assert not any("$H" in line or "G0 X" in line for line in new_log)
        assert "stopped" in dialog.operation_status.text().lower()
        assert "cancelled by software STOP" in dialog.operation_status.text()
        assert not dialog._photo_pose_confirmed
    finally:
        release_blocker.set()
        pool.waitForDone(3000)
        dialog.close()
        runtime.stop()


def test_machine_setup_lens_solve_uses_worker_and_presents_on_gui_thread(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    started = threading.Event()
    release = threading.Event()
    worker_threads: list[int] = []
    presentation_threads: list[int] = []
    changed: list[bool] = []

    def solve() -> object:
        worker_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3.0)
        return object()

    monkeypatch.setattr(runtime.context, "solve_lens_calibration", solve)
    monkeypatch.setattr(
        dialog,
        "refresh_all",
        lambda: presentation_threads.append(threading.get_ident()),
    )
    dialog.calibrationChanged.connect(lambda: changed.append(True))
    dialog.lens_view_errors.setRowCount(2)
    dialog._fine_registration_analysis = {"can_apply_translation": True}
    dialog.apply_registration_button.setEnabled(True)
    try:
        dialog.solve_lens()
        _wait_until(qt_application, started.is_set)
        assert dialog.operation_busy
        assert worker_threads[0] != threading.get_ident()
        assert dialog.lens_view_errors.rowCount() == 0
        assert dialog._fine_registration_analysis is None
        assert not dialog.apply_registration_button.isEnabled()

        release.set()
        _wait_until(qt_application, lambda: not dialog.operation_busy)
        assert presentation_threads == [threading.get_ident()]
        assert changed == [True]
    finally:
        release.set()
        _wait_until(qt_application, lambda: not dialog.operation_busy)
        dialog.close()
        runtime.stop()


def test_machine_setup_busy_stop_and_progress_fit_at_large_text(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    release = threading.Event()
    font = QtGui.QFont(dialog.font())
    font.setPointSize(13)
    dialog.setFont(font)

    def park() -> dict[str, object]:
        assert release.wait(3.0)
        return {"position": {"x": 100.0, "y": 100.0}}

    monkeypatch.setattr(runtime.context.machine, "prepare_photo_position", park)
    monkeypatch.setattr(QtWidgets.QMessageBox, "information", lambda *args, **kwargs: None)

    def global_rect(widget: QtWidgets.QWidget) -> QtCore.QRect:
        return QtCore.QRect(
            widget.mapToGlobal(QtCore.QPoint(0, 0)),
            widget.rect().size(),
        )

    try:
        dialog.resize(dialog.minimumSize())
        dialog.show()
        dialog.park()
        _wait_until(qt_application, lambda: dialog.operation_progress.isVisible())

        bounds = global_rect(dialog)
        controls = (
            dialog.machine_connection_button,
            dialog.machine_stop_button,
            dialog.operation_progress,
            dialog.close_button,
        )
        assert all(bounds.contains(global_rect(control)) for control in controls)
        assert not global_rect(dialog.machine_connection_button).intersects(
            global_rect(dialog.machine_stop_button)
        )
        assert not global_rect(dialog.operation_progress).intersects(
            global_rect(dialog.close_button)
        )
        assert dialog.machine_stop_button.isEnabled()
    finally:
        release.set()
        _wait_until(qt_application, lambda: not dialog.operation_busy)
        dialog.close()
        runtime.stop()


@pytest.mark.parametrize(("width", "height"), ((900, 680), (1080, 780)))
def test_machine_setup_all_tabs_keep_shared_chrome_inside_compact_screenshot(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    width: int,
    height: int,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    font = QtGui.QFont(dialog.font())
    font.setPointSize(13)
    dialog.setFont(font)

    def dialog_rect(widget: QtWidgets.QWidget) -> QtCore.QRect:
        return QtCore.QRect(widget.mapTo(dialog, QtCore.QPoint()), widget.size())

    try:
        expected_size = QtCore.QSize(width, height)
        dialog.resize(expected_size)
        dialog.show()
        qt_application.processEvents()

        shared_chrome = (
            dialog.calibration_warning,
            dialog.machine_connection_status,
            dialog.machine_connection_button,
            dialog.machine_stop_button,
            dialog.preferences_note,
            dialog.tabs,
            dialog.operation_status,
            dialog.close_button,
        )
        ordered_rows = (
            dialog.calibration_warning,
            dialog.machine_connection_button,
            dialog.preferences_note,
            dialog.tabs,
            dialog.close_button,
        )

        for tab_index in range(dialog.tabs.count()):
            dialog.tabs.setCurrentIndex(tab_index)
            dialog.resize(expected_size)
            qt_application.processEvents()

            bounds = dialog.rect()
            assert dialog.size() == expected_size
            assert all(widget.isVisible() for widget in shared_chrome)
            assert all(bounds.contains(dialog_rect(widget)) for widget in shared_chrome)
            for upper, lower in zip(ordered_rows, ordered_rows[1:], strict=False):
                assert dialog_rect(upper).bottom() < dialog_rect(lower).top()

            page = dialog.tabs.currentWidget()
            assert isinstance(page, QtWidgets.QScrollArea)
            assert page.property("setupTabScroll") is True
            assert page.widgetResizable()
            assert dialog_rect(dialog.tabs).contains(dialog_rect(page))
            assert page.viewport().isVisible()
            content = page.widget()
            assert content is not None
            if content.width() > page.viewport().width():
                assert page.horizontalScrollBar().maximum() > 0
            if content.height() > page.viewport().height():
                assert page.verticalScrollBar().maximum() > 0

            if tab_index == 2:
                assert page.horizontalScrollBar().maximum() == 0
                assert dialog.work_area_reference_button.width() >= (
                    dialog.work_area_reference_button.sizeHint().width()
                )

            screenshot = dialog.grab()
            assert not screenshot.isNull()
            assert screenshot.size() == expected_size
    finally:
        dialog.close()
        runtime.stop()


@pytest.mark.parametrize(
    ("entrypoint", "context_method"),
    (
        ("save_still", "save_capture"),
        ("capture_bed", "capture_bed_reference"),
        ("capture_lens", "capture_lens_calibration"),
        ("capture_base_bed_mapping", "capture_base_bed_mapping"),
        ("capture_fine_registration", "capture_fine_registration"),
        ("capture_dense_calibration", "capture_dense_calibration"),
        ("capture_accuracy_validation", "capture_accuracy_validation"),
        ("capture_dense_validation", "capture_dense_calibration"),
        ("detect_workpiece", "detect_workpiece"),
    ),
)
def test_machine_setup_precision_operations_run_outside_the_gui_thread(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    entrypoint: str,
    context_method: str,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    started = threading.Event()
    release = threading.Event()
    worker_threads: list[int] = []

    def operation(*args, **kwargs):
        worker_threads.append(threading.get_ident())
        started.set()
        assert release.wait(3.0)
        raise RuntimeError("expected background failure")

    monkeypatch.setattr(runtime.context, context_method, operation)
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *args, **kwargs: None)
    try:
        getattr(dialog, entrypoint)()
        _wait_until(qt_application, started.is_set)
        assert dialog.operation_busy
        assert worker_threads[0] != threading.get_ident()

        responsive: list[bool] = []
        QtCore.QTimer.singleShot(0, lambda: responsive.append(True))
        _wait_until(qt_application, lambda: bool(responsive))
        assert dialog.operation_busy

        release.set()
        _wait_until(qt_application, lambda: not dialog.operation_busy)
        assert "expected background failure" in dialog.operation_status.text()
    finally:
        release.set()
        _wait_until(qt_application, lambda: not dialog.operation_busy)
        dialog.close()
        runtime.stop()


def test_machine_setup_failed_precision_capture_discards_prior_review_state(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    release = threading.Event()

    def capture(*, home_first: bool = True):
        assert home_first
        assert release.wait(3.0)
        raise RuntimeError("camera timeout")

    monkeypatch.setattr(runtime.context, "capture_fine_registration", capture)
    monkeypatch.setattr(QtWidgets.QMessageBox, "critical", lambda *args, **kwargs: None)
    dialog._fine_registration_analysis = {"can_apply_translation": True}
    dialog._dense_analysis = {"can_apply": True}
    dialog.apply_registration_button.setEnabled(True)
    dialog.apply_registration_map_button.setEnabled(True)
    dialog.apply_dense_button.setEnabled(True)
    dialog.registration_results.setRowCount(2)
    try:
        dialog.capture_fine_registration()
        assert dialog._fine_registration_analysis is None
        assert dialog._dense_analysis is None
        assert dialog.registration_results.rowCount() == 0
        assert not dialog.apply_registration_button.isEnabled()
        assert not dialog.apply_registration_map_button.isEnabled()
        assert not dialog.apply_dense_button.isEnabled()

        release.set()
        _wait_until(qt_application, lambda: not dialog.operation_busy)
        assert dialog._fine_registration_analysis is None
        assert not dialog.apply_registration_button.isEnabled()
        assert "camera timeout" in dialog.registration_status.text()
    finally:
        release.set()
        _wait_until(qt_application, lambda: not dialog.operation_busy)
        dialog.close()
        runtime.stop()


def test_reset_fine_translation_rechecks_existing_marks_and_enables_full_map(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    measurements = [
        {"id": index, "machine_x": 0.0, "machine_y": 0.0, "observed_x": 0.0,
         "observed_y": 0.0, "error_x_mm": 0.0, "error_y_mm": 0.0}
        for index in range(1, 9)
    ]
    analysis = {
        "classification": "position_dependent",
        "can_apply_translation": False,
        "correction_x_mm": 0.0,
        "correction_y_mm": 0.0,
        "scatter_rms_mm": 0.4,
        "excluded_ids": [],
        "reason": "The errors require a full-bed refinement",
        "full_map_refinement": {
            "can_apply_full_map": True,
            "inlier_count": 7,
            "selected_count": 8,
            "rms_error_mm": 0.08,
            "coverage_ratio": 0.37,
            "correction_max_mm": 0.84,
            "ransac_outlier_ids": [8],
            "reason": "The reviewed marks support a bounded full-bed refinement",
        },
    }
    reset_calls: list[bool] = []
    review_calls: list[tuple[list[dict[str, Any]], list[int]]] = []

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        runtime.context,
        "reset_fine_registration",
        lambda: reset_calls.append(True) or {"fine_registration": {}},
    )

    def review(items: list[dict[str, Any]], excluded: list[int]) -> dict[str, Any]:
        review_calls.append((items, excluded))
        return analysis

    monkeypatch.setattr(runtime.context, "review_fine_registration_measurements", review)
    dialog._fine_registration_measurements = measurements
    dialog._populate_registration_results(measurements, {8})
    try:
        dialog.reset_fine_registration()
        assert reset_calls == [True]
        assert review_calls == [(measurements, [8])]
        assert dialog._fine_registration_analysis is analysis
        assert dialog.apply_registration_map_button.isEnabled()
        assert not dialog.apply_registration_button.isEnabled()
    finally:
        dialog.close()
        runtime.stop()


def test_shifted_confirmation_preparation_uses_confirmation_session_only(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    captured: dict[str, object] = {}

    def prepare(**kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(runtime.context, "prepare_dense_calibration_job", prepare)
    try:
        dialog.prepare_dense_validation_job(False, confirmation=True)
        assert captured["validation"] is False
        assert captured["confirmation"] is True
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


def test_bed_mapping_controls_do_not_overlap_at_minimum_dialog_size(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)

    def global_rect(widget: QtWidgets.QWidget) -> QtCore.QRect:
        return QtCore.QRect(widget.mapToGlobal(QtCore.QPoint(0, 0)), widget.rect().size())

    try:
        dialog.tabs.setCurrentIndex(2)
        dialog.resize(dialog.minimumSize())
        dialog.show()
        qt_application.processEvents()
        automatic = next(
            group
            for group in dialog.findChildren(QtWidgets.QGroupBox)
            if group.title() == "Fresh automatic base mapping (keyed 5 x 5)"
        )
        capture = next(
            button
            for button in dialog.findChildren(QtWidgets.QPushButton)
            if button.text() == "Home / park, capture and detect base grid"
        )
        assert not global_rect(dialog.bed_preview).intersects(global_rect(dialog.bed_status))
        assert not global_rect(dialog.bed_status).intersects(global_rect(automatic))
        assert automatic.rect().contains(capture.geometry())
        if dialog.save_axis_mapping.isVisible():
            assert not global_rect(dialog.axis_mapping_status).intersects(
                global_rect(dialog.save_axis_mapping)
            )
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
        assert runtime.context.fine_registration_path.exists()
    finally:
        dialog.close()
        runtime.stop()


def test_machine_setup_prepares_dry_base_map_through_main_job_signal(
    qt_application: QtWidgets.QApplication, tmp_path: Path
) -> None:
    runtime = _runtime(tmp_path)
    dialog = MachineSetupDialog(runtime)
    prepared = []
    dialog.registrationJobPrepared.connect(prepared.append)
    try:
        dialog.prepare_base_bed_mapping_job(False)
        assert len(prepared) == 1
        job = prepared[0]
        assert job.display_name == "Base bed mapping"
        assert job.powered is False
        assert len(job.targets) == 25
        assert "M3 " not in job.program.text
        assert "M4 " not in job.program.text
        assert runtime.context.base_bed_mapping_path.exists()
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
        assert runtime.context.accuracy_validation_path.exists()
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
        "capture_lens_calibration",
        "solve_lens_calibration",
        "capture_bed_reference",
        "capture_parked_work_area_reference",
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
