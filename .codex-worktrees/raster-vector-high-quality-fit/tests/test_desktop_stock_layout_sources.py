from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DESKTOP = ROOT / "laser_aligner" / "desktop"
PROJECT = ROOT / "laser_aligner" / "project"


def source(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    ast.parse(text, filename=str(path))
    return text


def test_trace_panel_exposes_stock_boundary_purpose() -> None:
    panels = source(DESKTOP / "panels.py")
    window = source(DESKTOP / "main_window.py")

    assert 'self.trace_purpose.addItem("Stock boundary (layout only)", "stock")' in panels
    assert '"purpose": str(self.trace_purpose.currentData())' in panels
    assert 'purpose = str(payload.get("purpose", "cut"))' in window
    assert "mark_stock_boundary(objects[0])" in window


def test_contextual_stock_toolbar_has_primary_layout_commands() -> None:
    toolbar = source(DESKTOP / "stock_layout_bar.py")
    window = source(DESKTOP / "main_window.py")

    assert "class StockLayoutToolBar" in toolbar
    assert "centerHorizontalRequested" in toolbar
    assert "centerVerticalRequested" in toolbar
    assert "snapRotationRequested" in toolbar
    assert "fitRequested" in toolbar
    assert 'self.stock_layout_toolbar = StockLayoutToolBar(self)' in window
    assert "center_selection_on_stock" in window
    assert "snap_selection_rotation_to_stock" in window
    assert "fit_selection_to_stock" in window


def test_vector_text_dialog_supports_stencil_bridges() -> None:
    dialog = source(DESKTOP / "text_dialog.py")
    geometry = source(DESKTOP / "text_geometry.py")
    window = source(DESKTOP / "main_window.py")

    assert 'self.mode_combo.addItem("Stencil-safe cut", "stencil")' in dialog
    assert "automatic_bridge_width" in geometry
    assert "path.subtracted(bridges)" in geometry
    assert '"text_bridge_count": bridge_count' in geometry
    assert "create_vector_text_object" in window


def test_stock_boundaries_are_filtered_from_every_output_entrypoint() -> None:
    model = source(PROJECT / "model.py")
    toolpath = source(PROJECT / "toolpath.py")

    assert "and item.is_output_geometry" in model
    assert toolpath.count("item.is_output_geometry") >= 4
