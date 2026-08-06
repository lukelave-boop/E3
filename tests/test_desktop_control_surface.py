from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "laser_aligner" / "desktop"


def source(name: str) -> str:
    text = (DESKTOP / name).read_text(encoding="utf-8")
    ast.parse(text, filename=name)
    return text


def test_wheel_guard_blocks_value_widgets_and_tab_wheel_switching():
    text = source("controls.py")
    assert "QAbstractSpinBox" in text
    assert "QComboBox" in text
    assert "QSlider" in text
    assert "QDial" in text
    assert "QTabBar" in text
    assert "scroll_area(page, event)" in text


def test_inspector_pages_are_opaque_and_scrollable():
    controls = source("controls.py")
    theme = source("theme.py")
    assert "class PanelScrollArea" in controls
    assert "SetMinAndMaxSize" in controls
    assert "wheelScrollContainer" in controls
    assert "QWidget#inspectorPage" in theme
    assert "QTabWidget#inspectorTabs::pane" in theme
    assert "background: #10161C" in theme


def test_main_window_uses_one_inspector_instead_of_tabified_docks():
    text = source("main_window.py")
    assert "self.inspector_tabs = InspectorTabs" in text
    assert 'self.inspector_dock = self._dock(' in text
    assert '"Objects", self.object_panel' in text
    assert '"Camera", self.camera_panel' in text
    assert "self.object_dock =" not in text
    assert "self.camera_dock =" not in text
    assert "mainWindow/state-v3" in text


def test_camera_focus_controls_are_present_and_persistent():
    panels = source("panels.py")
    controller = source("controller.py")
    main_window = source("main_window.py")
    assert "Continuous autofocus" in panels
    assert "Save as locked startup focus" in panels
    assert "Measure sharpness" in panels
    assert "def save_camera_focus(" in controller
    assert "def _persist_camera_focus(" in controller
    assert "cv2.Laplacian" in controller
    assert "cameraFocusChanged.connect" in main_window


def test_unimplemented_machine_controls_are_visibly_disabled():
    panels = source("panels.py")
    assert "Guarded jogging is not enabled" in panels
    assert "jog_group.setEnabled(False)" in panels
    assert "self.pause_button.setEnabled(False)" in panels
