from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtWidgets

from laser_aligner.desktop.laser_focus import LaserFocusCoordinator, LaserFocusPanel
from laser_aligner.desktop.machine_setup import MachineSetupDialog
from laser_aligner.desktop.machine_state import CONTROLLER_STATES, project_machine_state
from laser_aligner.desktop.runtime_strip import RuntimeSafetyStrip
from tests.test_desktop_laser_focus import FakeController, result, status
from tests.test_desktop_machine_setup import _runtime
from tests.test_desktop_machine_state import _CAPABILITIES


def remote_status(*, owned=False, in_use=True, **changes):
    return status(
        pi_owned_execution=True, node_reachable=True, status_stale=False,
        control_ownership_supported=True, control_owned=owned,
        control_in_use=in_use, **changes,
    )


@pytest.fixture
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


@pytest.mark.parametrize("controller_state", sorted(CONTROLLER_STATES))
def test_other_app_retains_control_while_observer_keeps_honest_status(controller_state):
    projection = project_machine_state(remote_status(controller_state=controller_state))
    assert projection.status_trusted
    assert projection.controller_state == controller_state
    assert {action for action in _CAPABILITIES if getattr(projection, f"can_{action}")} == {"stop"}
    assert projection.compact_connection_text == "PI IN USE"
    assert projection.compact_motion_text == "VIEW ONLY"
    assert "Disconnect that app first" in projection.blocked_reason("Home")


@pytest.mark.parametrize("in_use", [False, True])
def test_control_claim_is_explicit_and_restores_controls_only_after_ownership(app, in_use):
    strip = RuntimeSafetyStrip()
    connected, reconnected, disconnected = [], [], []
    strip.connectRequested.connect(lambda: connected.append(True))
    strip.reconnectRequested.connect(lambda: reconnected.append(True))
    strip.disconnectRequested.connect(lambda: disconnected.append(True))
    try:
        strip.set_status(remote_status(in_use=in_use))
        assert strip.connect_button.text() == "Connect"
        assert strip.connect_button.isEnabled() == (not in_use)
        assert not strip.disconnect_button.isEnabled()
        assert strip.stop_button.isEnabled()
        assert connected == reconnected == disconnected == []
        strip.connect_button.click()
        assert connected == ([] if in_use else [True])
        assert reconnected == disconnected == []
        assert not project_machine_state(remote_status(in_use=in_use)).can_home

        strip.set_status(remote_status(owned=True, in_use=False))
        assert not strip.connect_button.isEnabled()
        assert strip.disconnect_button.isEnabled()
        assert project_machine_state(remote_status(owned=True, in_use=False)).can_home
        assert strip.connection_label.text() == "SESSION READY"
        assert reconnected == disconnected == []
    finally:
        strip.deleteLater()


def test_unowned_reconnect_required_claims_control_before_replacing_session(app):
    strip = RuntimeSafetyStrip()
    calls = []
    strip.connectRequested.connect(lambda: calls.append("connect"))
    strip.reconnectRequested.connect(lambda: calls.append("reconnect"))
    try:
        strip.set_status(remote_status(in_use=False, controller_state="RECONNECT_REQUIRED"))
        assert strip.connect_button.text() == "Connect"
        strip.connect_button.click()
        assert calls == ["connect"]
    finally:
        strip.deleteLater()


@pytest.mark.parametrize("in_use", [False, True])
def test_setup_observer_cannot_disconnect_the_existing_controller(app, tmp_path, monkeypatch, in_use):
    runtime = _runtime(tmp_path)
    monkeypatch.setattr(runtime.context.machine, "status", lambda: remote_status(in_use=in_use))
    dialog = MachineSetupDialog(runtime, navigation_only=True)
    operations = []
    monkeypatch.setattr(dialog, "_start_operation", lambda name, *a, **k: operations.append(name))
    try:
        dialog.set_machine_status(remote_status(in_use=in_use))
        assert dialog.machine_connection_button.text() == "Connect machine"
        assert dialog.machine_connection_button.isEnabled() == (not in_use)
        assert "READY MOTION" in dialog.machine_connection_status.text()
        assert "VIEW ONLY" in dialog.machine_connection_status.text()
        assert dialog.machine_stop_button.isEnabled()
        assert operations == []
        dialog.toggle_machine_connection()
        assert operations == ([] if in_use else ["Controller connection"])
    finally:
        dialog.close()
        dialog.deleteLater()
        app.processEvents()
        runtime.stop()


@pytest.mark.parametrize("in_use", [False, True])
def test_focus_observer_does_not_poll_or_change_physical_focus(app, in_use):
    panel = LaserFocusPanel(calibration_mode=True)
    controller = FakeController()
    coordinator = LaserFocusCoordinator(panel, controller)
    coordinator._timer.stop()
    panel.show()
    app.processEvents()
    try:
        controller.machine.snapshot = remote_status(in_use=in_use)
        coordinator.set_status(controller.machine.snapshot)
        panel.set_result(result())
        panel.path_clear.setChecked(True)
        coordinator.tick()
        coordinator.refresh()
        coordinator.request("clear", {})
        assert controller.work == []
        assert controller.machine.calls == []
        assert not panel.reference.isEnabled()
        assert not panel.measure.isEnabled()
        assert not panel.up.isEnabled()
        assert panel.next_step.text() == project_machine_state(controller.machine.snapshot).control_reason

        controller.machine.snapshot = remote_status(owned=True, in_use=False)
        coordinator.set_status(controller.machine.snapshot)
        coordinator.tick()
        assert len(controller.work) == 1
        # Losing ownership before the queued worker runs must cancel serial I/O.
        controller.machine.snapshot = remote_status(in_use=True)
        operation, _callbacks = controller.work.pop()
        with pytest.raises(RuntimeError, match="idle, connected machine"):
            operation()
        assert controller.machine.calls == []
    finally:
        coordinator.close()
        panel.close()
        panel.deleteLater()
        app.processEvents()
