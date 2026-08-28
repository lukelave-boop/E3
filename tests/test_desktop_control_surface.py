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
    assert "SetNoConstraint" in controls
    assert "wheelScrollContainer" in controls
    assert "QWidget#inspectorPage" in theme
    assert "QTabWidget#inspectorTabs::pane" in theme
    assert "background: #1E1E1E" in theme


def test_main_window_uses_unified_sidebar_and_v7_layout_state():
    text = source("main_window.py")
    assert "self.inspector_tabs = InspectorTabs" in text
    assert '"Objects", self.object_panel' in text
    assert '"camera", "Camera", self.camera_panel' in text
    assert '"layers", "Cuts", self.layer_panel' in text
    assert '"machine", "Machine", self.machine_panel' in text
    assert '"Material Recipes"' in text
    assert "self.object_dock =" not in text
    assert "self.camera_dock =" not in text
    assert "self.job_tabs =" not in text
    assert "self.inspector_dock =" not in text
    assert "self.preview_dock =" not in text
    assert "self.gcode_preview =" not in text
    assert 'state = settings.value("mainWindow/state-v7")' in text
    assert 'self.saveState(7)' in text
    assert 'self.restoreState(state, 7)' in text
    assert 'state = settings.value("mainWindow/state-v6")' not in text
    assert 'state = settings.value("mainWindow/state-v5")' not in text


def test_main_window_has_no_bottom_job_docks_and_uses_status_progress():
    text = source("main_window.py")
    panels = source("panels.py")
    assert "self.setCorner(QtCore.Qt.Corner.BottomRightCorner, right)" in text
    assert '"layersDock"' in text
    assert '"consoleDock"' in text
    assert '"gcodeDock"' not in text
    assert '"inspectorDock"' not in text
    assert "self.console_dock.hide()" in text
    assert "self.job_progress = JobProgressWidget(self)" in text
    assert "status_bar.addPermanentWidget(self.job_progress, 1)" in text
    assert "class JobProgressWidget" in panels
    assert 'self.setObjectName("jobProgressWidget")' in panels
    assert 'self.progress.setObjectName("jobExecutionProgress")' in panels


def test_main_window_has_context_properties_and_persistent_safety_strip():
    text = source("main_window.py")
    context = source("context_bar.py")
    runtime = source("runtime_strip.py")

    assert "self.context_bar = ContextPropertyBar" in text
    assert "self.runtime_strip = RuntimeSafetyStrip" in text
    assert "self.context_bar.set_selection" in text
    assert "self.runtime_strip.connectRequested.connect" in text
    assert "self.runtime_strip.disconnectRequested.connect" in text
    assert "self.runtime_strip.pauseRequested.connect" in text
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
    runtime = source("runtime_strip.py")
    controller = source("controller.py")
    service = (ROOT / "laser_aligner" / "machine" / "service.py").read_text()
    assert "Jogging may move beyond the configured work area" in panels
    assert "self._jog_ready" in panels
    assert "self.runtime.context.machine.jog" in controller
    assert "def jog(" in service
    assert "self.pause_button.setEnabled(False)" in runtime
    assert "self.pause_button" not in panels
