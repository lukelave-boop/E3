from __future__ import annotations

import json
import os
import time
from pathlib import Path

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.calibration.lens import LensModel
from laser_aligner.camera.remote import RemoteCameraService
from laser_aligner.config import load_settings
from laser_aligner.core.runtime import CoreRuntime, RuntimeState
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.errors import CameraError, MachineError
from laser_aligner.machine import remote_service as remote_service_module
from laser_aligner.machine.pi_job_protocol import (
    CAPABILITY_PI_OWNED_JOBS,
    PiJobProtocolError,
)
from laser_aligner.machine.pi_machine_server import ACTION_SERVICE_CAPABILITIES
from laser_aligner.project import load_project
from tests.test_remote_machine_service import FakePi


@pytest.fixture(scope="module")
def application():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.processEvents()


def _wait_until(application, predicate) -> None:
    deadline = time.monotonic() + 5.0
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Desktop did not finish offline startup")
        time.sleep(0.002)


@pytest.mark.parametrize("failure", ["unreachable", "future_protocol", "missing_capability"])
def test_pi_failure_keeps_desktop_authoring_and_saved_state_usable(
    application, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    """A Pi update/outage cannot turn local startup into a required app update."""
    for key in ("LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME"):
        monkeypatch.setenv(key, str(tmp_path / "user-data"))
    monkeypatch.setenv("E3_BRIDGE_TOKEN", "remote-machine-test-token-value")
    config = tmp_path / "machine.json"
    config.write_text(json.dumps({
        "app": {"data_dir": str(tmp_path / "data"), "open_browser": False},
        "machine": {
            "backend": "serial", "protocol": "grbl",
            "port": "e3bridge://pi.test:9876", "allow_motion": True,
        },
        "camera": {"device": "e3camera://pi.test:9877", "autostart": True},
    }), encoding="utf-8")

    # Seed a genuine saved model before starting another app instance. No
    # remote request is made while constructing either runtime.
    seed = CoreRuntime(load_settings(config), hardware_enabled=True)
    seed.context.lens.save_model(LensModel(
        camera_matrix=np.array([[900.0, 0.0, 640.0], [0.0, 900.0, 360.0], [0.0, 0.0, 1.0]]),
        distortion=np.zeros(5), image_width=1280, image_height=720,
        rms_error=0.2, mean_reprojection_error=0.15,
        images_used=12, created_at=123456.0,
    ))
    model_id = seed.context.lens.model.model_id
    calibration_dir = seed.context.calibration_profiles.active_dir
    preserved = {
        path: path.read_bytes()
        for path in (config, seed.machine_registry.path, *calibration_dir.rglob("*"))
        if path.is_file()
    }
    seed.context.machine.detach()
    seed.context.camera.stop()

    fake = FakePi()
    if failure == "missing_capability":
        fake.capabilities.remove(CAPABILITY_PI_OWNED_JOBS)
    requests: list[str] = []

    def request(*args, **kwargs):
        action = args[3]["action"]
        requests.append(action)
        # An incompatible node may be queried; no controller action, raw
        # bridge fallback, upload, Home or START is permitted.
        assert action == ACTION_SERVICE_CAPABILITIES
        if failure == "unreachable":
            raise PiJobProtocolError("Could not connect to Pi: timed out")
        response = fake(*args, **kwargs)
        if failure == "future_protocol":
            response["protocol_version"] = "E3MACHINE/999"
        return response

    monkeypatch.setattr(remote_service_module, "request_response", request)
    camera_requests: list[str] = []

    def camera_request(_self, action, *_args, **_kwargs):
        camera_requests.append(action)
        raise CameraError("Remote camera protocol is incompatible")

    monkeypatch.setattr(RemoteCameraService, "_request", camera_request)
    errors: list[str] = []
    monkeypatch.setattr(E3MainWindow, "show_error", lambda _self, text: errors.append(text))
    runtime = CoreRuntime(load_settings(config), hardware_enabled=True)
    assert runtime.context.lens.model.model_id == model_id
    assert requests == camera_requests == []
    window = E3MainWindow(runtime)
    window.show()
    try:
        _wait_until(application, lambda: (
            runtime.running and not window.controller._active_tasks
            and runtime.context.machine.status()["status_error"] is not None
        ))
        window.controller.poll_status()
        assert window.isVisible()
        assert runtime.state is RuntimeState.RUNNING
        assert runtime.snapshot().error is None
        assert runtime.status()["camera"]["connected"] is False
        assert "incompatible" in runtime.status()["camera"]["last_error"]
        assert requests and camera_requests
        assert errors == []
        assert not window._machine_ui_state.can_home
        assert not window._machine_ui_state.can_jog
        assert not window._machine_ui_state.can_arm
        assert not window._machine_ui_state.can_start_job

        for action in ("rectangle", "save", "new", "open"):
            assert window.actions[action].isEnabled()
        window.actions["rectangle"].trigger()
        window.workspace.rectangleDrawCommitted.emit(25.0, 25.0, 10.0, 12.0)
        assert len(window.document.objects) == 1
        window.actions["undo"].trigger()
        assert window.document.objects == []
        window.actions["redo"].trigger()
        assert len(window.document.objects) == 1
        project_path = tmp_path / "offline.e3laser"
        window.project_path = project_path
        assert window.save_project()
        saved = load_project(project_path)
        assert len(saved.objects) == 1
        saved_object = saved.objects[0].to_dict()
        window.new_project()
        assert window.document.objects == []
        monkeypatch.setattr(QtWidgets.QFileDialog, "getOpenFileName", lambda *_a, **_k: (
            str(project_path), "E3 Laser Projects (*.e3laser)",
        ))
        window.open_project()
        assert window.document.objects[0].to_dict() == saved_object
        assert errors == []

        # Explicitly attempting Connect still fails closed at the unchanged
        # protocol boundary, while local editing remains available afterward.
        with pytest.raises(MachineError):
            runtime.context.machine.connect()
        assert runtime.running
        assert window.actions["rectangle"].isEnabled()
        assert runtime.context.machine.armed is False
        assert runtime.context.lens.model.model_id == model_id
        assert all(path.read_bytes() == content for path, content in preserved.items())
    finally:
        window.history.mark_clean()
        window.controller.stop()
        window._closing = True
        window.close()
        window.deleteLater()
        application.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
        application.processEvents()
    assert all(path.read_bytes() == content for path, content in preserved.items())
    assert set(requests) == {ACTION_SERVICE_CAPABILITIES}
