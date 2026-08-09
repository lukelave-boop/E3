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
    assert "background: #1E1E1E" in theme


def test_main_window_uses_lightburn_style_design_and_job_inspector_stacks():
    text = source("main_window.py")
    assert "self.inspector_tabs = InspectorTabs" in text
    assert "self.job_tabs = InspectorTabs" in text
    assert 'self.inspector_dock = self._dock(' in text
    assert '"Objects", self.object_panel' in text
    assert '"camera", "Camera", self.camera_panel' in text
    assert '"layers", "Cuts", self.layer_panel' in text
    assert '"Laser", self.job_panel' in text
    assert "self.object_dock =" not in text
    assert "self.camera_dock =" not in text
    assert 'state = settings.value("mainWindow/state-v6")' in text
    assert 'self.saveState(6)' in text
    assert 'self.restoreState(state, 6)' in text
    assert 'state = settings.value("mainWindow/state-v5")' not in text


def test_main_window_default_docks_match_full_height_inspector_layout():
    text = source("main_window.py")
    assert "self.setCorner(QtCore.Qt.Corner.BottomRightCorner, right)" in text
    assert '"gcodeDock"' in text
    assert '"inspectorDock"' in text
    assert "self.preview_dock,\n            self.inspector_dock," in text
    assert "QtCore.Qt.Orientation.Horizontal" in text
    assert (
        "self.layer_dock,\n            self.inspector_dock,\n"
        "            QtCore.Qt.Orientation.Vertical"
    ) not in text
    assert "self.preview_dock.hide()" not in text


def test_main_window_has_context_properties_and_persistent_safety_strip():
    text = source("main_window.py")
    context = source("context_bar.py")
    runtime = source("runtime_strip.py")

    assert "self.context_bar = ContextPropertyBar" in text
    assert "self.runtime_strip = RuntimeSafetyStrip" in text
    assert "self.context_bar.set_selection" in text
    assert "self.runtime_strip.stopRequested.connect" in text
    assert "self.safety_toolbar.toggleViewAction().setEnabled(False)" in text
    assert "self.safety_toolbar.show()" in text
    assert "class ContextPropertyBar" in context
    assert "class RuntimeSafetyStrip" in runtime


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


def test_machine_controls_expose_guarded_jogging_and_keep_pause_disabled():
    panels = source("panels.py")
    controller = source("controller.py")
    service = (ROOT / "laser_aligner" / "machine" / "service.py").read_text()
    assert "Jogging may move beyond the configured work area" in panels
    assert "self._jog_ready" in panels
    assert "self.runtime.context.machine.jog" in controller
    assert "def jog(" in service
    assert "self.pause_button.setEnabled(False)" in panels
