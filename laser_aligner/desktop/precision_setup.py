"""Explicit setup measurements and independent placement qualification."""

from __future__ import annotations

import time
from typing import Any

from ..setup_workflow import (
    REGIONS,
    SURVEY_REGIONS,
    BedSurvey,
    PlacementObservation,
    PrecisionEvidenceStore,
    PrecisionQualification,
    RepeatabilityAssessment,
    SurveyObservation,
    binding_json,
)
from .columns import configure_resizable_columns
from .controls import MeasurementSpinBox
from .qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()


def _spin(minimum: float = -1000, maximum: float = 1000) -> Any:
    control = MeasurementSpinBox()
    control.setRange(minimum, maximum)
    control.setDecimals(3)
    control.setSuffix(" mm")
    return control


def _note(text: str) -> Any:
    label = QtWidgets.QLabel(text)
    label.setWordWrap(True)
    return label


def _scroll_page(widget: Any) -> Any:
    scroll = QtWidgets.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
    scroll.setWidget(widget)
    return scroll


class PrecisionSetupDialog(QtWidgets.QDialog):
    def __init__(self, setup: Any):
        super().__init__(setup)
        self.setup = setup
        self.context = setup.context
        self.store = PrecisionEvidenceStore(self.context.surface_calibration.path.parent)
        self.assessment: RepeatabilityAssessment | None = None
        self._capturing = False
        self._qualification_domain = None
        self._saved_observations = {}
        self.setWindowTitle("Precision setup — measured XY placement")
        self.resize(900, 740)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(_note(
            "Target: maximum radial XY placement error plus measurement uncertainty ≤ 0.100 mm. "
            "A software fit or camera repeatability result alone does not qualify physical placement. "
            "Use one rigid, restrained flat surface; disable grid snapping for precise placement."
        ))
        self.tabs = QtWidgets.QTabWidget()
        layout.addWidget(self.tabs, 1)
        self._build_repeatability()
        self._build_qualification()
        self.status = _note("Begin with the one-height assessment. All captures require an explicit action.")
        layout.addWidget(self.status)
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.close)
        layout.addWidget(close)
        try:
            self._qualification_binding = binding_json(self._binding())
        except Exception:
            self._qualification_binding = None
        self._load_qualification()

    def closeEvent(self, event: Any) -> None:
        if self._capturing:
            self.status.setText("Wait for the current capture, or use Machine Setup's Software STOP.")
            event.ignore()
            return
        super().closeEvent(event)

    def _binding(self) -> dict[str, Any]:
        return self.context.precision_setup_binding()

    def _assessment_binding(self) -> str:
        return binding_json(self.context.precision_assessment_binding())

    def _build_repeatability(self) -> None:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.addWidget(_note(
            "Prepare and complete a powered holdout job in Accuracy Validation first, using the "
            "existing guarded Preview and verified marking settings. Leave the target fixed. "
            "Establish the photography pose once, then collect ten still captures and ten independent "
            "Home / park captures. This measures variation, not absolute placement accuracy."
        ))
        self.sampling_note = _note("Source-image sampling has not been inspected.")
        inspect_sampling = QtWidgets.QPushButton("Inspect current source-image sampling")
        inspect_sampling.clicked.connect(self._inspect_sampling)
        layout.addWidget(inspect_sampling)
        layout.addWidget(self.sampling_note)
        self.sampling_table = QtWidgets.QTableWidget(0, 4)
        self.sampling_table.setHorizontalHeaderLabels(("Machine X / Y mm", "mm per source pixel",
                                                       "XY mm per 1 mm height", "In image"))
        self.sampling_table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        configure_resizable_columns(self.sampling_table, (170, 175, 195, 80))
        self.sampling_table.setMinimumHeight(200)
        self.sampling_table.setVisible(False)
        layout.addWidget(self.sampling_table)
        actions = QtWidgets.QHBoxLayout()
        open_validation = QtWidgets.QPushButton("Open Accuracy Validation")
        open_validation.clicked.connect(self._open_validation)
        actions.addWidget(open_validation)
        self.start_assessment = QtWidgets.QPushButton("Start a new assessment")
        self.start_assessment.clicked.connect(self._start_assessment)
        actions.addWidget(self.start_assessment)
        layout.addLayout(actions)
        self.prime_capture = QtWidgets.QPushButton("Establish photo pose — Home / park capture")
        self.prime_capture.clicked.connect(lambda: self._capture("prime"))
        self.still_capture = QtWidgets.QPushButton("Capture without movement")
        self.still_capture.clicked.connect(lambda: self._capture("still"))
        self.park_capture = QtWidgets.QPushButton("Home / park and capture")
        self.park_capture.clicked.connect(lambda: self._capture("parked"))
        for control in (self.prime_capture, self.still_capture, self.park_capture):
            control.setEnabled(False)
            layout.addWidget(control)
        self.repeatability_status = _note("No assessment started.")
        layout.addWidget(self.repeatability_status)
        layout.addStretch()
        self.tabs.addTab(_scroll_page(widget), "1 · Repeatability")

    def _inspect_sampling(self) -> None:
        try:
            diagnostics = self.context.precision_sampling_diagnostics()
            worst = float(diagnostics["worst_mm_per_source_pixel"])
            resolution = diagnostics.get("source_image_size")
            description = f"Worst source sampling: {worst:.4f} mm/pixel. "
            if resolution:
                description += f"Camera source: {resolution[0]} × {resolution[1]} pixels. "
            sensitivity = diagnostics.get("maximum_xy_mm_per_height_mm")
            if sensitivity is not None:
                description += f"Largest XY shift per 1 mm height error: {float(sensitivity):.4f} mm. "
            budget = diagnostics.get("numerical_error_budget")
            if budget:
                known = budget.get("known_numerical_contribution_mm")
                remaining = budget.get("remaining_target_mm")
                if known is not None and remaining is not None:
                    description += (f"Advisory numerical contribution: {float(known):.4f} mm; "
                                    f"remaining from the 0.1 mm target: {float(remaining):.4f} mm. ")
                description += "Physical localization, positioning and height uncertainty still require measurements. "
            self.sampling_note.setText(description + "Sampling describes available image detail; it is not measured placement accuracy.")
            samples = diagnostics["samples"]
            self.sampling_table.setRowCount(len(samples))
            for row, sample in enumerate(samples):
                xy = sample["machine_xy_mm"]
                effect = sample.get("xy_mm_per_height_mm")
                values = (f"{xy[0]:.2f} / {xy[1]:.2f}",
                          f"{sample['worst_mm_per_source_pixel']:.4f}",
                          "Not calibrated" if effect is None else f"{effect:.4f}",
                          "Yes" if sample.get("inside_image") else "No")
                for column, value in enumerate(values):
                    self.sampling_table.setItem(row, column, QtWidgets.QTableWidgetItem(value))
            self.sampling_table.setVisible(True)
        except Exception as exc:
            self.sampling_note.setText(str(exc))

    def _open_validation(self) -> None:
        self.setup.tabs.setCurrentIndex(4)
        self.hide()

    def _start_assessment(self) -> None:
        try:
            self.assessment = RepeatabilityAssessment(self._assessment_binding())
            self._sync_assessment()
            self.status.setText("Assessment started. Establish the photo pose before the ten still captures.")
        except Exception as exc:
            self.status.setText(str(exc))

    def _sync_assessment(self) -> None:
        count = len(self.assessment.still) if self.assessment else 0
        parked = len(self.assessment.parked) if self.assessment else 0
        ready = self.assessment is not None and not self._capturing
        self.start_assessment.setEnabled(not self._capturing)
        self.prime_capture.setEnabled(ready and count == 0)
        pose = bool(getattr(self.setup, "_photo_pose_confirmed", False))
        self.still_capture.setEnabled(ready and pose and count < 10)
        self.park_capture.setEnabled(ready and count == 10 and parked < 10)
        if self.assessment:
            summary = self.assessment.summary()
            parts = []
            for phase, label in (("still", "Without movement"), ("parked", "Home / park")):
                item = summary[phase]
                maximum = item["max_pairwise_mm"]
                parts.append(f"{label}: {item['count']}/10; maximum point-to-point variation "
                             + (f"{maximum:.3f} mm" if maximum is not None else "not measured"))
            self.repeatability_status.setText("\n".join(parts))

    def _capture(self, phase: str) -> None:
        if self.assessment is None or self._capturing:
            return
        if phase == "still" and not getattr(self.setup, "_photo_pose_confirmed", False):
            self.status.setText("Establish the photography pose first.")
            return
        try:
            if self.assessment.binding != self._assessment_binding():
                raise ValueError("Setup identity changed; start a new assessment")
        except Exception as exc:
            self.status.setText(str(exc))
            return
        home_first = phase != "still"
        self._capturing = True
        self._sync_assessment()

        def failed(message: str) -> None:
            self._capturing = False
            self.status.setText(f"Capture was not recorded: {message}")
            self._sync_assessment()

        def captured(result: Any) -> None:
            self._capturing = False
            try:
                self.setup._accuracy_validation_captured(result, home_first=home_first)
                _image, payload = result
                rows = (payload.get("analysis") or {}).get("measurements") or []
                if not payload.get("detected") or payload.get("confidence") != "high" or len(rows) != 5:
                    raise ValueError("All five holdouts must be detected with high confidence")
                if phase != "prime":
                    points = {str(row["id"]): (row["observed_x"], row["observed_y"]) for row in rows}
                    self.assessment.record(phase, points, self._assessment_binding())
                    self.store.save(repeatability=self.assessment)
                self.status.setText("Photo pose established." if phase == "prime" else
                                    "Capture recorded. Choose the next capture explicitly.")
            except Exception as exc:
                self.status.setText(f"Capture was not recorded: {exc}")
            self._sync_assessment()

        started = self.setup._start_operation(
            "Precision assessment capture",
            lambda: self.context.capture_accuracy_validation(home_first=home_first, material_plane=True),
            captured, requires_controller=True, recapture_without_homing=not home_first,
            invalidate=(self.setup._invalidate_for_home_park if home_first else lambda: None),
            on_failure=failed,
        )
        if started is False:
            failed("Another operation is active or the controller is not ready")

    def _build_qualification(self) -> None:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.addWidget(_note(
            "Record independently measured mark placement relative to intended targets. A blank cell "
            "is unmeasured. Enter signed X/Y errors and the radial measurement uncertainty in mm. "
            "Enter each actual target's machine X/Y at the corners, edge midpoints and center of "
            "the checked rectangle. The checked area ends at those targets. Lower = upper records "
            "one-height evidence only."
        ))
        form = QtWidgets.QFormLayout()
        self.lower, self.upper = _spin(), _spin()
        form.addRow("Lower top elevation relative to border", self.lower)
        form.addRow("Upper top elevation relative to border", self.upper)
        self.method = QtWidgets.QLineEdit()
        self.method.setPlaceholderText("Physical measurement method, instrument and reference target")
        self.method.setMaxLength(500)
        form.addRow("Measurement method", self.method)
        self.area_controls = [_spin() for _ in range(4)]
        area = self.context.settings.machine.work_area
        for control, value, title in zip(self.area_controls,
                                       (area.x_min, area.x_max, area.y_min, area.y_max),
                                       ("Checked X minimum", "Checked X maximum", "Checked Y minimum", "Checked Y maximum"),
                                       strict=True):
            control.setValue(value)
            form.addRow(title, control)
        layout.addLayout(form)
        self.qual_table = QtWidgets.QTableWidget(27, 7)
        self.qual_table.setHorizontalHeaderLabels(("Plane", "XY region", "Target X mm", "Target Y mm",
                                                   "X error mm", "Y error mm", "Uncertainty mm"))
        configure_resizable_columns(self.qual_table, (70, 110, 100, 100, 100, 100, 115))
        self.qual_table.setMinimumHeight(220)
        for row, (plane, region) in enumerate((p, r) for p in ("lower", "middle", "upper") for r in REGIONS):
            for column, value in enumerate((plane, region)):
                item = QtWidgets.QTableWidgetItem(value)
                item.setFlags(item.flags() & ~QtCore.Qt.ItemFlag.ItemIsEditable)
                self.qual_table.setItem(row, column, item)
        self.qual_table.itemChanged.connect(self._observation_edited)
        layout.addWidget(self.qual_table, 1)
        self.confirm_physical = QtWidgets.QCheckBox(
            "These are independent physical measurements, with uncertainty, on the stated flat surfaces"
        )
        layout.addWidget(self.confirm_physical)
        new_evidence = QtWidgets.QPushButton("Start fresh physical evidence for the current setup")
        new_evidence.clicked.connect(self._new_qualification)
        layout.addWidget(new_evidence)
        self.save_qualification = QtWidgets.QPushButton("Save and assess independent XY evidence")
        self.save_qualification.clicked.connect(self._save_qualification)
        layout.addWidget(self.save_qualification)
        self.tabs.addTab(_scroll_page(widget), "2 · Independent XY evidence")

    def _evidence_domain(self) -> tuple[float, ...]:
        return (self.lower.value(), self.upper.value(), *(control.value() for control in self.area_controls))

    def _observation_edited(self, item: Any) -> None:
        if item.column() >= 2 and item.text().strip() and self._qualification_domain is None:
            self._qualification_domain = self._evidence_domain()

    def _new_qualification(self) -> None:
        try:
            current = binding_json(self._binding())
            for row in range(27):
                for column in range(2, 7):
                    self.qual_table.takeItem(row, column)
            self._qualification_binding = current
            self._qualification_domain = None
            self._saved_observations = {}
            self.confirm_physical.setChecked(False)
            self.status.setText("Fresh evidence form started. Record new physical measurements; existing saved evidence remains until Save.")
        except Exception as exc:
            self.status.setText(str(exc))

    def _load_qualification(self) -> None:
        try:
            raw = self.store.load().get("qualification")
            if raw is None:
                return
            qualification = PrecisionQualification.from_dict(raw)
            self.lower.setValue(qualification.lower_mm)
            self.upper.setValue(qualification.upper_mm)
            for control, value in zip(self.area_controls, qualification.area, strict=True):
                control.setValue(value)
            by_key = {(item.plane, item.region): item for item in qualification.observations}
            self._saved_observations = by_key
            for row in range(27):
                key = (self.qual_table.item(row, 0).text(), self.qual_table.item(row, 1).text())
                item = by_key.get(key)
                if item:
                    self.method.setText(item.method)
                    for column, value in enumerate((item.target_x_mm, item.target_y_mm,
                                                    item.error_x_mm, item.error_y_mm, item.uncertainty_mm), 2):
                        self.qual_table.setItem(row, column, QtWidgets.QTableWidgetItem(repr(value)))
            self._qualification_binding = qualification.binding
            self._show_review(qualification)
        except Exception as exc:
            self.status.setText(f"Saved evidence could not be accepted: {exc}")

    def _show_review(self, qualification: PrecisionQualification) -> None:
        result = qualification.review(self._binding())
        maximum = result["maximum_with_uncertainty_mm"]
        self.status.setText(
            f"{'QUALIFIED' if result['passed'] else 'NOT QUALIFIED'}: "
            f"{result['count']}/{result['expected_count']} measured positions; "
            + (f"maximum radial error + uncertainty {maximum:.3f} mm. " if maximum is not None else "")
            + " ".join(result["reasons"])
            + " This record does not initiate motion or grant laser authorization."
        )

    def _save_qualification(self) -> None:
        try:
            if not self.confirm_physical.isChecked():
                raise ValueError("Confirm the source is independent physical measurement")
            binding = binding_json(self._binding())
            previous_binding = getattr(self, "_qualification_binding", binding)
            if binding != previous_binding:
                raise ValueError("Saved rows belong to another setup; clear them and acquire fresh evidence")
            if self._qualification_domain is not None and self._qualification_domain != self._evidence_domain():
                raise ValueError("The checked heights or XY area changed; start fresh physical evidence instead of relabeling old measurements")
            observations = []
            now = time.time()
            for row in range(27):
                plane, region = (self.qual_table.item(row, column).text() for column in (0, 1))
                cells = [self.qual_table.item(row, column) for column in range(2, 7)]
                values = [cell.text().strip() if cell is not None else "" for cell in cells]
                if not any(values):
                    continue
                if not all(values):
                    raise ValueError(f"Complete actual target XY, measured errors and uncertainty for {plane} / {region}")
                height = {"lower": self.lower.value(), "upper": self.upper.value(),
                          "middle": (self.lower.value() + self.upper.value()) / 2}[plane]
                target_x, target_y, error_x, error_y, uncertainty = (float(value) for value in values)
                old = self._saved_observations.get((plane, region))
                unchanged = old is not None and (
                    old.height_mm, old.target_x_mm, old.target_y_mm, old.error_x_mm,
                    old.error_y_mm, old.uncertainty_mm, old.method,
                ) == (height, target_x, target_y, error_x, error_y, uncertainty, self.method.text())
                observations.append(PlacementObservation(plane, region, height, error_x, error_y,
                                                        uncertainty, old.observed_at if unchanged else now,
                                                        self.method.text(), target_x, target_y))
            qualification = PrecisionQualification(binding, self.lower.value(), self.upper.value(),
                                                     tuple(control.value() for control in self.area_controls),
                                                     tuple(observations))
            self.store.save(qualification=qualification)
            self._saved_observations = {(item.plane, item.region): item for item in qualification.observations}
            self._qualification_binding = binding
            self._show_review(qualification)
        except Exception as exc:
            self.status.setText(str(exc))


class BedSurveyDialog(QtWidgets.QDialog):
    """Record explicit existing probe actions; suggesting a datum sends no command."""

    def __init__(self, panel: Any):
        super().__init__(panel)
        self.panel = panel
        self.store = getattr(panel, "precision_evidence_store", None)
        self.survey: BedSurvey | None = None
        self.setWindowTitle("Bed leveling and datum — five positions")
        self.resize(650, 540)
        layout = QtWidgets.QVBoxLayout(self)
        layout.addWidget(_note(
            "First reference the fixed border. Place the same rigid target of known thickness at each "
            "corner and center. Position the probe over solid material using the existing controls, "
            "measure, then record that position here. Adjust leveling screws manually and repeat the "
            "whole survey. This records bed shape; it does not perform mesh compensation."
        ))
        self.region = QtWidgets.QComboBox()
        for region in SURVEY_REGIONS:
            self.region.addItem(region)
        self.thickness = _spin(0, 100)
        form = QtWidgets.QFormLayout()
        form.addRow("Target position", self.region)
        form.addRow("Known rigid target thickness", self.thickness)
        layout.addLayout(form)
        row = QtWidgets.QHBoxLayout()
        position = QtWidgets.QPushButton("Use existing probe positioning")
        position.clicked.connect(self._position)
        self.record = QtWidgets.QPushButton("Record current measured surface")
        self.record.clicked.connect(self._record)
        row.addWidget(position)
        row.addWidget(self.record)
        layout.addLayout(row)
        self.table = QtWidgets.QTableWidget(5, 3)
        self.table.setHorizontalHeaderLabels(("Position", "Measured carriage X / Y mm", "Bed elevation mm"))
        configure_resizable_columns(self.table, (130, 260, 155))
        self.table.setEditTriggers(QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers)
        for row, region in enumerate(SURVEY_REGIONS):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(region))
        layout.addWidget(self.table)
        self.summary = _note("No survey recorded.")
        layout.addWidget(self.summary)
        self.use_mean = QtWidgets.QPushButton("Copy surveyed mean into honeycomb height editor")
        self.use_mean.setEnabled(False)
        self.use_mean.clicked.connect(self._use_mean)
        layout.addWidget(self.use_mean)
        reset = QtWidgets.QPushButton("Start a new survey after adjustment")
        reset.clicked.connect(self._reset)
        layout.addWidget(reset)
        close = QtWidgets.QDialogButtonBox(QtWidgets.QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.close)
        layout.addWidget(close)
        if self.store is not None:
            try:
                raw = self.store.load().get("bed_survey")
                if raw is not None:
                    self.survey = BedSurvey.from_dict(raw)
                    self._refresh_survey()
            except Exception as exc:
                self.summary.setText(f"Saved survey cannot be used: {exc}")

    def _binding(self) -> str:
        result = self.panel._result
        if (not self.panel.fresh() or not result.get("reference_ready")
                or not result.get("reference") or not result.get("reference_id")):
            raise ValueError("Refresh status and establish the border reference first")
        # Capture reference identity rather than a changing material measurement ID.
        return binding_json({"reference": result["reference"], "reference_id": result["reference_id"],
                             "controller_session": self.panel._status.get("controller_session_generation"),
                             "probe_offset": result.get("probe_xy_offset_mm"),
                             "firmware_geometry": result.get("firmware_geometry")})

    def _position(self) -> None:
        self.panel.position_probe.setChecked(True)
        self.hide()

    def _reset(self) -> None:
        self.survey = None
        self.use_mean.setEnabled(False)
        self.table.clearContents()
        for row, region in enumerate(SURVEY_REGIONS):
            self.table.setItem(row, 0, QtWidgets.QTableWidgetItem(region))
        self.summary.setText("New survey: measure each position again.")

    def _record(self) -> None:
        try:
            current = self._binding()
            surface = self.panel._result.get("surface")
            if not surface or self.panel._busy or self.panel._pending:
                raise ValueError("Complete an explicit surface measurement before recording")
            if self.store is not None:
                self.store.load()  # Refuse an unreadable evidence file before changing the in-memory survey.
            observation = SurveyObservation(self.region.currentText(), surface["id"],
                                            surface["elevation_mm"], self.thickness.value(),
                                            tuple(surface["carriage_xy_mm"]), surface["measured_at"])
            if self.survey is None:
                self.survey = BedSurvey(current)
            self.survey.record(observation, current)
            if self.store is not None:
                self.store.save(bed_survey=self.survey)
            self._refresh_survey()
            self.region.setCurrentIndex(min(self.region.currentIndex() + 1, 4))
        except Exception as exc:
            self.summary.setText(str(exc))
            self.use_mean.setEnabled(False)

    def _refresh_survey(self) -> None:
        if self.survey is None:
            return
        for row, region in enumerate(SURVEY_REGIONS):
            value = self.survey.observations.get(region)
            if value:
                xy = value.carriage_xy_mm
                self.table.setItem(row, 1, QtWidgets.QTableWidgetItem(f"{xy[0]:.3f} / {xy[1]:.3f}"))
                self.table.setItem(row, 2, QtWidgets.QTableWidgetItem(f"{value.bed_elevation_mm:+.3f} mm"))
        result = self.survey.summary()
        if result["count"]:
            text = (f"{result['count']}/5 positions; mean {result['mean_mm']:+.3f} mm; "
                    f"highest–lowest difference {result['span_mm']:.3f} mm. ")
            if result["count"] == 5 and not result["coverage_valid"]:
                text += "Measured XY does not cover four distinct corners and the center. "
            if self.survey.saved_datum:
                text += f"Associated saved honeycomb datum: {self.survey.saved_datum['value_mm']:+.3f} mm. "
            text += "Review tilt and center deviation before saving a datum."
            self.summary.setText(text)
        try:
            current = self._binding() == self.survey.binding
        except ValueError:
            current = False
        if not current:
            self.summary.setText(self.summary.text() + " Saved reference is not current; start a new survey.")
        self.use_mean.setEnabled(result["complete"] and current)

    def record_saved_datum(self) -> None:
        if self.survey is None:
            return
        try:
            self.survey.associate_saved_datum(self.panel._result["honeycomb_height_mm"],
                                              time.time(), self._binding())
            if self.store is not None:
                self.store.save(bed_survey=self.survey)
            self._refresh_survey()
        except Exception as exc:
            self.summary.setText(f"Datum was saved by the controller; survey association was not recorded: {exc}")

    def _use_mean(self) -> None:
        try:
            if self.survey is None or self.survey.binding != self._binding():
                raise ValueError("Reference changed; repeat the survey")
            result = self.survey.summary()
            if not result["complete"]:
                raise ValueError("Record all five positions first")
            value = result["mean_mm"]
            if not self.panel.honeycomb_height.minimum() <= value <= self.panel.honeycomb_height.maximum():
                raise ValueError("Survey mean is outside the honeycomb datum editor range")
            self.panel.honeycomb_height.setValue(value)
            self.panel._edit_honeycomb()
            self.summary.setText("Mean copied. Review it, then use Save honeycomb height explicitly. No command was sent.")
        except Exception as exc:
            self.summary.setText(str(exc))
