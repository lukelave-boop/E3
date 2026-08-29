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
    assert "Create one combined vector" in panels
    assert "def detect_trace_objects" in controller
    assert "context.capture_parked_trace_frame(**capture_options)" in controller
    assert 'capture_options["coordinate_frame"] = coordinate_frame' in controller
    assert 'capture_options["timing"] = capture_timing' in controller
    assert "traceRasterPreviewReady = QtCore.Signal(int, object)" in controller
    assert "traceDetectionFailed = QtCore.Signal(int, str, bool)" in controller
    assert "raster_preview_callback=raster_preview_ready" in controller
    assert "QtCore.Qt.ConnectionType.QueuedConnection" in window
    assert 'addItem("Exposed bed", "exposed_bed")' in panels
    assert 'addItem("Normalized", "normalized")' in panels
    assert 'addItem("Mask", "mask")' in panels
    assert "def sample_trace_color" in controller
    assert "select_trace_cutout" not in controller
    assert "prepare_cutout_frame" not in controller
    assert 'addItem("Cutout / silhouette", "cutout")' not in panels
    assert 'add_panel("trace", "Trace"' in window
    assert "AddObjectsCommand" in window
    assert "def _combined_trace_object" in window
    assert "def set_trace_preview" in workspace
    assert "traceSelectionIdsChanged" in workspace
    assert "def begin_point_pick" in workspace
