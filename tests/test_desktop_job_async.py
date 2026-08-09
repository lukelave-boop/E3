from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.core import CoreRuntime
from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop.job_preview import JobPreviewCanvas
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.gcode.generator import GcodeProgram
from laser_aligner.gcode.job_plan import JobPlan, PlannedMove, build_job_plan
from laser_aligner.project import (
    LayerMode,
    ObjectKind,
    ProjectDocument,
    ProjectJob,
    SceneObject,
    Transform,
    capture_raster_asset_identity,
)


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _runtime(tmp_path: Path) -> CoreRuntime:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "config" / "default.json").read_text())
    payload["app"]["data_dir"] = str(tmp_path / "data")
    payload["app"]["open_browser"] = False
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return CoreRuntime.from_config(path, hardware_enabled=False)


def _job(move_count: int = 2) -> ProjectJob:
    lines = ["G21", "G90", "M5", "G0 X10 Y10 F2000"]
    x = 10
    for index in range(move_count):
        x = 11 if x == 10 else 10
        lines.append(f"G1 X{x} Y{10 + index % 20} F1000")
    lines.append("M5")
    text = "\n".join(lines)
    plan = build_job_plan(text, power_max=1000, start_position=(0.0, 0.0))
    return ProjectJob(
        text=text,
        bounds_mm=plan.bounds_mm,
        cut_length_mm=plan.cut_distance_mm,
        travel_length_mm=plan.travel_distance_mm,
        estimated_seconds=plan.total_seconds,
        path_count=len(plan.moves),
        point_count=len(plan.moves),
        plan=plan,
    )


def _repeated_plan(move_count: int, *, powered: bool = False) -> JobPlan:
    move = PlannedMove(
        index=0,
        line_number=4,
        start_x=10.0,
        start_y=10.0,
        end_x=11.0,
        end_y=10.0,
        rapid=False,
        laser_on=powered,
        power=100.0 if powered else 0.0,
        feed_mm_min=1000.0,
        layer_id="layer-1",
        layer_name="Line 01",
        layer_color="#E35D6A",
        layer_mode="line",
        pass_index=1,
        pass_count=1,
        source_name="Stress path",
        distance_mm=1.0,
        duration_seconds=1.0,
        start_seconds=0.0,
        end_seconds=1.0,
    )
    return JobPlan(
        moves=(move,) * move_count,
        bounds_mm=(10.0, 10.0, 11.0, 10.0),
        cut_distance_mm=float(move_count if powered else 0),
        travel_distance_mm=float(0 if powered else move_count),
        cut_seconds=float(move_count if powered else 0),
        travel_seconds=float(0 if powered else move_count),
        total_seconds=float(move_count),
        maximum_power=100.0 if powered else 0.0,
        power_max=1000,
        warnings=(),
    )


def _large_job(move_count: int, *, powered: bool = False) -> ProjectJob:
    plan = _repeated_plan(move_count, powered=powered)
    text = "\n".join(("G21", "G90", "M5", "G1 X11 Y10 F1000", "M5"))
    return ProjectJob(
        text=text,
        bounds_mm=plan.bounds_mm,
        cut_length_mm=plan.cut_distance_mm,
        travel_length_mm=plan.travel_distance_mm,
        estimated_seconds=plan.total_seconds,
        path_count=move_count if powered else 0,
        point_count=move_count,
        plan=plan,
    )


def _registration_job(job: ProjectJob) -> SimpleNamespace:
    return SimpleNamespace(
        program=job,
        power_percent=0.0,
        powered=False,
        display_name="Deterministic registration",
        filename="registration.gcode",
        targets=(object(),),
    )


def _core_registration_job(job: ProjectJob) -> SimpleNamespace:
    return SimpleNamespace(
        program=GcodeProgram(
            text=job.text,
            bounds_mm=job.bounds_mm,
            cut_length_mm=job.cut_length_mm,
            travel_length_mm=job.travel_length_mm,
            path_count=job.path_count,
            point_count=job.point_count,
        ),
        power_percent=0.0,
        powered=False,
        display_name="Base bed mapping",
        filename="base-bed-mapping.gcode",
        targets=(object(),),
    )


def _add_raster_source(window: E3MainWindow, path: Path) -> None:
    layer = window.document.layers[0]
    layer.mode = LayerMode.RASTER
    window.document.add_object(
        SceneObject(
            name=path.stem,
            kind=ObjectKind.IMAGE,
            layer_id=layer.id,
            transform=Transform(50.0, 50.0, 10.0, 10.0),
            geometry={"asset": str(path)},
        )
    )
    window._refresh_document()


def _wait_until(
    application: QtWidgets.QApplication,
    predicate,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for desktop preparation")
        time.sleep(0.002)


def _window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[E3MainWindow, list[str], list[str]]:
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    window = E3MainWindow(_runtime(tmp_path))
    errors: list[str] = []
    notices: list[str] = []
    window.show_error = errors.append  # type: ignore[method-assign]
    window.show_notice = notices.append  # type: ignore[method-assign]
    window.job_tabs.setCurrentIndex(0)
    window.show()
    return window, errors, notices


def _dispose(
    application: QtWidgets.QApplication,
    window: E3MainWindow,
) -> None:
    window.history.mark_clean()
    window._cancel_job_preparation("Test cleanup")
    window._cancel_job_render()
    window.controller.stop()
    window._closing = True
    window.close()
    window.deleteLater()
    application.processEvents()


def test_completed_calibration_job_reopens_setup_and_starts_capture(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _, _ = _window(tmp_path, monkeypatch)
    opened: list[tuple[int, str | None]] = []
    monkeypatch.setattr(
        window,
        "open_machine_setup",
        lambda tab_index=0, *, automatic_capture=None: opened.append(
            (tab_index, automatic_capture)
        ),
    )
    window._pending_calibration_capture = {
        "filename": "dense-validation.gcode",
        "tab_index": 4,
        "capture_action": "capture_dense_validation",
    }
    try:
        window._maybe_start_calibration_capture(
            {
                "job": {
                    "running": False,
                    "finished_at": 123.0,
                    "name": "dense-validation.gcode",
                    "phase": "complete",
                    "error": None,
                }
            }
        )
        qt_application.processEvents()

        assert opened == [(4, "capture_dense_validation")]
        assert window._pending_calibration_capture is None
    finally:
        _dispose(qt_application, window)


def test_failed_or_replaced_calibration_job_never_starts_capture(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _, _ = _window(tmp_path, monkeypatch)
    opened: list[tuple[int, str | None]] = []
    monkeypatch.setattr(
        window,
        "open_machine_setup",
        lambda tab_index=0, *, automatic_capture=None: opened.append(
            (tab_index, automatic_capture)
        ),
    )
    pending = {
        "filename": "fine-registration.gcode",
        "tab_index": 3,
        "capture_action": "capture_fine_registration",
    }
    try:
        window._pending_calibration_capture = dict(pending)
        window._maybe_start_calibration_capture(
            {
                "job": {
                    "running": False,
                    "finished_at": 123.0,
                    "name": "fine-registration.gcode",
                    "phase": "failed",
                    "error": "Controller error",
                }
            }
        )
        assert window._pending_calibration_capture is None

        window._pending_calibration_capture = dict(pending)
        window._invalidate_generated_job()
        qt_application.processEvents()

        assert opened == []
        assert window._pending_calibration_capture is None
    finally:
        _dispose(qt_application, window)


def test_generation_keeps_gui_and_stop_live_and_rejects_result(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, notices = _window(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def blocked_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        entered.set()
        assert release.wait(3.0)
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        blocked_generation,
    )
    heartbeat = 0
    timer = QtCore.QTimer()
    timer.setInterval(1)

    def beat() -> None:
        nonlocal heartbeat
        heartbeat += 1

    timer.timeout.connect(beat)
    timer.start()
    try:
        window.generate_toolpath()
        assert not window.job_panel.preparation_progress.isHidden()
        _wait_until(qt_application, entered.is_set)
        _wait_until(qt_application, lambda: heartbeat >= 5)

        assert window.runtime_strip.stop_button.isEnabled()
        window.runtime_strip.stop_button.click()
        qt_application.processEvents()
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and not window._job_preparation_busy,
        )

        assert window.last_job is None
        assert window.job_panel.preparation_progress.isHidden()
        assert not list((tmp_path / "data").rglob("*.gcode"))
        assert errors == []
        assert any("Stop cancelled" in notice for notice in notices)
    finally:
        release.set()
        timer.stop()
        _dispose(qt_application, window)


def test_queued_generation_cancelled_by_stop_skips_project_clone(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    queued: list[object] = []
    clone_calls = 0
    original_clone = ProjectDocument.clone

    def capture_task(operation, **_kwargs) -> None:
        queued.append(operation)

    def counted_clone(document: ProjectDocument) -> ProjectDocument:
        nonlocal clone_calls
        clone_calls += 1
        return original_clone(document)

    monkeypatch.setattr(window.controller, "run_background", capture_task)
    monkeypatch.setattr(ProjectDocument, "clone", counted_clone)
    try:
        window.generate_toolpath()
        assert len(queued) == 1
        assert clone_calls == 0

        window.runtime_strip.stop_button.click()
        qt_application.processEvents()

        operation = queued[0]
        assert callable(operation)
        assert operation() is None
        assert clone_calls == 0
        assert window.last_job is None
        assert not window.actions["run"].isEnabled()
        assert not list((tmp_path / "data").rglob("*.gcode"))
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_background_results_are_dispatched_on_gui_thread(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _errors, _notices = _window(tmp_path, monkeypatch)
    gui_thread = threading.get_ident()
    worker_threads: list[int] = []
    callback_threads: list[int] = []
    try:
        window.controller.run_background(
            lambda: worker_threads.append(threading.get_ident()),
            on_success=lambda _result: callback_threads.append(
                threading.get_ident()
            ),
            label="Thread-affinity probe",
        )
        _wait_until(qt_application, lambda: bool(callback_threads))

        assert worker_threads and worker_threads[0] != gui_thread
        assert callback_threads == [gui_thread]
    finally:
        _dispose(qt_application, window)


def test_large_job_text_workspace_and_dialog_render_in_event_loop_slices(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    job = _job(18_000)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: job,
    )
    heartbeat = 0
    timer = QtCore.QTimer()
    timer.setInterval(0)

    def beat() -> None:
        nonlocal heartbeat
        heartbeat += 1

    timer.timeout.connect(beat)
    timer.start()
    try:
        window.generate_toolpath()
        assert not window.job_panel.preparation_progress.isHidden()
        _wait_until(qt_application, lambda: window.last_job is job)
        _wait_until(qt_application, lambda: not window._job_preparation_busy)

        assert heartbeat >= 5
        assert window.gcode_preview.toPlainText() == job.text
        assert not window.last_job_powered
        assert 1 <= len(window.workspace._toolpath_items) <= 3
        assert window._job_preview_dialog is not None
        assert 1 <= len(window._job_preview_dialog.canvas._items) <= 3
        assert window.job_panel.preparation_progress.isHidden()
        assert errors == []
    finally:
        timer.stop()
        _dispose(qt_application, window)


def test_project_revision_rejects_inflight_generation_result(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, notices = _window(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def blocked_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        entered.set()
        assert release.wait(3.0)
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        blocked_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(qt_application, entered.is_set)
        window.document.touch()
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and not window._job_preparation_busy,
        )

        assert window.last_job is None
        assert errors == []
        assert any("stale generated result" in notice for notice in notices)
    finally:
        release.set()
        _dispose(qt_application, window)


def test_generation_failure_clears_busy_state_without_partial_job(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)

    def fail_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        raise ValueError("deterministic planning failure")

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        fail_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and not window._job_preparation_busy,
        )

        assert window.last_job is None
        assert window.actions["generate"].isEnabled()
        assert window.job_panel.preparation_progress.isHidden()
        assert errors == [
            "Toolpath generation failed: deterministic planning failure"
        ]
    finally:
        _dispose(qt_application, window)


@pytest.mark.parametrize("move_count", [1_000, 100_000])
def test_closing_unfinished_preview_invalidates_exact_job(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    move_count: int,
) -> None:
    window, errors, notices = _window(tmp_path, monkeypatch)
    job = _large_job(move_count)

    def stalled_slice(self: JobPreviewCanvas) -> None:
        self.buildProgress.emit(0, max(1, self._build_target))

    monkeypatch.setattr(JobPreviewCanvas, "_build_slice", stalled_slice)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: job,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window._job_preview_dialog is not None
            and window._job_preview_dialog.canvas._building,
            timeout=10.0,
        )
        dialog = window._job_preview_dialog
        assert dialog is not None
        assert not dialog.close_button.isEnabled()

        dialog.close()
        _wait_until(
            qt_application,
            lambda: window._job_preparation_owner is None
            and window.last_job is None,
        )

        assert not window.actions["run"].isEnabled()
        assert not window.actions["export_gcode"].isEnabled()
        assert not window.actions["preview_job"].isEnabled()
        assert not any("ready for review" in notice for notice in notices)
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_core_gcode_calibration_job_is_adapted_for_desktop_preview(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    payloads: list[dict[str, object]] = []

    def capture_install(request_id: int, payload: dict[str, object]) -> None:
        del request_id
        payloads.append(payload)

    monkeypatch.setattr(window, "_install_generated_job", capture_install)
    source = _job()
    try:
        window._load_fine_registration_job(_core_registration_job(source))

        assert len(payloads) == 1
        adapted = payloads[0]["job"]
        assert isinstance(adapted, ProjectJob)
        assert adapted.text == source.text
        assert adapted.bounds_mm == source.bounds_mm
        assert adapted.plan is not None
        assert tuple(
            (move.end_x, move.end_y, move.laser_on)
            for move in adapted.plan.moves
        ) == tuple(
            (move.end_x, move.end_y, move.laser_on)
            for move in source.plan.moves
        )
        assert adapted.plan.moves[0].start_x == 110.0
        assert adapted.plan.moves[0].start_y == 110.0
        assert adapted.raster_assets == ()
        assert errors == []
    finally:
        _dispose(qt_application, window)


@pytest.mark.parametrize("stale_failure", [False, True])
def test_registration_render_owns_busy_state_against_late_worker(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stale_failure: bool,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    callbacks: dict[str, object] = {}

    def blocked_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        entered.set()
        assert release.wait(5.0)
        if stale_failure:
            raise ValueError("late stale failure")
        return _job()

    def held_workspace(
        plan,
        *,
        on_progress=None,
        on_finished=None,
        on_failed=None,
    ) -> None:
        del plan, on_progress, on_failed
        callbacks["finished"] = on_finished

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        blocked_generation,
    )
    monkeypatch.setattr(window.workspace, "start_toolpath_preview", held_workspace)
    registration = _large_job(10)
    try:
        window.generate_toolpath()
        _wait_until(qt_application, entered.is_set)
        stale_request = next(
            request_id
            for request_id, phase in window._job_worker_phases.items()
            if phase == "planning"
        )

        window._load_fine_registration_job(_registration_job(registration))
        current_request = window._job_render_request_id
        assert current_request is not None
        assert window._job_preparation_owner == ("render", current_request)
        release.set()
        _wait_until(
            qt_application,
            lambda: stale_request not in window._job_worker_requests,
        )

        assert window.last_job is registration
        assert window._job_preparation_owner == ("render", current_request)
        assert window._job_preparation_busy
        assert not window.actions["run"].isEnabled()
        finished = callbacks["finished"]
        assert callable(finished)
        finished(True)
        _wait_until(
            qt_application,
            lambda: window._job_preparation_owner is None,
        )
        assert window.last_job is registration
        assert window.actions["run"].isEnabled()
        assert errors == []
    finally:
        release.set()
        _dispose(qt_application, window)


@pytest.mark.parametrize("failure_site", ["gcode", "workspace", "dialog"])
def test_render_construction_errors_fail_closed(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_site: str,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: _job(),
    )
    if failure_site == "gcode":
        class FailingCursor:
            def __init__(self, *args, **kwargs) -> None:
                del args, kwargs

            def insertText(self, _text: str) -> None:
                raise RuntimeError("deterministic G-code insertion failure")

        monkeypatch.setattr(main_window_module.QtGui, "QTextCursor", FailingCursor)
    elif failure_site == "workspace":
        monkeypatch.setattr(
            window.workspace,
            "start_toolpath_preview",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                MemoryError("deterministic workspace allocation failure")
            ),
        )
    else:
        monkeypatch.setattr(
            main_window_module,
            "JobPreviewDialog",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("deterministic dialog construction failure")
            ),
        )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
        )

        assert window.last_job is None
        assert not window._job_preparation_busy
        assert not window.actions["run"].isEnabled()
        assert len(errors) == 1
        assert "failed" in errors[0]
        assert "deterministic" in errors[0]
    finally:
        _dispose(qt_application, window)


@pytest.mark.parametrize("replacement", ["new", "open"])
def test_project_replacement_cancels_worker_without_late_install(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def blocked_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        entered.set()
        assert release.wait(5.0)
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        blocked_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(qt_application, entered.is_set)
        if replacement == "new":
            window.new_project()
            replacement_document = window.document
        else:
            replacement_document = ProjectDocument.new("Opened replacement")
            monkeypatch.setattr(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                lambda *args, **kwargs: (str(tmp_path / "replacement.e3laser"), ""),
            )
            monkeypatch.setattr(
                main_window_module,
                "load_project",
                lambda _path: replacement_document,
            )
            monkeypatch.setattr(
                main_window_module,
                "autosave_is_newer",
                lambda *args, **kwargs: False,
            )
            window.open_project()
        assert window.document is replacement_document

        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
        )

        assert window.document is replacement_document
        assert window.last_job is None
        assert not list((tmp_path / "data").rglob("*.gcode"))
        assert errors == []
    finally:
        release.set()
        _dispose(qt_application, window)


def test_close_waits_for_cancelled_worker_ownership_to_drain(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _errors, _notices = _window(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def blocked_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        entered.set()
        assert release.wait(5.0)
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        blocked_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(qt_application, entered.is_set)

        window.close()
        qt_application.processEvents()

        assert window._close_requested
        assert not window._closing
        assert window.controller.has_active_tasks
        assert window.isVisible()
        release.set()
        _wait_until(
            qt_application,
            lambda: window._closing and not window.isVisible(),
            timeout=10.0,
        )
        assert not window.controller.has_active_tasks
        assert not window.runtime.running
        assert window.last_job is None
    finally:
        release.set()
        if not window._closing:
            _wait_until(
                qt_application,
                lambda: not window.controller.has_active_tasks,
                timeout=10.0,
            )
            window.controller.stop()
            window._closing = True
            window.close()
        window.deleteLater()
        qt_application.processEvents()


def test_start_here_preserves_exact_raster_identities(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    image_path = tmp_path / "source.png"
    image = QtGui.QImage(2, 2, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("black"))
    assert image.save(str(image_path), "PNG")
    _add_raster_source(window, image_path)
    source = _job(8)
    source.raster_assets = (capture_raster_asset_identity(image_path),)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: source,
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Ok,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window.last_job is source and not window._job_preparation_busy,
        )

        window._prepare_start_here(0)
        _wait_until(
            qt_application,
            lambda: window.last_job is not None
            and window.last_job is not source
            and not window._job_preparation_busy,
        )

        assert window.last_job.raster_assets == source.raster_assets
        assert window.last_job_name == "start-here-move-1.gcode"
        assert '; @E3_JOB {"start_x":110.0,"start_y":110.0}' in window.last_job.text
        approach = window.last_job.plan.moves[0]
        assert approach.rapid and not approach.laser_on
        assert (approach.start_x, approach.start_y) == pytest.approx((110.0, 110.0))
        assert (approach.end_x, approach.end_y) == pytest.approx((0.0, 0.0))
        preflight = window.runtime.context.machine.preflight_program(
            window.last_job.text
        )
        assert preflight.requires_motion
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_software_stop_cancels_start_here_worker(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    source = _job(8)
    entered = threading.Event()
    release = threading.Event()
    original_restart = main_window_module.restart_program_from_move
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: source,
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Ok,
    )

    def blocked_restart(*args, **kwargs):
        entered.set()
        assert release.wait(5.0)
        return original_restart(*args, **kwargs)

    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window.last_job is source and not window._job_preparation_busy,
        )
        monkeypatch.setattr(
            main_window_module,
            "restart_program_from_move",
            blocked_restart,
        )

        window._prepare_start_here(0)
        _wait_until(qt_application, entered.is_set)
        window.runtime_strip.stop_button.click()
        qt_application.processEvents()
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
        )

        assert window.last_job is None
        assert not window.actions["run"].isEnabled()
        assert errors == []
    finally:
        release.set()
        _dispose(qt_application, window)


@pytest.mark.parametrize("action", ["preview", "export", "run"])
def test_changed_raster_asset_blocks_prepared_job_actions(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    image_path = tmp_path / "mutable.bmp"
    image = QtGui.QImage(2, 1, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("black"))
    assert image.save(str(image_path), "BMP")
    _add_raster_source(window, image_path)
    job = _job()
    job.raster_assets = (capture_raster_asset_identity(image_path),)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: job,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window.last_job is job and not window._job_preparation_busy,
        )
        image.fill(QtGui.QColor("white"))
        assert image.save(str(image_path), "BMP")

        if action == "preview":
            window.show_job_preview()
        elif action == "export":
            window.export_gcode()
        else:
            window.run_current_job()

        assert window.last_job is None
        assert not window.actions["run"].isEnabled()
        assert len(errors) == 1
        assert "changed on disk" in errors[0]
    finally:
        _dispose(qt_application, window)


def test_same_path_raster_change_refreshes_canvas_and_rejects_first_generation(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    image_path = tmp_path / "same-path.bmp"
    black = QtGui.QImage(2, 1, QtGui.QImage.Format.Format_RGB32)
    black.fill(QtGui.QColor("black"))
    assert black.save(str(image_path), "BMP")
    original_stat = image_path.stat()
    _add_raster_source(window, image_path)
    displayed_before = next(iter(window.workspace._items_by_id.values()))
    identity_before = displayed_before.raster_preview_identity
    assert identity_before is not None

    white = QtGui.QImage(2, 1, QtGui.QImage.Format.Format_RGB32)
    white.fill(QtGui.QColor("white"))
    assert white.save(str(image_path), "BMP")
    assert image_path.stat().st_size == original_stat.st_size
    os.utime(
        image_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    changed_job = _job()
    changed_job.raster_assets = (capture_raster_asset_identity(image_path),)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: changed_job,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
        )

        displayed_after = next(iter(window.workspace._items_by_id.values()))
        assert displayed_after is displayed_before
        assert displayed_after.raster_preview_identity == (
            changed_job.raster_assets[0].path,
            changed_job.raster_assets[0].sha256,
        )
        assert displayed_after.raster_preview_identity != identity_before
        assert window.last_job is None
        assert not window.actions["run"].isEnabled()
        assert not window.actions["export_gcode"].isEnabled()
        assert errors and "canvas has been refreshed" in errors[-1]

        errors.clear()
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window.last_job is changed_job
            and not window._job_preparation_busy,
        )

        assert window.actions["run"].isEnabled()
        assert window.actions["export_gcode"].isEnabled()
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_exact_plan_controls_zero_effective_power_state_and_run_gate(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    layer = window.document.layers[0]
    layer.power_percent = 0.04
    window.document.add_object(
        SceneObject.line(
            layer.id,
            name="Sub-quantized line",
            center=(55.0, 50.0),
            length_mm=10.0,
        )
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append("powered warning")
        or QtWidgets.QMessageBox.StandardButton.Cancel,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window.last_job is not None and not window._job_preparation_busy,
        )

        assert window.last_job.plan is not None
        assert not window.last_job.plan.powered
        assert not window.last_job_powered
        window.run_current_job()
        assert warnings == []
        assert errors == ["Motion is blocked in the local configuration"]
    finally:
        _dispose(qt_application, window)


def test_offline_prepared_job_reaches_controller_auto_connect_path(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    job = _job()
    calls: list[tuple[str, str, str | None]] = []
    try:
        window.runtime.settings.machine.allow_motion = True
        window.last_job = job
        window.last_job_name = "offline-prepared.gcode"
        window.last_job_revision = window.document.revision
        window.last_job_work_area = window._work_area_signature(
            window.runtime.settings.machine.work_area
        )
        window.last_job_powered = False
        window.controller.run_job = (  # type: ignore[method-assign]
            lambda text, name, *, arm_phrase=None: calls.append(
                (text, name, arm_phrase)
            )
        )
        assert window.runtime.context.machine.status()["connected"] is False

        window.run_current_job()

        assert calls == [(job.text, "offline-prepared.gcode", None)]
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_powered_start_has_no_confirmation_and_uses_one_time_arm(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    job = _large_job(2, powered=True)
    calls: list[tuple[str, str, str | None]] = []
    try:
        window.runtime.settings.machine.allow_motion = True
        window.last_job = job
        window.last_job_name = "powered-calibration.gcode"
        window.last_job_revision = window.document.revision
        window.last_job_work_area = window._work_area_signature(
            window.runtime.settings.machine.work_area
        )
        window.last_job_powered = True
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "warning",
            lambda *args, **kwargs: pytest.fail("powered warning was displayed"),
        )
        monkeypatch.setattr(
            QtWidgets.QInputDialog,
            "getText",
            lambda *args, **kwargs: pytest.fail("arming phrase was requested"),
        )
        window.controller.run_job = (  # type: ignore[method-assign]
            lambda text, name, *, arm_phrase=None: calls.append(
                (text, name, arm_phrase)
            )
        )

        window.run_current_job()

        assert calls == [
            (job.text, "powered-calibration.gcode", "ENABLE LASER CONTROL")
        ]
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_near_cap_snapshot_clone_keeps_gui_and_stop_live(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    window.document.metadata["near_cap_snapshot"] = [0] * 250_000
    entered = threading.Event()
    release = threading.Event()
    original_clone = ProjectDocument.clone

    def controlled_clone(document: ProjectDocument) -> ProjectDocument:
        snapshot = original_clone(document)
        entered.set()
        assert release.wait(5.0)
        return snapshot

    monkeypatch.setattr(ProjectDocument, "clone", controlled_clone)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: _job(),
    )
    heartbeat = 0
    timer = QtCore.QTimer()
    timer.setInterval(1)

    def beat() -> None:
        nonlocal heartbeat
        heartbeat += 1

    timer.timeout.connect(beat)
    timer.start()
    try:
        window.generate_toolpath()
        assert not window.workspace.isEnabled()
        assert not window.actions["new"].isEnabled()
        _wait_until(qt_application, entered.is_set, timeout=10.0)
        _wait_until(qt_application, lambda: heartbeat >= 5)
        assert window.runtime_strip.stop_button.isEnabled()

        window.runtime_strip.stop_button.click()
        qt_application.processEvents()
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
            timeout=10.0,
        )

        assert window.workspace.isEnabled()
        assert window.actions["new"].isEnabled()
        assert window.last_job is None
        assert errors == []
    finally:
        release.set()
        timer.stop()
        _dispose(qt_application, window)


def test_near_cap_backward_timeline_scrub_is_time_sliced(
    qt_application: QtWidgets.QApplication,
) -> None:
    plan = _repeated_plan(250_000)
    move_ends = tuple(float(index + 1) for index in range(len(plan.moves)))
    canvas = JobPreviewCanvas(
        plan,
        (0.0, 220.0, 0.0, 220.0),
        move_ends=move_ends,
        defer_render=True,
    )
    heartbeat = 0
    timer = QtCore.QTimer()
    timer.setInterval(1)

    def beat() -> None:
        nonlocal heartbeat
        heartbeat += 1

    timer.timeout.connect(beat)
    timer.start()
    try:
        canvas.start_deferred_render()
        _wait_until(
            qt_application,
            lambda: not canvas._building,
            timeout=20.0,
        )
        before = heartbeat
        started = time.perf_counter()
        canvas.set_elapsed(plan.total_seconds / 2.0)
        call_seconds = time.perf_counter() - started

        assert call_seconds < 0.08
        assert canvas._building
        _wait_until(
            qt_application,
            lambda: not canvas._building and heartbeat >= before + 5,
            timeout=20.0,
        )
        assert 124_999 <= canvas._rendered_count <= 125_001
    finally:
        timer.stop()
        canvas.cancel_deferred_render()
        canvas.deleteLater()
        qt_application.processEvents()
