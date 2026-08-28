from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "laser_aligner" / "desktop"


def source(name: str) -> str:
    text = (DESKTOP / name).read_text(encoding="utf-8")
    ast.parse(text, filename=name)
    return text


def test_trace_panel_and_controller_are_wired():
    panels = source("panels.py")
    controller = source("controller.py")
    window = source("main_window.py")
    workspace = source("workspace.py")

    assert "class TracePanel" in panels
    assert "click the target in the corrected camera image" in panels
    assert "Create vector objects" in panels
    assert "def detect_trace_objects" in controller
    assert "context.capture_parked_trace_frame(**capture_options)" in controller
    assert 'capture_options["coordinate_frame"] = coordinate_frame' in controller
    assert "def sample_trace_color" in controller
    assert "def select_trace_cutout" in controller
    assert "prepare_cutout_frame" in controller
    assert "detect_prepared_cutouts" in controller
    assert "fit_native=False" in controller
    assert "fit_native=True" in controller
    assert 'addItem("Cutout / silhouette", "cutout")' in panels
    assert "native_fit_pending" in panels
    assert 'add_panel("trace", "Trace"' in window
    assert "AddObjectsCommand" in window
    assert "def set_trace_preview" in workspace
    assert "def begin_point_pick" in workspace
