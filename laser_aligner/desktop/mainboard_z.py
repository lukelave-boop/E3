"""Reported Ender Z position and guarded, typed controls for the Machine tab."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

from .controls import MeasurementSpinBox
from .machine_state import (
    controller_node_boot_id,
    controller_session_generation,
    machine_payload,
    project_machine_state,
)
from .qt import require_qt

QtCore, _, QtWidgets = require_qt()

Z_STATUS_FRESH_SECONDS = 5.0
Z_POLL_SECONDS = 2.0


def _number(value: object) -> float | None:
    return (
        float(value)
        if type(value) in {float, int} and math.isfinite(float(value))
        else None
    )


def _session(status: Mapping[str, Any]) -> tuple[object, object]:
    return controller_node_boot_id(status), controller_session_generation(status)


def _read_allowed(status: Mapping[str, Any]) -> bool:
    probe = status.get("z_probe") or {}
    return (
        project_machine_state(status).can_send_diagnostic
        and status.get("status_stale") is not True
        and not status.get("status_refresh_error")
        and not (isinstance(probe, Mapping) and probe.get("active") is True)
    )


class MainboardZPanel(QtWidgets.QGroupBox):
    jogRequested = QtCore.Signal(float)
    maximumRequested = QtCore.Signal(float)
    refreshRequested = QtCore.Signal()
    focusRequested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__("Z axis · Ender", parent)
        self._machine_status: dict[str, Any] = {}
        self._result: dict[str, Any] = {}
        self._received_at: float | None = None
        self._busy = False
        self._pending = False
        self._maximum_edited = False
        layout = QtWidgets.QGridLayout(self)
        layout.setContentsMargins(6, 10, 6, 6)
        layout.setSpacing(4)
        self.down = QtWidgets.QPushButton("Z−")
        self.up = QtWidgets.QPushButton("Z+")
        self.step = QtWidgets.QComboBox()
        for value in (0.1, 1.0, 5.0):
            self.step.addItem(f"{value:g} mm", value)
        self.step.setCurrentIndex(1)
        self.height = QtWidgets.QLabel("Z — mm")
        self.height.setObjectName("zHeightReadout")
        self.height.setStyleSheet("font-size: 16px; font-weight: bold;")
        self.height.setToolTip(
            "Controller-reported Z above the homed border, not an encoder measurement."
        )
        layout.addWidget(self.down, 0, 0)
        layout.addWidget(self.up, 0, 1)
        layout.addWidget(self.height, 0, 2, 1, 2)
        layout.addWidget(QtWidgets.QLabel("Step"), 1, 0)
        layout.addWidget(self.step, 1, 1)
        self.readback_note = QtWidgets.QLabel("Reported position · waiting for connection")
        self.readback_note.setWordWrap(True)
        layout.addWidget(self.readback_note, 1, 2, 1, 2)
        self.confirm = QtWidgets.QCheckBox("Probe stowed and Z path clear")
        layout.addWidget(self.confirm, 2, 0, 1, 4)
        self.active_maximum = QtWidgets.QLabel("Active maximum: unknown")
        self.active_maximum.setObjectName("zActiveMaximum")
        self.active_maximum.setWordWrap(True)
        layout.addWidget(self.active_maximum, 3, 0, 1, 4)
        self.maximum = MeasurementSpinBox()
        self.maximum.setRange(20.0, 80.0)
        self.maximum.setDecimals(1)
        self.maximum.setSingleStep(1.0)
        self.maximum.setSuffix(" mm")
        self.maximum.setValue(80.0)
        self.maximum.setToolTip(
            "Maximum Z above the homed border. Apply saves the active machine's limit. "
            "The firmware ceiling is 80 mm; this setting can only lower it."
        )
        self.apply = QtWidgets.QPushButton("Apply")
        layout.addWidget(QtWidgets.QLabel("Set max"), 4, 0)
        layout.addWidget(self.maximum, 4, 1, 1, 2)
        layout.addWidget(self.apply, 4, 3)
        self.firmware_note = QtWidgets.QLabel("Firmware ceiling not confirmed")
        self.firmware_note.setObjectName("mutedLabel")
        self.firmware_note.setWordWrap(True)
        layout.addWidget(self.firmware_note, 5, 0, 1, 4)
        self.message = QtWidgets.QLabel("Connect to read the Ender Z position and limit.")
        self.message.setWordWrap(True)
        self.message.setTextFormat(QtCore.Qt.TextFormat.PlainText)
        layout.addWidget(self.message, 6, 0, 1, 3)
        self.refresh = QtWidgets.QPushButton("Refresh")
        layout.addWidget(self.refresh, 6, 3)
        self.focus = QtWidgets.QPushButton("Surface / laser focus…")
        self.focus.setToolTip("Measure surface elevation and teach or apply the laser's gauge offset.")
        layout.addWidget(self.focus, 7, 0, 1, 4)
        layout.setColumnStretch(2, 1)
        self.down.clicked.connect(lambda: self._jog(-1))
        self.up.clicked.connect(lambda: self._jog(1))
        self.apply.clicked.connect(self._apply)
        self.refresh.clicked.connect(self.refreshRequested)
        self.focus.clicked.connect(self.focusRequested)
        self.confirm.toggled.connect(self._sync)
        self.step.currentIndexChanged.connect(self._sync)
        self.maximum.valueChanged.connect(self._edit_maximum)
        self.maximum.lineEdit().textEdited.connect(self._edit_maximum)
        self._sync()

    def _edit_maximum(self, *_args: object) -> None:
        self._maximum_edited = True
        self._sync()

    def _draft_maximum(self) -> float | None:
        if not self.maximum.hasAcceptableInput():
            return None
        try:
            value = _number(self.maximum.valueFromText(self.maximum.text()))
        except ValueError:
            return None
        return value if value is not None and 20.0 <= value <= 80.0 else None

    def set_machine_status(self, status: Mapping[str, Any]) -> None:
        changed_session = _session(status) != _session(self._machine_status)
        self._machine_status = dict(status)
        if changed_session:
            # Discard the old session's draft before disabling its editor.
            # Otherwise focus loss can commit it and mark it dirty again.
            blocker = QtCore.QSignalBlocker(self.maximum)
            self.maximum.setValue(self.maximum.value())
            del blocker
        if changed_session or not _read_allowed(status):
            self.invalidate("Z readback unavailable", clear_confirmation=changed_session)
        if changed_session:
            self._maximum_edited = False
        self._sync()

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        if busy:
            self.invalidate("Z readback paused during machine operation")
        self._sync()

    def set_pending(self, pending: bool) -> None:
        self._pending = bool(pending)
        self._sync()

    def invalidate(self, message: str, *, clear_confirmation: bool = False) -> None:
        self._received_at = None
        self.height.setText("Z — mm")
        self.readback_note.setText("Reported position unavailable")
        self.message.setText(message)
        if clear_confirmation:
            self.confirm.setChecked(False)
        self._sync()

    def set_result(self, result: Mapping[str, Any]) -> None:
        if not isinstance(result, Mapping):
            raise ValueError("Ender returned invalid Z status; update Pi support and refresh.")
        maximum = _number(result.get("max_z_mm"))
        hard_max = _number(result.get("hard_max_z_mm"))
        z = _number(result.get("z_mm"))
        if (
            result.get("available") is not True or result.get("fresh") is not True
            or maximum is None or hard_max != 80.0 or not 20 <= maximum <= hard_max
            or type(result.get("z_known")) is not bool
            or (result.get("z_known") is True and z is None)
        ):
            raise ValueError("Ender returned incomplete Z status; update Pi support and refresh.")
        previous_max = self._result.get("max_z_mm")
        self._result = dict(result)
        self._received_at = time.monotonic()
        if not self._maximum_edited or result.get("action") == "z_max":
            blocker = QtCore.QSignalBlocker(self.maximum)
            self.maximum.setValue(maximum)
            del blocker
            self._maximum_edited = False
        if result.get("z_known") is True:
            self.height.setText(f"Z {z:.3f} mm")
            self.readback_note.setText("Reported position · live while idle")
            self.message.setText("Manual Z range: 20 mm to the active maximum.")
        else:
            self.height.setText("Z unknown")
            self.readback_note.setText("Z homing required")
            self.message.setText("Establish the border Z reference before jogging.")
        self.firmware_note.setText(
            "Firmware ceiling: 80 mm confirmed"
            if result.get("firmware_z_limit_verified") is True
            else "Firmware ceiling not confirmed · host limit still applies"
        )
        if result.get("action") == "z_max":
            self.message.setText(f"Maximum saved: {maximum:g} mm above the border.")
        elif previous_max is not None and previous_max != maximum:
            self.message.setText(f"Active maximum changed to {maximum:g} mm.")
        self._sync()

    def fresh(self) -> bool:
        return (
            self._received_at is not None
            and time.monotonic() - self._received_at <= Z_STATUS_FRESH_SECONDS
        )

    def expire(self) -> None:
        if self._received_at is not None and not self.fresh():
            self.invalidate("Z readback is stale; waiting for a fresh response.")

    def _sync(self) -> None:
        ready = _read_allowed(self._machine_status) and not self._busy and not self._pending
        fresh = self.fresh()
        maximum = _number(self._result.get("max_z_mm"))
        if maximum is not None:
            suffix = "" if fresh else " (last reported)"
            self.active_maximum.setText(f"Active maximum: {maximum:g} mm{suffix}")
        can_jog = (
            ready and fresh and self._machine_status.get("allow_motion") is True
            and self._result.get("z_known") is True and self.confirm.isChecked()
        )
        z = _number(self._result.get("z_mm"))
        step = float(self.step.currentData())
        self.down.setEnabled(bool(can_jog and z is not None and z - step >= 20.0))
        self.up.setEnabled(bool(can_jog and z is not None and maximum is not None
                                and 20.0 <= z + step <= maximum))
        self.confirm.setEnabled(ready)
        self.step.setEnabled(ready)
        self.maximum.setEnabled(ready and fresh)
        draft = self._draft_maximum()
        self.apply.setEnabled(bool(ready and fresh and self._maximum_edited
                                   and self._result.get("max_z_persistent") is True
                                   and draft is not None and draft != maximum))
        self.apply.setToolTip(
            "Save the maximum for this machine"
            if self._result.get("max_z_persistent") is True
            else "Saving the maximum needs the matching machine/Pi support update"
        )
        self.refresh.setEnabled(ready)

    def _jog(self, direction: int) -> None:
        self.expire()
        button = self.up if direction > 0 else self.down
        if button.isEnabled():
            self.jogRequested.emit(direction * float(self.step.currentData()))

    def _apply(self) -> None:
        self.expire()
        if self.apply.isEnabled() and self._draft_maximum() is not None:
            self.maximum.interpretText()
            self.maximumRequested.emit(float(self.maximum.value()))


class MainboardZCoordinator(QtCore.QObject):
    """Serialize bounded worker requests without making ordinary status polls busy."""

    def __init__(self, panel: MainboardZPanel, controller: Any,
                 parent: QtCore.QObject | None = None) -> None:
        super().__init__(parent)
        self.panel = panel
        self.controller = controller
        self._status: dict[str, Any] = {}
        self._busy = False
        self._epoch = 0
        self._pending = False
        self._mutation = False
        self._queued: tuple[str, float] | None = None
        self._error_latched = False
        self._ui_paused = False
        self._last_request = -math.inf
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self.tick)
        controller.statusChanged.connect(self.set_status)
        controller.busyChanged.connect(self.set_busy)
        controller.stopInitiated.connect(self.stopped)
        panel.jogRequested.connect(lambda delta: self.change("z_jog", delta))
        panel.maximumRequested.connect(lambda maximum: self.change("z_max", maximum))
        panel.refreshRequested.connect(self.refresh)
        self._timer.start()

    def set_status(self, status: Mapping[str, Any]) -> None:
        machine = dict(machine_payload(status))
        changed = _session(machine) != _session(self._status)
        own_probe_activity = self._mutation and project_machine_state(machine).can_send_diagnostic
        if changed or (not _read_allowed(machine) and not own_probe_activity):
            self._epoch += 1
            self._queued = None
            if changed:
                self._error_latched = False
                self._last_request = -math.inf
        self._status = machine
        self.panel.set_machine_status(machine)

    def set_busy(self, busy: bool) -> None:
        self._busy = bool(busy)
        if busy and not self._mutation:
            self._epoch += 1
            self._queued = None
        self.panel.set_busy(busy)

    def stopped(self) -> None:
        self._epoch += 1
        self._queued = None
        self._error_latched = True
        self.panel.invalidate("STOP requested; Z reference must be re-established.",
                              clear_confirmation=True)

    def tick(self) -> None:
        self.panel.expire()
        # Hidden controls need no serial traffic. Modal setup owns its own
        # workers and receives the same controller gate for an in-flight read.
        if not self.panel.isVisible() or QtWidgets.QApplication.activeModalWidget() is not None:
            if not self._ui_paused:
                self._epoch += 1
                self._queued = None
                self._ui_paused = True
            if self.panel.fresh():
                self.panel.invalidate("Z readback paused while controls are hidden or a dialog is open.")
            return
        if self._ui_paused:
            self._ui_paused = False
            self._last_request = -math.inf
        if (
            self._pending or self._busy or self.panel._busy or self._error_latched
            or getattr(self.controller, "_shutdown_started", False)
            or not _read_allowed(self._status)
            or time.monotonic() - self._last_request < Z_POLL_SECONDS
        ):
            return
        self._request("status", None)

    def refresh(self) -> None:
        self._error_latched = False
        self._last_request = -math.inf
        self.tick()

    def change(self, action: str, value: float) -> None:
        if (
            self._busy or self.panel._busy or self._mutation or self._queued is not None
            or not _read_allowed(self._status) or not self.panel.fresh()
            or (action == "z_jog" and not self.panel.confirm.isChecked())
        ):
            return
        self._epoch += 1  # An earlier poll can never restore its pre-move readback.
        self.panel.invalidate("Moving Z…" if action == "z_jog" else "Saving maximum…")
        self.panel.set_pending(True)
        if self._pending:
            self._queued = (action, value)
        else:
            self._request(action, value)

    def _request(self, action: str, value: float | None) -> None:
        self._pending = True
        self._mutation = action != "status"
        self._last_request = time.monotonic()
        epoch = self._epoch
        expected_session = _session(self._status)
        machine = self.controller.runtime.context.machine

        def operation() -> dict[str, Any]:
            current = machine.status()  # Cache only; serial/network exchange stays below.
            if epoch != self._epoch or _session(current) != expected_session:
                raise RuntimeError("Controller session changed; Z request cancelled.")
            if not _read_allowed(current):
                raise RuntimeError("Z controls require an idle, connected machine.")
            method = getattr(machine, "mainboard_control", None)
            if not callable(method):
                raise RuntimeError("Z controls need the matching Pi support update.")
            result = method(action, value=value, confirmed=action != "status")
            if _session(machine.status()) != expected_session:
                raise RuntimeError("Controller session changed during the Z request.")
            return result

        def success(result: dict[str, Any]) -> None:
            if epoch != self._epoch or _session(self._status) != expected_session:
                return
            try:
                self.panel.set_result(result)
            except (ValueError, TypeError) as exc:
                failure(str(exc))

        def failure(message: str) -> None:
            if epoch == self._epoch:
                self._error_latched = True
                self.panel.invalidate(f"Z unavailable: {message}")

        def finished() -> None:
            self._pending = False
            self._mutation = False
            self.panel.set_pending(False)
            queued, self._queued = self._queued, None
            if queued is not None and not self._busy and _read_allowed(self._status):
                self.panel.set_pending(True)
                self._request(*queued)

        # _run retains worker lifetime, operation_scope and shutdown suppression.
        # Own epoch/session checks intentionally avoid ensure_connected/autoconnect.
        self.controller._run(
            operation, on_success=success, on_failure=failure, on_finished=finished,
            label="Read Ender Z" if action == "status" else "Ender Z control",
            show_busy=action != "status",
            serialize_controller=True,
        )
