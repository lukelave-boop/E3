import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtWidgets

from tests.test_desktop_job_async import _add_line_output, _dispose, _window


def test_layer_edits_rebuild_tree_only_after_native_signal_returns(tmp_path, monkeypatch) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window, errors, _notices = _window(tmp_path, monkeypatch)
    try:
        _add_line_output(window)
        layer_id = window.document.layers[0].id
        tree = window.layer_panel.layer_list
        item = tree.topLevelItem(0)
        refreshed: list[object] = []
        original = window.layer_panel.set_document

        def refresh(*args, **kwargs):
            refreshed.append(args)
            return original(*args, **kwargs)

        monkeypatch.setattr(window.layer_panel, "set_document", refresh)
        item.setCheckState(3, QtCore.Qt.CheckState.Unchecked)
        assert tree.topLevelItem(0) is item
        assert refreshed == []
        assert window.document.get_layer(layer_id).output_enabled is True

        app.processEvents()
        assert refreshed
        assert window.document.get_layer(layer_id).output_enabled is False
        assert not errors
    finally:
        _dispose(app, window)


def test_queued_edit_does_not_mutate_replacement_document_with_same_layer_id(tmp_path, monkeypatch) -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    window, errors, _notices = _window(tmp_path, monkeypatch)
    try:
        layer_id = window.document.layers[0].id
        old_passes = window.document.get_layer(layer_id).passes
        window.layer_panel.layerEdited.emit(layer_id, {"passes": old_passes + 1})
        window.document = window.document.clone()
        app.processEvents()
        assert window.document.get_layer(layer_id).passes == old_passes
        assert not errors
    finally:
        _dispose(app, window)


def test_object_layer_color_requests_use_layer_panels_existing_color_path() -> None:
    source = Path("laser_aligner/desktop/main_window.py").read_text(encoding="utf-8")

    expected = """        self.object_panel.layerColorEditRequested.connect(
            self.layer_panel.choose_color
        )
"""
    assert expected in source
