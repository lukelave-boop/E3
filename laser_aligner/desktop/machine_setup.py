from __future__ import annotations

import csv
import json
from dataclasses import asdict
from typing import Any

import cv2
import numpy as np

from ..calibration.bed import BedPoint
from ..core import CoreRuntime
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


def _qimage(image: np.ndarray) -> QtGui.QImage:
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    height, width, channels = rgb.shape
    return QtGui.QImage(rgb.data, width, height, channels * width, QtGui.QImage.Format.Format_RGB888).copy()


class ImagePicker(QtWidgets.QLabel):
    pointPicked = QtCore.Signal(float, float)

    def __init__(self) -> None:
        super().__init__("No image captured")
        self.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(520, 320)
        self.setStyleSheet("background: #15191e; border: 1px solid #4b5563;")
        self._image: QtGui.QImage | None = None
        self._display_rect = QtCore.QRect()

    def set_image(self, image: np.ndarray) -> None:
        self._image = _qimage(image)
        self._render()

    def _render(self) -> None:
        if self._image is None:
            return
        pixmap = QtGui.QPixmap.fromImage(self._image).scaled(
            self.size(),
            QtCore.Qt.AspectRatioMode.KeepAspectRatio,
            QtCore.Qt.TransformationMode.SmoothTransformation,
        )
        x = (self.width() - pixmap.width()) // 2
        y = (self.height() - pixmap.height()) // 2
        self._display_rect = QtCore.QRect(x, y, pixmap.width(), pixmap.height())
        self.setPixmap(pixmap)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._render()

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        if self._image is None or not self._display_rect.contains(event.position().toPoint()):
            return
        x = (event.position().x() - self._display_rect.x()) * self._image.width() / self._display_rect.width()
        y = (event.position().y() - self._display_rect.y()) * self._image.height() / self._display_rect.height()
        self.pointPicked.emit(float(x), float(y))


class MachineSetupDialog(QtWidgets.QDialog):
    """Native access to every shared camera/calibration inspection operation."""

    calibrationChanged = QtCore.Signal()
    registrationJobPrepared = QtCore.Signal(object)
    validationJobPrepared = QtCore.Signal(object)

    def __init__(self, runtime: CoreRuntime, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.runtime = runtime
        self.context = runtime.context
        self._bed_image: np.ndarray | None = None
        self._bed_targets: list[dict[str, Any]] = []
        self._bed_target_index = 0
        self._fine_registration_analysis: dict[str, Any] | None = None
        self._fine_registration_measurements: list[dict[str, Any]] = []
        self._registration_table_updating = False
        self.setWindowTitle("Machine Setup")
        self.setMinimumSize(900, 680)
        self.resize(1080, 780)
        self._settings = QtCore.QSettings(
            str(self.context.settings.app.data_dir / "desktop-settings.ini"),
            QtCore.QSettings.Format.IniFormat,
        )

        layout = QtWidgets.QVBoxLayout(self)
        warning = QtWidgets.QLabel(
            "Calibration is not a safety function. Keep the laser incapable of emission while "
            "setting up the camera. Parking is available only when normal hardware and motion gates allow it."
        )
        warning.setWordWrap(True)
        warning.setObjectName("warningLabel")
        layout.addWidget(warning)
        preferences_note = QtWidgets.QLabel(
            "Setup remembers this window, selected tab, simulation scene, cross sizes, "
            "marking speeds, and saved axis orientation. Marking power intentionally "
            "returns to 0% whenever Setup opens."
        )
        preferences_note.setWordWrap(True)
        preferences_note.setObjectName("mutedLabel")
        layout.addWidget(preferences_note)
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_camera_tab()
        self._build_lens_tab()
        self._build_bed_tab()
        self._build_registration_tab()
        self._build_check_tab()
        self._restore_preferences()
        buttons = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.refresh_all()

    def _message(self, title: str, operation: Any) -> Any | None:
        try:
            result = operation()
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, title, str(exc))
            return None
        return result

    def _build_camera_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        self.camera_preview = ImagePicker()
        layout.addWidget(self.camera_preview, 1)
        self.camera_status = QtWidgets.QLabel()
        self.camera_status.setWordWrap(True)
        layout.addWidget(self.camera_status)
        row = QtWidgets.QHBoxLayout()
        refresh = QtWidgets.QPushButton("Refresh raw preview")
        apply_controls = QtWidgets.QPushButton("Apply all configured controls")
        save = QtWidgets.QPushButton("Save corrected still")
        row.addWidget(refresh)
        row.addWidget(apply_controls)
        row.addWidget(save)
        layout.addLayout(row)
        scene_row = QtWidgets.QHBoxLayout()
        scene_row.addWidget(QtWidgets.QLabel("Simulation scene"))
        self.synthetic_scene = QtWidgets.QComboBox()
        for label, value in (("Perspective bed", "bed"), ("Checkerboard", "checkerboard"), ("Workpiece", "workpiece")):
            self.synthetic_scene.addItem(label, value)
        self.synthetic_scene.setEnabled(self.context.settings.app.simulation)
        scene_row.addWidget(self.synthetic_scene, 1)
        set_scene = QtWidgets.QPushButton("Set scene")
        set_scene.setEnabled(self.context.settings.app.simulation)
        scene_row.addWidget(set_scene)
        layout.addLayout(scene_row)
        refresh.clicked.connect(self.refresh_camera)
        apply_controls.clicked.connect(self.apply_controls)
        save.clicked.connect(self.save_still)
        set_scene.clicked.connect(self.set_synthetic_scene)
        self.tabs.addTab(tab, "1 · Camera")

    def _build_lens_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        instructions = QtWidgets.QLabel(
            "Print targets/checkerboard_9x6_20mm.svg at 100%. Capture varied views at the "
            "center, edges and corners with the complete flat checkerboard visible."
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)
        self.lens_preview = ImagePicker()
        layout.addWidget(self.lens_preview, 1)
        self.lens_status = QtWidgets.QLabel()
        self.lens_status.setWordWrap(True)
        layout.addWidget(self.lens_status)
        row = QtWidgets.QHBoxLayout()
        preview = QtWidgets.QPushButton("Refresh preview")
        capture = QtWidgets.QPushButton("Capture checkerboard view")
        solve = QtWidgets.QPushButton("Solve lens calibration")
        clear = QtWidgets.QPushButton("Clear solved model")
        for button in (preview, capture, solve, clear):
            row.addWidget(button)
        layout.addLayout(row)
        preview.clicked.connect(self.refresh_lens_preview)
        capture.clicked.connect(self.capture_lens)
        solve.clicked.connect(self.solve_lens)
        clear.clicked.connect(self.clear_lens)
        self.tabs.addTab(tab, "2 · Lens")

    def _build_bed_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(tab)
        left = QtWidgets.QVBoxLayout()
        self.bed_preview = ImagePicker()
        self.bed_preview.pointPicked.connect(self._bed_point_picked)
        left.addWidget(self.bed_preview, 1)
        self.bed_status = QtWidgets.QLabel()
        self.bed_status.setWordWrap(True)
        left.addWidget(self.bed_status)
        layout.addLayout(left, 3)

        right = QtWidgets.QVBoxLayout()
        explanation = QtWidgets.QLabel(
            "Capture only at the repeatable photography pose. Click the exact center of a "
            "known mark, enter its commanded machine coordinates, then add the pair."
        )
        explanation.setWordWrap(True)
        right.addWidget(explanation)
        form = QtWidgets.QFormLayout()
        self.image_x = QtWidgets.QDoubleSpinBox()
        self.image_x.setRange(0, 10000)
        self.image_x.setDecimals(2)
        self.image_y = QtWidgets.QDoubleSpinBox()
        self.image_y.setRange(0, 10000)
        self.image_y.setDecimals(2)
        self.machine_x = QtWidgets.QDoubleSpinBox()
        self.machine_x.setRange(-10000, 10000)
        self.machine_x.setDecimals(3)
        self.machine_y = QtWidgets.QDoubleSpinBox()
        self.machine_y.setRange(-10000, 10000)
        self.machine_y.setDecimals(3)
        self.point_label = QtWidgets.QLineEdit()
        form.addRow("Image X (px)", self.image_x)
        form.addRow("Image Y (px)", self.image_y)
        form.addRow("Machine X (mm)", self.machine_x)
        form.addRow("Machine Y (mm)", self.machine_y)
        form.addRow("Label", self.point_label)
        right.addLayout(form)
        add = QtWidgets.QPushButton("Add point pair")
        add.clicked.connect(self.add_bed_point)
        right.addWidget(add)
        target_row = QtWidgets.QHBoxLayout()
        previous_target = QtWidgets.QPushButton("Previous target")
        next_target = QtWidgets.QPushButton("Next target")
        self.target_status = QtWidgets.QLabel("No coordinate CSV loaded")
        self.target_status.setWordWrap(True)
        target_row.addWidget(previous_target)
        target_row.addWidget(next_target)
        right.addLayout(target_row)
        right.addWidget(self.target_status)
        previous_target.clicked.connect(lambda: self.move_bed_target(-1))
        next_target.clicked.connect(lambda: self.move_bed_target(1))
        self.points = QtWidgets.QTableWidget(0, 5)
        self.points.setHorizontalHeaderLabels(("Label", "Image X", "Image Y", "Machine X", "Machine Y"))
        self.points.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.ResizeToContents)
        right.addWidget(self.points, 1)
        controls = QtWidgets.QGridLayout()
        park = QtWidgets.QPushButton("Park at camera pose")
        capture = QtWidgets.QPushButton("Capture fixed bed image")
        remove = QtWidgets.QPushButton("Delete selected point")
        detect = QtWidgets.QPushButton("Detect 5×5 cross grid")
        import_csv = QtWidgets.QPushButton("Import coordinate CSV")
        solve = QtWidgets.QPushButton("Solve bed mapping")
        axis_group = QtWidgets.QGroupBox("Persistent mapping orientation")
        axis_layout = QtWidgets.QVBoxLayout(axis_group)
        self.reverse_x = QtWidgets.QCheckBox("Reverse X mapping — OFF")
        self.reverse_y = QtWidgets.QCheckBox("Reverse Y mapping — OFF")
        for toggle in (self.reverse_x, self.reverse_y):
            toggle.setMinimumHeight(30)
        self.axis_mapping_status = QtWidgets.QLabel()
        self.axis_mapping_status.setWordWrap(True)
        self.save_axis_mapping = QtWidgets.QPushButton(
            "Confirm and save displayed axis states"
        )
        axis_layout.addWidget(self.reverse_x)
        axis_layout.addWidget(self.reverse_y)
        axis_layout.addWidget(self.axis_mapping_status)
        axis_layout.addWidget(self.save_axis_mapping)
        right.addWidget(axis_group)
        clear = QtWidgets.QPushButton("Clear mapping and points")
        for index, button in enumerate(
            (park, capture, remove, detect, import_csv, solve, clear)
        ):
            controls.addWidget(button, index // 2, index % 2)
        right.addLayout(controls)
        park.clicked.connect(self.park)
        capture.clicked.connect(self.capture_bed)
        remove.clicked.connect(self.delete_bed_point)
        detect.clicked.connect(self.detect_cross_grid)
        import_csv.clicked.connect(self.import_coordinate_csv)
        solve.clicked.connect(self.solve_bed)
        self.reverse_x.toggled.connect(
            lambda checked: self.set_bed_axis_reversed("x", checked)
        )
        self.reverse_y.toggled.connect(
            lambda checked: self.set_bed_axis_reversed("y", checked)
        )
        self.save_axis_mapping.clicked.connect(self.confirm_axis_mapping_state)
        clear.clicked.connect(self.clear_bed)
        layout.addLayout(right, 2)
        self.tabs.addTab(tab, "3 · Bed mapping")

    def _build_registration_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(tab)
        left = QtWidgets.QVBoxLayout()
        self.registration_preview = ImagePicker()
        left.addWidget(self.registration_preview, 1)
        self.registration_status = QtWidgets.QLabel(
            "Prepare and dry-run the registration path before making any marks."
        )
        self.registration_status.setWordWrap(True)
        left.addWidget(self.registration_status)
        layout.addLayout(left, 3)

        right = QtWidgets.QVBoxLayout()
        instructions = QtWidgets.QLabel(
            "This uses eight fresh crosses between the common 5×5 grid locations. "
            "Rigidly restrain a clean sacrificial surface at the calibrated height. "
            "The analysis can offer either a repeatable global translation or a "
            "strictly gated full-bed refinement. Both require explicit review and "
            "the independent holdout check on the next tab."
        )
        instructions.setWordWrap(True)
        right.addWidget(instructions)
        form = QtWidgets.QFormLayout()
        self.registration_power = QtWidgets.QDoubleSpinBox()
        self.registration_power.setRange(0.0, 100.0)
        self.registration_power.setDecimals(1)
        self.registration_power.setSuffix(" %")
        self.registration_power.setValue(0.0)
        self.registration_mark_size = QtWidgets.QDoubleSpinBox()
        self.registration_mark_size.setRange(2.0, 10.0)
        self.registration_mark_size.setDecimals(1)
        self.registration_mark_size.setSuffix(" mm")
        self.registration_mark_size.setValue(5.0)
        self.registration_speed = QtWidgets.QDoubleSpinBox()
        self.registration_speed.setRange(1.0, 50000.0)
        self.registration_speed.setDecimals(0)
        self.registration_speed.setSuffix(" mm/min")
        self.registration_speed.setValue(self.context.settings.laser.engrave_feed_mm_min)
        form.addRow("Verified marking power", self.registration_power)
        form.addRow("Cross size", self.registration_mark_size)
        form.addRow("Marking speed", self.registration_speed)
        right.addLayout(form)

        prepare_row = QtWidgets.QHBoxLayout()
        dry = QtWidgets.QPushButton("Prepare dry registration path")
        powered = QtWidgets.QPushButton("Prepare powered mark job")
        prepare_row.addWidget(dry)
        prepare_row.addWidget(powered)
        right.addLayout(prepare_row)
        capture = QtWidgets.QPushButton("Home / park, capture and analyze marks")
        right.addWidget(capture)
        self.registration_results = QtWidgets.QTableWidget(0, 8)
        self.registration_results.setHorizontalHeaderLabels(
            ("Use", "#", "Command X", "Command Y", "Observed X", "Observed Y", "ΔX", "ΔY")
        )
        self.registration_results.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        right.addWidget(self.registration_results, 1)
        correction_row = QtWidgets.QHBoxLayout()
        self.apply_registration_button = QtWidgets.QPushButton(
            "Apply reviewed translation"
        )
        self.apply_registration_button.setEnabled(False)
        reset = QtWidgets.QPushButton("Reset fine translation")
        correction_row.addWidget(self.apply_registration_button)
        correction_row.addWidget(reset)
        right.addLayout(correction_row)
        map_row = QtWidgets.QHBoxLayout()
        self.apply_registration_map_button = QtWidgets.QPushButton(
            "Apply reviewed full-bed map"
        )
        self.apply_registration_map_button.setEnabled(False)
        reset_map = QtWidgets.QPushButton("Reset full-bed refinement")
        map_row.addWidget(self.apply_registration_map_button)
        map_row.addWidget(reset_map)
        right.addLayout(map_row)
        layout.addLayout(right, 2)

        dry.clicked.connect(lambda: self.prepare_registration_job(False))
        powered.clicked.connect(lambda: self.prepare_registration_job(True))
        capture.clicked.connect(self.capture_fine_registration)
        self.apply_registration_button.clicked.connect(self.apply_fine_registration)
        self.apply_registration_map_button.clicked.connect(
            self.apply_fine_registration_homography
        )
        reset.clicked.connect(self.reset_fine_registration)
        reset_map.clicked.connect(self.reset_fine_registration_homography)
        self.registration_results.itemChanged.connect(
            self.registration_measurement_changed
        )
        self.tabs.addTab(tab, "4 · Fine registration")

    def _build_check_tab(self) -> None:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(tab)
        self.validation_preview = ImagePicker()
        self.validation_preview.setText("No accuracy-validation capture")
        self.validation_preview.setMinimumSize(420, 360)
        layout.addWidget(self.validation_preview, 3)

        right = QtWidgets.QVBoxLayout()
        text = QtWidgets.QLabel(
            "This uses five new holdout crosses that are not part of the eight-mark "
            "fine-registration fit. Prepare and run the dry path, then the guarded powered "
            "job on a clean restrained surface. Capture reports accuracy automatically; "
            "it never changes calibration."
        )
        text.setWordWrap(True)
        right.addWidget(text)

        form = QtWidgets.QFormLayout()
        self.validation_power = QtWidgets.QDoubleSpinBox()
        self.validation_power.setRange(0.0, 100.0)
        self.validation_power.setDecimals(1)
        self.validation_power.setSuffix(" %")
        self.validation_power.setValue(0.0)
        self.validation_mark_size = QtWidgets.QDoubleSpinBox()
        self.validation_mark_size.setRange(2.0, 10.0)
        self.validation_mark_size.setDecimals(1)
        self.validation_mark_size.setSuffix(" mm")
        self.validation_mark_size.setValue(5.0)
        self.validation_speed = QtWidgets.QDoubleSpinBox()
        self.validation_speed.setRange(1.0, 50000.0)
        self.validation_speed.setDecimals(0)
        self.validation_speed.setSuffix(" mm/min")
        self.validation_speed.setValue(
            self.context.settings.laser.engrave_feed_mm_min
        )
        form.addRow("Verified marking power", self.validation_power)
        form.addRow("Cross size", self.validation_mark_size)
        form.addRow("Marking speed", self.validation_speed)
        right.addLayout(form)

        prepare_row = QtWidgets.QHBoxLayout()
        validation_dry = QtWidgets.QPushButton("Prepare dry validation path")
        validation_powered = QtWidgets.QPushButton("Prepare powered validation job")
        prepare_row.addWidget(validation_dry)
        prepare_row.addWidget(validation_powered)
        right.addLayout(prepare_row)
        validation_capture = QtWidgets.QPushButton(
            "Home / park, capture and score holdouts"
        )
        right.addWidget(validation_capture)
        self.validation_status = QtWidgets.QLabel("No validation capture analyzed")
        self.validation_status.setWordWrap(True)
        right.addWidget(self.validation_status)
        self.validation_results = QtWidgets.QTableWidget(0, 8)
        self.validation_results.setHorizontalHeaderLabels(
            (
                "#",
                "Command X",
                "Command Y",
                "Observed X",
                "Observed Y",
                "ΔX",
                "ΔY",
                "Error",
            )
        )
        self.validation_results.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents
        )
        right.addWidget(self.validation_results, 1)

        diagnostics = QtWidgets.QGroupBox("Other camera diagnostics")
        diagnostics_layout = QtWidgets.QVBoxLayout(diagnostics)
        row = QtWidgets.QHBoxLayout()
        workpiece = QtWidgets.QPushButton("Detect workpiece")
        fiducials = QtWidgets.QPushButton("Detect ArUco fiducials")
        row.addWidget(workpiece)
        row.addWidget(fiducials)
        diagnostics_layout.addLayout(row)
        self.check_results = QtWidgets.QPlainTextEdit()
        self.check_results.setReadOnly(True)
        self.check_results.setMaximumHeight(110)
        diagnostics_layout.addWidget(self.check_results)
        right.addWidget(diagnostics)
        layout.addLayout(right, 2)

        validation_dry.clicked.connect(
            lambda: self.prepare_accuracy_validation_job(False)
        )
        validation_powered.clicked.connect(
            lambda: self.prepare_accuracy_validation_job(True)
        )
        validation_capture.clicked.connect(self.capture_accuracy_validation)
        workpiece.clicked.connect(self.detect_workpiece)
        fiducials.clicked.connect(self.detect_fiducials)
        self.tabs.addTab(tab, "5 · Accuracy validation")

    def refresh_all(self) -> None:
        camera = asdict(self.context.camera.status())
        self.camera_status.setText(
            f"{'Online' if camera.get('connected') else 'Offline'} · {camera.get('width', 0)} × "
            f"{camera.get('height', 0)} · {camera.get('device', '')}\n{camera.get('last_error') or ''}"
        )
        lens = self.context.lens.status()
        model = lens.get("model") or {}
        self.lens_status.setText(
            f"{lens['usable_image_count']}/{lens['pattern']['minimum_images']} usable captures · "
            + (
                f"Solved: {model.get('mean_reprojection_error', 0):.4f} px mean error"
                if lens["calibrated"]
                else "Not solved"
            )
        )
        bed = self.context.bed.status()
        calibration = bed.get("calibration") or {}
        fine = calibration.get("fine_registration") or {}
        fine_x = float(fine.get("translation_x_mm", 0.0))
        fine_y = float(fine.get("translation_y_mm", 0.0))
        self.bed_status.setText(
            f"{len(bed['points'])}/{bed['minimum_points']} point pairs · "
            + (
                f"Solved: {calibration.get('rms_error_mm', 0):.4f} mm RMS, "
                f"{calibration.get('max_error_mm', 0):.4f} mm max"
                + (
                    f" · fine translation X{fine_x:+.3f} Y{fine_y:+.3f} mm"
                    if abs(fine_x) > 1e-12 or abs(fine_y) > 1e-12
                    else ""
                )
                if bed["calibrated"]
                else "Not solved"
            )
        )
        self._refresh_axis_mapping(bed)
        self.points.setRowCount(len(bed["points"]))
        for row, point in enumerate(bed["points"]):
            values = (point["label"], point["image_x"], point["image_y"], point["machine_x"], point["machine_y"])
            for column, value in enumerate(values):
                self.points.setItem(row, column, QtWidgets.QTableWidgetItem(str(value)))

    def refresh_camera(self) -> None:
        image = self._message("Camera preview", lambda: self.context.camera_frame(undistort=False))
        if image is not None:
            self.camera_preview.set_image(image)

    def refresh_lens_preview(self) -> None:
        image = self._message("Lens preview", lambda: self.context.camera_frame(undistort=False))
        if image is not None:
            self.lens_preview.set_image(image)

    def apply_controls(self) -> None:
        result = self._message("Camera controls", self.context.camera.apply_configured_controls)
        if result is not None:
            QtWidgets.QMessageBox.information(
                self, "Camera controls", f"Applied: {result.applied}\n\nSkipped: {result.skipped}"
            )

    def save_still(self) -> None:
        path = self._message("Save still", lambda: self.context.save_capture("desktop-camera", undistort=True))
        if path is not None:
            QtWidgets.QMessageBox.information(self, "Saved", str(path))

    def set_synthetic_scene(self) -> None:
        result = self._message(
            "Simulation scene",
            lambda: self.context.synthetic_scene(str(self.synthetic_scene.currentData())),
        )
        if result is None:
            self.refresh_camera()

    def capture_lens(self) -> None:
        result = self._message(
            "Checkerboard capture", lambda: self.context.lens.capture(self.context.camera_frame(undistort=False))
        )
        if result is not None:
            self.refresh_all()
            self.refresh_lens_preview()
            QtWidgets.QMessageBox.information(
                self,
                "Checkerboard capture",
                "Checkerboard found." if result["found"] else "Image saved, but the checkerboard was not detected.",
            )

    def solve_lens(self) -> None:
        model = self._message("Lens calibration", self.context.lens.solve)
        if model is not None:
            self.refresh_all()
            self.calibrationChanged.emit()

    def clear_lens(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self, "Clear lens model", "Clear the solved lens model? Captured images will be retained."
        )
        if answer == QtWidgets.QMessageBox.StandardButton.Yes:
            self.context.lens.clear(delete_images=False)
            self.refresh_all()
            self.calibrationChanged.emit()

    def _bed_point_picked(self, x: float, y: float) -> None:
        self.image_x.setValue(x)
        self.image_y.setValue(y)

    def park(self) -> None:
        result = self._message("Park at camera pose", self.context.machine.prepare_photo_position)
        if result is not None:
            position = result["position"]
            QtWidgets.QMessageBox.information(
                self,
                "Camera pose",
                f"Machine idle at X{position['x']} Y{position['y']}.",
            )

    def capture_bed(self) -> None:
        result = self._message("Bed image", self.context.capture_bed_reference)
        if result is not None:
            self._bed_image = self.context.bed_reference()
            self.bed_preview.set_image(self._bed_image)

    def add_bed_point(self) -> None:
        result = self._message(
            "Add point",
            lambda: self.context.bed.add_point(
                BedPoint(
                    self.image_x.value(),
                    self.image_y.value(),
                    self.machine_x.value(),
                    self.machine_y.value(),
                    self.point_label.text()[:80],
                )
            ),
        )
        if result is not None:
            self.refresh_all()
            self.move_bed_target(1)

    def delete_bed_point(self) -> None:
        row = self.points.currentRow()
        if row < 0:
            return
        try:
            self.context.bed.delete_point(row)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Delete point", str(exc))
        else:
            self.refresh_all()

    def import_coordinate_csv(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Import bed coordinates", "", "CSV files (*.csv);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, newline="", encoding="utf-8-sig") as handle:
                rows = list(csv.DictReader(handle))
            normalized = {name.lower(): name for name in (rows[0] if rows else {})}
            x_key = next((normalized[name] for name in ("x_mm", "machine_x", "x") if name in normalized), None)
            y_key = next((normalized[name] for name in ("y_mm", "machine_y", "y") if name in normalized), None)
            label_key = next(
                (normalized[name] for name in ("fiducial", "index", "id", "label") if name in normalized), None
            )
            if x_key is None or y_key is None:
                raise ValueError("CSV headers must include x_mm and y_mm")
            targets = []
            for index, row in enumerate(rows):
                identifier = row.get(label_key, "") if label_key else str(index + 1)
                targets.append(
                    {
                        "machine_x": float(row[x_key]),
                        "machine_y": float(row[y_key]),
                        "label": f"Fiducial {identifier or index + 1}",
                    }
                )
            if len(targets) < self.context.settings.calibration.bed.minimum_points:
                raise ValueError("The CSV does not contain enough coordinates")
            self._bed_targets = targets
            self._bed_target_index = min(len(self.context.bed.points), len(targets) - 1)
        except Exception as exc:
            QtWidgets.QMessageBox.critical(self, "Import coordinates", str(exc))
            return
        self.show_bed_target()

    def move_bed_target(self, offset: int) -> None:
        if not self._bed_targets:
            return
        self._bed_target_index = max(0, min(self._bed_target_index + offset, len(self._bed_targets) - 1))
        self.show_bed_target()

    def show_bed_target(self) -> None:
        if not self._bed_targets:
            self.target_status.setText("No coordinate CSV loaded")
            return
        target = self._bed_targets[self._bed_target_index]
        self.machine_x.setValue(target["machine_x"])
        self.machine_y.setValue(target["machine_y"])
        self.point_label.setText(target["label"])
        self.target_status.setText(
            f"{self._bed_target_index + 1} of {len(self._bed_targets)}: {target['label']} · "
            f"X{target['machine_x']} Y{target['machine_y']}"
        )

    def detect_cross_grid(self) -> None:
        result = self._message("Cross-grid detection", self.context.detect_bed_cross_grid)
        if result is None:
            return
        if not result.get("detected"):
            QtWidgets.QMessageBox.warning(self, "Cross-grid detection", result.get("reason", "Grid not detected"))
            return
        points = result.get("points", [])
        if (
            QtWidgets.QMessageBox.question(
                self, "Accept detected grid", f"Replace current points with {len(points)} detected points?"
            )
            == QtWidgets.QMessageBox.StandardButton.Yes
        ):
            self.context.replace_bed_points({"points": points})
            self.refresh_all()

    def solve_bed(self) -> None:
        result = self._message("Bed mapping", self.context.solve_bed)
        if result is not None:
            self.refresh_all()
            self.calibrationChanged.emit()

    def _refresh_axis_mapping(self, bed: dict[str, Any]) -> None:
        mapping = bed.get("axis_mapping") or {}
        messages: list[str] = []
        for axis, toggle in (("x", self.reverse_x), ("y", self.reverse_y)):
            state = mapping.get(axis) or {}
            reversed_axis = bool(state.get("reversed", False))
            recorded = bool(state.get("recorded", False))
            blocker = QtCore.QSignalBlocker(toggle)
            toggle.setChecked(reversed_axis)
            del blocker
            toggle.setText(
                f"Reverse {axis.upper()} mapping — {'ON' if reversed_axis else 'OFF'}"
            )
            if mapping and not recorded:
                messages.append(f"{axis.upper()} was inferred from a legacy saved map")
            toggle.setEnabled(bool(bed.get("calibrated")))
        self.axis_mapping_status.setText(
            (
                "; ".join(messages)
                + ". After a laser-off direction check, use the confirmation button to record these states without changing the map."
            )
            if messages
            else (
                "Axis states are saved in the bed calibration and restored when Setup reopens."
                if bed.get("calibrated")
                else "Solve a bed mapping before selecting axis orientation."
            )
        )
        self.save_axis_mapping.setVisible(bool(messages))
        self.save_axis_mapping.setEnabled(bool(messages) and bool(bed.get("calibrated")))

    def set_bed_axis_reversed(self, axis: str, enabled: bool) -> None:
        axis = axis.upper()
        mode = "REVERSED" if enabled else "NORMAL"
        answer = QtWidgets.QMessageBox.warning(
            self,
            f"Set {axis} mapping {mode}",
            f"This sets the saved {axis} mapping to {mode}. If that differs from the "
            f"current effective state, every saved machine-{axis} point is mirrored and the bed "
            "mapping is re-solved. Use it only when a laser-off direction check or repeated "
            "homed measurements prove that camera and controller directions are "
            f"opposite on {axis}. The state persists across restarts.\n\nContinue?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            self.refresh_all()
            return
        result = self._message(
            f"Set {axis} mapping {mode}",
            lambda: self.context.bed.set_machine_axis_reversed(axis, enabled),
        )
        if result is not None:
            self.refresh_all()
            self.calibrationChanged.emit()

    def reverse_bed_axis(self, axis: str) -> None:
        """Retain the prior toggle entry point for compatibility."""
        state = self.context.bed.axis_mapping_state()[axis.lower()]
        self.set_bed_axis_reversed(axis, not state["reversed"])

    def confirm_axis_mapping_state(self) -> None:
        state = self.context.bed.axis_mapping_state()
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Confirm inferred axis orientation",
            "This records the displayed legacy-map orientation without mirroring any "
            "points. Confirm only after a laser-off direction check.\n\n"
            f"X: {'REVERSED' if state['x']['reversed'] else 'NORMAL'}\n"
            f"Y: {'REVERSED' if state['y']['reversed'] else 'NORMAL'}\n\nContinue?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        result = self._message(
            "Save axis orientation",
            lambda: (
                self.context.bed.set_machine_axis_reversed(
                    "x", state["x"]["reversed"]
                ),
                self.context.bed.set_machine_axis_reversed(
                    "y", state["y"]["reversed"]
                ),
            ),
        )
        if result is not None:
            self.refresh_all()
            self.calibrationChanged.emit()

    def _restore_preferences(self) -> None:
        geometry = self._settings.value("machineSetup/geometry-v1")
        if geometry:
            self.restoreGeometry(geometry)
        self.tabs.setCurrentIndex(
            max(0, min(self.tabs.count() - 1, int(self._settings.value("machineSetup/tab", 0))))
        )
        scene = str(self._settings.value("machineSetup/syntheticScene", "bed"))
        scene_index = self.synthetic_scene.findData(scene)
        if scene_index >= 0:
            self.synthetic_scene.setCurrentIndex(scene_index)
        for key, widget in (
            ("registrationMarkSize", self.registration_mark_size),
            ("registrationSpeed", self.registration_speed),
            ("validationMarkSize", self.validation_mark_size),
            ("validationSpeed", self.validation_speed),
        ):
            value = self._settings.value(f"machineSetup/{key}")
            if value is not None:
                widget.setValue(float(value))

    def _save_preferences(self) -> None:
        self._settings.setValue("machineSetup/geometry-v1", self.saveGeometry())
        self._settings.setValue("machineSetup/tab", self.tabs.currentIndex())
        self._settings.setValue(
            "machineSetup/syntheticScene", self.synthetic_scene.currentData()
        )
        for key, widget in (
            ("registrationMarkSize", self.registration_mark_size),
            ("registrationSpeed", self.registration_speed),
            ("validationMarkSize", self.validation_mark_size),
            ("validationSpeed", self.validation_speed),
        ):
            self._settings.setValue(f"machineSetup/{key}", widget.value())
        self._settings.sync()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        self._save_preferences()
        super().closeEvent(event)

    def reject(self) -> None:
        self._save_preferences()
        super().reject()

    def clear_bed(self) -> None:
        if (
            QtWidgets.QMessageBox.question(self, "Clear bed mapping", "Clear all bed points and the solved mapping?")
            == QtWidgets.QMessageBox.StandardButton.Yes
        ):
            self.context.bed.clear()
            self.refresh_all()
            self.calibrationChanged.emit()

    def prepare_registration_job(self, powered: bool) -> None:
        if powered:
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Prepare powered registration marks",
                "This prepares eight powered crosses. Use only a previously verified "
                "visible-marking power on a restrained sacrificial surface inside the "
                "required enclosure. The main window will still require the normal "
                "powered-job confirmation and arming phrase.\n\nContinue?",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        job = self._message(
            "Fine registration",
            lambda: self.context.prepare_fine_registration_job(
                powered=powered,
                power_percent=self.registration_power.value(),
                mark_size_mm=self.registration_mark_size.value(),
                speed_mm_min=self.registration_speed.value(),
            ),
        )
        if job is not None:
            self.registrationJobPrepared.emit(job)
            self.accept()

    def capture_fine_registration(self) -> None:
        def operation() -> tuple[np.ndarray, dict[str, Any]]:
            self.context.machine.prepare_photo_position()
            image = self.context.camera_frame(undistort=True)
            return image, self.context.analyze_fine_registration_image(image)

        result = self._message("Fine registration", operation)
        if result is None:
            return
        image, payload = result
        preview = image.copy()
        for point in payload.get("points", []):
            center = (int(round(point["image_x"])), int(round(point["image_y"])))
            cv2.circle(preview, center, 14, (0, 220, 0), 2, cv2.LINE_AA)
            cv2.putText(
                preview,
                str(point["id"]),
                (center[0] + 16, center[1] - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (0, 220, 0),
                2,
                cv2.LINE_AA,
            )
        self.registration_preview.set_image(preview)
        analysis = payload.get("analysis")
        if not isinstance(analysis, dict):
            self._fine_registration_analysis = None
            self._fine_registration_measurements = []
            self.apply_registration_button.setEnabled(False)
            self.apply_registration_map_button.setEnabled(False)
            self.registration_status.setText(
                payload.get("reason", "Registration marks were not detected")
            )
            return
        self._fine_registration_measurements = list(payload.get("measurements", []))
        self._populate_registration_results(
            self._fine_registration_measurements,
            set(int(value) for value in analysis.get("excluded_ids", [])),
        )
        self._show_registration_analysis(analysis)

    def _show_registration_analysis(self, analysis: dict[str, Any]) -> None:
        self._fine_registration_analysis = analysis
        self.apply_registration_button.setEnabled(bool(analysis.get("can_apply_translation")))
        refinement = analysis.get("full_map_refinement")
        can_apply_map = isinstance(refinement, dict) and bool(
            refinement.get("can_apply_full_map")
        )
        self.apply_registration_map_button.setEnabled(can_apply_map)
        excluded = [int(value) for value in analysis.get("excluded_ids", [])]
        exclusion_text = (
            " · excluded " + ", ".join(f"#{value}" for value in excluded)
            if excluded
            else ""
        )
        status = (
            f"{analysis['classification'].replace('_', ' ').title()} · "
            f"proposed correction X{analysis['correction_x_mm']:+.3f} "
            f"Y{analysis['correction_y_mm']:+.3f} mm · "
            f"scatter {analysis['scatter_rms_mm']:.3f} mm RMS{exclusion_text}\n"
            f"{analysis['reason']}"
        )
        if isinstance(refinement, dict):
            ransac_outliers = [
                int(value) for value in refinement.get("ransac_outlier_ids", [])
            ]
            outlier_text = (
                " · geometric outlier "
                + ", ".join(f"#{value}" for value in ransac_outliers)
                if ransac_outliers
                else ""
            )
            status += (
                f"\nFull-bed fit: {refinement['inlier_count']}/"
                f"{refinement['selected_count']} inliers · "
                f"{refinement['rms_error_mm']:.3f} mm RMS · "
                f"{refinement['coverage_ratio']:.0%} coverage · "
                f"{refinement['correction_max_mm']:.3f} mm maximum correction"
                f"{outlier_text}\n{refinement['reason']}"
            )
        self.registration_status.setText(status)

    def _populate_registration_results(
        self,
        measurements: list[dict[str, Any]],
        excluded_ids: set[int],
    ) -> None:
        self._registration_table_updating = True
        self.registration_results.setRowCount(len(measurements))
        for row, item in enumerate(measurements):
            use = QtWidgets.QTableWidgetItem()
            use.setFlags(
                QtCore.Qt.ItemFlag.ItemIsEnabled
                | QtCore.Qt.ItemFlag.ItemIsUserCheckable
            )
            use.setCheckState(
                QtCore.Qt.CheckState.Unchecked
                if int(item["id"]) in excluded_ids
                else QtCore.Qt.CheckState.Checked
            )
            use.setToolTip(
                "Uncheck only when the preview clearly shows an obstructed, damaged, "
                "or incorrectly detected cross. At most two may be excluded."
            )
            self.registration_results.setItem(row, 0, use)
            values = (
                item["id"],
                f"{item['machine_x']:.3f}",
                f"{item['machine_y']:.3f}",
                f"{item['observed_x']:.3f}",
                f"{item['observed_y']:.3f}",
                f"{item['error_x_mm']:+.3f}",
                f"{item['error_y_mm']:+.3f}",
            )
            for column, value in enumerate(values):
                self.registration_results.setItem(
                    row, column + 1, QtWidgets.QTableWidgetItem(str(value))
                )
        self._registration_table_updating = False

    def registration_measurement_changed(
        self, item: QtWidgets.QTableWidgetItem
    ) -> None:
        if (
            self._registration_table_updating
            or item.column() != 0
            or not self._fine_registration_measurements
        ):
            return
        excluded_ids = []
        for row, measurement in enumerate(self._fine_registration_measurements):
            use = self.registration_results.item(row, 0)
            if use is None or use.checkState() != QtCore.Qt.CheckState.Checked:
                excluded_ids.append(int(measurement["id"]))
        analysis = self._message(
            "Review fine registration",
            lambda: self.context.review_fine_registration_measurements(
                self._fine_registration_measurements,
                excluded_ids,
            ),
        )
        if analysis is None:
            previous = set(
                int(value)
                for value in (self._fine_registration_analysis or {}).get(
                    "excluded_ids", []
                )
            )
            self._populate_registration_results(
                self._fine_registration_measurements, previous
            )
            return
        self._show_registration_analysis(analysis)

    def apply_fine_registration(self) -> None:
        analysis = self._fine_registration_analysis
        if not analysis or not analysis.get("can_apply_translation"):
            return
        correction_x = float(analysis["correction_x_mm"])
        correction_y = float(analysis["correction_y_mm"])
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Apply fine registration",
            f"Apply the reviewed camera-map translation X{correction_x:+.3f} "
            f"Y{correction_y:+.3f} mm?\n\nThis changes camera placement, not "
            "laser-head offset configuration. It can be reset from this tab.",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        result = self._message(
            "Apply fine registration",
            lambda: self.context.apply_fine_registration(analysis),
        )
        if result is not None:
            self._fine_registration_analysis = None
            self.apply_registration_button.setEnabled(False)
            self.apply_registration_map_button.setEnabled(False)
            self.refresh_all()
            self.calibrationChanged.emit()

    def apply_fine_registration_homography(self) -> None:
        analysis = self._fine_registration_analysis
        refinement = (
            analysis.get("full_map_refinement") if isinstance(analysis, dict) else None
        )
        if not isinstance(refinement, dict) or not refinement.get(
            "can_apply_full_map"
        ):
            return
        outliers = [int(value) for value in refinement.get("ransac_outlier_ids", [])]
        outlier_text = (
            "\nGeometric outlier excluded by the fit: "
            + ", ".join(f"#{value}" for value in outliers)
            if outliers
            else ""
        )
        answer = QtWidgets.QMessageBox.warning(
            self,
            "Apply full-bed refinement",
            f"Replace the camera-to-bed map with this reviewed {refinement['inlier_count']}-"
            f"point fit?\n\nFit error: {refinement['rms_error_mm']:.3f} mm RMS\n"
            f"Maximum modeled bed correction: {refinement['correction_max_mm']:.3f} mm"
            f"{outlier_text}\n\nThis is calibration, not a safety function. The previous "
            "solved map will be retained for Reset full-bed refinement.",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        result = self._message(
            "Apply full-bed refinement",
            lambda: self.context.apply_fine_registration_homography(analysis),
        )
        if result is not None:
            self._fine_registration_analysis = None
            self.apply_registration_button.setEnabled(False)
            self.apply_registration_map_button.setEnabled(False)
            self.refresh_all()
            self.calibrationChanged.emit()

    def reset_fine_registration(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Reset fine registration",
            "Remove the applied fine-registration translation and restore the solved bed map?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        result = self._message(
            "Reset fine registration", self.context.reset_fine_registration
        )
        if result is not None:
            self._fine_registration_analysis = None
            self.apply_registration_button.setEnabled(False)
            self.apply_registration_map_button.setEnabled(False)
            self.refresh_all()
            self.calibrationChanged.emit()

    def reset_fine_registration_homography(self) -> None:
        answer = QtWidgets.QMessageBox.question(
            self,
            "Reset full-bed refinement",
            "Restore the solved bed map saved immediately before the reviewed full-bed refinement?",
            QtWidgets.QMessageBox.StandardButton.Yes
            | QtWidgets.QMessageBox.StandardButton.Cancel,
            QtWidgets.QMessageBox.StandardButton.Cancel,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        result = self._message(
            "Reset full-bed refinement",
            self.context.reset_fine_registration_homography,
        )
        if result is not None:
            self._fine_registration_analysis = None
            self.apply_registration_button.setEnabled(False)
            self.apply_registration_map_button.setEnabled(False)
            self.refresh_all()
            self.calibrationChanged.emit()

    def prepare_accuracy_validation_job(self, powered: bool) -> None:
        if powered:
            answer = QtWidgets.QMessageBox.warning(
                self,
                "Prepare powered accuracy validation",
                "This prepares five powered holdout crosses. Use a clean, restrained "
                "sacrificial surface at the calibrated height and only a previously "
                "verified visible-marking power. The main window still requires its "
                "normal powered-job confirmation and arming phrase.\n\nContinue?",
                QtWidgets.QMessageBox.StandardButton.Yes
                | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
        job = self._message(
            "Accuracy validation",
            lambda: self.context.prepare_accuracy_validation_job(
                powered=powered,
                power_percent=self.validation_power.value(),
                mark_size_mm=self.validation_mark_size.value(),
                speed_mm_min=self.validation_speed.value(),
            ),
        )
        if job is not None:
            self.validationJobPrepared.emit(job)
            self.accept()

    def capture_accuracy_validation(self) -> None:
        def operation() -> tuple[np.ndarray, dict[str, Any]]:
            self.context.machine.prepare_photo_position()
            image = self.context.camera_frame(undistort=True)
            return image, self.context.analyze_accuracy_validation_image(image)

        result = self._message("Accuracy validation", operation)
        if result is None:
            return
        image, payload = result
        analysis = payload.get("analysis")
        if not isinstance(analysis, dict):
            self.validation_preview.set_image(image)
            self.validation_results.setRowCount(0)
            self.validation_status.setText(
                payload.get("reason", "Validation holdouts were not detected")
            )
            return

        measurements = list(analysis.get("measurements", []))
        by_id = {int(item["id"]): item for item in measurements}
        preview = image.copy()
        for point in payload.get("points", []):
            item = by_id.get(int(point["id"]))
            error = float(item.get("error_mm", float("inf"))) if item else float("inf")
            if analysis.get("passed"):
                color = (0, 220, 0)
            elif error <= float(analysis["max_limit_mm"]):
                color = (0, 165, 255)
            else:
                color = (0, 0, 255)
            center = (int(round(point["image_x"])), int(round(point["image_y"])))
            cv2.circle(preview, center, 14, color, 2, cv2.LINE_AA)
            cv2.putText(
                preview,
                str(point["id"]),
                (center[0] + 16, center[1] - 8),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                color,
                2,
                cv2.LINE_AA,
            )
        self.validation_preview.set_image(preview)
        self.validation_results.setRowCount(len(measurements))
        for row, item in enumerate(measurements):
            values = (
                item["id"],
                f"{item['machine_x']:.3f}",
                f"{item['machine_y']:.3f}",
                f"{item['observed_x']:.3f}",
                f"{item['observed_y']:.3f}",
                f"{item['error_x_mm']:+.3f}",
                f"{item['error_y_mm']:+.3f}",
                f"{item['error_mm']:.3f}",
            )
            for column, value in enumerate(values):
                self.validation_results.setItem(
                    row, column, QtWidgets.QTableWidgetItem(str(value))
                )
        self.validation_status.setText(
            f"{analysis['classification'].upper()} · RMS "
            f"{analysis['rms_error_mm']:.3f} / ≤{analysis['rms_limit_mm']:.3f} mm · "
            f"maximum {analysis['max_error_mm']:.3f} / "
            f"≤{analysis['max_limit_mm']:.3f} mm · mean X"
            f"{analysis['mean_error_x_mm']:+.3f} Y"
            f"{analysis['mean_error_y_mm']:+.3f} mm\n{analysis['reason']}"
        )

    def detect_workpiece(self) -> None:
        result = self._message("Workpiece detection", self.context.detect_workpiece)
        if result is not None:
            self.check_results.setPlainText(json.dumps(result, indent=2))

    def detect_fiducials(self) -> None:
        result = self._message("Fiducial detection", self.context.detect_fiducials)
        if result is not None:
            self.check_results.setPlainText(json.dumps(result, indent=2))
