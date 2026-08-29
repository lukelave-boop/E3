from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "laser_aligner" / "desktop"


def _source(name: str) -> str:
    text = (DESKTOP / name).read_text(encoding="utf-8")
    ast.parse(text, filename=name)
    return text


def test_live_camera_overlay_has_rate_limit_and_inflight_guard():
    controller = _source("controller.py")
    panels = _source("panels.py")

    assert "_camera_refresh_in_flight" in controller
    assert "set_live_camera(" in controller
    assert "_camera_live_timer" in controller
    assert "Live corrected overlay" in panels
    assert "refreshIntervalChanged" in panels


def test_workspace_has_fit_selection_zoom_and_space_pan():
    workspace = _source("workspace.py")

    assert "def fit_selection(" in workspace
    assert "def zoom_in(" in workspace
    assert "def zoom_out(" in workspace
    assert "Key_Space" in workspace
    assert "_space_pan" in workspace


def test_palette_click_assigns_selected_objects():
    main_window = _source("main_window.py")

    assert "_palette_layer_selected" in main_window
    assert "selected objects are assigned to this layer" in main_window


def test_desktop_action_shortcuts_are_unique():
    main_window = _source("main_window.py")
    shortcuts = re.findall(
        r'action\("[^"]+",\s*"[^"]+"(?:,\s*"([^"]+)")?',
        main_window,
    )
    shortcuts = [shortcut for shortcut in shortcuts if shortcut]
    duplicates = {
        shortcut
        for shortcut in shortcuts
        if shortcuts.count(shortcut) > 1
    }
    assert duplicates == set()
