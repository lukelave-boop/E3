from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtWidgets

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
    assert strip.cancel_button.isEnabled() == (phase in {"running", "paused"})
    strip.set_status({**status, "control_in_use": True})
    assert not strip.connect_button.isEnabled()
    strip.close()
    app.processEvents()


def test_normal_stop_handler_retains_identity_and_never_connects_or_emergency_stops():
    requests = []
    options = []
    machine = SimpleNamespace(request_cancel=lambda **kwargs: requests.append(kwargs))
    def run(callback, **kwargs):
        options.append(kwargs)
        callback()
    controller = SimpleNamespace(runtime=SimpleNamespace(context=SimpleNamespace(machine=machine)), _run=run)
    DesktopController.cancel_job(controller, expected_pause={"run_id": 7, "revision": 3}, finish_buffered=True)
    assert requests == [{"expected_run_id": 7, "expected_pause_revision": 3, "finish_buffered": True}]
    assert options[0]["requires_controller"] is True
    assert options[0]["ensure_connection"] is False
