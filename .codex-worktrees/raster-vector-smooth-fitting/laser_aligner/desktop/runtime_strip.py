from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


_STOP_DISCLAIMER = (
    "Software stop requests controller stop/reset and laser off. It is not a "
    "replacement for the physical emergency stop. In an emergency, use the "
    "hardware emergency stop or disconnect power."
)
_FULL_STOP_TEXT = "Software stop / laser off"
_COMPACT_STOP_TEXT = "STOP\nLASER OFF"
_CHROME_STOP_TEXT = "STOP / LASER OFF"
_COMPACT_BREAKPOINT_PX = 1100


class RuntimeSafetyStrip(QtWidgets.QWidget):
    """Persistent, presentation-only summary of machine runtime authority.

    ``set_status`` accepts either the complete ``CoreRuntime.status()`` payload
    or its nested machine-status mapping. The widget emits requests for the
    existing primary machine actions; callers retain ownership of every guarded
    operation and its connection to ``MachineService``.
    """

    connectRequested = QtCore.Signal()
    reconnectRequested = QtCore.Signal()
    disconnectRequested = QtCore.Signal()
    pauseRequested = QtCore.Signal()
    stopRequested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("runtimeSafetyStrip")
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self._compact = True
        self._chrome_mode = False
        self._mode_text = "HARDWARE LOCKED"
        self._mode_style = "statusWarning"
        self._mode_description = "Machine backend without process-level hardware access."
        self._busy = False
        self._wrapped = False
        self._connecting = False
        self._connected = False
        self._motion_enabled = False
        self._coordinate_reference_ready = False
        self._reconnect_required = False
        self._serial_backend = False

        layout = QtWidgets.QGridLayout(self)
        self._layout = layout
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setHorizontalSpacing(10)
        layout.setVerticalSpacing(4)

        self.heading = QtWidgets.QLabel("Runtime")
        self.heading.setObjectName("runtimeStripHeading")
        self.heading.setAccessibleName("Runtime safety status")

        self.mode_label = self._indicator("Runtime mode")
        self.connection_label = self._indicator("Controller connection")
        self.motion_label = self._indicator("Motion permission")

        self.connect_button = QtWidgets.QPushButton("Connect")
        self.connect_button.setObjectName("runtimeConnectButton")
        self.connect_button.setAccessibleName("Connect machine")
        self.disconnect_button = QtWidgets.QPushButton("Disconnect")
        self.disconnect_button.setObjectName("runtimeDisconnectButton")
        self.disconnect_button.setAccessibleName("Disconnect machine")
        self.pause_button = QtWidgets.QPushButton("Pause")
        self.pause_button.setObjectName("runtimePauseButton")
        self.pause_button.setAccessibleName("Pause or resume machine job")
        self.pause_button.setToolTip(
            "Disabled until Falcon realtime hold/resume is validated."
        )
        for button in (
            self.connect_button,
            self.disconnect_button,
            self.pause_button,
        ):
            button.setAutoDefault(False)
            button.setDefault(False)
            button.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Preferred,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )

        self.stop_button = QtWidgets.QPushButton(_COMPACT_STOP_TEXT)
        self.stop_button.setObjectName("dangerButton")
        self.stop_button.setToolTip(_STOP_DISCLAIMER)
        self.stop_button.setAccessibleName("Software stop and laser off")
        self.stop_button.setAccessibleDescription(_STOP_DISCLAIMER)
        self.stop_button.setAutoDefault(False)
        self.stop_button.setDefault(False)
        self.stop_button.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self._update_stop_minimum_size()
        self.connect_button.clicked.connect(self._connect_clicked)
        self.disconnect_button.clicked.connect(self.disconnectRequested)
        self.pause_button.clicked.connect(self.pauseRequested)
        self.stop_button.clicked.connect(self.stopRequested)
        self._reflow_layout()

        self.set_status(None)

    @staticmethod
    def _indicator(accessible_name: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel()
        label.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        label.setAccessibleName(accessible_name)
        label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Preferred,
            QtWidgets.QSizePolicy.Policy.Fixed,
        )
        return label

    @staticmethod
    def _machine_payload(status: Mapping[str, Any] | None) -> Mapping[str, Any]:
        if status is None:
            return {}
        nested = status.get("machine")
        if isinstance(nested, Mapping):
            return nested
        return status

    @staticmethod
    def _set_indicator(
        label: QtWidgets.QLabel,
        text: str,
        object_name: str,
        description: str,
    ) -> None:
        label.setText(text)
        label.setObjectName(object_name)
        label.setAccessibleDescription(description)
        label.setToolTip(description)
        style = label.style()
        style.unpolish(label)
        style.polish(label)

    def set_status(self, status: Mapping[str, Any] | None) -> None:
        """Project a runtime or machine status payload into visible indicators."""

        machine = self._machine_payload(status)
        backend = str(machine.get("backend", "")).strip().lower()
        self._serial_backend = backend == "serial"
        hardware_enabled = bool(machine.get("hardware_enabled", False))
        laser_lockout = bool(machine.get("laser_lockout", False))

        if hardware_enabled and laser_lockout:
            self._mode_text = "LASER LOCKOUT"
            self._mode_style = "statusWarning"
            self._mode_description = (
                "Hardware and motion access enabled; laser-enable programs are blocked."
            )
        elif hardware_enabled:
            self._mode_text = "HARDWARE ENABLED"
            self._mode_style = "statusBad"
            self._mode_description = (
                f"{backend or 'Machine'} backend with process-level hardware access enabled."
            )
        else:
            self._mode_text = "HARDWARE LOCKED"
            self._mode_style = "statusWarning"
            self._mode_description = (
                f"{backend or 'Machine'} backend without process-level hardware access."
            )
        self._connecting = bool(machine.get("connecting", False))
        self._connected = bool(machine.get("connected", False))
        self._motion_enabled = bool(machine.get("allow_motion", False))
        self._coordinate_reference_ready = bool(
            machine.get("coordinate_reference_ready", False)
        )
        self._reconnect_required = bool(
            machine.get("controller_reconnect_required", False)
        )
        self._render_status()

        # Status refreshes, disconnection, and ordinary background work must not
        # hide or disable the software stop request.
        self.stop_button.setEnabled(True)

    def _connect_clicked(self) -> None:
        if self._connected and self._reconnect_required:
            self.reconnectRequested.emit()
        else:
            self.connectRequested.emit()

    def _sync_primary_controls(self) -> None:
        reconnect_available = self._connected and self._reconnect_required
        self.connect_button.setText(
            "Reconnect" if reconnect_available else "Connect"
        )
        self.connect_button.setEnabled(
            not self._busy
            and not self._connecting
            and (not self._connected or reconnect_available)
        )
        self.connect_button.setToolTip(
            "Explicitly disconnect this untrusted session and connect again; "
            "Home / park will still be required"
            if reconnect_available
            else "Connect to the configured controller"
        )
        self.disconnect_button.setEnabled(
            not self._busy and not self._connecting and self._connected
        )
        self.disconnect_button.setToolTip(
            "Disconnect this untrusted controller session before reconnecting"
            if self._reconnect_required
            else "Disconnect the controller"
        )
        # Pause/resume remains presentation-only and unavailable until the
        # controller's realtime hold behavior is physically validated.
        self.pause_button.setEnabled(False)

    def _render_status(self) -> None:
        self.heading.setVisible(not self._compact and not self._chrome_mode)
        if self._chrome_mode:
            self.stop_button.setText(_CHROME_STOP_TEXT)
        else:
            self.stop_button.setText(
                _COMPACT_STOP_TEXT if self._compact else _FULL_STOP_TEXT
            )
        self._update_stop_minimum_size()
        self.stop_button.setToolTip(_STOP_DISCLAIMER)
        self.stop_button.setAccessibleName("Software stop and laser off")
        self.stop_button.setAccessibleDescription(_STOP_DISCLAIMER)

        mode_text = self._mode_text
        if self._chrome_mode:
            mode_text = {
                "HARDWARE LOCKED": "HW LOCKED",
                "HARDWARE ENABLED": "HW ENABLED",
                "LASER LOCKOUT": "LASER LOCKOUT",
            }.get(mode_text, mode_text)
        self._set_indicator(
            self.mode_label,
            mode_text,
            self._mode_style,
            self._mode_description,
        )

        connection_text = (
            "ONLINE" if self._connected else "OFFLINE"
        ) if self._compact or self._chrome_mode else (
            "Connected" if self._connected else "Disconnected"
        )
        self._set_indicator(
            self.connection_label,
            connection_text,
            "statusGood" if self._connected else "statusBad",
            "Controller connection is active."
            if self._connected
            else "Controller is disconnected.",
        )

        reference_required = (
            self._serial_backend
            and self._connected
            and self._motion_enabled
            and not self._coordinate_reference_ready
        )
        if self._connected and self._reconnect_required:
            motion_text = (
                "RECONNECT REQUIRED"
                if self._compact or self._chrome_mode
                else "Reconnect required"
            )
            motion_style = "statusBad"
            motion_description = (
                "Controller command ordering is no longer trusted. Disconnect and "
                "reconnect before Home / park or job execution."
            )
        elif reference_required:
            motion_text = (
                "HOME REQUIRED"
                if self._compact or self._chrome_mode
                else "Home required"
            )
            motion_style = "statusBad"
            motion_description = (
                "Absolute machine motion is blocked until Home / park establishes "
                "the coordinate reference for this controller session."
            )
        else:
            motion_text = (
                "MOTION READY" if self._motion_enabled else "MOTION OFF"
            ) if self._compact or self._chrome_mode else (
                "Motion ready" if self._motion_enabled else "Motion blocked"
            )
            motion_style = "statusWarning" if self._motion_enabled else "statusGood"
            motion_description = (
                "Configuration permits guarded machine motion and the required "
                "coordinate reference is ready."
                if self._motion_enabled
                else "Configuration blocks machine motion."
            )
        self._set_indicator(
            self.motion_label,
            motion_text,
            motion_style,
            motion_description,
        )
        self._sync_primary_controls()

    @property
    def compact(self) -> bool:
        return self._compact

    @property
    def chrome_mode(self) -> bool:
        return self._chrome_mode

    def set_chrome_mode(self, enabled: bool) -> None:
        """Use the persistent one-line main-window safety presentation."""

        enabled = bool(enabled)
        if enabled == self._chrome_mode:
            return
        self._chrome_mode = enabled
        self.setProperty("chromeMode", enabled)
        if enabled:
            self._layout.setContentsMargins(4, 1, 4, 1)
            self._layout.setHorizontalSpacing(6)
            self._layout.setVerticalSpacing(2)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
        else:
            self._layout.setContentsMargins(8, 4, 8, 4)
            self._layout.setHorizontalSpacing(10)
            self._layout.setVerticalSpacing(4)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
        self._render_status()
        self._set_wrapped(self.width() < _COMPACT_BREAKPOINT_PX)
        self.updateGeometry()

    def _set_wrapped(self, wrapped: bool) -> None:
        wrapped = bool(wrapped)
        if wrapped == self._wrapped:
            return
        self._wrapped = wrapped
        self.setProperty("wrapped", wrapped)
        self._reflow_layout()
        self.updateGeometry()

    def _reflow_layout(self) -> None:
        widgets = (
            self.heading,
            self.mode_label,
            self.connection_label,
            self.motion_label,
            self.connect_button,
            self.disconnect_button,
            self.pause_button,
            self.stop_button,
        )
        for widget in widgets:
            self._layout.removeWidget(widget)
        for column in range(12):
            self._layout.setColumnStretch(column, 0)

        if self._wrapped:
            # Use independent-looking thirds and quarters for the two rows.
            # Sharing three label-sized columns with four buttons lets a long
            # status label force the final STOP control beyond the toolbar at
            # compact widths and larger accessibility fonts.
            for column in range(12):
                self._layout.setColumnStretch(column, 1)
            self._layout.addWidget(self.mode_label, 0, 0, 1, 4)
            self._layout.addWidget(self.connection_label, 0, 4, 1, 4)
            self._layout.addWidget(self.motion_label, 0, 8, 1, 4)
            self._layout.addWidget(self.connect_button, 1, 0, 1, 3)
            self._layout.addWidget(self.disconnect_button, 1, 3, 1, 3)
            self._layout.addWidget(self.pause_button, 1, 6, 1, 3)
            self._layout.addWidget(self.stop_button, 1, 9, 1, 3)
            return

        self._layout.addWidget(self.heading, 0, 0)
        self._layout.addWidget(self.mode_label, 0, 1)
        self._layout.addWidget(self.connection_label, 0, 2)
        self._layout.addWidget(self.motion_label, 0, 3)
        self._layout.setColumnStretch(4, 1)
        self._layout.addWidget(self.connect_button, 0, 5)
        self._layout.addWidget(self.disconnect_button, 0, 6)
        self._layout.addWidget(self.pause_button, 0, 7)
        self._layout.addWidget(self.stop_button, 0, 8)

    def _set_compact(self, compact: bool) -> None:
        compact = bool(compact)
        if compact == self._compact:
            return
        self._compact = compact
        self.setProperty("compact", compact)
        self._render_status()
        self.updateGeometry()

    def _update_stop_minimum_size(self) -> None:
        metrics = self.stop_button.fontMetrics()
        if self._chrome_mode:
            self.stop_button.setMinimumWidth(
                metrics.horizontalAdvance(_CHROME_STOP_TEXT) + 32
            )
            self.stop_button.setMinimumHeight(metrics.lineSpacing() + 10)
            return
        compact_stop_width = max(
            metrics.horizontalAdvance(line)
            for line in _COMPACT_STOP_TEXT.splitlines()
        )
        self.stop_button.setMinimumWidth(compact_stop_width + 60)
        minimum_height = (
            metrics.lineSpacing() * len(_COMPACT_STOP_TEXT.splitlines()) + 18
            if self._compact
            else 34
        )
        self.stop_button.setMinimumHeight(minimum_height)

    def resizeEvent(self, event: QtGui.QResizeEvent) -> None:
        super().resizeEvent(event)
        self._set_wrapped(event.size().width() < _COMPACT_BREAKPOINT_PX)
        if not self._chrome_mode:
            self._set_compact(event.size().width() < _COMPACT_BREAKPOINT_PX)

    def changeEvent(self, event: QtCore.QEvent) -> None:
        super().changeEvent(event)
        if hasattr(self, "stop_button") and event.type() in {
            QtCore.QEvent.Type.FontChange,
            QtCore.QEvent.Type.StyleChange,
        }:
            self._update_stop_minimum_size()

    def set_busy(self, busy: bool) -> None:
        """Record ordinary UI activity without disabling the stop request."""

        self._busy = bool(busy)
        self.setProperty("busy", self._busy)
        self._sync_primary_controls()
        self.stop_button.setEnabled(True)
