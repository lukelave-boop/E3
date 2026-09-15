"""Non-destructive collection and review for material-height camera geometry."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from ..calibration.material_plane import require_precision_model
from .controls import MeasurementSpinBox
from .qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()


class SurfaceHeightDialog(QtWidgets.QDialog):
    def __init__(
        self, context: Any, preview: Any, image: np.ndarray | None, parent: Any = None,
        *, image_binding: dict[str, Any] | None = None,
    ):
        super().__init__(parent)
        self.context = context
        self.preview = preview
        self.image = None if image is None else image.copy()
        self.image_binding = image_binding
        self.model = None
        self.captured_evidence = None
        self._capture_active = False
        self.setWindowTitle("Material height — camera calibration")
        self.resize(920, 700)
        outer = QtWidgets.QVBoxLayout(self)
        self.scroll = QtWidgets.QScrollArea()
        self.scroll.setWidgetResizable(True)
        content = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(content)
        self.scroll.setWidget(content)
        outer.addWidget(self.scroll, 1)
        intro = QtWidgets.QLabel(
            "Keep the camera, focus, XY reference and photography pose fixed. Acquire independent "
            "keyed targets at lower, upper and middle heights without replacing the support map. "
            "Use a flat, restrained target and include paper and spacers in the measurement. "
            "The fixed border under the parked probe is the height datum. Negative heights are below it."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        warning = QtWidgets.QLabel(
            "Height evidence preserves the support map. Reviewed marking jobs retain normal bounds, "
            "focus and temporary arming guards. A model fit is separate from independent physical "
            "XY qualification; diagnostic previews alone do not qualify placement."
        )
        warning.setWordWrap(True)
        warning.setObjectName("heightStudyScope")
        layout.addWidget(warning)
        form = QtWidgets.QFormLayout()
        self.reference = QtWidgets.QLineEdit("Fixed honeycomb border at Home / park")
        self.reference.setMaxLength(160)
        self.height = MeasurementSpinBox()
        self.height.setRange(-1000, 1000)
        self.height.setDecimals(3)
        self.height.setSuffix(" mm")
        self.height.setToolTip(
            "Material-top height relative to the fixed border = honeycomb height relative to border "
            "+ material/spacers/paper thickness. This is a measurement, not a motion command."
        )
        form.addRow("Fixed reference", self.reference)
        form.addRow("Measured top height above reference", self.height)
        self.use_measured_height = QtWidgets.QPushButton("Use current probed top elevation")
        self.use_measured_height.clicked.connect(lambda: self.use_current_height())
        self.height.setToolTip(
            "Signed top elevation relative to the fixed border, including supports. Powered target "
            "preparation requires this value to agree with the current probe measurement within "
            "the declared uncertainty. Measuring the surface and establishing focus are separate explicit actions."
        )
        form.addRow(self.use_measured_height)
        self.uncertainty = MeasurementSpinBox()
        self.uncertainty.setRange(0, 100)
        self.uncertainty.setDecimals(3)
        self.uncertainty.setSuffix(" mm")
        form.addRow("Measured height uncertainty", self.uncertainty)
        self.slot = QtWidgets.QComboBox()
        for slot, label in (("lower", "Lower fit plane"), ("upper", "Upper fit plane"),
                            ("check", "Independent middle check")):
            self.slot.addItem(label, slot)
        form.addRow("Acquire", self.slot)
        layout.addLayout(form)
        collection = QtWidgets.QGroupBox("Acquire a fresh height target")
        collection_layout = QtWidgets.QGridLayout(collection)
        self.begin_button = QtWidgets.QPushButton("Begin new height calibration")
        self.begin_button.clicked.connect(self.begin_calibration)
        self.prepare_button = QtWidgets.QPushButton("Prepare powered height target job")
        self.prepare_button.clicked.connect(self.prepare_capture)
        self.capture_button = QtWidgets.QPushButton("Home / park and capture completed height target")
        self.capture_button.clicked.connect(self.capture_evidence)
        self.save_capture_button = QtWidgets.QPushButton("Save captured height evidence")
        self.save_capture_button.setEnabled(False)
        self.save_capture_button.clicked.connect(self.save_captured)
        for row, button in enumerate((self.begin_button, self.prepare_button,
                                      self.capture_button, self.save_capture_button)):
            collection_layout.addWidget(button, row, 0)
        self.collection_note = QtWidgets.QLabel(
            "Begin once after fixing the mount and datum. For each plane, measure and focus on a "
            "flat restrained target, then prepare its job using Bed Mapping's reviewed marking "
            "settings. Review the exact Preview and use START JOB. Reopen this dialog to capture "
            "and save that completed target before changing its height."
        )
        self.collection_note.setWordWrap(True)
        collection_layout.addWidget(self.collection_note, 4, 0)
        layout.addWidget(collection)
        available = callable(getattr(context, "prepare_surface_height_capture", None))
        self.begin_button.setEnabled(available)
        self.prepare_button.setEnabled(available and parent is not None)
        self.capture_button.setEnabled(available and parent is not None)
        legacy = QtWidgets.QCheckBox("Show legacy diagnostic map import")
        layout.addWidget(legacy)
        legacy_container = QtWidgets.QWidget()
        legacy_layout = QtWidgets.QVBoxLayout(legacy_container)
        legacy_note = QtWidgets.QLabel(
            "Legacy import reads the current base map. Its original collection workflow replaces "
            "that map; use fresh capture above for production height evidence."
        )
        legacy_note.setWordWrap(True)
        legacy_layout.addWidget(legacy_note)
        actions = QtWidgets.QHBoxLayout()
        self.save_buttons = {}
        for slot, label in (("lower", "Save lower map"), ("upper", "Save upper map"), ("check", "Save independent check")):
            button = QtWidgets.QPushButton(label)
            button.setObjectName(f"saveSurface{slot.title()}")
            button.clicked.connect(lambda _checked=False, chosen=slot: self.save_map(chosen))
            actions.addWidget(button)
            self.save_buttons[slot] = button
        legacy_layout.addLayout(actions)
        layout.addWidget(legacy_container)
        legacy_container.setVisible(False)
        legacy.toggled.connect(legacy_container.setVisible)
        self.solve_button = QtWidgets.QPushButton("Fit and check camera model")
        self.solve_button.clicked.connect(self.solve)
        layout.addWidget(self.solve_button)
        activation = QtWidgets.QHBoxLayout()
        self.enable_button = QtWidgets.QPushButton("Use accepted height model")
        self.enable_button.clicked.connect(lambda: self.set_correction(True))
        self.disable_button = QtWidgets.QPushButton("Disable height correction")
        self.disable_button.clicked.connect(lambda: self.set_correction(False))
        can_activate = callable(getattr(context, "enable_material_height_correction", None))
        self.enable_button.setEnabled(can_activate)
        self.disable_button.setEnabled(can_activate)
        activation.addWidget(self.enable_button)
        activation.addWidget(self.disable_button)
        layout.addLayout(activation)
        self.evidence_status = QtWidgets.QLabel()
        self.evidence_status.setWordWrap(True)
        layout.addWidget(self.evidence_status)
        self.result_status = QtWidgets.QLabel("Save lower and upper maps, then fit. Use a third height near the middle for an independent check.")
        self.result_status.setWordWrap(True)
        layout.addWidget(self.result_status)
        review = QtWidgets.QHBoxLayout()
        self.preview_height = MeasurementSpinBox()
        self.preview_height.setRange(-1000, 1000)
        self.preview_height.setDecimals(3)
        self.preview_height.setSuffix(" mm")
        self.preview_height.setEnabled(False)
        review.addWidget(QtWidgets.QLabel("Assumed surface height for this photograph"))
        review.addWidget(self.preview_height)
        self.preview_button = QtWidgets.QPushButton("Preview corrected photograph")
        self.preview_button.setEnabled(False)
        self.preview_button.clicked.connect(self.render_preview)
        review.addWidget(self.preview_button)
        layout.addLayout(review)
        layout.addWidget(preview, 1)
        caption = QtWidgets.QLabel(
            "Preview uses the Bed Mapping capture already in this window. Capture the actual material "
            "at the parked pose before reopening this study. Changing the assumed height reprojects "
            "that photograph; it cannot reveal hidden surfaces or correct defocus."
        )
        caption.setWordWrap(True)
        layout.addWidget(caption)
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        outer.addWidget(close)
        self.refresh_evidence()
        self.use_current_height(quiet=True)

    def use_current_height(self, *, quiet: bool = False) -> None:
        try:
            provider = getattr(getattr(self.context, "machine", None), "material_surface_snapshot", None)
            snapshot = provider() if callable(provider) else None
            if snapshot is None:
                if not quiet:
                    self.result_status.setText("Measure the surface and establish its current job focus first.")
                return
            self.height.setValue(float(snapshot["surface_elevation_mm"]))
            if not quiet:
                self.result_status.setText("Current probed top elevation copied. Enter the measured uncertainty before preparing the target.")
        except Exception as exc:
            if not quiet:
                self.result_status.setText(str(exc))

    def closeEvent(self, event: Any) -> None:
        if self._capture_active:
            self.result_status.setText("Wait for the current capture, or use Machine Setup's Software STOP.")
            event.ignore()
            return
        super().closeEvent(event)

    def reject(self) -> None:
        if not self._capture_active:
            super().reject()

    def _set_capture_busy(self, busy: bool) -> None:
        self._capture_active = busy
        available = callable(getattr(self.context, "prepare_surface_height_capture", None))
        self.begin_button.setEnabled(available and not busy)
        self.prepare_button.setEnabled(available and self.parent() is not None and not busy)
        self.capture_button.setEnabled(available and self.parent() is not None and not busy)

    def _capture_failed(self, message: str) -> None:
        self._set_capture_busy(False)
        self.result_status.setText(f"Height capture failed: {message}")

    def set_correction(self, enabled: bool) -> None:
        try:
            self.context.enable_material_height_correction(enabled)
            self.result_status.setText(
                "Height model enabled. Measure the actual surface, close Setup, then choose Capture "
                "for placement in the main Camera panel before positioning geometry. Independent "
                "physical XY qualification remains separate."
                if enabled else "Height correction disabled. Capture again before using camera placement."
            )
        except Exception as exc:
            self.result_status.setText(str(exc))

    def begin_calibration(self) -> None:
        try:
            self.context.begin_surface_height_calibration(self.reference.text())
            self.captured_evidence = None
            self.save_capture_button.setEnabled(False)
            self.model = None
            self.preview_button.setEnabled(False)
            self.result_status.setText("New mount/datum generation started. Acquire fresh lower, upper and independent check targets.")
            self.refresh_evidence()
        except Exception as exc:
            self.result_status.setText(str(exc))

    def prepare_capture(self) -> None:
        setup = self.parent()
        try:
            answer = QtWidgets.QMessageBox.warning(
                self, "Prepare powered height target",
                "This prepares a powered keyed target at the entered measured height. Confirm a "
                "restrained flat target covers the reviewed pattern and the existing measured-focus "
                "workflow is ready. Use only previously verified marking settings. Review Preview "
                "and use START JOB separately.",
                QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.Cancel,
                QtWidgets.QMessageBox.StandardButton.Cancel,
            )
            if answer != QtWidgets.QMessageBox.StandardButton.Yes:
                return
            job = self.context.prepare_surface_height_capture(
                self.slot.currentData(), self.height.value(), self.reference.text(), self.uncertainty.value(),
                powered=True, power_percent=setup.base_grid_power.value(),
                mark_size_mm=setup.base_grid_mark_size.value(), speed_mm_min=setup.base_grid_speed.value(),
            )
            setup.registrationJobPrepared.emit(job)
            self.accept()
            setup.accept()
        except Exception as exc:
            self.result_status.setText(str(exc))

    def capture_evidence(self) -> None:
        setup = self.parent()
        if setup is None or self._capture_active:
            return
        self.captured_evidence = None
        self.save_capture_button.setEnabled(False)
        self._set_capture_busy(True)
        started = setup._start_operation(
            "Height calibration capture", self.context.capture_surface_height_evidence,
            self._captured, requires_controller=True, invalidate=setup._invalidate_for_home_park,
            on_failure=self._capture_failed,
        )
        if started is False:
            self._capture_failed("Capture did not start; finish the active operation and check controller readiness.")

    def _captured(self, result: tuple[np.ndarray, dict[str, Any]]) -> None:
        self._set_capture_busy(False)
        image, detection = result
        try:
            if not detection.get("detected"):
                raise ValueError(detection.get("reason", "Height targets were not detected"))
            acquisition = detection.get("acquisition", detection)
            if "slot" in acquisition:
                index = self.slot.findData(acquisition["slot"])
                if index < 0:
                    raise ValueError("Captured height slot is invalid")
                self.slot.setCurrentIndex(index)
            for name, control in (("height_mm", self.height), ("uncertainty_mm", self.uncertainty)):
                if name in acquisition:
                    control.setValue(float(acquisition[name]))
            if "reference" in acquisition:
                self.reference.setText(acquisition["reference"])
            self.image = image.copy()
            self.image_binding = self.context.surface_calibration_binding()
            annotated = image.copy()
            for point in detection.get("points", []):
                center = (round(point["image_x"]), round(point["image_y"]))
                cv2.circle(annotated, center, 12, (0, 220, 0), 2, cv2.LINE_AA)
                cv2.putText(annotated, str(point["id"]), (center[0] + 14, center[1] - 8),
                            cv2.FONT_HERSHEY_SIMPLEX, .6, (0, 220, 0), 2, cv2.LINE_AA)
            self.preview.set_image(annotated)
            self.captured_evidence = detection
            self.save_capture_button.setEnabled(True)
            self.result_status.setText("Fresh target captured. Review the detected points and save its exact measured height evidence.")
        except Exception as exc:
            self.result_status.setText(str(exc))

    def save_captured(self) -> None:
        if self.captured_evidence is None:
            return
        try:
            self.context.save_surface_height_evidence(
                self.slot.currentData(), self.height.value(), self.reference.text(),
                self.uncertainty.value(), self.captured_evidence,
            )
            self.model = None
            self.preview_button.setEnabled(False)
            self.save_capture_button.setEnabled(False)
            self.captured_evidence = None
            self.result_status.setText("Evidence saved without replacing the support map. Fit and check the model after all three planes.")
            self.refresh_evidence()
        except Exception as exc:
            self.result_status.setText(str(exc))

    def refresh_evidence(self) -> None:
        try:
            entries = self.context.surface_calibration.load()
            text = "; ".join(
                f"{slot.title()}: {entry.height_mm:.3f} mm ({len(entry.points)} observations)"
                for slot in ("lower", "upper", "check") if (entry := entries.get(slot)) is not None
            )
            self.evidence_status.setText(text or "No height maps saved in this camera profile.")
            if entries:
                self.reference.setText(next(iter(entries.values())).reference)
        except Exception as exc:
            self.evidence_status.setText(str(exc))

    def save_map(self, slot: str) -> None:
        try:
            self.context.save_surface_height_map(slot, self.height.value(), self.reference.text())
        except Exception as exc:
            self.result_status.setText(str(exc))
            return
        self.model = None
        self.preview_button.setEnabled(False)
        self.preview_height.setEnabled(False)
        self.result_status.setText("Evidence saved. Fit again to review the current evidence; replacing either endpoint clears the old check.")
        self.refresh_evidence()

    def solve(self) -> None:
        self.model = None
        self.preview_button.setEnabled(False)
        self.preview_height.setEnabled(False)
        try:
            model = self.context.solve_surface_height_model()
        except Exception as exc:
            self.result_status.setText(str(exc))
            return
        self.model = model
        check = "Independent height check required."
        if model.check_rms_mm is not None:
            check = (
                f"Independent diagnostic check {'PASSES' if model.check_passed else 'FAILS'}: "
                f"RMS {model.check_rms_mm:.3f} mm / max {model.check_max_mm:.3f} mm "
                "(limits 0.30 / 0.60 mm)."
            )
        try:
            require_precision_model(model)
            production = "The 0.100 mm numeric model gate passes; independent physical qualification is still required."
        except Exception as exc:
            production = f"Height correction is not ready: {exc}."
        self.result_status.setText(
            f"Fit RMS {model.fit_rms_mm:.3f} mm / max {model.fit_max_mm:.3f} mm. {check} "
            f"{production} This result does not activate compensation."
        )
        self.preview_height.setRange(model.lower_mm, model.upper_mm)
        self.preview_height.setValue((model.lower_mm + model.upper_mm) / 2)
        self.preview_height.setEnabled(True)
        self.preview_button.setEnabled(self.image is not None and self.image_binding is not None)

    def render_preview(self) -> None:
        if self.image is None or self.model is None:
            return
        try:
            if self.image_binding != self.context.surface_calibration_binding():
                raise ValueError("Photograph belongs to a different camera setup; capture again")
            # Revalidate the evidence and optical binding before every preview.
            current = self.context.solve_surface_height_model()
            if current.model_id != self.model.model_id:
                raise ValueError("Height evidence changed; fit the model again")
            self.preview.set_image(current.rectify(self.image, self.preview_height.value()))
        except Exception as exc:
            self.result_status.setText(str(exc))
            self.preview_button.setEnabled(False)
