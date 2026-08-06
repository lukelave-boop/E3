from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "laser_aligner" / "desktop"


def source(name: str) -> str:
    text = (DESKTOP / name).read_text(encoding="utf-8")
    ast.parse(text, filename=name)
    return text


def test_workspace_scene_has_scrollable_pan_margin():
    text = source("workspace.py")
    assert "pan_margin = max(" in text
    assert "self.setFocus(QtCore.Qt.FocusReason.MouseFocusReason)" in text


def test_live_camera_refresh_is_silent():
    text = source("controller.py")
    assert "show_busy: bool = True" in text
    assert 'label="Corrected bed-image refresh",' in text
    assert "show_busy=False" in text


def test_toolbars_are_compact():
    text = source("main_window.py")
    assert 'setObjectName("arrangeToolbar")' in text
    assert 'setObjectName("alignToolbar")' not in text
    assert "drawing_labels = {" in text
    assert "job_labels = {" in text
