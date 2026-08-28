from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from types import SimpleNamespace

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop menu tests")

from PySide6 import QtGui, QtWidgets

from laser_aligner.desktop.main_window import E3MainWindow


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class _MenuHarness(QtWidgets.QMainWindow):
    _create_actions = E3MainWindow._create_actions
    _create_menus = E3MainWindow._create_menus

    def __init__(self) -> None:
        super().__init__()

        def no_op(*_args: object, **_kwargs: object) -> None:
            return None

        self.history = SimpleNamespace(undo=no_op, redo=no_op)
        self.workspace = SimpleNamespace(
            select_objects=no_op,
            fit_work_area=no_op,
            fit_selection=no_op,
            zoom_in=no_op,
            zoom_out=no_op,
            set_snap_enabled=no_op,
        )
        self.controller = SimpleNamespace(
            retry_camera_image=no_op,
            emergency_stop=no_op,
        )
        for callback_name in (
            "new_project",
            "open_project",
            "save_project",
            "save_current_as_template",
            "import_svg",
            "import_gcode",
            "import_lightburn",
            "import_image",
            "delete_selection",
            "duplicate_selection",
            "group_selection",
            "ungroup_selection",
            "align_selection",
            "distribute_selection",
            "reorder_selection",
            "_activate_selection_tool",
            "add_rectangle",
            "add_ellipse",
            "add_line",
            "add_text",
            "open_grid_template_designer",
            "open_trace_panel",
            "open_template_panel",
            "open_machine_manager",
            "open_machine_setup",
            "generate_toolpath",
            "show_job_preview",
            "export_gcode",
            "_toggle_maximized",
            "_reset_window_size",
            "_reset_workspace_layout",
            "show_about",
        ):
            setattr(self, callback_name, no_op)
        self._create_actions()
        self._create_menus()


def _top_level_menu(window: QtWidgets.QMainWindow, label: str) -> QtWidgets.QMenu:
    matches = [
        action.menu()
        for action in window.menuBar().actions()
        if action.text().replace("&", "") == label
    ]
    assert len(matches) == 1
    assert matches[0] is not None
    return matches[0]


def test_file_menu_contains_one_import_submenu_with_short_child_labels(
    qt_application: QtWidgets.QApplication,
) -> None:
    window = _MenuHarness()
    window.show()
    qt_application.processEvents()

    file_menu = _top_level_menu(window, "File")
    import_entries = [
        action
        for action in file_menu.actions()
        if action.text().replace("&", "") == "Import"
    ]
    assert len(import_entries) == 1
    assert import_entries[0].menu() is window.import_menu

    import_keys = (
        "import_svg",
        "import_gcode",
        "import_lightburn",
        "import_image",
    )
    import_actions = [window.actions[key] for key in import_keys]
    assert window.import_menu.actions() == import_actions
    assert [action.text() for action in import_actions] == [
        "SVG…",
        "G-code…",
        "LightBurn project…",
        "Raster image…",
    ]
    assert [action.toolTip() for action in import_actions] == [
        "Import SVG…",
        "Import G-code…",
        "Import LightBurn project…",
        "Import raster image…",
    ]
    assert all("import" not in action.text().casefold() for action in import_actions)
    assert all(action not in file_menu.actions() for action in import_actions)
    assert not any(
        action.text() in {
            "Import SVG…",
            "Import G-code…",
            "Import LightBurn project…",
            "Import raster image…",
        }
        for action in file_menu.actions()
    )
    assert window.actions["import_svg"].shortcut() == QtGui.QKeySequence("Ctrl+I")
    assert window.actions["import_image"].shortcut() == QtGui.QKeySequence(
        "Ctrl+Shift+I"
    )

    window.close()
    window.deleteLater()
    qt_application.processEvents()
