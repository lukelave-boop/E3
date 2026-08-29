from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.runtime_strip import RuntimeSafetyStrip
from laser_aligner.desktop.theme import DARK_STYLESHEET


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


@pytest.mark.parametrize(
    ("payload", "expected_mode", "connected", "motion"),
    [
        (
            {
                "runtime_state": "running",
                "machine": {
                    "backend": "serial",
                    "hardware_enabled": False,
                    "connected": True,
                    "allow_motion": False,
                },
            },
            "HARDWARE LOCKED",
            "Connected",
            "Motion blocked",
        ),
        (
            {
                "backend": "serial",
                "hardware_enabled": False,
                "connected": False,
                "allow_motion": False,
            },
            "HARDWARE LOCKED",
            "Disconnected",
            "Motion blocked",
        ),
        (
            {
                "machine": {
                    "backend": "serial",
                    "hardware_enabled": True,
                    "connected": True,
                    "allow_motion": True,
                    "coordinate_reference_ready": True,
                }
            },
            "HARDWARE ENABLED",
            "Connected",
            "Motion ready",
        ),
        (
            {
                "machine": {
                    "backend": "serial",
                    "hardware_enabled": True,
                    "laser_lockout": True,
                    "connected": True,
                    "allow_motion": True,
                    "coordinate_reference_ready": True,
                }
            },
            "LASER LOCKOUT",
            "Connected",
            "Motion ready",
        ),
    ],
)
def test_runtime_strip_derives_visible_machine_state(
    qt_application: QtWidgets.QApplication,
    payload: dict[str, object],
    expected_mode: str,
    connected: str,
    motion: str,
) -> None:
    strip = RuntimeSafetyStrip()
    strip.resize(1300, 60)
    strip.show()
    qt_application.processEvents()

    strip.set_status(payload)

    assert strip.mode_label.text() == expected_mode
    assert strip.connection_label.text() == connected
    assert strip.motion_label.text() == motion
    assert strip.mode_label.toolTip()
    assert strip.stop_button.isEnabled()


def test_runtime_strip_defaults_to_locked_disconnected_and_blocked(
    qt_application: QtWidgets.QApplication,
) -> None:
    strip = RuntimeSafetyStrip()
    strip.resize(1300, 60)
    strip.show()
    qt_application.processEvents()

    strip.set_status(None)

    assert strip.mode_label.text() == "HARDWARE LOCKED"
    assert strip.connection_label.text() == "Disconnected"
    assert strip.motion_label.text() == "Motion blocked"
    assert strip.connect_button.text() == "Connect"
    assert strip.connect_button.isEnabled()
    assert not strip.disconnect_button.isEnabled()
    assert not strip.pause_button.isEnabled()
    assert strip.stop_button.isEnabled()


def test_primary_controls_preserve_connection_gates_and_emit_requests(
    qt_application: QtWidgets.QApplication,
) -> None:
    strip = RuntimeSafetyStrip()
    requests: list[str] = []
    strip.connectRequested.connect(lambda: requests.append("connect"))
    strip.reconnectRequested.connect(lambda: requests.append("reconnect"))
    strip.disconnectRequested.connect(lambda: requests.append("disconnect"))
    strip.pauseRequested.connect(lambda: requests.append("pause"))

    strip.set_status(
        {
            "backend": "serial",
            "hardware_enabled": True,
            "connected": False,
            "allow_motion": True,
        }
    )
    strip.connect_button.click()
    assert requests == ["connect"]
    assert not strip.disconnect_button.isEnabled()
    assert not strip.pause_button.isEnabled()

    strip.set_status(
        {
            "backend": "serial",
            "hardware_enabled": True,
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": True,
        }
    )
    assert strip.connect_button.text() == "Connect"
    assert not strip.connect_button.isEnabled()
    assert strip.disconnect_button.isEnabled()
    strip.disconnect_button.click()
    assert requests == ["connect", "disconnect"]

    strip.set_status(
        {
            "backend": "serial",
            "hardware_enabled": True,
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": False,
            "controller_reconnect_required": True,
        }
    )
    assert strip.connect_button.text() == "Reconnect"
    assert strip.connect_button.isEnabled()
    assert strip.disconnect_button.isEnabled()
    assert "home / park" in strip.connect_button.toolTip().lower()
    strip.connect_button.click()
    assert requests == ["connect", "disconnect", "reconnect"]

    strip.pause_button.click()
    assert requests == ["connect", "disconnect", "reconnect"]

    strip.set_busy(True)
    assert not strip.connect_button.isEnabled()
    assert not strip.disconnect_button.isEnabled()
    assert not strip.pause_button.isEnabled()
    assert strip.stop_button.isEnabled()

    strip.set_busy(False)
    strip.set_status(
        {
            "backend": "serial",
            "hardware_enabled": True,
            "connecting": True,
            "connected": False,
            "allow_motion": True,
        }
    )
    assert not strip.connect_button.isEnabled()
    assert not strip.disconnect_button.isEnabled()
    assert not strip.pause_button.isEnabled()
    assert strip.stop_button.isEnabled()


def test_runtime_strip_marks_unreferenced_serial_motion_as_home_required(
    qt_application: QtWidgets.QApplication,
) -> None:
    strip = RuntimeSafetyStrip()
    strip.resize(900, 60)
    strip.show()
    strip.set_status(
        {
            "backend": "serial",
            "hardware_enabled": True,
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": False,
        }
    )
    qt_application.processEvents()

    assert strip.motion_label.text() == "HOME REQUIRED"
    assert "blocked" in strip.motion_label.toolTip().lower()


def test_runtime_strip_prioritizes_reconnect_over_home_required(
    qt_application: QtWidgets.QApplication,
) -> None:
    strip = RuntimeSafetyStrip()
    strip.resize(900, 60)
    strip.show()
    strip.set_status(
        {
            "backend": "serial",
            "hardware_enabled": True,
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": False,
            "controller_reconnect_required": True,
        }
    )
    qt_application.processEvents()

    assert strip.motion_label.text() == "RECONNECT REQUIRED"
    assert "disconnect and reconnect" in strip.motion_label.toolTip().lower()
    assert strip.stop_button.isEnabled()


def test_stop_remains_enabled_while_busy_and_emits_request(
    qt_application: QtWidgets.QApplication,
) -> None:
    strip = RuntimeSafetyStrip()
    requests: list[bool] = []
    strip.stopRequested.connect(lambda: requests.append(True))

    strip.set_busy(True)
    strip.set_status(
        {
            "backend": "serial",
            "hardware_enabled": True,
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": True,
            "job": {"running": True},
        }
    )
    strip.stop_button.click()

    assert strip.stop_button.isEnabled()
    assert requests == [True]


def test_stop_explains_physical_emergency_stop_boundary(
    qt_application: QtWidgets.QApplication,
) -> None:
    strip = RuntimeSafetyStrip()

    tooltip = strip.stop_button.toolTip().lower()
    accessible_description = strip.stop_button.accessibleDescription().lower()

    assert "physical emergency stop" in tooltip
    assert "hardware emergency stop" in tooltip
    assert "physical emergency stop" in accessible_description
    assert "hardware emergency stop" in accessible_description


def test_compact_strip_keeps_stop_text_visible_at_900px_with_large_font(
    qt_application: QtWidgets.QApplication,
) -> None:
    window = QtWidgets.QMainWindow()
    window.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 13pt; }")
    toolbar = QtWidgets.QToolBar("Runtime and safety", window)
    toolbar.setMovable(False)
    toolbar.setFloatable(False)
    strip = RuntimeSafetyStrip()
    toolbar.addWidget(strip)
    window.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
    window.resize(1320, 180)
    window.show()
    qt_application.processEvents()
    assert not strip.compact

    window.setFixedSize(900, 180)
    qt_application.processEvents()
    strip.set_status(
        {
            "backend": "serial",
            "hardware_enabled": True,
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": True,
        }
    )
    qt_application.processEvents()

    assert window.width() == 900
    assert strip.compact
    assert not strip.heading.isVisible()
    assert strip.mode_label.text() == "HARDWARE ENABLED"
    assert strip.connection_label.text() == "ONLINE"
    assert strip.motion_label.text() == "MOTION READY"
    assert strip.stop_button.text().splitlines() == ["STOP", "LASER OFF"]
    assert strip.stop_button.isVisible()
    assert strip.stop_button.isEnabled()

    option = QtWidgets.QStyleOptionButton()
    strip.stop_button.initStyleOption(option)
    content_rect = strip.stop_button.style().subElementRect(
        QtWidgets.QStyle.SubElement.SE_PushButtonContents,
        option,
        strip.stop_button,
    )
    text_width = max(
        option.fontMetrics.horizontalAdvance(line)
        for line in strip.stop_button.text().splitlines()
    )
    assert content_rect.width() >= text_width + 8
    assert content_rect.height() >= option.fontMetrics.lineSpacing() * 2

    button_top_left = strip.stop_button.mapTo(toolbar, QtCore.QPoint(0, 0))
    assert button_top_left.x() >= 0
    assert button_top_left.x() + strip.stop_button.width() <= toolbar.width()
    assert "physical emergency stop" in strip.stop_button.toolTip().lower()
    assert "physical emergency stop" in strip.stop_button.accessibleDescription().lower()

    window.close()
    window.deleteLater()
    qt_application.processEvents()


def test_chrome_mode_is_one_line_compact_and_keeps_safety_meaning(
    qt_application: QtWidgets.QApplication,
) -> None:
    strip = RuntimeSafetyStrip()
    strip.set_chrome_mode(True)
    strip.set_status(
        {
            "backend": "serial",
            "connected": True,
            "allow_motion": False,
        }
    )
    strip.resize(900, 34)
    strip.show()
    qt_application.processEvents()

    assert strip.chrome_mode
    assert not strip.heading.isVisible()
    assert strip.mode_label.text() == "HW LOCKED"
    assert strip.connection_label.text() == "ONLINE"
    assert strip.motion_label.text() == "MOTION OFF"
    assert strip.connect_button.text() == "Connect"
    assert not strip.connect_button.isEnabled()
    assert strip.disconnect_button.isEnabled()
    assert not strip.pause_button.isEnabled()
    assert strip.stop_button.text() == "STOP / LASER OFF"
    assert strip.stop_button.isEnabled()
    assert "physical emergency stop" in strip.stop_button.toolTip().lower()
    controls = (
        strip.connect_button,
        strip.disconnect_button,
        strip.pause_button,
        strip.stop_button,
    )
    assert all(
        button.geometry().center().y() == controls[0].geometry().center().y()
        for button in controls
    )
    assert all(
        left.geometry().right() < right.geometry().left()
        for left, right in zip(controls[:-1], controls[1:], strict=True)
    )


def test_chrome_mode_controls_stay_inside_900px_toolbar_with_large_font(
    qt_application: QtWidgets.QApplication,
) -> None:
    window = QtWidgets.QMainWindow()
    window.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 13pt; }")
    toolbar = QtWidgets.QToolBar("Runtime and safety", window)
    toolbar.setMovable(False)
    toolbar.setFloatable(False)
    strip = RuntimeSafetyStrip()
    strip.set_chrome_mode(True)
    toolbar.addWidget(strip)
    window.addToolBar(QtCore.Qt.ToolBarArea.TopToolBarArea, toolbar)
    window.setFixedSize(900, 180)
    window.show()
    strip.set_status(
        {
            "backend": "serial",
            "hardware_enabled": True,
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": False,
        }
    )
    qt_application.processEvents()

    assert strip.chrome_mode
    assert strip.width() <= toolbar.contentsRect().width()
    for control in (
        strip.connect_button,
        strip.disconnect_button,
        strip.pause_button,
        strip.stop_button,
    ):
        mapped = QtCore.QRect(control.mapTo(toolbar, QtCore.QPoint()), control.size())
        assert toolbar.contentsRect().contains(mapped)
    assert strip.stop_button.text() == "STOP / LASER OFF"
    assert strip.stop_button.isEnabled()

    window.close()
    window.deleteLater()
    qt_application.processEvents()
