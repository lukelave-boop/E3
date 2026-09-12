"""Offscreen regression checks for the consolidated surface focus controls."""

from __future__ import annotations

import copy
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtWidgets

from laser_aligner.desktop.laser_focus import LaserFocusCoordinator, LaserFocusPanel
from laser_aligner.machine.laser_focus import PI_CAPABILITY
from laser_aligner.machine.pi_machine_server import (
    ACTION_MACHINE_FOCUS,
    ACTION_MACHINE_PREPARE_PHOTO_POSITION,
    ACTION_MACHINE_STATUS,
)
from tests.test_desktop_laser_focus import FakeController, result, status
from tests.test_remote_machine_service import FakePi, _service


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def combined(app):
    panel = LaserFocusPanel()
    controller = FakeController()
    coordinator = LaserFocusCoordinator(panel, controller)
    coordinator._timer.stop()
    controller.machine.payload = result(
        reference_ready=False, surface=None, preview=None,
        ender={"ready": True, "generation": 4, "recovery_required": False},
    )
    coordinator.set_status(controller.machine.snapshot)
    panel.set_result(controller.machine.payload)
    panel.path_clear.setChecked(True)
    panel.show()
    app.processEvents()
    yield panel, coordinator, controller
    coordinator.close()
    panel.close()
    panel.deleteLater()
    app.processEvents()


def install_home(controller, events, hook=None):
    def home():
        events.append("home")
        controller.machine.snapshot = status()
        if hook is not None:
            hook()
        return {"position": {"x": 15.0, "y": 195.0}}

    controller.machine.prepare_photo_position = home
    focus = controller.machine.focus_control

    def focus_control(action, **arguments):
        if action != "status":
            events.append(action)
        return focus(action, **arguments)

    controller.machine.focus_control = focus_control


def motion_calls(controller):
    return [call for call in controller.machine.calls if call[0] != "status"]


@pytest.mark.parametrize("home_required", [False, True])
def test_combined_reference_waits_for_successful_home(combined, home_required):
    panel, coordinator, controller = combined
    if home_required:
        controller.machine.snapshot = status(
            controller_state="READY_HOME_REQUIRED", coordinate_reference_ready=False,
            jog_ready=False,
        )
        coordinator.set_status(controller.machine.snapshot)
        panel.set_result(controller.machine.payload)
        panel.path_clear.setChecked(True)
    events = []
    install_home(controller, events)
    assert panel.reference.isEnabled()
    panel.reference.click()
    assert len(controller.work) == 1 and events == []
    assert coordinator._mutation and not panel.reference.isEnabled()
    assert controller.work[0][1]["serialize_controller"] is True
    controller.complete()
    assert events == ["home", "reference"]
    assert motion_calls(controller) == [
        ("reference", {"confirmed": True, "clearance_z_mm": 30.0, "gap_mm": 7.0})
    ]
    assert not coordinator._mutation


def test_failed_home_never_references_or_retries(combined):
    panel, coordinator, controller = combined
    events = []

    def failure():
        raise RuntimeError("Parking position was not verified")

    install_home(controller, events, failure)
    panel.reference.click()
    controller.complete()
    assert events == ["home"] and motion_calls(controller) == []
    assert "Parking position was not verified" in panel.message.text()
    coordinator.tick()
    assert controller.work == []


def test_own_home_status_updates_do_not_cancel_its_reference(combined):
    panel, coordinator, controller = combined
    events = []

    def report_home():
        coordinator.set_status(status(
            controller_state="READY_HOME_REQUIRED", coordinate_reference_ready=False,
            jog_ready=False,
        ))
        coordinator.set_status(controller.machine.snapshot)

    install_home(controller, events, report_home)
    panel.reference.click()
    controller.complete()
    assert events == ["home", "reference"]


@pytest.mark.parametrize("change", ["stop", "session", "ender", "clearance", "gap", "disconnect", "not_ready"])
def test_changed_authority_between_home_and_reference_blocks_descent(combined, change):
    panel, coordinator, controller = combined
    events = []

    def change_authority():
        if change == "stop":
            controller.emergency_stop()
        elif change == "session":
            controller.machine.snapshot = status(controller_session_generation=9)
        elif change == "ender":
            controller.machine.payload["ender"]["generation"] = 5
        elif change == "clearance":
            panel.clearance.setValue(40.0)
        elif change == "gap":
            panel.gap.setCurrentIndex(1)
        elif change == "disconnect":
            controller.machine.snapshot = status(connected=False, controller_state="DISCONNECTED")
        else:
            controller.machine.snapshot = status(
                controller_state="READY_HOME_REQUIRED", coordinate_reference_ready=False,
                jog_ready=False,
            )

    install_home(controller, events, change_authority)
    panel.reference.click()
    controller.complete()
    assert events == ["home"] and motion_calls(controller) == []
    assert not coordinator._mutation
    assert not panel.measure.isEnabled()
    coordinator.tick()
    assert controller.work == []


def test_stop_before_combined_worker_starts_prevents_home(combined):
    panel, coordinator, controller = combined
    events = []
    install_home(controller, events)
    panel.reference.click()
    controller.emergency_stop()
    controller.complete()
    assert events == [] and controller.machine.calls == []
    assert "STOP" in panel.message.text()
    coordinator.tick()
    assert controller.work == []


@pytest.mark.parametrize("phase", ["before_home", "after_home"])
@pytest.mark.parametrize("fault", ["unavailable", "stale"])
def test_combined_home_requires_fresh_focus_readback_at_each_phase(combined, phase, fault):
    panel, coordinator, controller = combined
    events = []

    def lose_focus_readback():
        if fault == "unavailable":
            controller.machine.payload["available"] = False
        else:
            controller.machine.payload["current_readback"]["fresh"] = False

    install_home(controller, events, lose_focus_readback if phase == "after_home" else None)
    panel.reference.click()
    if phase == "before_home":
        lose_focus_readback()
    controller.complete()
    assert events == (["home"] if phase == "after_home" else [])
    assert motion_calls(controller) == []
    coordinator.tick()
    assert controller.work == []


def test_recovery_reference_skips_home_and_keeps_fresh_headroom_confirmation(combined):
    panel, _, controller = combined
    events = []
    install_home(controller, events)
    controller.machine.payload = result(
        reference_ready=False, surface=None, preview=None,
        requires_clearance=True, xy_recovery_pending_reference=True,
        current_readback={"z_mm": None, "z_known": False, "fresh": True},
        ender={"ready": True, "generation": 4, "recovery_required": False},
    )
    panel.set_result(controller.machine.payload)
    panel.path_clear.setChecked(False)
    assert not panel.reference.isEnabled()
    panel.path_clear.setChecked(True)
    assert panel.reference.text() == "Reference border"
    assert panel.reference.isEnabled()
    panel.reference.click()
    controller.complete()
    assert events == ["reference"]
    assert [action for action, _ in motion_calls(controller)] == ["reference"]


def test_second_click_cannot_queue_a_second_combined_home(combined):
    panel, _, controller = combined
    events = []
    install_home(controller, events)
    panel.reference.click()
    panel.reference.click()
    assert len(controller.work) == 1
    controller.complete()
    assert events == ["home", "reference"]


@pytest.mark.parametrize("gate", ["headroom", "motion", "armed", "stale", "unavailable"])
def test_combined_reference_preserves_operator_and_machine_gates(combined, gate):
    panel, coordinator, controller = combined
    events = []
    install_home(controller, events)
    if gate == "headroom":
        panel.path_clear.setChecked(False)
    elif gate == "motion":
        coordinator.set_status(status(allow_motion=False))
    elif gate == "armed":
        coordinator.set_status(status(armed=True))
    elif gate == "stale":
        coordinator.set_status(status(status_stale=True))
    else:
        panel._result["available"] = False
        panel._sync()
    assert not panel.reference.isEnabled()
    panel.reference.click()
    assert controller.work == [] and events == []


@pytest.fixture
def remote_combined(combined, monkeypatch):
    panel, coordinator, controller = combined
    monkeypatch.setenv("E3_BRIDGE_TOKEN", "remote-machine-test-token-value")
    pi, machine = FakePi(), _service()
    pi.capabilities.append(PI_CAPABILITY)
    payload = copy.deepcopy(controller.machine.payload)
    seen = []

    def exchange(host, port, token, request, **kwargs):
        action = request["action"]
        seen.append((action, request.get("control")))
        if action == ACTION_MACHINE_PREPARE_PHOTO_POSITION:
            # Actual Home changes its state revision even when the session
            # began READY_MOTION. The normal response updates metadata only.
            pi.state_revision += 2
            return pi._response(request, result={"position": {"x": 15., "y": 195.}})
        if action == ACTION_MACHINE_FOCUS:
            response = copy.deepcopy(payload)
            response["action"] = request["control"]
            if request["control"] == "reference":
                response["reference_ready"] = True
            return pi._response(request, result=response)
        return pi(host, port, token, request, **kwargs)

    monkeypatch.setattr("laser_aligner.machine.remote_service.request_response", exchange)
    machine._refresh_once()
    controller.machine = machine
    controller.runtime.context.machine = machine
    coordinator.set_status(machine.status())
    panel.set_result(payload)
    panel.path_clear.setChecked(True)
    seen.clear()
    return panel, coordinator, controller, pi, seen


@pytest.mark.parametrize("publish_stale", [False, True])
def test_remote_combined_home_refreshes_actual_revision_cache_before_reference(remote_combined, publish_stale):
    panel, coordinator, controller, _, seen = remote_combined
    if publish_stale:
        prepare = controller.machine.prepare_photo_position

        def home():
            parked = prepare()
            assert controller.machine.status()["status_stale"] is True
            # A concurrent desktop status poll can publish this expected gap
            # between the new metadata and the complete read-only snapshot.
            coordinator.set_status(controller.machine.status())
            return parked

        controller.machine.prepare_photo_position = home
    panel.reference.click()
    controller.complete()
    assert seen == [
        (ACTION_MACHINE_FOCUS, "status"),
        (ACTION_MACHINE_PREPARE_PHOTO_POSITION, None),
        (ACTION_MACHINE_STATUS, None),
        (ACTION_MACHINE_FOCUS, "status"),
        (ACTION_MACHINE_FOCUS, "reference"),
    ]
    assert controller.machine.status()["status_stale"] is False
    assert panel._result["reference_ready"] is True
    assert not coordinator._home_refresh_pending


def test_stop_after_remote_home_prevents_even_the_following_refresh(remote_combined):
    panel, coordinator, controller, _, seen = remote_combined
    prepare = controller.machine.prepare_photo_position

    def stop_after_home():
        parked = prepare()
        controller.emergency_stop()
        return parked

    controller.machine.prepare_photo_position = stop_after_home
    panel.reference.click()
    controller.complete()
    assert seen == [
        (ACTION_MACHINE_FOCUS, "status"),
        (ACTION_MACHINE_PREPARE_PHOTO_POSITION, None),
    ]
    assert "STOP" in panel.message.text()
    assert not coordinator._home_refresh_pending


@pytest.mark.parametrize("change", ["stop", "session", "unavailable", "read_failure"])
def test_remote_home_status_refresh_cannot_restore_cancelled_motion(remote_combined, change):
    panel, coordinator, controller, pi, seen = remote_combined

    def during_refresh(action, request):
        if action != ACTION_MACHINE_STATUS:
            return
        if change == "stop":
            controller.emergency_stop()
        elif change == "session":
            pi.session_generation += 1
        elif change == "unavailable":
            pi.connected = False
            pi.controller_state = "DISCONNECTED"
            pi.state_revision += 1
        else:
            raise RuntimeError("Status read failed")

    pi.before_request = during_refresh
    panel.reference.click()
    controller.complete()
    assert seen == [
        (ACTION_MACHINE_FOCUS, "status"),
        (ACTION_MACHINE_PREPARE_PHOTO_POSITION, None),
        (ACTION_MACHINE_STATUS, None),
    ]
    assert not panel.measure.isEnabled()
    assert not coordinator._home_refresh_pending
