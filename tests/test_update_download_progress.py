from __future__ import annotations

import hashlib
import io
import os
from threading import Thread
from types import SimpleNamespace

import pytest

from laser_aligner import updates


@pytest.mark.parametrize("body", [b"abcdefgh", b"abc", b"abcdefghi", b"xxxxxxxx"])
def test_download_progress_preserves_verification(monkeypatch, tmp_path, body):
    expected = b"abcdefgh"
    asset = updates.UpdateAsset(
        "windows-x86_64", "setup.exe", "https://example.com/setup.exe",
        hashlib.sha256(expected).hexdigest(), len(expected),
    )
    monkeypatch.setattr(updates, "_DOWNLOAD_CHUNK_BYTES", 3)
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(body))
    events = []
    if body == expected:
        path = updates.download_update(
            asset, destination_dir=tmp_path, progress=lambda *args: events.append(args),
        )
        assert path.read_bytes() == expected
        assert events == [(3, 8), (6, 8), (8, 8)]
    else:
        with pytest.raises(updates.UpdateError):
            updates.download_update(
                asset, destination_dir=tmp_path,
                progress=lambda *args: events.append(args),
            )
        assert not (tmp_path / asset.name).exists()
    assert all(received <= total == 8 for received, total in events)
    assert not (tmp_path / ".setup.exe.part").exists()


def test_download_reports_partial_reads_before_network_failure(monkeypatch, tmp_path):
    class SlowResponse(io.BytesIO):
        def read(self, size=-1):
            pytest.fail("The downloader must not wait to fill a chunk")

        def read1(self, size=-1):
            if self.tell() == 2:
                raise OSError("Connection lost")
            return super().read1(1)

    asset = updates.UpdateAsset(
        "windows-x86_64", "setup.exe", "https://example.com/setup.exe", "a" * 64, 5,
    )
    monkeypatch.setattr(updates.urllib.request, "urlopen", lambda *a, **k: SlowResponse(b"abc"))
    events = []
    with pytest.raises(updates.UpdateError, match="Connection lost"):
        updates.download_update(
            asset, destination_dir=tmp_path, progress=lambda *args: events.append(args),
        )
    assert events == [(1, 5), (2, 5)]
    assert list(tmp_path.iterdir()) == []


@pytest.fixture
def ui():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from laser_aligner.desktop import update_ui

    app = update_ui.QtWidgets.QApplication.instance() or update_ui.QtWidgets.QApplication([])
    yield update_ui
    app.processEvents()


def test_download_dialog_size_speed_stall_and_verification(ui, monkeypatch):
    clock = [10.0]
    monkeypatch.setattr(ui.time, "monotonic", lambda: clock[0])
    window = ui.QtWidgets.QMainWindow()
    dialog = ui._DownloadProgress(window, "setup.exe", 5_000_000_000)
    try:
        assert dialog.minimum() == 0 and dialog.maximum() == 1000
        assert "0.0 B / 5.0 GB" in dialog.labelText()
        worker = Thread(target=dialog.report, args=(1_000_000_000, 5_000_000_000))
        worker.start()
        worker.join()
        assert dialog.value() == 0  # Worker does not touch Qt.
        clock[0] = 11.0
        dialog._refresh()
        assert dialog.value() == 200
        assert "20%" in dialog.labelText()
        assert "1.0 GB/s" in dialog.labelText()
        clock[0] = 15.0
        dialog._refresh()
        assert "0.0 B/s" in dialog.labelText()
        dialog.report(5_000_000_000, 5_000_000_000)
        dialog._refresh()
        assert dialog.value() == 1000
        assert "Verifying update" in dialog.labelText()
        assert not dialog.autoClose()
        dialog.close()
        assert not dialog._timer.isActive()
    finally:
        window.deleteLater()


@pytest.mark.parametrize("failure", [False, True])
def test_begin_download_wires_progress_and_closes_on_result(ui, monkeypatch, tmp_path, failure):
    window = ui.QtWidgets.QMainWindow()
    action = ui.QtGui.QAction(window)
    pending = {}
    window.controller = SimpleNamespace(
        run_background=lambda work, **kwargs: pending.update(work=work, **kwargs)
    )
    asset = SimpleNamespace(name="setup.exe", size=1000)
    package = tmp_path / "setup.exe"
    def download(actual_asset, *, progress):
        assert actual_asset is asset
        progress(500, 1000)
        return package
    monkeypatch.setattr(ui, "download_update", download)
    monkeypatch.setattr(ui.QtWidgets.QMessageBox, "critical", lambda *args: None)
    monkeypatch.setattr(
        ui.QtWidgets.QMessageBox, "question",
        lambda *args: ui.QtWidgets.QMessageBox.StandardButton.No,
    )
    try:
        ui._begin_download(window, action, SimpleNamespace(asset=asset))
        dialog = window._e3_update_progress
        assert not action.isEnabled()
        assert pending["work"]() == package
        dialog._refresh()
        assert dialog.value() == 500
        if failure:
            pending["on_failure"]("Network interrupted")
        else:
            pending["on_success"](package)
        assert window._e3_update_progress is None
        assert action.isEnabled()
        assert not dialog._timer.isActive()
    finally:
        window.deleteLater()
