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
    or its nested machine-status mapping. The widget deliberately emits only a
    request for software stop; callers retain ownership of the guarded machine
    action and its connection to ``MachineService``.
    """

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
        self._connected = False
        self._motion_enabled = False
        self._coordinate_reference_ready = False
        self._serial_backend = False

        layout = QtWidgets.QHBoxLayout(self)
        self._layout = layout
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(10)

        self.heading = QtWidgets.QLabel("Runtime")
        self.heading.setObjectName("runtimeStripHeading")
        self.heading.setAccessibleName("Runtime safety status")
        layout.addWidget(self.heading)

        self.mode_label = self._indicator("Runtime mode")
        self.connection_label = self._indicator("Controller connection")
        self.motion_label = self._indicator("Motion permission")
        layout.addWidget(self.mode_label)
        layout.addWidget(self.connection_label)
        layout.addWidget(self.motion_label)
        layout.addStretch(1)

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
        self.stop_button.clicked.connect(self.stopRequested)
        layout.addWidget(self.stop_button)

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

        if backend == "simulator":
            self._mode_text = "SIMULATION"
            self._mode_style = "statusGood"
            self._mode_description = "Simulator backend; serial hardware is not in use."
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
        self._connected = bool(machine.get("connected", False))
        self._motion_enabled = bool(machine.get("allow_motion", False))
        self._coordinate_reference_ready = bool(
            machine.get("coordinate_reference_ready", backend == "simulator")
        )
        self._render_status()

        # Status refreshes, disconnection, and ordinary background work must not
        # hide or disable the software stop request.
        self.stop_button.setEnabled(True)

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
                "SIMULATION": "SIM",
                "HARDWARE LOCKED": "HW LOCKED",
                "HARDWARE ENABLED": "HW ENABLED",
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
        if reference_required:
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
            self._layout.setSpacing(6)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Preferred,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
        else:
            self._layout.setContentsMargins(8, 4, 8, 4)
            self._layout.setSpacing(10)
            self.setSizePolicy(
                QtWidgets.QSizePolicy.Policy.Expanding,
                QtWidgets.QSizePolicy.Policy.Fixed,
            )
        self._render_status()
        self.updateGeometry()

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

        self.setProperty("busy", bool(busy))
        self.stop_button.setEnabled(True)
