"""Explicit, laser-off surface measurement and gauge-based focus setup."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

from ..errors import CalibrationError
from .controls import MeasurementSpinBox
from .focus_bed_view import FocusBedView
from .machine_state import machine_payload, project_machine_state
from .main_view_probe import MainViewProbe
from .mainboard_z import _number, _read_allowed, _session
from .qt import require_qt
from .z_telemetry import EnderZTelemetry

QtCore, _, QtWidgets = require_qt()

FRESH_SECONDS = 5.0
POLL_SECONDS = 2.0


def _validate_main_view_selection(controller: Any, selection: dict[str, Any]) -> None:
    """Recheck display provenance in the worker without touching Qt widgets."""
    if selection.get("main_view") is not True:
        return
    context = controller.runtime.context
    if (not controller.review_signature_is_current(selection.get("review_signature"))
            or controller._camera_review_active()
            or controller._camera_source_generation != selection.get("source_generation")
            or getattr(getattr(context.lens, "model", None), "model_id", None)
            != selection.get("lens_model_id")
            or controller.runtime.settings.calibration.bed.pixels_per_mm
            != selection.get("pixels_per_mm")):
        raise RuntimeError("Main camera view changed; select the probe point again.")


class LaserFocusPanel(QtWidgets.QWidget):
    actionRequested = QtCore.Signal(str, dict)
    refreshRequested = QtCore.Signal()
    previewInvalidated = QtCore.Signal()
    cameraModeRequested = QtCore.Signal(bool)
    cameraMoveRequested = QtCore.Signal()
    cameraSelectionInvalidated = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None, *, calibration_mode: bool = False) -> None:
        super().__init__(parent)
        self.calibration_mode = calibration_mode
        self._camera_location = "live image" if calibration_mode else "main view on the left"
        self._camera_prompt = f"Choose Position probe, then click a solid spot in the {self._camera_location}."
        self._status: dict[str, Any] = {}
        self._result: dict[str, Any] = {}
        self._received_at: float | None = None
        self._live_z = EnderZTelemetry()
        self._busy = False
        self._pending = False
        self._preview_id: str | None = None
        self._clearance_edited = False
        self._offset_edited = False
        self._offset_entered = [False, False]
        self._camera_target: dict[str, Any] | None = None
        self._camera_point_rejected = False
        self._camera_live = False
        self._failure_message: str | None = None
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Measure surface elevation above the border, then position the laser at a known gap. "
            "This is laser-off setup; it does not change camera calibration or start a job."
            if calibration_mode else
            "Measure the workpiece to calculate the focus for every job on it. "
            "The job moves to that focus before laser output."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        readouts = QtWidgets.QGridLayout()
        self.height = QtWidgets.QLabel("Z — mm")
        self.height.setWordWrap(True)
        self.height.setObjectName("focusZReadout")
        self.height.setStyleSheet("font-size: 18px; font-weight: bold;")
        self.height.setToolTip("Controller-reported Z; not an encoder measurement.")
        self.surface = QtWidgets.QLabel("Surface elevation: not measured")
        self.surface.setWordWrap(True)
        self.maximum = QtWidgets.QLabel("Active maximum: unknown")
        self.maximum.setWordWrap(True)
        self.calibration = QtWidgets.QLabel("Laser offset: not taught")
        self.calibration.setWordWrap(True)
        readouts.addWidget(self.height, 0, 0)
        readouts.addWidget(self.surface, 0, 1)
        readouts.addWidget(self.maximum, 1, 0)
        readouts.addWidget(self.calibration, 1, 1)
        layout.addLayout(readouts)
        self.live_z_note = QtWidgets.QLabel()
        self.live_z_note.setWordWrap(True)
        self.live_z_note.setObjectName("focusLiveZNote")
        self.live_z_note.hide()
        layout.addWidget(self.live_z_note)
        ender_row = QtWidgets.QHBoxLayout()
        self.ender_status = QtWidgets.QLabel("Ender: waiting for connection status")
        self.ender_status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.ender_status.setWordWrap(True)
        self.reconnect_ender = QtWidgets.QPushButton("Reconnect Ender")
        self.reconnect_ender.setToolTip(
            "Retry the Ender connection and verify its identity and outputs off. "
            "Supported recovery firmware may restart and initialize the probe pin. "
            "No XY/Z movement or homing is replayed."
        )
        recovery_notice = QtWidgets.QLabel(
            "Reconnect may restart the Ender and move its probe pin. Keep the pin path clear."
        )
        recovery_notice.setWordWrap(True)
        layout.addWidget(recovery_notice)
        ender_row.addWidget(self.ender_status, 1)
        ender_row.addWidget(self.reconnect_ender)
        layout.addLayout(ender_row)
        self.z_retention_group = QtWidgets.QGroupBox("Saved Z between sessions")
        retention_layout = QtWidgets.QVBoxLayout(self.z_retention_group)
        retention_row = QtWidgets.QHBoxLayout()
        self.z_retention_status = QtWidgets.QLabel()
        self.z_retention_status.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.z_retention_status.setWordWrap(True)
        self.forget_z = QtWidgets.QPushButton("Forget saved Z")
        self.forget_z.setToolTip(
            "Discard the saved Z position and current border reference. "
            "Reference the border again before Z positioning. The taught gauge offset is kept."
        )
        retention_row.addWidget(self.z_retention_status, 1)
        retention_row.addWidget(self.forget_z)
        retention_layout.addLayout(retention_row)
        self.z_retention_note = QtWidgets.QLabel(
            "If the Z axis, probe mount, or border/support was physically moved while off, "
            "choose Forget saved Z, then Reference border again. The taught gauge offset is kept."
        )
        self.z_retention_note.setWordWrap(True)
        retention_layout.addWidget(self.z_retention_note)
        layout.addWidget(self.z_retention_group)
        self.failure_detail = QtWidgets.QLabel()
        self.failure_detail.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.failure_detail.setWordWrap(True)
        self.failure_detail.hide()
        layout.addWidget(self.failure_detail)
        self.next_step = QtWidgets.QLabel("Next: Home / park XY, then reference the border.")
        self.next_step.setObjectName("focusNextStep")
        self.next_step.setWordWrap(True)
        self.next_step.setStyleSheet("font-weight: bold; padding: 6px;")
        layout.addWidget(self.next_step)

        self.xy_recovery_group = QtWidgets.QGroupBox("Recover after interrupted probing")
        recovery_layout = QtWidgets.QVBoxLayout(self.xy_recovery_group)
        self.xy_recovery_clear = QtWidgets.QCheckBox(
            "Probe is physically retracted; the ENTIRE XY homing/search/parking path\n"
            "is clear at the actual current height"
        )
        recovery_layout.addWidget(self.xy_recovery_clear)
        self.recover_xy = QtWidgets.QPushButton("Recover XY at current height")
        self.recover_xy.setToolTip(
            "Home and park the primary XY controller without moving Ender Z. "
            "The retained clearance restriction remains until a separate border reference."
        )
        recovery_layout.addWidget(self.recover_xy)
        self.xy_recovery_note = QtWidgets.QLabel(
            "Z stays at its current height. Then separately confirm headroom and reference the border."
        )
        self.xy_recovery_note.setWordWrap(True)
        recovery_layout.addWidget(self.xy_recovery_note)
        layout.addWidget(self.xy_recovery_group)

        self.reference_group = QtWidgets.QGroupBox("Reference and measure")
        grid = self.reference_layout = QtWidgets.QGridLayout(self.reference_group)
        self.clearance = MeasurementSpinBox()
        self.clearance.setRange(20.0, 80.0)
        self.clearance.setDecimals(1)
        self.clearance.setSuffix(" mm")
        self.clearance.setValue(30.0)
        self.clearance.setToolTip("Clearance above the border; must fit the machine's remaining travel.")
        self.clearance_label = QtWidgets.QLabel("Clearance Z")
        grid.addWidget(self.clearance_label, 0, 0)
        grid.addWidget(self.clearance, 0, 1)
        self.range_note = QtWidgets.QLabel("Measurement range awaits firmware readback")
        self.range_note.setWordWrap(True)
        grid.addWidget(self.range_note, 0, 2, 1, 2)
        self.path_clear = QtWidgets.QCheckBox("Headroom and Z path clear to the selected clearance")
        grid.addWidget(self.path_clear, 1, 0, 1, 4)
        self.reference = QtWidgets.QPushButton("Home / park + reference")
        self.reference.setToolTip("Home and park XY over the border, establish Z, then return to clearance. Keep the entire XY and Z path clear.")
        self.return_clearance = QtWidgets.QPushButton("Return to clearance")
        grid.addWidget(self.reference, 2, 0, 1, 2)
        grid.addWidget(self.return_clearance, 2, 2, 1, 2)
        self.position_probe = QtWidgets.QPushButton("Position probe")
        self.position_probe.setCheckable(True)
        self.position_probe.setStyleSheet("QPushButton:checked { border: 1px solid #ffcf40; color: #ffcf40; }")
        self.position_probe.setToolTip(f"Select a probe target in the {self._camera_location}.")
        self.camera_target = QtWidgets.QLabel(self._camera_prompt)
        self.camera_target.setWordWrap(True)
        grid.addWidget(self.position_probe, 4, 0, 1, 4)
        grid.addWidget(self.camera_target, 6, 0, 1, 4)
        self.position_probe.toggled.connect(self.cameraModeRequested)
        self.position_probe.clicked.connect(self._position_probe)
        self.offset_readout = QtWidgets.QLabel("Probe XY offset: not set · use a wide, flat patch")
        self.offset_readout.setWordWrap(True)
        grid.addWidget(self.offset_readout, 9, 0, 1, 4)
        self.align_probe = QtWidgets.QPushButton("Put probe over laser spot")
        self.align_probe.setToolTip("At clearance, shift XY by minus the measured probe offset. Then measure.")
        self.align_laser = QtWidgets.QPushButton("Return laser to measured spot")
        self.align_laser.setToolTip("At clearance, return XY by the measured offset and retain this measurement.")
        grid.addWidget(self.align_probe, 10, 0, 1, 2)
        grid.addWidget(self.align_laser, 10, 2, 1, 2)
        self.measure = QtWidgets.QPushButton("Measure surface")
        self.clear_surface = QtWidgets.QPushButton("Clear measurement")
        grid.addWidget(self.measure, 12, 0, 1, 2)
        grid.addWidget(self.clear_surface, 12, 2, 1, 2)
        self.position_note = QtWidgets.QLabel("Remove the gauge and keep the full XY path clear. Measure only a solid, flat target with room for probe deployment.")
        if not calibration_mode:
            self.position_note.setText(
                "Measure surface sets the focus for every job on this flat workpiece. "
                "Confirm one flat surface across the job, the gauge removed, and clear Z/XY paths. "
                "Measure again after changing the material or its supports."
            )
        self.position_note.setWordWrap(True)
        grid.addWidget(self.position_note, 11, 0, 1, 4)
        if not calibration_mode:
            # Full-width actions fit the existing narrow Machine inspector.
            self.align_laser.setText("Return laser to spot")
            self.path_clear.setText("Headroom and Z path clear\nto the selected clearance")
            self.xy_recovery_group.setTitle("Interrupted probe recovery")
            self.xy_recovery_clear.setText(
                "Probe is physically\nretracted;\n"
                "the ENTIRE XY homing/\n"
                "search/parking path\n"
                "is clear at the actual\ncurrent height"
            )
            while grid.count():
                grid.takeAt(0)
            grid.addWidget(self.clearance_label, 0, 0)
            grid.addWidget(self.clearance, 0, 1)
            for row, widget in (
                (1, self.range_note), (2, self.path_clear), (3, self.reference),
                (4, self.return_clearance), (5, self.position_probe),
                (7, self.camera_target), (8, self.offset_readout),
                (9, self.align_probe), (10, self.align_laser),
                (11, self.position_note), (12, self.measure), (13, self.clear_surface),
            ):
                grid.addWidget(widget, row, 0, 1, 2)
        layout.addWidget(self.reference_group)

        self.offset_editor = self.offset_x = self.offset_y = self.apply_offset = None
        self.down = self.up = self.z_step = self.teach = self.gauge = self.forget = self.teaching_limits = None
        if calibration_mode:
            self.offset_editor = QtWidgets.QGroupBox("Probe / laser XY offset")
            offset_layout = QtWidgets.QGridLayout(self.offset_editor)
            offset_note = QtWidgets.QLabel(
                "Measured probe position relative to laser center, in machine axes: "
                "probe = laser + offset. Leave unset until measured."
            )
            offset_note.setWordWrap(True)
            offset_layout.addWidget(offset_note, 0, 0, 1, 4)
            self.offset_x, self.offset_y = MeasurementSpinBox(), MeasurementSpinBox()
            for column, name, editor in ((0, "X", self.offset_x), (2, "Y", self.offset_y)):
                editor.setRange(-100.0, 100.0)
                editor.setDecimals(3)
                editor.setSuffix(" mm")
                editor.lineEdit().clear()
                editor.lineEdit().setPlaceholderText("Not set")
                editor.lineEdit().textEdited.connect(lambda _text, i=column // 2: self._edit_offset(i))
                editor.valueChanged.connect(lambda _value, i=column // 2: self._edit_offset(i))
                offset_layout.addWidget(QtWidgets.QLabel(name), 1, column)
                offset_layout.addWidget(editor, 1, column + 1)
            self.apply_offset = QtWidgets.QPushButton("Save measured XY offset")
            offset_layout.addWidget(self.apply_offset, 2, 0, 1, 4)
            layout.addWidget(self.offset_editor)

            teach = QtWidgets.QGroupBox("Teach once with the 7 mm gauge")
            teach_layout = QtWidgets.QGridLayout(teach)
            hint = QtWidgets.QLabel(
                "Approach with 1, 2 or 5 mm steps only while there is room for the whole step. "
                "Switch to 0.1 mm near the 7 mm gauge fit. Remove the gauge before each move; "
                "fit it between the laser reference face and this same surface."
            )
            hint.setWordWrap(True)
            teach_layout.addWidget(hint, 0, 0, 1, 4)
            self.down = QtWidgets.QPushButton("Z−")
            self.up = QtWidgets.QPushButton("Z+")
            self.z_step = QtWidgets.QComboBox()
            for step in (0.1, 0.5, 1.0):
                self.z_step.addItem(f"{step:g} mm", step)
            self.z_step.setCurrentIndex(0)
            teach_layout.addWidget(self.down, 1, 0)
            teach_layout.addWidget(self.up, 1, 1)
            teach_layout.addWidget(self.z_step, 1, 2, 1, 2)
            self.teach = QtWidgets.QPushButton("Save current Z as 7 mm gap")
            teach_layout.addWidget(self.teach, 3, 0, 1, 3)
            self.gauge = QtWidgets.QCheckBox("7 mm gauge fits at this Z")
            teach_layout.addWidget(self.gauge, 2, 0, 1, 4)
            self.forget = QtWidgets.QPushButton("Forget taught offset")
            teach_layout.addWidget(self.forget, 3, 3)
            self.teaching_limits = QtWidgets.QLabel()
            self.teaching_limits.setWordWrap(True)
            teach_layout.addWidget(self.teaching_limits, 4, 0, 1, 4)
            layout.addWidget(teach)

        self.preview_group = QtWidgets.QGroupBox("Preview and position" if calibration_mode else "Workpiece focus", self)
        focus_layout = QtWidgets.QGridLayout(self.preview_group)
        self.gap = QtWidgets.QComboBox()
        for gap in (7.0, 5.0, 3.0):
            self.gap.addItem(f"{gap:g} mm gap", gap)
        self.preview = QtWidgets.QPushButton("Preview target")
        self.move = QtWidgets.QPushButton("Move to focus")
        self.target = QtWidgets.QLabel("Target Z: preview required")
        self.target.setWordWrap(True)
        self.move_note = QtWidgets.QLabel()
        self.move_note.setWordWrap(True)
        self.move_note.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        focus_layout.addWidget(self.gap, 0, 0)
        focus_layout.addWidget(self.preview, 0, 1, 1, 2)
        focus_layout.addWidget(self.move_note, 3, 0, 1, 3)
        focus_layout.addWidget(self.move, 4, 0, 1, 3)
        focus_layout.addWidget(self.target, 1, 0, 1, 3)
        self.use_job = QtWidgets.QPushButton("Use measured focus for next job")
        self.job_note = QtWidgets.QLabel("Job focus: not selected. Preview the measured surface, then select.")
        self.job_note.setWordWrap(True)
        self.move_confirmation = QtWidgets.QLabel(
            "Choosing Move to focus confirms the gauge is removed and the path to the target is clear."
        )
        self.move_confirmation.setWordWrap(True)
        focus_layout.addWidget(self.move_confirmation, 2, 0, 1, 3)
        self.job_confirmation = QtWidgets.QLabel(
            "Choosing Use measured focus for next job confirms one flat surface across the job, "
            "the gauge removed, and clear Z and travel paths."
        )
        self.job_confirmation.setWordWrap(True)
        focus_layout.addWidget(self.job_confirmation, 5, 0, 1, 3)
        focus_layout.addWidget(self.use_job, 6, 0, 1, 3)
        focus_layout.addWidget(self.job_note, 7, 0, 1, 3)
        if not calibration_mode:
            for control in (self.preview, self.move, self.move_note, self.move_confirmation,
                            self.use_job, self.job_confirmation):
                control.hide()
            while focus_layout.count():
                focus_layout.takeAt(0)
            for row, control in enumerate((
                self.gap, self.target, self.job_note,
            )):
                focus_layout.addWidget(control, row, 0)
        layout.addWidget(self.preview_group)
        footer = QtWidgets.QHBoxLayout()
        self.message = QtWidgets.QLabel("Connect the machine, then refresh focus support.")
        self.message.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.refresh = QtWidgets.QPushButton("Refresh")
        footer.addWidget(self.message, 1)
        footer.addWidget(self.refresh)
        layout.addLayout(footer)
        layout.addStretch()

        for button, action in ((self.measure, "measure"),
                               (self.teach, "teach"), (self.preview, "preview"),
                               (self.use_job, "use_job"), (self.move, "move"), (self.return_clearance, "clearance"),
                               (self.clear_surface, "clear_surface"), (self.forget, "forget"),
                               (self.forget_z, "forget_z"),
                               (self.apply_offset, "set_xy_offset"), (self.align_probe, "align_probe"),
                               (self.align_laser, "align_laser")):
            if button is not None:
                button.clicked.connect(lambda _checked=False, a=action: self.request(a))
        if calibration_mode:
            self.down.clicked.connect(lambda: self.request("jog", -float(self.z_step.currentData())))
            self.up.clicked.connect(lambda: self.request("jog", float(self.z_step.currentData())))
        self.reference.clicked.connect(lambda: self.request(self._home_action()))
        self.reconnect_ender.clicked.connect(lambda: self.request("recover"))
        self.recover_xy.clicked.connect(lambda: self.request("recover_xy"))
        self.refresh.clicked.connect(self.refreshRequested)
        self.clearance.valueChanged.connect(self._edit_clearance)
        self.clearance.lineEdit().textEdited.connect(self._edit_clearance)
        self.gap.currentIndexChanged.connect(self._edit_gap)
        for checkbox in (self.path_clear, self.gauge, self.xy_recovery_clear):
            if checkbox is not None:
                checkbox.toggled.connect(self._sync)
        if calibration_mode:
            self.z_step.currentIndexChanged.connect(self._sync)
        # Enter commits an editor; it must never activate a motion button.
        for button in self.findChildren(QtWidgets.QPushButton):
            button.setAutoDefault(False)
        self._display_timer = QtCore.QTimer(self)
        self._display_timer.setInterval(200)
        self._display_timer.timeout.connect(self._sync_z_display)
        self._display_timer.start()
        self._sync()

    def fresh(self) -> bool:
        return self._received_at is not None and time.monotonic() - self._received_at <= FRESH_SECONDS

    def clear_camera_target(self, message: str | None = None) -> None:
        message = self._camera_prompt if message is None else message
        self._camera_target = None
        self._camera_point_rejected = False
        self.camera_target.setText(message)
        self.cameraSelectionInvalidated.emit(message)
        self._sync()

    def reject_camera_target(self, message: str) -> None:
        # Retain only visual feedback for the new click. A previous valid
        # target must never remain actionable after a rejected selection.
        self._camera_target = None
        self._camera_point_rejected = True
        self.camera_target.setText(message)
        self._sync()

    def set_camera_target(self, target: dict[str, Any]) -> None:
        self._camera_target = dict(target)
        self._camera_point_rejected = False
        x, y = target["target_machine_xy_mm"]
        ox, oy = self._result["probe_xy_offset_mm"]
        sx, sy = self._result.get("laser_spot_offset_mm", [0.0, 0.0])
        self.camera_target.setText(
            f"Probe target: X {x:.2f}, Y {y:.2f} mm · Head: X {x-ox-sx:.2f}, Y {y-oy-sy:.2f} mm. "
            + ("Outside original calibration grid. "
               if target.get("within_original_calibration_grid") is False else "")
            + "Bed-plane estimate; check probe alignment before measuring raised work."
        )
        self._sync()

    def _edit_offset(self, axis: int) -> None:
        self._offset_edited = True
        self._offset_entered[axis] = True
        self._parameters_edited()

    def _offset_value(self) -> list[float] | None:
        if not self.calibration_mode or not all(self._offset_entered):
            return None
        values = []
        for editor in (self.offset_x, self.offset_y):
            if not editor.hasAcceptableInput():
                return None
            value = _number(editor.valueFromText(editor.text()))
            if value is None or not -100 <= value <= 100:
                return None
            values.append(value)
        return values

    def invalidate(self, message: str, *, clear_surface: bool = False,
                   clear_confirmation: bool = False) -> None:
        self.xy_recovery_clear.setChecked(False)
        self.clear_camera_target()
        self._received_at = None
        self._preview_id = None
        self.target.setText("Target Z: preview required")
        self.height.setText("Z — mm")
        self._show_configured_values()
        if clear_surface:
            self._result["surface"] = None
            self.surface.setText("Surface elevation: not measured")
        if clear_confirmation:
            self._live_z.clear()
            self._reset_checks(self.path_clear, self.gauge)
        self.message.setText(message)
        self._sync()

    def set_failure(self, message: str) -> None:
        """Keep the actual failure visible through transient status changes."""
        self._failure_message = message
        self.failure_detail.setText(message)
        self.failure_detail.show()
        self.invalidate(message, clear_surface=True)

    def _show_configured_values(self) -> None:
        if self._result.get("available") is not False:
            self.ender_status.setText("Ender: awaiting current status")
        maximum = _number(self._result.get("max_z_mm"))
        if maximum is not None:
            self.maximum.setText(f"Configured maximum: {maximum:g} mm above border")
        offset = self._result.get("probe_xy_offset_mm")
        if (isinstance(offset, (list, tuple)) and len(offset) == 2
                and all(_number(value) is not None and -100 <= value <= 100 for value in offset)):
            self.offset_readout.setText(
                f"Configured probe offset: X {offset[0]:+.3f} mm · Y {offset[1]:+.3f} mm"
            )
        calibration = self._result.get("calibration")
        if isinstance(calibration, Mapping) and _number(calibration.get("focus_offset_mm")) is not None:
            self.calibration.setText(
                f"Configured laser offset: {calibration['focus_offset_mm']:+.3f} mm · compatibility not checked"
            )

    def _set_unavailable_result(self, result: Mapping[str, Any]) -> None:
        ender = result.get("ender")
        readback = result.get("current_readback")
        maximum = _number(result.get("max_z_mm"))
        if (result.get("recovery_available") is not True or not isinstance(ender, Mapping)
                or ender.get("ready") is not False or ender.get("recovery_required") is not True
                or type(ender.get("generation")) is not int or ender["generation"] < 0
                or (ender.get("fault") is not None and type(ender.get("fault")) is not str)
                or not isinstance(readback, Mapping) or readback.get("fresh") is not False
                or readback.get("z_known") is not False or readback.get("z_mm") is not None
                or result.get("reference_ready") is not False
                or any(result.get(key) is not None for key in ("surface", "preview", "xy_sequence"))
                or maximum is None or not 20 <= maximum <= 80):
            raise ValueError("Invalid unavailable Ender status; update the matching Pi support.")
        offset = result.get("probe_xy_offset_mm")
        if offset is not None and not (isinstance(offset, (list, tuple)) and len(offset) == 2
                and all(_number(value) is not None and -100 <= value <= 100 for value in offset)):
            raise ValueError("Invalid configured probe offset.")
        calibration = result.get("calibration")
        if calibration is not None and (not isinstance(calibration, Mapping) or not calibration.get("id")
                                       or _number(calibration.get("focus_offset_mm")) is None):
            raise ValueError("Invalid configured laser offset.")
        self._result = dict(result)
        self.ender_status.setText("Ender: unavailable · connection retry required")
        if offset is None:
            self.offset_readout.setText("Configured probe offset: not set")
        if calibration is None:
            self.calibration.setText("Configured laser offset: not taught")
        self.set_failure(ender.get("fault") or "Ender connection is not initialized. Choose Reconnect Ender.")

    def _parameters_edited(self, *_args: object) -> None:
        self.clear_camera_target()
        self._preview_id = None
        self.target.setText("Target Z: preview required")
        self.previewInvalidated.emit()
        self._sync()

    def _edit_clearance(self, *_args: object) -> None:
        self._clearance_edited = True
        self.path_clear.setChecked(False)
        self._parameters_edited()

    def _edit_gap(self, *_args: object) -> None:
        self._parameters_edited()
        if not self.calibration_mode:
            self.request("set_job_gap")

    def _can_set_job_gap(self) -> bool:
        return bool(
            not self.calibration_mode and not self._busy and not self._pending
            and _read_allowed(self._status) and self.fresh()
            and self._result.get("available") is True
            and self._result.get("workpiece_focus_available") is True
            and (self._result.get("job_focus") or {}).get("reusable") is True
        )

    def _clearance_value(self) -> float | None:
        if not self.clearance.hasAcceptableInput():
            return None
        value = _number(self.clearance.valueFromText(self.clearance.text()))
        maximum = _number(self._result.get("max_z_mm"))
        return value if value is not None and maximum is not None and 20 <= value <= maximum else None

    def set_result(self, result: Mapping[str, Any]) -> None:
        if not isinstance(result, Mapping):
            raise ValueError("Invalid focus status; update the matching Pi support.")
        if result.get("available") is False:
            self._set_unavailable_result(result)
            return
        readback = result.get("current_readback")
        maximum = _number(result.get("max_z_mm"))
        if (result.get("available") is not True or not isinstance(readback, Mapping)
                or readback.get("fresh") is not True or type(readback.get("z_known")) is not bool
                or maximum is None or not 20 <= maximum <= 80
                or (readback.get("z_known") is True and _number(readback.get("z_mm")) is None)):
            raise ValueError("Focus support requires the matching Pi update and surface-height V2 firmware.")
        ender = result.get("ender")
        if ender is not None and (not isinstance(ender, Mapping) or ender.get("ready") is not True
                                  or ender.get("recovery_required") is not False):
            raise ValueError("Invalid connected Ender status.")
        for key, number_key in (("surface", "elevation_mm"), ("calibration", "focus_offset_mm"),
                                ("preview", "target_z_mm")):
            item = result.get(key)
            if item is not None and (not isinstance(item, Mapping) or not item.get("id")
                                     or _number(item.get(number_key)) is None):
                raise ValueError(f"Invalid focus {key} readback.")
        previous_readback = self._result.get("current_readback") or {}
        previous_ender = self._result.get("ender") or {}
        if (result.get("action") != "status"
                or previous_ender.get("generation") != (ender or {}).get("generation")
                or any(self._result.get(key) != result.get(key) for key in (
                    "xy_recovery_available", "requires_clearance", "max_z_mm"
                ))
                or any(previous_readback.get(key) != readback.get(key) for key in ("z_known", "z_mm"))):
            self.xy_recovery_clear.setChecked(False)
        if (self._camera_target is not None or self._camera_point_rejected) and (
            any(self._result.get(key) != result.get(key) for key in (
                "probe_xy_offset_mm", "laser_spot_offset_mm", "current_carriage_xy_mm", "reference_ready", "max_z_mm"
            )) or any(previous_readback.get(key) != readback.get(key) for key in ("z_known", "z_mm"))
        ):
            self.clear_camera_target("Machine position or offset changed; select the probe point again.")
        self._result = dict(result)
        job_focus = result.get("job_focus")
        if not self.calibration_mode and isinstance(job_focus, Mapping) and job_focus.get("reusable") is True:
            gap_index = self.gap.findData(job_focus.get("gap_mm"))
            if gap_index >= 0:
                blocker = QtCore.QSignalBlocker(self.gap)
                self.gap.setCurrentIndex(gap_index)
                del blocker
        self._failure_message = None
        self.failure_detail.clear()
        self.failure_detail.hide()
        self.ender_status.setText("Ender: connected · current firmware readback received")
        offset = result.get("probe_xy_offset_mm")
        valid_offset = (isinstance(offset, (list, tuple)) and len(offset) == 2
                        and all(_number(v) is not None and -100 <= v <= 100 for v in offset))
        if valid_offset:
            self.offset_readout.setText(f"Probe relative to laser: X {offset[0]:+.3f} mm · Y {offset[1]:+.3f} mm")
            if self.calibration_mode and (not self._offset_edited or result.get("action") == "set_xy_offset"):
                for editor, value in zip((self.offset_x, self.offset_y), offset, strict=True):
                    blocker = QtCore.QSignalBlocker(editor)
                    editor.setValue(value)
                    del blocker
                self._offset_edited = False
                self._offset_entered = [True, True]
        else:
            self.offset_readout.setText("Probe XY offset: not set · use a wide, flat patch")
        self._received_at = time.monotonic()
        required_return = _number(result.get("return_clearance_mm"))
        if required_return is not None and not self._clearance_edited:
            previous_clearance = self.clearance.value()
            blocker = QtCore.QSignalBlocker(self.clearance)
            self.clearance.setValue(min(maximum, max(self.clearance.value(), required_return)))
            del blocker
            if self.clearance.value() != previous_clearance:
                self.path_clear.setChecked(False)
        known = readback.get("z_known") is True
        self.height.setText(f"Z {readback['z_mm']:.3f} mm" if known else "Z unknown · reference required")
        self.maximum.setText(f"Active maximum: {maximum:g} mm above border")
        surface = result.get("surface")
        self.surface.setText(
            f"Surface elevation: {surface['elevation_mm']:+.3f} mm above border"
            if surface else "Surface elevation: not measured"
        )
        if self.calibration_mode:
            # Only offer larger steps after the Pi explicitly advertises support.
            steps = (0.1, 0.5, 1.0, 2.0, 5.0) if result.get("max_teaching_step_mm") == 5 else (0.1, 0.5, 1.0)
            if tuple(self.z_step.itemData(i) for i in range(self.z_step.count())) != steps:
                selected = self.z_step.currentData()
                blocker = QtCore.QSignalBlocker(self.z_step)
                self.z_step.clear()
                for step in steps:
                    self.z_step.addItem(f"{step:g} mm", step)
                self.z_step.setCurrentIndex(steps.index(selected) if selected in steps else 0)
                del blocker
        calibration = result.get("calibration")
        self.calibration.setText(
            f"Taught laser offset: {calibration['focus_offset_mm']:+.3f} mm"
            + (" · re-teach required" if result.get("calibration_compatible") is not True else "")
            if calibration else "Laser offset: not taught"
        )
        preview = result.get("preview")
        if result.get("action") == "preview":
            self._preview_id = str(preview["id"]) if preview else None
        if not self._preview_matches(preview):
            self._preview_id = None
        self.target.setText(
            f"Target Z: {preview['target_z_mm']:.3f} mm · {preview['gap_mm']:g} mm gap"
            if self._preview_id else "Target Z: preview required"
        )
        self.message.setText({
            "reference": "Border reference saved. Position over a flat surface at clearance, then measure.",
            "home_retained": "XY parked. Saved Z reference retained; select and measure the current workpiece.",
            "measure": (
                "Surface measured. Teach with the gauge, or preview using the saved offset."
                if self.calibration_mode else
                "Surface measured. Check Workpiece focus for the calculated job height."
            ),
            "set_job_gap": "Workpiece focus gap updated; no movement sent.",
            "jog": "Z jog finished. Check the 7 mm gauge fit before saving.",
            "teach": "Taught offset saved. Remove the gauge before moving.",
            "preview": ("Target previewed; no movement sent." if self._preview_id else
                        "Preview is no longer current; choose Preview target again."),
            "move": "Focus position reached. Return to clearance before moving XY or changing the workpiece.",
            "clearance": "At clearance. XY positioning and workpiece changes can resume.",
            "clear_surface": "Surface measurement cleared.",
            "forget": "Taught offset forgotten.",
            "forget_z": "Saved Z forgotten. Reference the border again. The taught gauge offset is kept.",
            "set_xy_offset": "Measured XY offset saved. Align the laser visually over your target, then put the probe there.",
            "align_probe": "Probe is over the selected laser spot. Confirm the solid target, then measure.",
            "position_probe": "Probe positioned at the camera target. Check alignment and the solid target, then measure.",
            "align_laser": "Laser returned to the measured spot at clearance. Fit the gauge using small Z steps.",
            "recover": "Ender connection verified. Establish the border reference before Z positioning.",
            "recover_xy": "XY recovered at the current height. Separately confirm headroom and reference the border.",
        }.get(str(result.get("action")), "Reported position is refreshed while idle."))
        self._sync()

    def _preview_matches(self, preview: Any) -> bool:
        surface = self._result.get("surface")
        calibration = self._result.get("calibration")
        current = _number((self._result.get("current_readback") or {}).get("z_mm"))
        target = _number(preview.get("target_z_mm")) if isinstance(preview, Mapping) else None
        previous_z = _number(preview.get("current_z_mm")) if isinstance(preview, Mapping) else None
        maximum = _number(self._result.get("max_z_mm"))
        return bool(
            self._preview_id and isinstance(preview, Mapping) and surface
            and str(preview.get("id")) == self._preview_id
            and preview.get("measurement_id") == surface.get("id")
            and isinstance(calibration, Mapping) and bool(calibration.get("id"))
            and preview.get("calibration_id") == calibration.get("id")
            and preview.get("gap_mm") == self.gap.currentData()
            and preview.get("clearance_z_mm") == self._clearance_value()
            and target is not None and maximum is not None and 0 <= target <= maximum
            and current is not None and previous_z is not None and abs(current - previous_z) <= .05
        )

    def _move_block_reason(self) -> str | None:
        """Explain the same prerequisites that enable the explicit Z move."""
        if self._busy or self._pending:
            return "Wait for the current operation to finish."
        if not _read_allowed(self._status):
            return "Wait for an idle, connected controller with the laser disarmed."
        if self._result.get("available") is False:
            return "Choose Reconnect Ender to restore its position readback."
        if not self.fresh():
            return "Refresh focus status to obtain a current Z position."
        if not project_machine_state(self._status).can_jog:
            return "Home / park XY and enable motion before positioning Z."
        if self._result.get("xy_recovery_pending_reference") is True:
            return "Separately confirm headroom and reference the border after XY recovery."
        readback = self._result.get("current_readback") or {}
        if self._result.get("reference_ready") is not True or readback.get("z_known") is not True:
            return "Reference the border to establish the Z position."
        surface = self._result.get("surface")
        if not surface:
            return "Measure the surface before previewing its focus position."
        if (self._result.get("xy_sequence") or {}).get("phase") == "probe":
            return "Return the laser to the measured spot at clearance."
        if self._result.get("calibration_compatible") is not True:
            return "Teach and save the 7 mm gauge fit in Machine Setup → 7 · Z / laser focus."
        clearance = self._clearance_value()
        if clearance is None:
            return "Set Clearance Z between 20 mm and the active maximum."
        preview = self._result.get("preview")
        if isinstance(preview, Mapping):
            target = _number(preview.get("target_z_mm"))
            maximum = _number(self._result.get("max_z_mm"))
            if target is not None and maximum is not None and not 0 <= target <= maximum:
                return f"Target Z {target:.3f} mm is outside the allowed 0 to {maximum:g} mm range."
            if preview.get("measurement_id") != surface.get("id"):
                return "The measured surface changed; choose Preview target again."
            if preview.get("calibration_id") != (self._result.get("calibration") or {}).get("id"):
                return "The taught offset changed; choose Preview target again."
            if preview.get("gap_mm") != self.gap.currentData():
                return "The selected gap changed; choose Preview target again."
            if preview.get("clearance_z_mm") != clearance:
                return "Clearance Z changed; choose Preview target again."
            current, previous = _number(readback.get("z_mm")), _number(preview.get("current_z_mm"))
            if current is not None and previous is not None and abs(current - previous) > .05:
                return "Z moved after the preview; choose Preview target again."
        if not self._preview_matches(preview):
            return "Choose Preview target for the selected gap."
        if not self.path_clear.isChecked():
            return "Confirm ‘Headroom and Z path clear to the selected clearance’ above."
        return None

    def observe_z_status(self, status: Mapping[str, Any]) -> None:
        self._live_z.update(status)
        self._sync_z_display()

    def _sync_z_display(self) -> None:
        probe = self._status.get("z_probe")
        active = bool(self._busy or self._pending or (isinstance(probe, Mapping) and probe.get("active") is True))
        fresh = self.fresh() and _read_allowed(self._status) and self._result.get("available") is True
        observed = self._live_z.readout(active=active, readback_at=self._received_at if fresh else None)
        self.live_z_note.setVisible(observed is not None)
        if observed is not None:
            self.height.setText(observed[0])
            self.live_z_note.setText(observed[1])
        elif fresh and not active and self._live_z.readback_current(self._received_at):
            readback = self._result.get("current_readback") or {}
            z = _number(readback.get("z_mm"))
            known = readback.get("z_known") is True and z is not None
            self.height.setText(f"Z {z:.3f} mm" if known else "Z unknown · reference required")
        else:
            self.height.setText("Z — mm")

    def _sync_z_retention(self, ready: bool) -> None:
        retention = self._result.get("z_retention")
        reported = (
            isinstance(retention, Mapping)
            and type(retention.get("available")) is bool
            and type(retention.get("restored")) is bool
            and isinstance(retention.get("reason"), str)
        )
        self.z_retention_group.setVisible(reported)
        self.forget_z.setEnabled(bool(ready and reported and retention["available"]
                                     and self._result.get("available") is True))
        if not reported:
            self.z_retention_status.clear()
        elif not self.fresh():
            self.z_retention_status.setText("Saved Z: refresh to check the saved position.")
        else:
            prefix = ("Saved Z: restored from a normal shutdown. " if retention["restored"]
                      and retention["available"] else "Saved Z: ")
            self.z_retention_status.setText(prefix + (retention["reason"] or "No saved position restored."))

    def _sync(self) -> None:
        self._sync_z_display()
        idle = _read_allowed(self._status) and not self._busy and not self._pending
        ready = idle and self.fresh()
        self._sync_z_retention(ready)
        projection = project_machine_state(self._status)
        clearance = self._clearance_value()
        moving = ready and projection.can_jog and self.path_clear.isChecked() and clearance is not None
        reference = self._result.get("reference_ready") is True
        surface = bool(self._result.get("surface"))
        known = (self._result.get("current_readback") or {}).get("z_known") is True
        z = _number((self._result.get("current_readback") or {}).get("z_mm"))
        maximum = _number(self._result.get("max_z_mm"))
        requires_clearance = self._result.get("requires_clearance") is True
        recovery_reference_only = self._result.get("xy_recovery_pending_reference") is True
        sequence = self._result.get("xy_sequence") or {}
        probe_phase = sequence.get("phase") == "probe"
        reference_only = self._reference_only()
        home_action = self._home_action()
        self.reference.setText({"reference": "Reference border", "home_retained": "Home / park XY",
                                "home_reference": "Home / park + reference"}[home_action])
        self.reference.setToolTip(
            "Home and park XY, keeping the restored Z reference. No border probing is requested."
            if home_action == "home_retained" else
            "Establish Z at the border and return to clearance. Keep the Z path clear."
            if reference_only else
            "Home and park XY over the border, establish Z, then return to clearance. Keep the entire XY and Z path clear."
        )
        self.reference.setEnabled(bool(
            ready and self._result.get("available") is True and self.path_clear.isChecked()
            and clearance is not None and (projection.can_jog if reference_only else projection.can_home)
            and (reference_only or not requires_clearance or known)
        ))
        self.reconnect_ender.setEnabled(bool(idle and self._result.get("recovery_available") is True))
        recovery_needed = requires_clearance and projection.controller_state == "READY_HOME_REQUIRED"
        self.xy_recovery_group.setVisible(recovery_needed)
        ender = self._result.get("ender") or {}
        recovery_ready = bool(
            ready and recovery_needed and projection.can_home and clearance is not None
            and self._result.get("xy_recovery_available") is True
            and self._result.get("available") is True and ender.get("ready") is True
            and ender.get("recovery_required") is False
        )
        self.xy_recovery_clear.setEnabled(recovery_ready)
        self.recover_xy.setEnabled(recovery_ready and self.xy_recovery_clear.isChecked())
        self.xy_recovery_note.setText(
            "Update the matching Pi companion to recover XY at the current height."
            if self._result.get("xy_recovery_available") is not True else
            "Choose Reconnect Ender first, then freshly check the physical probe and entire XY path."
            if self._result.get("available") is not True or ender.get("ready") is not True else
            "Z stays at its current height. Then separately confirm headroom and reference the border."
        )
        moving = moving and not recovery_reference_only
        self.measure.setEnabled(bool(moving and known and reference
                                     and sequence.get("phase") != "laser"))
        return_minimum = _number(self._result.get("return_clearance_mm"))
        return_allowed = return_minimum is None or (clearance is not None and clearance >= return_minimum)
        self.return_clearance.setEnabled(bool(moving and known and z is not None and return_allowed
                                             and clearance is not None and z <= clearance + .05))
        self.clear_surface.setEnabled(bool(ready and surface))
        if self.calibration_mode:
            self.forget.setEnabled(bool(ready and self._result.get("calibration")
                                        and self._result.get("calibration_persistent") is True))
        at_clearance = not requires_clearance and (
            not reference or (z is not None and clearance is not None and z >= clearance - .05)
        )
        offset = self._result.get("probe_xy_offset_mm")
        offset_valid = (isinstance(offset, (list, tuple)) and len(offset) == 2
                        and all(_number(v) is not None and -100 <= v <= 100 for v in offset))
        xy_available = self._result.get("xy_offset_available") is True
        transfer_ready = (moving and known and reference and at_clearance
                          and xy_available and offset_valid and not self._offset_edited
                          and z is not None and clearance is not None and abs(z - clearance) <= .05)
        if self.calibration_mode:
            self.apply_offset.setEnabled(bool(ready and xy_available and not requires_clearance
                                              and self._offset_edited and self._offset_value() is not None))
        self.align_probe.setEnabled(bool(transfer_ready and not probe_phase))
        self.align_laser.setEnabled(bool(transfer_ready and probe_phase and surface))
        selection_ready = (moving and known and reference and not requires_clearance
                           and offset_valid and not self._offset_edited and self._camera_live
                           and self._result.get("position_probe_available") is True
                           and z is not None and clearance is not None and abs(z-clearance) <= .05)
        self.position_probe.setEnabled(bool(selection_ready))
        self.position_probe.setText("Move probe here" if self._camera_target else "Position probe")
        self.position_probe.setToolTip("Move to the selected point at clearance; gauge removed and full XY path clear."
                                       if self._camera_target else f"Select a probe target in the {self._camera_location}.")
        if not selection_ready and self.position_probe.isChecked():
            self.position_probe.setChecked(False)
        if self.calibration_mode:
            for editor in (self.offset_x, self.offset_y):
                editor.setEnabled(bool(idle and xy_available))
        if self.calibration_mode:
            teach_ready = moving and reference and surface and known and not probe_phase
            step = float(self.z_step.currentData())
            measured = self._result.get("surface") or {}
            geometry = self._result.get("firmware_geometry") or {}
            contact, probe_z = _number(measured.get("contact_z_mm")), _number(geometry.get("probe_z_mm"))
            # Older companions keep their original restriction until updated.
            travel_min = self._result.get("focus_travel_min_z_mm")
            floor = (0.0 if type(travel_min) in {int, float} and travel_min == 0 else
                     max(0.0, contact - probe_z) if contact is not None and probe_z is not None else None)
            self.teaching_limits.setText(
                f"Teaching Z range: {floor:g} to {maximum:g} mm. Z is the controller position, not the laser gap."
                + (" Select a smaller step to descend further." if known and z is not None and z > floor and z-step < floor else
                   " Z travel minimum reached." if known and z is not None and z <= floor else "")
                if floor is not None and maximum is not None else "Teaching Z range: waiting for controller readback."
            )
            self.down.setEnabled(bool(teach_ready and z is not None and floor is not None and z - step >= floor))
            self.up.setEnabled(bool(teach_ready and z is not None and maximum is not None and z + step <= maximum))
            self.teach.setEnabled(bool(teach_ready and self.gauge.isChecked()
                                      and self._result.get("calibration_persistent") is True))
        self.preview.setEnabled(bool(self.calibration_mode and ready and known and reference and surface and not probe_phase and clearance is not None
                                    and self._result.get("calibration_compatible") is True))
        move_block = self._move_block_reason()
        self.move.setEnabled(self.calibration_mode and move_block is None)
        self.use_job.setEnabled(bool(self.calibration_mode and move_block is None
                                    and self._result.get("job_focus_available") is True))
        job_focus = self._result.get("job_focus")
        self.job_note.setText(
            f"Next job: {job_focus['gap_mm']:g} mm gap at Z{job_focus['target_z_mm']:.3f}; "
            f"travel at Z{job_focus['clearance_z_mm']:g}. Re-select after changing the work."
            if job_focus else "Job focus: not selected. Preview the measured surface, then select."
        )
        workpiece_ready = self._sync_workpiece_focus() if not self.calibration_mode else False
        self.move_note.setText(
            f"Move unavailable: {move_block}" if move_block else
            f"Choose Move to focus to move Z to {self._result['preview']['target_z_mm']:.3f} mm."
        )
        for control in (self.clearance, self.gap, self.path_clear, self.gauge, self.z_step):
            if control is not None:
                control.setEnabled(idle)
        self.refresh.setEnabled(idle)
        self.next_step.setText(
            projection.control_reason if projection.status_trusted and not projection.can_control else
            "Next: Wait for controller recovery, then Home / park XY."
            if machine_payload(self._status).get("controller_state") in {"RECOVERING", "OPENING", "SYNCHRONIZING", "STOPPING"} else
            "Next: Connect the controller before focus setup." if not _read_allowed(self._status) else
            "Waiting for the current operation to finish." if self._busy or self._pending else
            "Next: Reconnect Ender to restore its readback." if self._result.get("available") is False else
            "Next: Refresh focus status before positioning." if not self.fresh() else
            "Next: Check the physical probe and entire XY path, then Recover XY at current height."
            if recovery_needed and self._result.get("xy_recovery_available") is True else
            "Next: Update the matching Pi companion for XY recovery at the current height."
            if recovery_needed else
            "Next: Confirm headroom and the Z path, then reference the border before further positioning."
            if recovery_reference_only else
            "Next: Confirm the Z path, then reference the border." if reference_only else
            "Next: Confirm the clear path, then Home / park XY; the saved Z needs no border probe."
            if home_action == "home_retained" and not projection.can_jog else
            "Workpiece focus is ready for every job. Measure again after changing the material or supports."
            if workpiece_ready and not self._camera_target and not self.position_probe.isChecked() else
            "Next: Confirm the clear path, then choose Home / park + reference." if not reference else
            "Next: Return Z to clearance before transferring XY." if requires_clearance and probe_phase else
            "Next: Choose Move probe here with the gauge removed and XY path clear." if self._camera_target else
            f"Next: Click a solid probe spot in the {self._camera_location}." if self.position_probe.isChecked() else
            f"Jobs blocked: {self._result['job_focus_block_reason']}"
            if not self.calibration_mode and self._result.get("job_focus_block_reason") else
            "Next: Return the laser to the measured spot at clearance." if probe_phase and surface else
            "Next: Confirm the solid target under the probe, then measure surface." if probe_phase else
            f"Next: Choose Position probe and click a flat target in the {self._camera_location}." if not surface and offset_valid else
            "Next: Position over a wide, flat patch and measure its surface." if not surface else
            ("Next: Fit the 7 mm gauge with small Z steps, then save the setting." if self.calibration_mode else
             "Next: Teach the 7 mm gauge in Machine Setup → 7 · Z / laser focus.") if not self._result.get("calibration_compatible") else
            "Next: Measure the current workpiece to establish the focus for jobs."
            if not self.calibration_mode else
            f"Next: {move_block}" if move_block else
            "Next: Choose Move to focus to position the laser."
        )
        contact_min = _number(self._result.get("contact_min_mm"))
        self.range_note.setText(
            (f"Contact elevation range: {contact_min:g} to {clearance - 15:g} mm; higher work needs more clearance.").replace("-", "−")
            if clearance is not None and contact_min is not None else
            "Contact elevation range: waiting for controller geometry." if clearance is not None else
            "Clearance must be 20 mm to the active maximum."
        )
        if requires_clearance and return_minimum is not None:
            self.range_note.setText(self.range_note.text() + f" Return requires Z ≥ {return_minimum:g} mm.")

    def _sync_workpiece_focus(self) -> bool:
        plan = self._result.get("job_focus")
        self.target.setText("Focus Z: measure the current workpiece")
        if not self.fresh() or self._busy or self._pending or not _read_allowed(self._status):
            self.target.setText("Focus Z: awaiting current status")
            self.job_note.setText("Workpiece focus: refresh when the machine is idle.")
            return False
        reason = self._result.get("job_focus_block_reason")
        if reason:
            self.job_note.setText(f"Jobs blocked: {reason}")
            return False
        if isinstance(plan, Mapping) and plan.get("reusable") is True:
            gap = _number(plan.get("gap_mm"))
            target = _number(plan.get("target_z_mm"))
            clearance = _number(plan.get("clearance_z_mm"))
            if gap is not None and target is not None and clearance is not None:
                self.target.setText(f"Focus Z: {target:.3f} mm · {gap:g} mm gap")
                self.job_note.setText(
                    f"Every job uses this focus and travels at Z{clearance:g} mm. "
                    "Measure again after changing the material or its supports."
                )
                return True
        self.job_note.setText(
            "Workpiece focus: measure a solid, flat spot before starting jobs."
            if self._result.get("workpiece_focus_available") is True else
            "Automatic workpiece focus requires the matching Pi companion update."
        )
        return False

    def request(self, action: str, value: float | None = None) -> None:
        self._sync()
        if action == "set_job_gap":
            if self._can_set_job_gap():
                self.actionRequested.emit(action, {"confirmed": True, "gap_mm": float(self.gap.currentData())})
            return
        button = self.up if action == "jog" and value and value > 0 else self.down if action == "jog" else {
            "reference": self.reference, "home_reference": self.reference, "home_retained": self.reference,
            "measure": self.measure, "teach": self.teach,
            "preview": self.preview, "use_job": self.use_job, "move": self.move, "clearance": self.return_clearance,
            "clear_surface": self.clear_surface, "forget": self.forget, "forget_z": self.forget_z,
            "set_xy_offset": self.apply_offset, "align_probe": self.align_probe, "align_laser": self.align_laser,
            "recover": self.reconnect_ender, "recover_xy": self.recover_xy,
        }.get(action)
        if action in {"reference", "home_reference", "home_retained"} and action != self._home_action():
            return
        if button is None or not button.isEnabled():
            return
        self.clearance.interpretText()
        arguments: dict[str, Any] = {
            "confirmed": True, "clearance_z_mm": float(self.clearance.value()),
            "gap_mm": 7.0 if action == "teach" else float(self.gap.currentData()),
        }
        if action in {"teach", "jog", "preview", "align_laser"}:
            arguments["measurement_id"] = self._result["surface"]["id"]
        if action in {"move", "use_job"}:
            arguments["preview_id"] = self._preview_id
        if action == "jog":
            arguments["value"] = value
        if action == "set_xy_offset":
            arguments["value"] = self._offset_value()
        if action == "forget_z":
            arguments = {"confirmed": True}
        if action != "recover_xy":
            self.xy_recovery_clear.setChecked(False)
        if action in {"recover", "recover_xy", "forget_z"}:
            self._reset_checks(self.path_clear, self.gauge)
        if action in {"jog", "home_reference", "home_retained", "reference", "measure", "move", "clearance",
                      "set_xy_offset", "align_probe", "align_laser"}:
            self._reset_checks(self.gauge)
        self.actionRequested.emit(action, arguments)
        self.xy_recovery_clear.setChecked(False)

    @staticmethod
    def _reset_checks(*checkboxes) -> None:
        for checkbox in checkboxes:
            if checkbox is not None:
                checkbox.setChecked(False)

    @staticmethod
    def _has_retained_reference(result: Mapping[str, Any]) -> bool:
        retention = result.get("z_retention")
        readback = result.get("current_readback") or {}
        return bool(
            isinstance(retention, Mapping) and retention.get("available") is True
            and retention.get("restored") is True and isinstance(retention.get("reason"), str)
            and result.get("available") is True and result.get("reference_ready") is True
            and result.get("xy_recovery_pending_reference") is not True
            and isinstance(readback, Mapping) and readback.get("fresh") is True
            and readback.get("z_known") is True and _number(readback.get("z_mm")) is not None
        )

    def _home_action(self) -> str:
        if self._reference_only():
            return "reference"
        return "home_retained" if self._has_retained_reference(self._result) else "home_reference"

    def _reference_only(self) -> bool:
        return bool(self._result.get("xy_recovery_pending_reference") is True or (
            self._result.get("requires_clearance") is True
            and (self._result.get("current_readback") or {}).get("z_known") is not True
            and project_machine_state(self._status).can_jog
        ))

    def can_move_probe(self) -> bool:
        return bool(self.position_probe.isEnabled() and self._camera_target)

    def _position_probe(self) -> None:
        self._sync()
        if self.can_move_probe():
            self.position_probe.setChecked(True)
            self.cameraMoveRequested.emit()



class LaserFocusCoordinator(QtCore.QObject):
    """Own local preview authority; serialize all communication off the Qt thread."""

    def __init__(self, panel: LaserFocusPanel, controller: Any,
                 parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.panel = panel
        self.controller = controller
        self._status: dict[str, Any] = {}
        self._epoch = 0
        self._parameter_epoch = 0
        self._camera_epoch = 0
        self._pending = False
        self._mutation = False
        self._home_refresh_pending = False
        self._external_busy = False
        self._controller_busy = bool(getattr(controller, "_active_tasks", 0))
        self._busy = self._controller_busy
        self.panel._busy = self._busy
        self._closed = False
        self._suspended = False
        self._error = False
        self._queued: tuple[str, dict[str, Any]] | None = None
        self._last_request = -math.inf
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.tick)
        controller.statusChanged.connect(self.set_status)
        controller.busyChanged.connect(self.set_busy)
        controller.stopInitiated.connect(self.stopped)
        panel.actionRequested.connect(self.request)
        panel.refreshRequested.connect(self.refresh)
        panel.previewInvalidated.connect(self.parameters_edited)
        panel._display_timer.timeout.connect(self.observe_cached_z)
        self._timer.start()

    def observe_cached_z(self) -> None:
        probe = self._status.get("z_probe")
        active = self._busy or self._mutation or self.panel._pending or (
            isinstance(probe, Mapping) and probe.get("active") is True
        )
        if (self._closed or self._suspended or self._modal_blocked()
                or not active or not self.panel.isVisible()
                or getattr(self.controller, "_shutdown_started", False)):
            return
        # Read only the machine cache, not AppContext.status or typed focus
        # status. The live readout cannot alter this coordinator's authority.
        machine = getattr(getattr(getattr(self.controller, "runtime", None), "context", None), "machine", None)
        read = getattr(machine, "status", None)
        if not callable(read):
            return
        try:
            snapshot = read()
        except Exception:
            snapshot = {}
        self.panel.observe_z_status(snapshot if isinstance(snapshot, Mapping) else {})

    def set_status(self, status: Mapping[str, Any]) -> None:
        machine = dict(machine_payload(status))
        changed = _session(machine) != _session(self._status)
        became_readable = _read_allowed(machine) and not _read_allowed(self._status)
        own_activity = self._mutation and project_machine_state(machine).can_send_diagnostic
        if (self._mutation and self._home_refresh_pending and machine.get("status_stale") is True
                and project_machine_state(machine).can_control
                and machine.get("controller_state") in {"READY_HOME_REQUIRED", "READY_MOTION"}
                and machine.get("connected") is True and not machine.get("armed")
                and not (machine.get("job") or {}).get("running")):
            # Home changes the remote state revision before its full cached
            # snapshot is refreshed. This permits only the pending status read;
            # motion remains disabled until that fresh snapshot is checked.
            own_activity = True
        if changed or (not _read_allowed(machine) and not own_activity):
            self._epoch += 1
            self._queued = None
            self.panel.invalidate("Machine state changed; refresh after it is ready.",
                                  clear_surface=True, clear_confirmation=changed)
            if changed:
                self._error = False
                self._last_request = -math.inf
        self._status = machine
        self.panel._status = machine
        self.panel.observe_z_status(machine)
        if became_readable:
            # One observation after recovery may clear an old read failure.
            # A repeated failure relatches; no connection or motion is retried.
            self._error = False
            self._last_request = -math.inf
        self.panel._sync()

    def set_busy(self, busy: bool) -> None:
        self._controller_busy = bool(busy)
        self._busy = self._controller_busy or self._external_busy
        self.panel._busy = self._busy
        if self._busy and not self._mutation:
            self._epoch += 1
            self._queued = None
            self.panel.invalidate("Machine operation in progress; measure again after positioning.", clear_surface=True)
        self.panel._sync()

    @property
    def mutation_busy(self) -> bool:
        return self._mutation or self._queued is not None

    def set_external_busy(self, busy: bool) -> None:
        self._external_busy = bool(busy)
        self.set_busy(self._controller_busy)

    def parameters_edited(self) -> None:
        self._parameter_epoch += 1
        self._camera_epoch += 1

    def camera_selection_changed(self) -> None:
        # Camera freshness/selection only authorizes XY positioning. A focus
        # preview uses the already measured surface, independently of the view.
        self._camera_epoch += 1

    def stopped(self) -> None:
        self._epoch += 1
        self._queued = None
        self._error = True
        self.panel.invalidate("STOP requested. Reconnect / Home and establish the border reference again.",
                              clear_surface=True, clear_confirmation=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._epoch += 1
        self._queued = None
        self._timer.stop()
        self.panel._display_timer.timeout.disconnect(self.observe_cached_z)
        self.controller.statusChanged.disconnect(self.set_status)
        self.controller.busyChanged.disconnect(self.set_busy)
        self.controller.stopInitiated.disconnect(self.stopped)

    def set_suspended(self, suspended: bool) -> None:
        """Pause observation and revoke camera targets; let reference work finish."""
        if self._closed or self._suspended == suspended:
            return
        self._suspended = suspended
        if suspended:
            self._camera_epoch += 1
            self._queued = None
            if not self._mutation:
                self._epoch += 1
                self.panel._pending = False
                self.panel.invalidate("Z controls paused; refresh when this section is active.")
        else:
            self._last_request = -math.inf

    def _modal_blocked(self) -> bool:
        modal = QtWidgets.QApplication.activeModalWidget()
        return modal is not None and modal is not self.panel.window()

    def tick(self) -> None:
        if not self.panel.fresh() and self.panel._received_at is not None:
            self.panel.invalidate("Z readback expired; waiting for a fresh response.")
        if (self._closed or self._suspended or self._modal_blocked()
                or not self.panel.isVisible() or self._pending or self._busy
                or self._error or not _read_allowed(self._status)
                or getattr(self.controller, "_shutdown_started", False)
                or time.monotonic() - self._last_request < POLL_SECONDS):
            return
        self.request("status", {})

    def refresh(self) -> None:
        self._error = False
        self._last_request = -math.inf
        self.tick()

    def request(self, action: str, arguments: dict[str, Any]) -> None:
        if (self._closed or self._suspended or self._modal_blocked()
                or self._busy or not _read_allowed(self._status)):
            return
        if action in {"preview", "move", "use_job"} and not self.panel.calibration_mode:
            return
        if action == "set_job_gap" and (arguments.get("confirmed") is not True
                                        or not self.panel._can_set_job_gap()):
            return
        if action == "set_job_gap":
            arguments = dict(arguments, _gap_epoch=self._parameter_epoch)
        if action not in {"status", "recover"} and not self.panel.fresh():
            return
        if action == "recover" and (arguments.get("confirmed") is not True
                                     or not self.panel.reconnect_ender.isEnabled()):
            return
        if action == "recover_xy" and (arguments.get("confirmed") is not True
                                        or not self.panel.recover_xy.isEnabled()):
            return
        if action in {"reference", "home_reference", "home_retained"} and (
            arguments.get("confirmed") is not True or not self.panel.reference.isEnabled()
            or action != self.panel._home_action()
        ):
            return
        if action == "position_probe":
            arguments = dict(arguments, _camera_epoch=self._camera_epoch)
        if self._pending:
            if action != "status" and not self._mutation and self._queued is None:
                self._epoch += 1
                self._queued = (action, dict(arguments))
                self.panel._pending = True
                self.panel.invalidate("Waiting for the current read to finish…")
            return
        self._start_request(action, arguments)

    def _start_request(self, action: str, arguments: dict[str, Any]) -> None:
        arguments = dict(arguments)
        camera_selection = arguments.pop("_camera_selection", None)
        camera_epoch = arguments.pop("_camera_epoch", None)
        gap_epoch = arguments.pop("_gap_epoch", None)
        self._pending = True
        self._mutation = action != "status"
        # Background reads keep controls responsive. A clicked action takes
        # priority after that read; STOP/session invalidation clears the queue.
        self.panel._pending = self._mutation
        if self._mutation:
            self._epoch += 1
            self.panel.invalidate("Reconnecting Ender…" if action == "recover"
                                  else f"{action.replace('_', ' ').capitalize()}…",
                                  clear_surface=action == "forget_z", clear_confirmation=action == "forget_z")
        self._last_request = time.monotonic()
        epoch, parameter_epoch = self._epoch, self._parameter_epoch
        session = _session(self._status)
        machine = self.controller.runtime.context.machine
        if action == "status":
            arguments = {"clearance_z_mm": float(self.panel.clearance.value()),
                         "gap_mm": float(self.panel.gap.currentData())}
        self.panel._sync()

        def operation() -> dict[str, Any]:
            current = machine.status()
            if self._closed or epoch != self._epoch or _session(current) != session:
                raise RuntimeError("Focus request cancelled because the controller session changed.")
            if not _read_allowed(current):
                raise RuntimeError("Focus setup requires an idle, connected machine with laser disarmed.")
            if action == "set_job_gap" and gap_epoch != self._parameter_epoch:
                raise RuntimeError("Workpiece focus gap changed; choose the gap again after refreshing.")
            method = getattr(machine, "focus_control", None)
            if not callable(method):
                raise RuntimeError("Install the matching Pi support and surface-height V2 firmware for focus setup.")
            if action == "position_probe":
                if (not isinstance(camera_selection, dict) or camera_epoch != self._camera_epoch
                        or parameter_epoch != self._parameter_epoch):
                    raise RuntimeError("Camera target changed; select the probe point again.")
                age = camera_selection["frame_age_seconds"] + max(
                    0.0, time.monotonic() - camera_selection["snapshot_monotonic"]
                )
                _validate_main_view_selection(self.controller, camera_selection)
                target = self.controller.runtime.context.focus_probe_target(
                    camera_selection["image_x"], camera_selection["image_y"],
                    source_image_size=[camera_selection["width"], camera_selection["height"]],
                    frame_age_seconds=age, frame_metadata=camera_selection,
                    mapping_signature=camera_selection["mapping_signature"],
                )
                _validate_main_view_selection(self.controller, camera_selection)
                if (target["target_machine_xy_mm"] != arguments.get("value")
                        or epoch != self._epoch or parameter_epoch != self._parameter_epoch
                        or camera_epoch != self._camera_epoch):
                    raise RuntimeError("Camera target changed; select the probe point again.")
            if action in {"home_reference", "home_retained"}:
                def check_home_authority(*, require_readable: bool = True) -> None:
                    snapshot = machine.status()
                    if (self._closed or epoch != self._epoch or parameter_epoch != self._parameter_epoch
                            or _session(snapshot) != session
                            or (require_readable and not _read_allowed(snapshot))):
                        raise RuntimeError("Home / reference cancelled; machine state or focus parameters changed.")

                check_home_authority()
                before = method("status", clearance_z_mm=arguments["clearance_z_mm"], gap_mm=arguments["gap_mm"])
                generation = (before.get("ender") or {}).get("generation")
                if (before.get("available") is not True or type(generation) is not int
                        or (before.get("current_readback") or {}).get("fresh") is not True
                        or before.get("xy_recovery_pending_reference") is True):
                    raise RuntimeError("Refresh Ender readiness before Home / park + reference.")
                if action == "home_retained" and not self.panel._has_retained_reference(before):
                    raise RuntimeError("Saved Z reference changed; refresh before Home / park XY.")
                check_home_authority()
                self._home_refresh_pending = True
                try:
                    machine.prepare_photo_position()
                    # Verify cancellation before a new read even when the
                    # successful Home reply has invalidated the remote cache.
                    check_home_authority(require_readable=False)
                    refresh = getattr(machine, "refresh_status", None)
                    if callable(refresh):
                        refresh()
                    check_home_authority()
                finally:
                    self._home_refresh_pending = False
                if not project_machine_state(machine.status()).can_jog:
                    raise RuntimeError("Home / park did not establish motion readiness; border reference cancelled.")
                after = method("status", clearance_z_mm=arguments["clearance_z_mm"], gap_mm=arguments["gap_mm"])
                check_home_authority()
                if (after.get("available") is not True
                        or (after.get("current_readback") or {}).get("fresh") is not True
                        or (after.get("ender") or {}).get("generation") != generation):
                    raise RuntimeError("Ender session changed during Home / park; border reference cancelled.")
                if action == "home_retained":
                    if not self.panel._has_retained_reference(after):
                        raise RuntimeError("Saved Z reference was not retained through Home / park; reference again.")
                    result = dict(after, action="home_retained")
                else:
                    result = method("reference", **arguments)
            else:
                result = method(action, **arguments)
            if _session(machine.status()) != session:
                raise RuntimeError("Controller session changed during focus setup.")
            return result

        def success(result: dict[str, Any]) -> None:
            if self._closed or epoch != self._epoch or _session(self._status) != session:
                return
            if parameter_epoch != self._parameter_epoch:
                result = dict(result)
                result["preview"] = None
            try:
                self.panel.set_result(result)
                self._error = result.get("available") is not True
            except (ValueError, TypeError, KeyError) as exc:
                failure(str(exc))

        def failure(message: str) -> None:
            if not self._closed and epoch == self._epoch:
                self._error = True
                self.panel.set_failure(f"Focus unavailable: {message}")

        def finished() -> None:
            # A completed operation already includes fresh Z readback. Wait a
            # full idle interval from completion, not request start, so a long
            # jog cannot immediately trigger another read ahead of the operator.
            self._last_request = time.monotonic()
            self._pending = False
            self._mutation = False
            self.panel._pending = False
            if not self._closed:
                self.panel._sync()
            queued, self._queued = self._queued, None
            if (queued is not None and not self._closed and not self._suspended
                    and not self._modal_blocked() and not self._busy and _read_allowed(self._status)):
                self._start_request(*queued)

        self.controller._run(operation, on_success=success, on_failure=failure,
                             on_finished=finished, show_busy=action != "status",
                             label="Z reference / focus", serialize_controller=True)


class LaserFocusWorkspace(QtWidgets.QWidget):
    def __init__(self, controller: Any, parent: QtWidgets.QWidget | None = None, *,
                 calibration_mode: bool = False, main_view: Any | None = None) -> None:
        super().__init__(parent)
        self._suspended = False
        self._shutdown = False
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.panel = LaserFocusPanel(calibration_mode=calibration_mode)
        context = controller.runtime.context
        self.bed_view = (MainViewProbe(controller, main_view, self) if main_view is not None
                         else FocusBedView(getattr(context, "camera", None), self))
        self.splitter = None
        if calibration_mode:
            self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
            scroll = QtWidgets.QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setMinimumWidth(620)
            scroll.setWidget(self.panel)
            self.splitter.addWidget(scroll)
            self.splitter.addWidget(self.bed_view)
            self.splitter.setStretchFactor(0, 3)
            self.splitter.setStretchFactor(1, 2)
            self.splitter.setSizes([820, 540])
            layout.addWidget(self.splitter, 1)
        else:
            # The Machine inspector already scrolls; daily positioning uses
            # the calibrated image in the main workspace.
            self.panel.layout().setContentsMargins(0, 0, 0, 0)
            note = QtWidgets.QLabel(
                "Select in the main view on the left. Camera targets use the bed plane; "
                "raised surfaces are not height-corrected. Check the actual probe, surface and clearance."
            )
            note.setWordWrap(True)
            self.panel.reference_layout.addWidget(note, 6, 0, 1, 2)
            layout.addWidget(self.panel)
        self.coordinator = LaserFocusCoordinator(self.panel, controller, self)
        self._context = context
        self.panel.cameraModeRequested.connect(self.bed_view.set_selection_enabled)
        self.panel.cameraSelectionInvalidated.connect(lambda _message: self.bed_view.clear_selection())
        self.panel.cameraMoveRequested.connect(self._move_camera_probe)
        self.bed_view.pointSelected.connect(self._camera_point_selected)
        self.bed_view.selectionInvalidated.connect(self._camera_selection_invalidated)
        self._camera_timer = QtCore.QTimer(self)
        self._camera_timer.setInterval(500)
        self._camera_timer.timeout.connect(self._camera_tick)
        self._camera_timer.start()
        footer = QtWidgets.QHBoxLayout()
        self.stop = QtWidgets.QPushButton("Software STOP / laser off")
        self.stop.setObjectName("dangerButton")
        self.stop.setAutoDefault(False)
        self.stop.clicked.connect(controller.emergency_stop)
        footer.addWidget(self.stop)
        footer.addStretch()
        layout.addLayout(footer)

    def _map_camera_selection(self, selection: dict[str, Any], signature: str | None = None) -> dict[str, Any]:
        if selection.get("fresh") is not True:
            raise ValueError("Camera target expired; select a point from the live image again.")
        mapper = getattr(self._context, "focus_probe_target", None)
        if not callable(mapper):
            raise ValueError("Camera calibration is unavailable for probe positioning.")
        return mapper(
            selection["image_x"], selection["image_y"],
            source_image_size=[selection["width"], selection["height"]],
            frame_age_seconds=selection["frame_age_seconds"], frame_metadata=selection,
            mapping_signature=signature,
        )

    def _camera_point_selected(self, selection: dict[str, Any]) -> None:
        if not self.panel.position_probe.isEnabled() or not self.panel.position_probe.isChecked():
            return
        self.coordinator.camera_selection_changed()
        try:
            self.panel.set_camera_target(self._map_camera_selection(selection))
        except (CalibrationError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            self.panel.reject_camera_target(f"Cannot position probe: {exc}")
            self.bed_view.reject_selection()

    def _camera_selection_invalidated(self, message: str) -> None:
        self.coordinator.camera_selection_changed()
        self.panel.clear_camera_target(message)

    def _camera_tick(self) -> None:
        if not self._update_observers():
            return
        camera_live = self.bed_view.current_frame_fresh()
        if self.panel._camera_live and not camera_live:
            self.coordinator.camera_selection_changed()
        self.panel._camera_live = camera_live
        target = self.panel._camera_target
        if target is not None:
            selection = self.bed_view.selection_snapshot()
            try:
                if selection is None:
                    raise ValueError("Camera target expired; select a point from the live image again.")
                self._map_camera_selection(selection, target["mapping_signature"])
            except (CalibrationError, ValueError, RuntimeError, KeyError, TypeError) as exc:
                self._camera_selection_invalidated(str(exc))
        self.panel._sync()

    def _move_camera_probe(self) -> None:
        self._camera_tick()
        if not self.panel.can_move_probe():
            return
        selection = self.bed_view.selection_snapshot()
        if selection is None:
            return
        try:
            target = self._map_camera_selection(selection, self.panel._camera_target["mapping_signature"])
        except (CalibrationError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            self._camera_selection_invalidated(str(exc))
            return
        selection["mapping_signature"] = target["mapping_signature"]
        selection["snapshot_monotonic"] = time.monotonic()
        arguments = {
            "confirmed": True, "clearance_z_mm": self.panel._clearance_value(),
            "value": target["target_machine_xy_mm"], "_camera_selection": selection,
        }
        self.panel._reset_checks(self.panel.gauge)
        self.coordinator.request("position_probe", arguments)

    def set_machine_status(self, status: Mapping[str, Any]) -> None:
        self.coordinator.set_status(status)

    def set_suspended(self, suspended: bool) -> None:
        self._suspended = bool(suspended)
        self._update_observers()

    def _update_observers(self) -> bool:
        active = (not self._shutdown and not self._suspended and self.isVisible()
                  and not self.coordinator._modal_blocked())
        self.coordinator.set_suspended(not active)
        self.setEnabled(active)
        if active:
            if not self.bed_view._active:
                self.bed_view.begin()
            self.panel._display_timer.start()
        else:
            if self.bed_view._active:
                self.bed_view.end()
            self.panel._display_timer.stop()
            self.panel._camera_live = False
            self.panel.position_probe.setChecked(False)
        return active

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._shutdown:
            self._camera_timer.start()
            self._update_observers()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._camera_timer.stop()
        self._update_observers()
        super().hideEvent(event)

    def shutdown(self, *, force: bool = False) -> bool:
        if self.coordinator.mutation_busy and not force:
            self.panel.message.setText("Wait for the operation to finish, or use Software STOP.")
            return False
        self._shutdown = True
        self.coordinator.close()
        self._camera_timer.stop()
        self.panel._display_timer.stop()
        self.bed_view.end()
        return True
