"""Diagnostic collection and review UI for material-height camera calibration."""

from __future__ import annotations

from typing import Any

import numpy as np

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
        self.setWindowTitle("Material height — calibration study")
        self.resize(920, 700)
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Keep the camera, focus, XY reference and photography pose fixed. Solve a fresh base map "
            "at each measured height, then save it here before mapping the next height. "
            "Use a flat, restrained target and include paper and spacers in the measurement. "
            "The fixed border under the parked probe is the height datum. Negative heights are below it."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        warning = QtWidgets.QLabel(
            "Study only: this preserves evidence and previews a height-specific image. "
            "It does not change tracing, job coordinates or the active bed map. "
            "Automatic thickness probing requires a verified controller measurement operation."
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
        layout.addLayout(form)
        actions = QtWidgets.QHBoxLayout()
        self.save_buttons = {}
        for slot, label in (("lower", "Save lower map"), ("upper", "Save upper map"), ("check", "Save independent check")):
            button = QtWidgets.QPushButton(label)
            button.setObjectName(f"saveSurface{slot.title()}")
            button.clicked.connect(lambda _checked=False, chosen=slot: self.save_map(chosen))
            actions.addWidget(button)
            self.save_buttons[slot] = button
        self.solve_button = QtWidgets.QPushButton("Fit and check camera model")
        self.solve_button.clicked.connect(self.solve)
        actions.addWidget(self.solve_button)
        layout.addLayout(actions)
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
        layout.addWidget(close)
        self.refresh_evidence()

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
                f"Independent check {'PASSES' if model.check_passed else 'FAILS'}: "
                f"RMS {model.check_rms_mm:.3f} mm / max {model.check_max_mm:.3f} mm "
                "(limits 0.30 / 0.60 mm)."
            )
        self.result_status.setText(
            f"Fit RMS {model.fit_rms_mm:.3f} mm / max {model.fit_max_mm:.3f} mm. {check} "
            "This result does not activate compensation."
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
