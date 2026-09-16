# ruff: noqa: E402, I001
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6 import QtWidgets

from laser_aligner.desktop.layer_profiles import LayerProfilesBar
from laser_aligner.materials.layer_profiles import LayerProfileStore
from laser_aligner.project import CommandStack, OperationLayer, ProjectDocument


def test_profile_bar_saves_loads_and_filters_machine_scope(tmp_path, monkeypatch):
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    store = LayerProfileStore(tmp_path / "profiles.json")
    monkeypatch.setattr("laser_aligner.desktop.layer_profiles.LayerProfileStore", lambda: store)
    window = QtWidgets.QMainWindow()
    window.document = ProjectDocument(layers=[OperationLayer(power_percent=17)])
    window.history = CommandStack()
    window._running_material_profile_ids = lambda: ("machine", "tool")
    window._commit_project_numeric_edit = lambda: None
    notices = []
    window.show_notice = notices.append
    window.show_error = lambda message: pytest.fail(message)
    bar = LayerProfilesBar(window)
    window.setCentralWidget(bar)
    assert not bar.load_button.isEnabled()
    monkeypatch.setattr(
        QtWidgets.QInputDialog, "getText", lambda *args: ("Paper", True),
    )
    bar.save_profile()
    assert bar.selector.currentData() == "Paper"
    window.document.layers[0].power_percent = 50
    bar.load_profile()
    assert window.document.layers[0].power_percent == 17
    window.history.undo()
    assert window.document.layers[0].power_percent == 50
    store.save("Other machine", ("other", "tool"), window.document.layers)
    bar.refresh()
    assert bar.selector.findData("Other machine") == -1
    window.close()
    application.processEvents()


def test_profile_load_invalidates_prepared_job_and_preserves_artwork(tmp_path, monkeypatch):
    import json
    from pathlib import Path

    from PySide6 import QtCore, QtGui

    from laser_aligner.core import CoreRuntime
    from laser_aligner.desktop.main_window import E3MainWindow
    from laser_aligner.desktop.theme import DARK_STYLESHEET
    from laser_aligner.project import SceneObject
    from tests.test_desktop_material_recipes import _dispose, _wait_until

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "local"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    config = json.loads((Path(__file__).parents[1] / "config/default.json").read_text())
    config["app"]["data_dir"] = str(tmp_path / "data")
    config["camera"]["autostart"] = False
    config["machine"]["port"] = "COM_TEST"
    config["machine"]["allow_motion"] = False
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    original_font = application.font()
    font_id = -1
    if os.name == "nt":
        font_path = Path(os.environ["WINDIR"]) / "Fonts" / "segoeui.ttf"
        font_id = QtGui.QFontDatabase.addApplicationFont(str(font_path))
    application.setFont(QtGui.QFont("Segoe UI", 13))
    window = E3MainWindow(CoreRuntime.from_config(config_path, hardware_enabled=False))
    window.setStyleSheet(DARK_STYLESHEET + '\nQWidget { font-family: "Segoe UI"; font-size: 13pt; }')
    window.setFont(QtGui.QFont("Segoe UI", 13))
    window.show()
    try:
        _wait_until(application, lambda: window.runtime.running and not window.controller.has_active_tasks)
        bar = window.layer_profiles_bar
        layer = window.document.layers[0]
        obj = SceneObject.rectangle(layer.id)
        window.document.add_object(obj)
        bar.store.save("Whole set", bar._scope(), window.document.layers)
        bar.refresh("Whole set")
        window._layer_edited(layer.id, {"power_percent": 19})
        window.last_job = object()
        window.last_job_revision = window.document.revision
        bar.load_profile()
        assert window.last_job is None
        assert window.document.get_layer(layer.id).power_percent == layer.power_percent
        assert window.document.get_object(obj.id).layer_id == layer.id
        window.history.undo()
        assert window.document.get_layer(layer.id).power_percent == 19
        window.history.redo()
        assert window.document.get_layer(layer.id).power_percent == layer.power_percent
        for width, height in ((1080, 780), (900, 680)):
            window.resize(width, height)
            application.processEvents()
            application.processEvents()
            assert window.width() == width
            assert bar.geometry().bottom() < window.layer_panel.layer_list.geometry().top()
            for button in (bar.load_button, bar.save_button, bar.delete_button):
                assert button.isVisibleTo(window)
                assert bar.rect().contains(button.geometry())
            assert bar.selector.width() >= 80
        tools_menu = next(action.menu() for action in window.menuBar().actions() if action.text() == "&Tools")
        rail = window.findChild(QtWidgets.QToolBar, "drawingToolbar")
        for key in ("rectangle", "ellipse", "line", "text", "grid_template_designer", "trace_objects", "template_alignment"):
            assert window.actions[key] in rail.actions()
            assert window.actions[key] not in tools_menu.actions()
        assert window.actions["rectangle"].shortcut() == QtGui.QKeySequence("Alt+R")
        assert window.layer_panel.layout().itemAt(0).widget() is bar
        assert window.runtime.settings.machine.allow_motion is False
        assert window.runtime.context.machine.status()["armed"] is False
    finally:
        _dispose(application, window)
        application.setFont(original_font)
        if font_id >= 0:
            QtGui.QFontDatabase.removeApplicationFont(font_id)
        application.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
