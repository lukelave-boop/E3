from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop menu tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.desktop.main_window import E3MainWindow


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


_MENU_ACTION_KEYS = (
    "new",
    "open",
    "save",
    "save_as",
    "save_template",
    "import_svg",
    "import_gcode",
    "import_lightburn",
    "import_image",
    "quit",
    "undo",
    "redo",
    "duplicate",
    "delete",
    "select_all",
    "group",
    "ungroup",
    "rectangle",
    "ellipse",
    "line",
    "text",
    "grid_template_designer",
    "trace_objects",
    "template_alignment",
    "refresh_camera",
    "machine_manager",
    "machine_setup",
    "align_left",
    "align_center_x",
    "align_right",
    "align_bottom",
    "align_center_y",
    "align_top",
    "distribute_h",
    "distribute_v",
    "bring_front",
    "raise",
    "lower",
    "send_back",
    "generate",
    "optimize_paths",
    "preview_job",
    "export_gcode",
    "stop",
    "minimize_window",
    "maximize_window",
    "reset_window_size",
    "reset_workspace_layout",
    "fit",
    "fit_selection",
    "zoom_in",
    "zoom_out",
    "snap",
    "setup_guide",
    "about",
)

_IMPORT_ACTIONS = (
    ("import_svg", "Import SVG…", "Ctrl+I"),
    ("import_gcode", "Import G-code…", ""),
    ("import_lightburn", "Import LightBurn project…", ""),
    ("import_image", "Import raster image…", "Ctrl+Shift+I"),
)


def test_file_menu_groups_existing_import_actions_in_one_submenu(
    qt_application: QtWidgets.QApplication,
) -> None:
    window = QtWidgets.QMainWindow()
    triggered: list[str] = []
    window.actions = {}
    for key in _MENU_ACTION_KEYS:
        action = QtGui.QAction(key.replace("_", " "), window)
        action.triggered.connect(
            lambda checked=False, action_key=key: triggered.append(action_key)
        )
        window.actions[key] = action

    for key, text, shortcut in _IMPORT_ACTIONS:
        action = window.actions[key]
        action.setText(text)
        action.setShortcut(QtGui.QKeySequence(shortcut))
        action.setStatusTip(f"Status for {key}")
        pixmap = QtGui.QPixmap(2, 2)
        pixmap.fill(QtGui.QColor("#38AFC4"))
        action.setIcon(QtGui.QIcon(pixmap))
    window.actions["import_gcode"].setEnabled(False)

    E3MainWindow._create_menus(window)

    file_menu = window.menuBar().actions()[0].menu()
    assert file_menu is not None
    import_entries = [
        action
        for action in file_menu.actions()
        if action.text() == "Import" and action.menu() is not None
    ]
    assert len(import_entries) == 1
    import_menu = import_entries[0].menu()
    assert import_menu is window.import_menu
    assert [action.text() for action in import_menu.actions()] == [
        text for _key, text, _shortcut in _IMPORT_ACTIONS
    ]
    assert import_menu.actions() == [
        window.actions[key] for key, _text, _shortcut in _IMPORT_ACTIONS
    ]
    assert not window.actions["import_gcode"].isEnabled()

    top_level_actions = file_menu.actions()
    assert all(
        window.actions[key] not in top_level_actions
        for key, _text, _shortcut in _IMPORT_ACTIONS
    )
    assert [action.text() for action in top_level_actions] == [
        "new",
        "open",
        "save",
        "save as",
        "save template",
        "Import",
        "",
        "quit",
    ]

    window.actions["import_gcode"].setEnabled(True)
    for key, _text, shortcut in _IMPORT_ACTIONS:
        action = window.actions[key]
        assert action.shortcut() == QtGui.QKeySequence(shortcut)
        assert action.statusTip() == f"Status for {key}"
        assert not action.icon().isNull()
        action.trigger()
    assert triggered == [key for key, _text, _shortcut in _IMPORT_ACTIONS]

    triggered.clear()
    window.show()
    window.activateWindow()
    window.setFocus()
    qt_application.processEvents()
    QtTest.QTest.keyClick(
        window,
        QtCore.Qt.Key.Key_I,
        QtCore.Qt.KeyboardModifier.ControlModifier,
    )
    QtTest.QTest.keyClick(
        window,
        QtCore.Qt.Key.Key_I,
        QtCore.Qt.KeyboardModifier.ControlModifier
        | QtCore.Qt.KeyboardModifier.ShiftModifier,
    )
    qt_application.processEvents()
    assert triggered == ["import_svg", "import_image"]

    window.close()
    window.deleteLater()
