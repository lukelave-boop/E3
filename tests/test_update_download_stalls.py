from __future__ import annotations

import hashlib
import io
import json
import os
import threading
import time
from contextlib import nullcontext
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from laser_aligner import updates


def asset(body=b"abcdefgh"):
    return updates.UpdateAsset(
        "windows-x86_64", "setup.exe", "https://example.com/setup.exe",
        hashlib.sha256(body).hexdigest(), len(body),
    )


def evidence(folder):
    return [json.loads(p.read_text()) for p in folder.glob("*.download.json")]


@pytest.mark.parametrize("cancel", [False, True])
def test_trickle_is_bounded_and_partial_discarded(monkeypatch, tmp_path, cancel):
    clock = [0.0]
    stop = threading.Event()

    class Trickle(io.BytesIO):
        def read1(self, size=-1):
            clock[0] += 1.0
            if cancel and self.tell() == 2:
                stop.set()
            return super().read1(1)

    monkeypatch.setattr(updates.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **kw: Trickle(b"abcdefgh"))
    with pytest.raises(updates.UpdateError, match="cancelled" if cancel else "time limit"):
        updates.download_update(
            asset(), destination_dir=tmp_path, cancelled=stop.is_set, max_duration=3.0,
        )
    assert not (tmp_path / "setup.exe").exists()
    assert not list(tmp_path.glob("*.part"))
    report, = evidence(tmp_path)
    assert report["stage"] == ("cancelled" if cancel else "failed")
    assert report["received"] == 2


def test_cancel_before_open_sends_no_request(monkeypatch, tmp_path):
    def unexpected(*a, **kw):
        pytest.fail("Cancelled attempt must not open a connection")
    monkeypatch.setattr(updates.urllib.request, "urlopen", unexpected)
    with pytest.raises(updates.UpdateError, match="cancelled"):
        updates.download_update(asset(), destination_dir=tmp_path, cancelled=lambda: True)
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("duration", [0, -1, float("inf"), float("nan")])
def test_invalid_duration_rejected_before_io(tmp_path, duration):
    with pytest.raises(ValueError):
        updates.download_update(asset(), destination_dir=tmp_path, max_duration=duration)
    assert list(tmp_path.iterdir()) == []


def test_cancelled_worker_cannot_destroy_successful_retry(monkeypatch, tmp_path):
    entered = threading.Event()
    release = threading.Event()
    cancelled = threading.Event()
    failures = []

    class Blocked(io.BytesIO):
        def read1(self, size=-1):
            entered.set()
            assert release.wait(5)
            return super().read1(size)

    calls = 0
    def open_response(*a, **kw):
        nonlocal calls
        calls += 1
        return Blocked(b"abcdefgh") if calls == 1 else io.BytesIO(b"abcdefgh")
    monkeypatch.setattr(updates.urllib.request, "urlopen", open_response)
    def work():
        try:
            updates.download_update(asset(), destination_dir=tmp_path, cancelled=cancelled.is_set)
        except updates.UpdateError as exc:
            failures.append(str(exc))
    worker = threading.Thread(target=work)
    worker.start()
    try:
        assert entered.wait(5)
        cancelled.set()
        result = updates.download_update(asset(), destination_dir=tmp_path)
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert failures == ["Update download cancelled"]
    assert result.read_bytes() == b"abcdefgh"
    assert not list(tmp_path.glob("*.part"))
    assert sorted(item["stage"] for item in evidence(tmp_path)) == ["cancelled", "verified"]


@pytest.fixture
def slow_server(monkeypatch):
    body = b"x" * 100
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            for byte in body:
                try:
                    self.wfile.write(bytes([byte]))
                    self.wfile.flush()
                    time.sleep(0.02)
                except OSError:
                    break
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    url = f"http://127.0.0.1:{server.server_port}/setup.exe"
    # Only the test redirects HTTPS to loopback; production HTTPS rules stay intact.
    monkeypatch.setattr(updates, "_request", lambda _: updates.urllib.request.Request(url))
    try:
        yield asset(body)
    finally:
        server.shutdown()
        server.server_close()
        worker.join(5)


def test_real_socket_trickle_expires_despite_regular_bytes(slow_server, tmp_path):
    events = []
    started = time.monotonic()
    with pytest.raises(updates.UpdateError, match="time limit"):
        updates.download_update(
            slow_server, destination_dir=tmp_path, timeout=0.2, max_duration=0.3,
            progress=lambda n, t: events.append(n),
        )
    assert events and events[0] < slow_server.size
    assert time.monotonic() - started < 2.0
    assert not (tmp_path / slow_server.name).exists()


@pytest.fixture
def ui(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from laser_aligner.desktop import update_ui
    app = update_ui.QtWidgets.QApplication.instance() or update_ui.QtWidgets.QApplication([])
    monkeypatch.setattr(updates, "update_cache_dir", lambda: tmp_path)
    yield update_ui, app
    for worker in threading.enumerate():
        if worker.name == "E3 update diagnostics":
            worker.join(5)
            assert not worker.is_alive()
    app.processEvents()


@pytest.mark.parametrize("stage", ["queued", "body", "saving"])
def test_ui_watchdog_recovers_and_ignores_late_results(ui, monkeypatch, tmp_path, stage):
    module, app = ui
    window = module.QtWidgets.QMainWindow()
    action = module.QtGui.QAction(window)
    pending = []
    failures = []
    confirmations = []
    window.controller = SimpleNamespace(
        run_background=lambda work, **kw: pending.append(dict(work=work, **kw)),
    )
    monkeypatch.setattr(module.QtWidgets.QMessageBox, "critical", lambda *a: failures.append(a[-1]))
    monkeypatch.setattr(module, "_download_complete", lambda *a: confirmations.append(a[-1]))
    try:
        module._begin_download(window, action, SimpleNamespace(asset=asset()))
        old = window._e3_update_progress
        if stage == "body":
            old.report(4, 8)
        elif stage == "saving":
            old.report(8, 8)
        old._last_progress -= module.DOWNLOAD_STALL_SECONDS + 1
        old._refresh()
        assert old.cancel_event.is_set()
        assert window._e3_update_progress is None
        assert action.isEnabled()
        assert len(failures) == 1 and "no progress" in failures[0]
        report = tmp_path / "setup.exe.last-failure.json"
        deadline = time.monotonic() + 5
        recorded = None
        while recorded is None and time.monotonic() < deadline:
            try:
                recorded = json.loads(report.read_text())
            except (OSError, ValueError):
                time.sleep(0.005)
        assert recorded is not None and "no progress" in recorded["error"]
        module._begin_download(window, action, SimpleNamespace(asset=asset()))
        current = window._e3_update_progress
        pending[0]["on_success"](tmp_path / "stale.exe")
        pending[0]["on_failure"]("late failure")
        assert window._e3_update_progress is current
        assert not confirmations and len(failures) == 1
        pending[1]["on_success"](tmp_path / "current.exe")
        assert confirmations == [tmp_path / "current.exe"]
    finally:
        module._close_progress(window)
        window.deleteLater()
        app.processEvents()


def test_ui_total_deadline_cannot_be_extended_by_trickle(ui, monkeypatch):
    module, _ = ui
    window = module.QtWidgets.QMainWindow()
    dialog = module._DownloadProgress(window, "setup.exe", 100)
    errors = []
    dialog.stalled.connect(errors.append)
    try:
        dialog._started -= module.DOWNLOAD_MAX_SECONDS + 1
        dialog.report(10, 100)
        dialog._refresh()
        assert dialog.cancel_event.is_set()
        assert errors and "time limit" in errors[0]
    finally:
        dialog.close()
        window.deleteLater()


def test_late_completion_checks_deadline_even_before_timer(ui, monkeypatch, tmp_path):
    module, app = ui
    window = module.QtWidgets.QMainWindow()
    action = module.QtGui.QAction(window)
    pending = {}
    errors = []
    window.controller = SimpleNamespace(run_background=lambda work, **kw: pending.update(**kw))
    monkeypatch.setattr(module.QtWidgets.QMessageBox, "critical", lambda *a: errors.append(a[-1]))
    monkeypatch.setattr(module, "_download_complete", lambda *a: pytest.fail("Expired install"))
    try:
        module._begin_download(window, action, SimpleNamespace(asset=asset()))
        dialog = window._e3_update_progress
        dialog._started -= module.DOWNLOAD_MAX_SECONDS + 1
        dialog.report(8, 8)
        pending["on_success"](tmp_path / "setup.exe")
        assert errors and "time limit" in errors[0]
        assert window._e3_update_progress is None and action.isEnabled()
    finally:
        module._close_progress(window)
        window.deleteLater()
        app.processEvents()


def test_blocked_diagnostic_write_does_not_block_watchdog(ui, monkeypatch):
    module, app = ui
    window = module.QtWidgets.QMainWindow()
    action = module.QtGui.QAction(window)
    entered = threading.Event()
    release = threading.Event()
    done = threading.Event()
    errors = []
    def blocked_record(*args):
        entered.set()
        try:
            release.wait(5)
        finally:
            done.set()
    window.controller = SimpleNamespace(run_background=lambda *a, **kw: None)
    monkeypatch.setattr(module, "record_download_problem", blocked_record)
    monkeypatch.setattr(module.QtWidgets.QMessageBox, "critical", lambda *a: errors.append(a[-1]))
    try:
        module._begin_download(window, action, SimpleNamespace(asset=asset()))
        dialog = window._e3_update_progress
        dialog._last_progress -= module.DOWNLOAD_STALL_SECONDS + 1
        dialog._refresh()
        assert entered.wait(2)
        assert not done.is_set()
        assert errors and action.isEnabled() and window._e3_update_progress is None
    finally:
        release.set()
        assert done.wait(5)
        module._close_progress(window)
        window.deleteLater()
        app.processEvents()


def test_cancel_button_releases_ui_and_suppresses_late_install(ui, monkeypatch, tmp_path):
    module, app = ui
    window = module.QtWidgets.QMainWindow()
    action = module.QtGui.QAction(window)
    pending = {}
    window.controller = SimpleNamespace(
        run_background=lambda work, **kw: pending.update(work=work, **kw),
    )
    monkeypatch.setattr(module, "_download_complete", lambda *a: pytest.fail("Cancelled install"))
    try:
        module._begin_download(window, action, SimpleNamespace(asset=asset()))
        dialog = window._e3_update_progress
        button = next(b for b in dialog.findChildren(module.QtWidgets.QPushButton)
                      if b.text() == "Cancel download")
        button.click()
        assert dialog.cancel_event.is_set()
        assert window._e3_update_progress is None and action.isEnabled()
        pending["on_success"](tmp_path / "setup.exe")
        pending["on_failure"]("late error")
    finally:
        module._close_progress(window)
        window.deleteLater()
        app.processEvents()


def test_real_worker_stuck_in_headers_cannot_hold_dialog_open(ui, monkeypatch, tmp_path):
    module, app = ui
    from laser_aligner.desktop.controller import DesktopController
    entered = threading.Event()
    release = threading.Event()
    machine = SimpleNamespace(operation_generation=lambda: 0, operation_scope=lambda _: nullcontext())
    window = module.QtWidgets.QMainWindow()
    window.controller = DesktopController(SimpleNamespace(context=SimpleNamespace(machine=machine)), window)
    action = module.QtGui.QAction(window)
    errors = []
    def blocked_open(*a, **kw):
        entered.set()
        assert release.wait(5)
        return io.BytesIO(b"abcdefgh")
    monkeypatch.setattr(updates.urllib.request, "urlopen", blocked_open)
    monkeypatch.setattr(module, "DOWNLOAD_STALL_SECONDS", 0.1)
    monkeypatch.setattr(module.QtWidgets.QMessageBox, "critical", lambda *a: errors.append(a[-1]))
    monkeypatch.setattr(module, "_download_complete", lambda *a: pytest.fail("Late install"))
    try:
        module._begin_download(window, action, SimpleNamespace(asset=asset()))
        assert entered.wait(2)
        deadline = time.monotonic() + 2
        while not errors and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert len(errors) == 1
        assert window.controller.has_active_tasks  # Network worker is STILL blocked.
        assert window._e3_update_progress is None and action.isEnabled()
        release.set()
        assert window.controller.thread_pool.waitForDone(2000)
        app.processEvents()
        assert not window.controller.has_active_tasks
        assert not (tmp_path / "setup.exe").exists()
        assert evidence(tmp_path)[0]["stage"] == "cancelled"
    finally:
        release.set()
        window.controller.thread_pool.waitForDone(5000)
        app.processEvents()
        module._close_progress(window)
        window.deleteLater()


@pytest.mark.parametrize("failure", [False, True])
def test_actual_controller_worker_delivers_download_result(ui, monkeypatch, tmp_path, failure):
    module, app = ui
    from laser_aligner.desktop.controller import DesktopController
    machine = SimpleNamespace(operation_generation=lambda: 0, operation_scope=lambda _: nullcontext())
    runtime = SimpleNamespace(context=SimpleNamespace(machine=machine))
    window = module.QtWidgets.QMainWindow()
    window.controller = DesktopController(runtime, window)
    action = module.QtGui.QAction(window)
    outcomes = []
    def open_response(*a, **kw):
        if failure:
            raise TimeoutError("simulated stalled socket")
        return io.BytesIO(b"abcdefgh")
    monkeypatch.setattr(updates.urllib.request, "urlopen", open_response)
    monkeypatch.setattr(module.QtWidgets.QMessageBox, "critical", lambda *a: outcomes.append("failed"))
    monkeypatch.setattr(module.QtWidgets.QMessageBox, "question", lambda *a: (
        outcomes.append("verified") or module.QtWidgets.QMessageBox.StandardButton.No
    ))
    try:
        module._begin_download(window, action, SimpleNamespace(asset=asset()))
        deadline = time.monotonic() + 5
        while (not outcomes or window.controller.has_active_tasks) and time.monotonic() < deadline:
            app.processEvents()
            time.sleep(0.005)
        assert outcomes == ["failed" if failure else "verified"]
        assert window._e3_update_progress is None and action.isEnabled()
        assert not window.controller.has_active_tasks
    finally:
        window.controller.thread_pool.waitForDone(5000)
        app.processEvents()
        module._close_progress(window)
        window.deleteLater()
