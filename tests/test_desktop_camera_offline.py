from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.controller import DesktopController
from laser_aligner.desktop.main_window import E3MainWindow


@pytest.fixture
def qt_application():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class _Camera:
    def __init__(self) -> None:
        self.connected = False
        self.frames_read = 0
        self.last_error = "offline"

    def status(self):
        return SimpleNamespace(
            connected=self.connected,
            frames_read=self.frames_read,
            last_error=self.last_error,
        )


class _Context:
    def __init__(self) -> None:
        self.camera = _Camera()
        self.bed = SimpleNamespace(calibration=object())

    def bed_calibration_validity(self):
        return {"state": "VALID", "reasons": []}


def _controller(device: str) -> DesktopController:
    context = _Context()
    runtime = SimpleNamespace(
        context=context,
        settings=SimpleNamespace(
            camera=SimpleNamespace(device=device),
        ),
        running=True,
        status=lambda: {"machine": {"job": {}}},
    )
    return DesktopController(runtime)


def test_automatic_unreachable_remote_camera_is_silent(qt_application) -> None:
    controller = _controller("e3camera://192.168.5.18:8766")
    errors: list[str] = []
    controller.cameraErrorOccurred.connect(errors.append)

    message = (
        "Could not communicate with remote camera at "
        "192.168.5.18:8766: timed out"
    )
    controller._camera_refresh_failed(message, 0)

    assert errors == []
    assert controller._camera_error_latched == message


def test_manual_unreachable_remote_camera_still_alerts(qt_application) -> None:
    controller = _controller("e3camera://192.168.5.18:8766")
    errors: list[str] = []
    controller.cameraErrorOccurred.connect(errors.append)

    message = (
        "Could not communicate with remote camera at "
        "192.168.5.18:8766: timed out"
    )
    controller._camera_refresh_failed(message, 0, manual=True)

    assert len(errors) == 1
    assert "Remote camera is unavailable" in errors[0]
    assert "usable offline" in errors[0]


def test_remote_configuration_error_is_not_silenced(qt_application) -> None:
    controller = _controller("e3camera://192.168.5.18:8766")
    errors: list[str] = []
    controller.cameraErrorOccurred.connect(errors.append)

    controller._camera_refresh_failed(
        "Remote Pi camera profile does not match the desktop profile: width",
        0,
    )

    assert len(errors) == 1
    assert "Remote camera is unavailable" in errors[0]


def test_local_camera_failure_keeps_existing_warning(qt_application) -> None:
    controller = _controller("0")
    errors: list[str] = []
    controller.cameraErrorOccurred.connect(errors.append)

    controller._camera_refresh_failed("Could not open camera 0", 0)

    assert len(errors) == 1
    assert "Another application may have exclusive control" in errors[0]


def _mapping_window():
    statuses: list[tuple[str, int]] = []
    setup_tabs: list[int] = []
    status_bar = SimpleNamespace(
        showMessage=lambda message, timeout: statuses.append((message, timeout))
    )
    window = SimpleNamespace(
        statusBar=lambda: status_bar,
        open_machine_setup=setup_tabs.append,
    )
    return window, statuses, setup_tabs


def test_offline_stale_bed_map_updates_status_without_dialog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, statuses, setup_tabs = _mapping_window()
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "question",
        lambda *_args, **_kwargs: pytest.fail("opened bed-mapping dialog offline"),
    )

    E3MainWindow.show_camera_mapping_required(
        window,
        {
            "camera_online": False,
            "setup_tab": 2,
            "reasons": ["Bed-map dependency changed: camera"],
        },
    )

    assert statuses == [("The corrected overlay needs a new bed map.", 15000)]
    assert setup_tabs == []


def test_online_stale_bed_map_keeps_recovery_dialog(
    qt_application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, statuses, setup_tabs = _mapping_window()
    questions: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def question(*args, **kwargs):
        questions.append((args, kwargs))
        return QtWidgets.QMessageBox.StandardButton.Open

    monkeypatch.setattr(QtWidgets.QMessageBox, "question", question)

    E3MainWindow.show_camera_mapping_required(
        window,
        {
            "camera_online": True,
            "setup_tab": 2,
            "reasons": ["Bed-map dependency changed: camera"],
        },
    )
    qt_application.processEvents()

    assert statuses == [
        ("Camera is online; the corrected overlay needs a new bed map.", 15000)
    ]
    assert len(questions) == 1
    args, kwargs = questions[0]
    assert kwargs == {}
    assert args[0] is window
    assert args[1] == "Bed mapping required"
    assert "Open Machine Setup at Bed mapping" in args[2]
    assert "Details: Bed-map dependency changed: camera" in args[2]
    assert args[3] == (
        QtWidgets.QMessageBox.StandardButton.Open
        | QtWidgets.QMessageBox.StandardButton.Cancel
    )
    assert args[4] == QtWidgets.QMessageBox.StandardButton.Open
    assert setup_tabs == [2]


def test_corrected_overlay_interval_defaults_to_two_fps_and_allows_15_fps(
    qt_application,
) -> None:
    controller = _controller("0")
    try:
        assert controller._live_camera_interval_ms == 500
        assert controller._camera_live_timer.interval() == 500

        for requested, expected in (
            (1, 67),
            (66, 67),
            (67, 67),
            (68, 68),
            (20_000, 10_000),
        ):
            controller.set_live_camera_interval(requested)
            assert controller._live_camera_interval_ms == expected
            assert controller._camera_live_timer.interval() == expected
    finally:
        controller.deleteLater()
        qt_application.processEvents()


def _hold_camera_refresh_tasks(
    controller: DesktopController,
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, object]]:
    launches: list[dict[str, object]] = []

    def fake_run(callback: object, **kwargs: object) -> object:
        launches.append({"callback": callback, **kwargs})
        return SimpleNamespace()

    monkeypatch.setattr(controller, "_run", fake_run)
    return launches


def _finish_camera_launch(launch: dict[str, object]) -> None:
    finished = launch["on_finished"]
    assert callable(finished)
    finished()


def test_slow_corrected_overlay_drops_periodic_ticks_without_backlog(
    qt_application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller("0")
    launches = _hold_camera_refresh_tasks(controller, monkeypatch)
    try:
        controller.refresh_camera_image()

        for _ in range(20):
            controller._camera_live_timer.timeout.emit()

        assert len(launches) == 1
        assert controller._camera_refresh_in_flight
        assert not controller._camera_refresh_pending

        _finish_camera_launch(launches[0])
        qt_application.processEvents()
        assert len(launches) == 1

        controller._camera_live_timer.timeout.emit()
        assert len(launches) == 2
        assert controller._camera_refresh_in_flight
    finally:
        controller.deleteLater()
        qt_application.processEvents()


def test_explicit_corrected_refresh_requests_coalesce_to_one_pending_job(
    qt_application,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    controller = _controller("0")
    launches = _hold_camera_refresh_tasks(controller, monkeypatch)
    try:
        controller.refresh_camera_image()

        for _ in range(20):
            controller.request_camera_refresh()

        assert len(launches) == 1
        assert controller._camera_refresh_in_flight
        assert controller._camera_refresh_pending

        _finish_camera_launch(launches[0])
        qt_application.processEvents()

        assert len(launches) == 2
        assert controller._camera_refresh_in_flight
        assert not controller._camera_refresh_pending

        _finish_camera_launch(launches[1])
        qt_application.processEvents()
        assert len(launches) == 2
        assert not controller._camera_refresh_in_flight
        assert not controller._camera_refresh_pending
    finally:
        controller.deleteLater()
        qt_application.processEvents()
