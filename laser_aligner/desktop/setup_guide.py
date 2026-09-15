from __future__ import annotations

from importlib.resources import files

from ..setup_workflow import SETUP_STEPS, PrecisionEvidenceStore, binding_json, evaluate_setup_steps
from .qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()

_STEP_HEADINGS = (
    "1. Camera",
    "2. Lens",
    "3. Bed Mapping",
    "4. Fine Registration",
    "5. Accuracy Validation",
    "6. Coordinate Audit",
    "7. Z / laser focus",
)


def load_setup_runbook() -> str:
    return (
        files("laser_aligner.operator_docs")
        .joinpath("PERMANENT_CAMERA_SETUP.md")
        .read_text(encoding="utf-8")
    )


class SetupGuideDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Permanent Camera Setup Guide")
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.resize(820, 760)
        layout = QtWidgets.QVBoxLayout(self)
        checklist = QtWidgets.QGroupBox("Guided setup checklist")
        checklist_layout = QtWidgets.QGridLayout(checklist)
        self.step_checks = {}
        self.step_buttons = {}
        self.step_status_labels = {}
        for row, step in enumerate(SETUP_STEPS):
            check = QtWidgets.QCheckBox(step.title + " · note")
            check.setToolTip(step.instructions + " Checklist marks are operator notes, not calibration authority.")
            self.step_checks[step.id] = check
            checklist_layout.addWidget(check, row * 2, 0)
            button = QtWidgets.QPushButton("Open step")
            button.setObjectName(f"setupStep{step.id.title()}")
            button.clicked.connect(lambda _checked=False, action=step.action: self.open_step(action))
            checklist_layout.addWidget(button, row * 2, 1)
            self.step_buttons[step.id] = button
            status = QtWidgets.QLabel()
            status.setWordWrap(True)
            status.setToolTip("Requires: " + ", ".join(step.evidence_requirements))
            checklist_layout.addWidget(status, row * 2 + 1, 0, 1, 2)
            self.step_status_labels[step.id] = status
        self.checklist_note = QtWidgets.QLabel("Checklist marks record operator progress only; each action keeps its existing guards.")
        self.checklist_note.setWordWrap(True)
        checklist_layout.addWidget(self.checklist_note, len(SETUP_STEPS) * 2, 0, 1, 2)
        self.checklist_scroll = QtWidgets.QScrollArea()
        self.checklist_scroll.setWidgetResizable(True)
        self.checklist_scroll.setWidget(checklist)
        self.checklist_scroll.setMinimumHeight(240)
        layout.addWidget(self.checklist_scroll, 3)
        refresh = QtWidgets.QPushButton("Refresh evidence status")
        refresh.clicked.connect(self.refresh_status)
        layout.addWidget(refresh)
        self.browser = QtWidgets.QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setMarkdown(load_setup_runbook())
        layout.addWidget(self.browser, 2)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close
        )
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)
        self._store = None
        context = getattr(parent, "context", None)
        if context is not None and hasattr(context, "surface_calibration"):
            self._store = PrecisionEvidenceStore(context.surface_calibration.path.parent)
            try:
                checked = self._store.load()["checklist"]
                for key, check in self.step_checks.items():
                    check.setChecked(key in checked)
            except Exception as exc:
                self.checklist_note.setText(f"Saved checklist could not be loaded: {exc}")
        for check in self.step_checks.values():
            check.toggled.connect(self._save_checklist)
        self.refresh_status()

    def refresh_status(self) -> None:
        """Read cached/service calibration status; never probe, capture or move."""
        context = getattr(self.parent(), "context", None)
        readiness, evidence = {}, {}
        current = assessment = survey_identity = datum = None
        errors = []

        def inspect(operation, default=None):
            try:
                return operation()
            except Exception as exc:
                errors.append(str(exc))
                return default

        if self._store is not None:
            evidence = inspect(self._store.load, {})
        if context is not None:
            current = inspect(context.precision_setup_binding)
            assessment = inspect(lambda: binding_json(context.precision_assessment_binding()))
            camera = inspect(lambda: context.camera_calibration_readiness(), {})
            readiness["camera"] = camera.get("state") == "READY"
            model = getattr(getattr(context, "lens", None), "model", None)
            readiness["lens"] = bool(model and model.quality.get("gate") in {"pass", "warning"}
                                     and list(model.image_size) == camera.get("expected_resolution"))
            bed = inspect(lambda: context.bed_status(), {})
            readiness["bed"] = bed.get("calibrated") is True
            datum = None if current is None else current.get("honeycomb_height_mm")
            snapshot_method = getattr(getattr(context, "machine", None), "setup_evidence_snapshot", None)
            setup_snapshot = inspect(snapshot_method) if callable(snapshot_method) else None
            if setup_snapshot is not None:
                readiness["gauge"] = setup_snapshot.get("calibration_compatible") is True
                survey_identity = binding_json({
                    "reference": setup_snapshot["reference"], "reference_id": setup_snapshot["reference_id"],
                    "controller_session": setup_snapshot["controller_session"],
                    "probe_offset": setup_snapshot.get("probe_xy_offset_mm"),
                    "firmware_geometry": setup_snapshot.get("firmware_geometry"),
                })
            # Older services can display a fresh focus readback, but an unavailable
            # new snapshot must never fall back to stale widget authority.
            panel = getattr(getattr(self.parent(), "focus_workspace", None), "panel", None)
            if not callable(snapshot_method) and panel is not None and panel.fresh():
                result = panel._result
                readiness["gauge"] = result.get("calibration_compatible") is True
                if result.get("reference_ready") and result.get("reference") and result.get("reference_id"):
                    survey_identity = binding_json({
                        "reference": result["reference"], "reference_id": result["reference_id"],
                        "controller_session": panel._status.get("controller_session_generation"),
                        "probe_offset": result.get("probe_xy_offset_mm"),
                        "firmware_geometry": result.get("firmware_geometry"),
                    })
            def height_ready():
                from ..calibration.material_plane import require_precision_model
                require_precision_model(context.solve_surface_height_model())
                return True
            readiness["heights"] = inspect(height_ready, False)
        self.step_statuses = evaluate_setup_steps(
            readiness=readiness, evidence=evidence, current_binding=current,
            assessment_binding=assessment, survey_binding=survey_identity, saved_datum_mm=datum,
        )
        for key, status in self.step_statuses.items():
            self.step_status_labels[key].setText(f"{status.state.upper()}: {status.reason}")
            self.step_buttons[key].setToolTip(f"Suggested next action: {status.next_action}. All tabs remain available.")
        if errors:
            self.checklist_note.setText("Some current evidence is unavailable. Refresh its setup tab. Note checkboxes do not complete steps.")

    def open_step(self, action: str) -> None:
        navigate = getattr(self.parent(), "navigate_setup_step", None)
        if callable(navigate):
            self.hide()
            navigate(action)

    def _save_checklist(self) -> None:
        if self._store is not None:
            try:
                self._store.save(checklist=[key for key, check in self.step_checks.items() if check.isChecked()])
            except Exception as exc:
                self.checklist_note.setText(f"Checklist was not saved: {exc}")

    def show_step(self, tab_index: int) -> None:
        self.refresh_status()
        index = max(0, min(int(tab_index), len(_STEP_HEADINGS) - 1))
        cursor = self.browser.document().find(_STEP_HEADINGS[index])
        if not cursor.isNull():
            self.browser.setTextCursor(cursor)
            self.browser.ensureCursorVisible()
        self.show()
        self.raise_()
        self.activateWindow()


def show_setup_guide(
    parent: QtWidgets.QWidget,
    tab_index: int = 0,
) -> SetupGuideDialog:
    dialog = getattr(parent, "_setup_guide_dialog", None)
    if not isinstance(dialog, SetupGuideDialog):
        dialog = SetupGuideDialog(parent)
        parent._setup_guide_dialog = dialog
    dialog.show_step(tab_index)
    return dialog
