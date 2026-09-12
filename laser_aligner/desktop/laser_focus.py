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
from .mainboard_z import _number, _read_allowed, _session
from .qt import require_qt

QtCore, _, QtWidgets = require_qt()

FRESH_SECONDS = 5.0
POLL_SECONDS = 2.0


class LaserFocusPanel(QtWidgets.QWidget):
    actionRequested = QtCore.Signal(str, dict)
    homeRequested = QtCore.Signal()
    xyRequested = QtCore.Signal(float, float)
    refreshRequested = QtCore.Signal()
    previewInvalidated = QtCore.Signal()
    cameraModeRequested = QtCore.Signal(bool)
    cameraMoveRequested = QtCore.Signal()
    cameraSelectionInvalidated = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._status: dict[str, Any] = {}
        self._result: dict[str, Any] = {}
        self._received_at: float | None = None
        self._busy = False
        self._pending = False
        self._preview_id: str | None = None
        self._clearance_edited = False
        self._offset_edited = False
        self._offset_entered = [False, False]
        self._camera_target: dict[str, Any] | None = None
        self._camera_live = False
        self._failure_message: str | None = None
        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Measure surface elevation above the border, then position the laser at a known gap. "
            "This is laser-off setup; it does not change camera calibration or start a job."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)
        readouts = QtWidgets.QGridLayout()
        self.height = QtWidgets.QLabel("Z — mm")
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

        prepare = QtWidgets.QGroupBox("1 · Reference and measure")
        grid = QtWidgets.QGridLayout(prepare)
        self.clearance = MeasurementSpinBox()
        self.clearance.setRange(20.0, 80.0)
        self.clearance.setDecimals(1)
        self.clearance.setSuffix(" mm")
        self.clearance.setValue(30.0)
        self.clearance.setToolTip("Clearance above the border; must fit the machine's remaining travel.")
        grid.addWidget(QtWidgets.QLabel("Clearance Z"), 0, 0)
        grid.addWidget(self.clearance, 0, 1)
        self.range_note = QtWidgets.QLabel("Measurement range awaits firmware readback")
        self.range_note.setWordWrap(True)
        grid.addWidget(self.range_note, 0, 2, 1, 2)
        self.path_clear = QtWidgets.QCheckBox("Headroom and Z path clear to the selected clearance")
        grid.addWidget(self.path_clear, 1, 0, 1, 4)
        self.home = QtWidgets.QPushButton("Home / park XY")
        self.reference = QtWidgets.QPushButton("Reference border")
        self.reference.setToolTip("Place the probe over the border. Establish Z and return to clearance.")
        self.return_clearance = QtWidgets.QPushButton("Return to clearance")
        grid.addWidget(self.home, 2, 0)
        grid.addWidget(self.reference, 2, 1)
        grid.addWidget(self.return_clearance, 2, 2, 1, 2)
        self.xy_step = QtWidgets.QComboBox()
        for step in (1.0, 5.0, 10.0):
            self.xy_step.addItem(f"{step:g} mm", step)
        self.xy_buttons: list[QtWidgets.QPushButton] = []
        xy = QtWidgets.QHBoxLayout()
        xy.addWidget(QtWidgets.QLabel("Position XY"))
        xy.addWidget(self.xy_step)
        for text, dx, dy in (("X−", -1, 0), ("X+", 1, 0), ("Y−", 0, -1), ("Y+", 0, 1)):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(lambda _checked=False, x=dx, y=dy: self._xy(x, y))
            xy.addWidget(button)
            self.xy_buttons.append(button)
        grid.addLayout(xy, 3, 0, 1, 4)
        self.xy_clear = QtWidgets.QCheckBox("XY transfer path clear at clearance; gauge removed")
        grid.addWidget(self.xy_clear, 4, 0, 1, 4)
        self.position_probe = QtWidgets.QPushButton("Position probe")
        self.position_probe.setCheckable(True)
        self.position_probe.setStyleSheet("QPushButton:checked { border: 1px solid #ffcf40; color: #ffcf40; }")
        self.position_probe.setToolTip("Select a probe target in the calibrated camera image.")
        self.move_probe = QtWidgets.QPushButton("Move probe here")
        self.camera_target = QtWidgets.QLabel("Choose Position probe, then click a solid spot in the live image.")
        self.camera_target.setWordWrap(True)
        grid.addWidget(self.position_probe, 5, 0, 1, 2)
        grid.addWidget(self.move_probe, 5, 2, 1, 2)
        grid.addWidget(self.camera_target, 6, 0, 1, 4)
        self.position_probe.toggled.connect(self.cameraModeRequested)
        self.move_probe.clicked.connect(self.cameraMoveRequested)
        self.offset_toggle = QtWidgets.QPushButton("Probe / laser XY offset…")
        self.offset_toggle.setCheckable(True)
        grid.addWidget(self.offset_toggle, 7, 0, 1, 4)
        self.offset_editor = QtWidgets.QWidget()
        offset_layout = QtWidgets.QGridLayout(self.offset_editor)
        offset_layout.setContentsMargins(0, 0, 0, 0)
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
        self.offset_editor.hide()
        self.offset_toggle.toggled.connect(self.offset_editor.setVisible)
        grid.addWidget(self.offset_editor, 8, 0, 1, 4)
        self.offset_readout = QtWidgets.QLabel("Probe XY offset: not set · use a wide, flat patch")
        self.offset_readout.setWordWrap(True)
        grid.addWidget(self.offset_readout, 9, 0, 1, 4)
        self.align_probe = QtWidgets.QPushButton("Put probe over laser spot")
        self.align_probe.setToolTip("At clearance, shift XY by minus the measured probe offset. Then measure.")
        self.align_laser = QtWidgets.QPushButton("Return laser to measured spot")
        self.align_laser.setToolTip("At clearance, return XY by the measured offset and retain this measurement.")
        grid.addWidget(self.align_probe, 10, 0, 1, 2)
        grid.addWidget(self.align_laser, 10, 2, 1, 2)
        self.flat_patch = QtWidgets.QCheckBox("Solid, flat patch spans probe and laser at the same height")
        grid.addWidget(self.flat_patch, 11, 0, 1, 4)
        self.measure = QtWidgets.QPushButton("Measure surface")
        self.clear_surface = QtWidgets.QPushButton("Clear measurement")
        grid.addWidget(self.measure, 12, 0, 1, 2)
        grid.addWidget(self.clear_surface, 12, 2, 1, 2)
        layout.addWidget(prepare)

        teach = QtWidgets.QGroupBox("2 · Teach once with the 7 mm gauge")
        teach_layout = QtWidgets.QGridLayout(teach)
        hint = QtWidgets.QLabel(
            "After measuring, lower Z in small steps until the 7 mm gauge fits between the "
            "laser reference face and this same surface. Save that position, then remove the gauge."
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
        layout.addWidget(teach)

        focus = QtWidgets.QGroupBox("3 · Preview and position")
        focus_layout = QtWidgets.QGridLayout(focus)
        self.gap = QtWidgets.QComboBox()
        for gap in (7.0, 5.0, 3.0):
            self.gap.addItem(f"{gap:g} mm gap", gap)
        self.preview = QtWidgets.QPushButton("Preview target")
        self.move = QtWidgets.QPushButton("Move to focus")
        self.target = QtWidgets.QLabel("Target Z: preview required")
        self.target.setWordWrap(True)
        self.gauge_removed = QtWidgets.QCheckBox("Gauge removed; path to target clear")
        focus_layout.addWidget(self.gap, 0, 0)
        focus_layout.addWidget(self.preview, 0, 1, 1, 2)
        focus_layout.addWidget(self.move, 3, 0, 1, 3)
        focus_layout.addWidget(self.target, 1, 0, 1, 3)
        focus_layout.addWidget(self.gauge_removed, 2, 0, 1, 3)
        layout.addWidget(focus)
        footer = QtWidgets.QHBoxLayout()
        self.message = QtWidgets.QLabel("Connect the machine, then refresh focus support.")
        self.message.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        self.message.setWordWrap(True)
        self.refresh = QtWidgets.QPushButton("Refresh")
        footer.addWidget(self.message, 1)
        footer.addWidget(self.refresh)
        layout.addLayout(footer)
        layout.addStretch()

        for button, action in ((self.reference, "reference"), (self.measure, "measure"),
                               (self.teach, "teach"), (self.preview, "preview"),
                               (self.move, "move"), (self.return_clearance, "clearance"),
                               (self.clear_surface, "clear_surface"), (self.forget, "forget"),
                               (self.apply_offset, "set_xy_offset"), (self.align_probe, "align_probe"),
                               (self.align_laser, "align_laser")):
            button.clicked.connect(lambda _checked=False, a=action: self.request(a))
        self.down.clicked.connect(lambda: self.request("jog", -float(self.z_step.currentData())))
        self.up.clicked.connect(lambda: self.request("jog", float(self.z_step.currentData())))
        self.home.clicked.connect(self._home)
        self.reconnect_ender.clicked.connect(lambda: self.request("recover"))
        self.refresh.clicked.connect(self.refreshRequested)
        self.clearance.valueChanged.connect(self._edit_clearance)
        self.clearance.lineEdit().textEdited.connect(self._edit_clearance)
        self.gap.currentIndexChanged.connect(self._parameters_edited)
        for checkbox in (self.path_clear, self.flat_patch, self.gauge, self.gauge_removed, self.xy_clear):
            checkbox.toggled.connect(self._sync)
        self.z_step.currentIndexChanged.connect(self._sync)
        # Enter commits an editor; it must never activate a motion button.
        for button in self.findChildren(QtWidgets.QPushButton):
            button.setAutoDefault(False)
        self._sync()

    def fresh(self) -> bool:
        return self._received_at is not None and time.monotonic() - self._received_at <= FRESH_SECONDS

    def clear_camera_target(self, message: str = "Choose Position probe, then click a solid spot in the live image.") -> None:
        self._camera_target = None
        self.camera_target.setText(message)
        self.cameraSelectionInvalidated.emit(message)
        self._sync()

    def set_camera_target(self, target: dict[str, Any]) -> None:
        self._camera_target = dict(target)
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
        self.xy_clear.setChecked(False)
        self._parameters_edited()

    def _offset_value(self) -> list[float] | None:
        if not all(self._offset_entered):
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
            for checkbox in (self.path_clear, self.flat_patch, self.gauge, self.gauge_removed, self.xy_clear):
                checkbox.setChecked(False)
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
            self.maximum.setText(f"Configured maximum: {maximum:g} mm above border · Z status unavailable")
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
        if self._camera_target is not None and (
            any(self._result.get(key) != result.get(key) for key in (
                "probe_xy_offset_mm", "laser_spot_offset_mm", "current_carriage_xy_mm", "reference_ready", "max_z_mm"
            )) or any(previous_readback.get(key) != readback.get(key) for key in ("z_known", "z_mm"))
        ):
            self.clear_camera_target("Machine position or offset changed; select the probe point again.")
        self._result = dict(result)
        self._failure_message = None
        self.failure_detail.clear()
        self.failure_detail.hide()
        self.ender_status.setText("Ender: connected · current firmware readback received")
        offset = result.get("probe_xy_offset_mm")
        valid_offset = (isinstance(offset, (list, tuple)) and len(offset) == 2
                        and all(_number(v) is not None and -100 <= v <= 100 for v in offset))
        if valid_offset:
            self.offset_readout.setText(f"Probe relative to laser: X {offset[0]:+.3f} mm · Y {offset[1]:+.3f} mm")
            if not self._offset_edited or result.get("action") == "set_xy_offset":
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
            "measure": "Surface measured. Teach with the gauge, or preview using the saved offset.",
            "jog": "Z jog finished. Check the 7 mm gauge fit before saving.",
            "teach": "Taught offset saved. Remove the gauge before moving.",
            "preview": "Target previewed; no movement sent.",
            "move": "Focus position reached. Return to clearance before moving XY or changing the workpiece.",
            "clearance": "At clearance. XY positioning and workpiece changes can resume.",
            "clear_surface": "Surface measurement cleared.",
            "forget": "Taught offset forgotten.",
            "set_xy_offset": "Measured XY offset saved. Align the laser visually over your target, then put the probe there.",
            "align_probe": "Probe is over the selected laser spot. Confirm the solid target, then measure.",
            "position_probe": "Probe positioned at the camera target. Check alignment and the solid target, then measure.",
            "align_laser": "Laser returned to the measured spot at clearance. Fit the gauge using small Z steps.",
            "recover": "Ender connection verified. Establish the border reference before Z positioning.",
        }.get(str(result.get("action")), "Reported position is refreshed while idle."))
        self._sync()

    def _preview_matches(self, preview: Any) -> bool:
        surface = self._result.get("surface")
        current = _number((self._result.get("current_readback") or {}).get("z_mm"))
        target = _number(preview.get("target_z_mm")) if isinstance(preview, Mapping) else None
        previous_z = _number(preview.get("current_z_mm")) if isinstance(preview, Mapping) else None
        maximum = _number(self._result.get("max_z_mm"))
        return bool(
            self._preview_id and isinstance(preview, Mapping) and surface
            and str(preview.get("id")) == self._preview_id
            and preview.get("measurement_id") == surface.get("id")
            and preview.get("gap_mm") == self.gap.currentData()
            and preview.get("clearance_z_mm") == self._clearance_value()
            and target is not None and maximum is not None and 0 <= target <= maximum
            and current is not None and previous_z is not None and abs(current - previous_z) <= .05
        )

    def _sync(self) -> None:
        idle = _read_allowed(self._status) and not self._busy and not self._pending
        ready = idle and self.fresh()
        projection = project_machine_state(self._status)
        clearance = self._clearance_value()
        moving = ready and projection.can_jog and self.path_clear.isChecked() and clearance is not None
        reference = self._result.get("reference_ready") is True
        surface = bool(self._result.get("surface"))
        known = (self._result.get("current_readback") or {}).get("z_known") is True
        z = _number((self._result.get("current_readback") or {}).get("z_mm"))
        maximum = _number(self._result.get("max_z_mm"))
        requires_clearance = self._result.get("requires_clearance") is True
        sequence = self._result.get("xy_sequence") or {}
        probe_phase = sequence.get("phase") == "probe"
        self.flat_patch.setText("Solid, flat target under probe; deployment path clear" if probe_phase
                               else "Same measured spot under laser; gauge-contact area is flat"
                               if sequence.get("phase") == "laser"
                               else "Solid, flat patch spans probe and laser at the same height")
        self.home.setEnabled(idle and projection.can_home and self.path_clear.isChecked()
                             and not requires_clearance)
        self.reconnect_ender.setEnabled(bool(idle and self._result.get("recovery_available") is True))
        self.reference.setEnabled(bool(moving))
        self.measure.setEnabled(bool(moving and known and reference and self.flat_patch.isChecked()
                                     and sequence.get("phase") != "laser"))
        return_minimum = _number(self._result.get("return_clearance_mm"))
        return_allowed = return_minimum is None or (clearance is not None and clearance >= return_minimum)
        self.return_clearance.setEnabled(bool(moving and known and z is not None and return_allowed
                                             and clearance is not None and z <= clearance + .05))
        self.clear_surface.setEnabled(bool(ready and surface))
        self.forget.setEnabled(bool(ready and self._result.get("calibration")
                                    and self._result.get("calibration_persistent") is True))
        at_clearance = not requires_clearance and (
            not reference or (z is not None and clearance is not None and z >= clearance - .05)
        )
        offset = self._result.get("probe_xy_offset_mm")
        offset_valid = (isinstance(offset, (list, tuple)) and len(offset) == 2
                        and all(_number(v) is not None and -100 <= v <= 100 for v in offset))
        xy_available = self._result.get("xy_offset_available") is True
        transfer_ready = (moving and known and reference and at_clearance and self.xy_clear.isChecked()
                          and xy_available and offset_valid and not self._offset_edited
                          and z is not None and clearance is not None and abs(z - clearance) <= .05)
        self.apply_offset.setEnabled(bool(ready and xy_available and not requires_clearance
                                          and self._offset_edited and self._offset_value() is not None))
        self.align_probe.setEnabled(bool(transfer_ready and not probe_phase))
        self.align_laser.setEnabled(bool(transfer_ready and probe_phase and surface))
        selection_ready = (moving and known and reference and not requires_clearance
                           and offset_valid and not self._offset_edited and self._camera_live
                           and self._result.get("position_probe_available") is True
                           and z is not None and clearance is not None and abs(z-clearance) <= .05)
        self.position_probe.setEnabled(bool(selection_ready))
        self.move_probe.setEnabled(bool(selection_ready and self.xy_clear.isChecked() and self._camera_target))
        if not selection_ready and self.position_probe.isChecked():
            self.position_probe.setChecked(False)
        for editor in (self.offset_x, self.offset_y):
            editor.setEnabled(bool(idle and xy_available))
        self.xy_clear.setEnabled(bool(idle and xy_available))
        for button in self.xy_buttons:
            button.setEnabled(bool(moving and known and at_clearance))
        self.xy_step.setEnabled(idle)
        teach_ready = moving and reference and surface and self.flat_patch.isChecked() and known and not probe_phase
        step = float(self.z_step.currentData())
        measured = self._result.get("surface") or {}
        geometry = self._result.get("firmware_geometry") or {}
        contact, probe_z = _number(measured.get("contact_z_mm")), _number(geometry.get("probe_z_mm"))
        floor = max(0.0, contact - probe_z) if contact is not None and probe_z is not None else None
        self.down.setEnabled(bool(teach_ready and z is not None and floor is not None and z - step >= floor))
        self.up.setEnabled(bool(teach_ready and z is not None and maximum is not None and z + step <= maximum))
        self.teach.setEnabled(bool(teach_ready and self.gauge.isChecked()
                                  and self._result.get("calibration_persistent") is True))
        self.preview.setEnabled(bool(ready and known and reference and surface and not probe_phase and clearance is not None
                                    and self._result.get("calibration_compatible") is True))
        self.move.setEnabled(bool(moving and self._preview_matches(self._result.get("preview"))
                                 and self.gauge_removed.isChecked()))
        for control in (self.clearance, self.gap, self.path_clear, self.flat_patch,
                        self.gauge, self.gauge_removed, self.z_step):
            control.setEnabled(idle)
        self.refresh.setEnabled(idle)
        self.next_step.setText(
            "Next: Wait for controller recovery, then Home / park XY."
            if machine_payload(self._status).get("controller_state") in {"RECOVERING", "OPENING", "SYNCHRONIZING", "STOPPING"} else
            "Next: Connect the controller before focus setup." if not _read_allowed(self._status) else
            "Waiting for the current operation to finish." if self._busy or self._pending else
            "Next: Reconnect Ender to restore its readback." if self._result.get("available") is False else
            "Next: Refresh focus status before positioning." if not self.fresh() else
            "Next: Confirm the Z path, then reference the border." if not reference and projection.can_jog else
            "Next: Home / park XY, then reference the border." if not reference else
            "Next: Return Z to clearance before transferring XY." if requires_clearance and probe_phase else
            "Next: Confirm the XY path and choose Move probe here." if self._camera_target else
            "Next: Click a solid probe spot in the live camera image." if self.position_probe.isChecked() else
            "Next: Return the laser to the measured spot at clearance." if probe_phase and surface else
            "Next: Confirm the solid target under the probe, then measure surface." if probe_phase else
            "Next: Choose Position probe and click a flat target in the camera view." if not surface and offset_valid else
            "Next: Position over a wide, flat patch and measure its surface." if not surface else
            "Next: Fit the 7 mm gauge with small Z steps, then save the setting." if not self._result.get("calibration_compatible") else
            "Next: Remove the gauge, preview a gap, then move to focus."
        )
        self.range_note.setText(
            f"Contact elevation range: −2 to {clearance - 15:g} mm; higher work needs more clearance."
            if clearance is not None else "Clearance must be 20 mm to the active maximum."
        )
        if requires_clearance and return_minimum is not None:
            self.range_note.setText(self.range_note.text() + f" Return requires Z ≥ {return_minimum:g} mm.")

    def request(self, action: str, value: float | None = None) -> None:
        self._sync()
        button = self.up if action == "jog" and value and value > 0 else self.down if action == "jog" else {
            "reference": self.reference, "measure": self.measure, "teach": self.teach,
            "preview": self.preview, "move": self.move, "clearance": self.return_clearance,
            "clear_surface": self.clear_surface, "forget": self.forget,
            "set_xy_offset": self.apply_offset, "align_probe": self.align_probe, "align_laser": self.align_laser,
            "recover": self.reconnect_ender,
        }.get(action)
        if button is None or not button.isEnabled():
            return
        self.clearance.interpretText()
        arguments: dict[str, Any] = {
            "confirmed": True, "clearance_z_mm": float(self.clearance.value()),
            "gap_mm": 7.0 if action == "teach" else float(self.gap.currentData()),
        }
        if action in {"teach", "jog", "preview", "align_laser"}:
            arguments["measurement_id"] = self._result["surface"]["id"]
        if action == "move":
            arguments["preview_id"] = self._preview_id
        if action == "jog":
            arguments["value"] = value
        if action == "set_xy_offset":
            arguments["value"] = self._offset_value()
        if action in {"jog", "reference", "measure", "move", "clearance"}:
            self.gauge.setChecked(False)
            self.gauge_removed.setChecked(False)
        if action == "reference":
            self.flat_patch.setChecked(False)
        if action in {"set_xy_offset", "align_probe", "align_laser"}:
            self.xy_clear.setChecked(False)
            self.gauge.setChecked(False)
            self.gauge_removed.setChecked(False)
            if action != "align_laser":
                self.flat_patch.setChecked(False)
        self.actionRequested.emit(action, arguments)

    def _home(self) -> None:
        self._sync()
        if self.home.isEnabled():
            self.xy_clear.setChecked(False)
            self.homeRequested.emit()

    def _xy(self, dx: float, dy: float) -> None:
        self._sync()
        if all(button.isEnabled() for button in self.xy_buttons):
            self.flat_patch.setChecked(False)
            self.xy_clear.setChecked(False)
            self.xyRequested.emit(dx * float(self.xy_step.currentData()), dy * float(self.xy_step.currentData()))


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
        self._pending = False
        self._mutation = False
        self._busy = bool(getattr(controller, "_active_tasks", 0))
        self.panel._busy = self._busy
        self._closed = False
        self._error = False
        self._home_status_retry = False
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
        panel.homeRequested.connect(self.home)
        panel.xyRequested.connect(self.xy)
        self._timer.start()

    def set_status(self, status: Mapping[str, Any]) -> None:
        machine = dict(machine_payload(status))
        changed = _session(machine) != _session(self._status)
        became_readable = _read_allowed(machine) and not _read_allowed(self._status)
        own_activity = self._mutation and project_machine_state(machine).can_send_diagnostic
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
        if became_readable:
            # One observation after recovery may clear an old read failure.
            # A repeated failure relatches; no connection or motion is retried.
            self._error = False
            self._last_request = -math.inf
        self._retry_after_home()
        self.panel._sync()

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        self.panel._busy = bool(busy)
        if busy and not self._mutation:
            self._epoch += 1
            self._queued = None
            self.panel.invalidate("Machine operation in progress; measure again after positioning.", clear_surface=True)
        self._retry_after_home()
        self.panel._sync()

    def _retry_after_home(self) -> None:
        if (self._home_status_retry and not self._busy and _read_allowed(self._status)
                and project_machine_state(self._status).can_jog):
            self._home_status_retry = False
            self._error = False
            self._last_request = -math.inf

    def parameters_edited(self) -> None:
        self._parameter_epoch += 1

    def stopped(self) -> None:
        self._epoch += 1
        self._queued = None
        self._error = True
        self._home_status_retry = False
        self.panel.invalidate("STOP requested. Reconnect / Home and establish the border reference again.",
                              clear_surface=True, clear_confirmation=True)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._epoch += 1
        self._queued = None
        self._timer.stop()
        self.controller.statusChanged.disconnect(self.set_status)
        self.controller.busyChanged.disconnect(self.set_busy)
        self.controller.stopInitiated.disconnect(self.stopped)

    def tick(self) -> None:
        if not self.panel.fresh() and self.panel._received_at is not None:
            self.panel.invalidate("Z readback expired; waiting for a fresh response.")
        if (self._closed or not self.panel.isVisible() or self._pending or self._busy
                or self._error or not _read_allowed(self._status)
                or getattr(self.controller, "_shutdown_started", False)
                or time.monotonic() - self._last_request < POLL_SECONDS):
            return
        self.request("status", {})

    def refresh(self) -> None:
        self._error = False
        self._last_request = -math.inf
        self.tick()

    def home(self) -> None:
        if self._closed or self._mutation or self._busy or not self.panel.home.isEnabled():
            return
        self._epoch += 1
        self._home_status_retry = True
        self.panel.invalidate("Homing and parking XY…", clear_surface=True)
        self.controller.park_at_camera_pose()

    def xy(self, dx: float, dy: float) -> None:
        if self._closed or self._mutation or self._busy or not all(b.isEnabled() for b in self.panel.xy_buttons):
            return
        self._epoch += 1
        self.panel.invalidate("Positioning XY; measure the new patch afterward.", clear_surface=True)
        self.controller.jog(dx, dy, 300.0)

    def request(self, action: str, arguments: dict[str, Any]) -> None:
        if self._closed or self._busy or not _read_allowed(self._status):
            return
        if action not in {"status", "recover"} and not self.panel.fresh():
            return
        if action == "recover" and (arguments.get("confirmed") is not True
                                     or not self.panel.reconnect_ender.isEnabled()):
            return
        if action == "position_probe":
            arguments = dict(arguments, _camera_parameter_epoch=self._parameter_epoch)
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
        camera_parameter_epoch = arguments.pop("_camera_parameter_epoch", None)
        self._pending = True
        self._mutation = action != "status"
        # Background reads keep controls responsive. A clicked action takes
        # priority after that read; STOP/session invalidation clears the queue.
        self.panel._pending = self._mutation
        if self._mutation:
            self._epoch += 1
            self.panel.invalidate("Reconnecting Ender…" if action == "recover"
                                  else f"{action.replace('_', ' ').capitalize()}…")
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
            method = getattr(machine, "focus_control", None)
            if not callable(method):
                raise RuntimeError("Install the matching Pi support and surface-height V2 firmware for focus setup.")
            if action == "position_probe":
                if (not isinstance(camera_selection, dict) or camera_parameter_epoch != self._parameter_epoch
                        or parameter_epoch != self._parameter_epoch):
                    raise RuntimeError("Camera target changed; select the probe point again.")
                age = camera_selection["frame_age_seconds"] + max(
                    0.0, time.monotonic() - camera_selection["snapshot_monotonic"]
                )
                target = self.controller.runtime.context.focus_probe_target(
                    camera_selection["image_x"], camera_selection["image_y"],
                    source_image_size=[camera_selection["width"], camera_selection["height"]],
                    frame_age_seconds=age, frame_metadata=camera_selection,
                    mapping_signature=camera_selection["mapping_signature"],
                )
                if (target["target_machine_xy_mm"] != arguments.get("value")
                        or epoch != self._epoch or parameter_epoch != self._parameter_epoch):
                    raise RuntimeError("Camera target changed; select the probe point again.")
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
            self._pending = False
            self._mutation = False
            self.panel._pending = False
            if not self._closed:
                self.panel._sync()
            queued, self._queued = self._queued, None
            if queued is not None and not self._closed and not self._busy and _read_allowed(self._status):
                self._start_request(*queued)

        self.controller._run(operation, on_success=success, on_failure=failure,
                             on_finished=finished, show_busy=action != "status",
                             label="Surface / laser focus", serialize_controller=True)


class LaserFocusDialog(QtWidgets.QDialog):
    def __init__(self, controller: Any, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Surface / laser focus")
        self.resize(1400, 900)
        layout = QtWidgets.QVBoxLayout(self)
        self.splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(620)
        self.panel = LaserFocusPanel()
        scroll.setWidget(self.panel)
        self.splitter.addWidget(scroll)
        context = controller.runtime.context
        self.bed_view = FocusBedView(getattr(context, "camera", None), self)
        self.splitter.addWidget(self.bed_view)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 2)
        self.splitter.setSizes([820, 540])
        layout.addWidget(self.splitter, 1)
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
        self.close_button = QtWidgets.QPushButton("Close")
        self.close_button.setAutoDefault(False)
        self.close_button.clicked.connect(self.reject)
        footer.addWidget(self.stop)
        footer.addStretch()
        footer.addWidget(self.close_button)
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
        try:
            self.panel.set_camera_target(self._map_camera_selection(selection))
        except (CalibrationError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            self.panel.clear_camera_target(f"Cannot position probe: {exc}")

    def _camera_selection_invalidated(self, message: str) -> None:
        self.coordinator.parameters_edited()
        self.panel.clear_camera_target(message)

    def _camera_tick(self) -> None:
        camera_live = self.bed_view.current_frame_fresh()
        if self.panel._camera_live and not camera_live:
            self.coordinator.parameters_edited()
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
        if not self.panel.move_probe.isEnabled() or self.panel._camera_target is None:
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
        self.panel.xy_clear.setChecked(False)
        self.panel.flat_patch.setChecked(False)
        self.panel.gauge.setChecked(False)
        self.panel.gauge_removed.setChecked(False)
        self.coordinator.request("position_probe", arguments)

    def set_machine_status(self, status: Mapping[str, Any]) -> None:
        self.coordinator.set_status(status)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self.bed_view.begin()

    def done(self, result: int) -> None:
        if self.coordinator._mutation:
            self.panel.message.setText("Wait for the operation to finish, or use Software STOP.")
            return
        if not self.coordinator._closed:
            self.coordinator.close()
        self._camera_timer.stop()
        self.bed_view.end()
        super().done(result)
