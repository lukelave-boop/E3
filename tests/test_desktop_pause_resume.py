from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.controller import DesktopController
from laser_aligner.desktop.runtime_strip import RuntimeSafetyStrip


@pytest.mark.parametrize("phase,label,enabled", [
    ("running", "Pause", True), ("pausing", "Pausing…", False),
    ("paused", "Resume", True), ("resuming", "Resuming…", False),
])
def test_actual_runtime_pause_button(phase, label, enabled):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    strip = RuntimeSafetyStrip()
    status = {
        "connected": True, "backend": "serial", "hardware_enabled": True,
        "allow_motion": True, "controller_state": "JOB_RUNNING",
        "controller_session_generation": 1, "job": {"running": True},
        "job_pause": {"available": True, "state": phase},
    }
    strip.set_status(status)
    assert strip.pause_button.text() == label
    assert strip.pause_button.isEnabled() == enabled
    if phase == "paused":
        assert strip.connection_label.text() == "JOB PAUSED"
    requested = []
    strip.pauseRequested.connect(lambda: requested.append(True))
    strip.pause_button.click()
    assert bool(requested) == enabled
    strip.set_busy(True)
    assert not strip.pause_button.isEnabled()
    assert strip.stop_button.isEnabled()
    strip.set_busy(False)
    strip.set_status({**status, "job_pause": {}})
    assert not strip.pause_button.isEnabled()
    strip.close()
    strip.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize("phase,resume", [("running", False), ("paused", True)])
def test_desktop_handler_sends_bound_request(phase, resume):
    requests = []
    machine = SimpleNamespace(
        status=lambda: {"job_pause": {"state": phase, "run_id": 7, "revision": 3}},
        request_pause=lambda **kwargs: requests.append(kwargs),
    )
    controller = SimpleNamespace(
        runtime=SimpleNamespace(context=SimpleNamespace(machine=machine)),
        _run=lambda callback, **_kwargs: callback(), _machine_changed=lambda _text: None,
    )
    DesktopController.pause_resume(controller)
    assert requests == [{"resume": resume, "expected_run_id": 7, "expected_pause_revision": 3}]

@pytest.mark.parametrize("phase", ["running", "paused", "pausing", "resuming"])
def test_unowned_active_job_exposes_take_control(phase):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    strip = RuntimeSafetyStrip()
    status = {
        "backend": "serial", "port": "e3bridge://127.0.0.1:8765", "remote": True,
        "hardware_enabled": True, "allow_motion": True, "connected": True,
        "controller_state": "JOB_RUNNING", "job": {"running": True},
        "control_ownership_supported": True, "control_owned": False,
        "control_in_use": False, "job_controls_supported": True,
        "job_pause": {"available": True, "state": phase, "cancel_available": True},
    }
    strip.set_status(status)
    assert strip.connect_button.isEnabled()
    assert strip.connect_button.text() == "Take control"
    assert not strip.pause_button.isEnabled()
    assert "Connect" in strip.pause_button.toolTip()
    strip.set_status({**status, "control_owned": True})
    assert strip.pause_button.isEnabled() == (phase in {"running", "paused"})
    assert strip.cancel_button.isEnabled()
    strip.set_status({**status, "control_in_use": True})
    assert not strip.connect_button.isEnabled()
    strip.close()
    strip.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize("failure", [False, True])
def test_stop_job_uses_priority_stop_without_queue_connect_or_resume(failure):
    events = []
    def stop(**kwargs):
        events.append(("stop", kwargs))
        if failure:
            raise RuntimeError("controller unreachable")
    controller = SimpleNamespace(
        runtime=SimpleNamespace(context=SimpleNamespace(machine=SimpleNamespace(request_job_stop=stop))),
        stopInitiated=SimpleNamespace(emit=lambda: events.append("invalidated")),
        errorOccurred=SimpleNamespace(emit=lambda message: events.append(("error", message))),
        _machine_changed=lambda message: events.append(("notice", message)),
    )
    DesktopController.cancel_job(controller)
    assert events[:2] == ["invalidated", ("stop", {})]
    assert events[2][0] == ("error" if failure else "notice")
    assert "controller unreachable" in events[2][1] if failure else "retained" in events[2][1]


def test_paused_stop_has_no_resume_confirmation_or_queued_work():
    from laser_aligner.desktop.main_window import E3MainWindow
    requested = []
    window = SimpleNamespace(controller=SimpleNamespace(cancel_job=lambda: requested.append(True)))
    E3MainWindow._cancel_running_job(window)
    assert requested == [True]


@pytest.mark.parametrize("change,enabled", [
    ({}, True), ({"job_controls_supported": False}, True),
    ({"status_stale": True}, False), ({"control_owned": False}, False),
    ({"controller_state": "READY_MOTION", "job": {"running": False}}, False),
])
def test_stop_job_availability_uses_current_job_not_pause_or_worker_gate(change, enabled):
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    strip = RuntimeSafetyStrip()
    status = {
        "backend": "serial", "remote": True, "connected": True, "port": "e3bridge://127.0.0.1:8765",
        "controller_state": "JOB_RUNNING", "job": {"running": True},
        "control_ownership_supported": True, "control_owned": True,
        "job_pause": {"state": "paused", "cancel_available": False},
    }
    strip.set_status({**status, **change})
    strip.set_busy(True)
    assert strip.cancel_button.isEnabled() is enabled
    assert strip.stop_button.isEnabled()
    requested = []
    strip.cancelRequested.connect(lambda: requested.append(True))
    strip.cancel_button.click()
    assert bool(requested) is enabled
    assert "discard" in strip.cancel_button.toolTip() or not enabled
    strip.close()
    strip.deleteLater()
    app.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    app.processEvents()
