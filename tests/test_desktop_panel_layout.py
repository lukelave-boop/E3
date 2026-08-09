from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.desktop.controls import PanelScrollArea
from laser_aligner.desktop.panels import CameraPanel, TracePanel, TransformPanel
from laser_aligner.desktop.theme import DARK_STYLESHEET


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _show_in_narrow_inspector(
    panel: QtWidgets.QWidget,
    application: QtWidgets.QApplication,
) -> PanelScrollArea:
    scroll = PanelScrollArea(panel)
    scroll.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 13pt; }")
    scroll.resize(360, 700)
    scroll.show()
    application.processEvents()
    return scroll


def _assert_no_hidden_horizontal_content(
    scroll: PanelScrollArea,
    panel: QtWidgets.QWidget,
) -> None:
    assert (
        scroll.horizontalScrollBarPolicy()
        == QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert scroll.horizontalScrollBar().maximum() == 0
    assert panel.width() <= scroll.viewport().width()
    for button in panel.findChildren(QtWidgets.QAbstractButton):
        if not button.isVisible() or not button.text():
            continue
        top_left = button.mapTo(scroll.viewport(), button.rect().topLeft())
        bottom_right = button.mapTo(scroll.viewport(), button.rect().bottomRight())
        assert top_left.x() >= 0, button.text()
        assert bottom_right.x() < scroll.viewport().width(), button.text()


@pytest.mark.parametrize(
    "panel_type",
    [CameraPanel, TracePanel],
    ids=["camera", "trace"],
)
def test_action_heavy_panels_fit_narrow_inspector_with_large_text(
    qt_application: QtWidgets.QApplication,
    panel_type: type[QtWidgets.QWidget],
) -> None:
    panel = panel_type()
    scroll = _show_in_narrow_inspector(panel, qt_application)

    _assert_no_hidden_horizontal_content(scroll, panel)

    scroll.close()
    scroll.deleteLater()


def test_trace_output_mode_enables_only_applicable_smoothing(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()

    assert panel.output_mode.currentData() == "rounded"
    assert panel.output_mode.currentText() == "Fitted rounded rectangles"
    assert not panel.smoothing.isEnabled()
    assert not panel.smoothing_label.isEnabled()
    assert panel.normalize_grid.isEnabled()
    assert panel.snap_grid_cells.isEnabled()
    assert "does not apply" in panel.smoothing.toolTip()

    panel.output_mode.setCurrentIndex(panel.output_mode.findData("smoothed"))
    qt_application.processEvents()
    assert panel.output_mode.currentText() == "Simplified contours"
    assert panel.smoothing.isEnabled()
    assert panel.smoothing_label.isEnabled()
    assert not panel.normalize_grid.isEnabled()
    assert not panel.snap_grid_cells.isEnabled()

    panel.output_mode.setCurrentIndex(panel.output_mode.findData("exact"))
    qt_application.processEvents()
    assert not panel.smoothing.isEnabled()
    assert not panel.smoothing_label.isEnabled()
    assert not panel.normalize_grid.isEnabled()
    assert not panel.snap_grid_cells.isEnabled()

    panel.output_mode.setCurrentIndex(panel.output_mode.findData("rounded"))
    panel.regular_grid.setChecked(False)
    qt_application.processEvents()
    assert not panel.infer_missing.isEnabled()
    assert not panel.normalize_grid.isEnabled()
    assert not panel.snap_grid_cells.isEnabled()

    panel.regular_grid.setChecked(True)
    panel.normalize_grid.setChecked(False)
    qt_application.processEvents()
    assert not panel.snap_grid_cells.isEnabled()

    legend_text = " ".join(
        label.text() for label in panel.findChildren(QtWidgets.QLabel)
    )
    assert "Green outlines show the proposed vector output" in legend_text

    panel.close()
    panel.deleteLater()


def test_trace_result_exposes_fitted_corner_radius(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    panel.set_result(
        {
            "message": "One fitted object",
            "detections": [
                {
                    "id": "trace-rounded",
                    "index": 1,
                    "source": "direct",
                    "confidence": 0.95,
                    "selected_default": True,
                    "shape": "rounded_rectangle",
                    "width_mm": 78.5,
                    "height_mm": 21.5,
                    "corner_radius_mm": 2.75,
                    "rotation_deg": 7.0,
                }
            ],
        }
    )

    item = panel.result_tree.topLevelItem(0)
    assert panel.result_tree.headerItem().text(4) == "Geometry"
    assert "R 2.75 mm" in item.text(4)
    assert "corner radius 2.75 mm" in item.toolTip(4)

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_trace_result_can_select_complete_normalized_grid(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    detections = []
    for index in range(1, 5):
        detections.append(
            {
                "id": f"grid-{index}",
                "index": index,
                "source": "inferred" if index == 4 else "direct",
                "confidence": 0.9,
                "selected_default": index < 4,
                "shape": "rounded_rectangle",
                "width_mm": 80.0,
                "height_mm": 20.0,
                "corner_radius_mm": 3.0,
                "rotation_deg": 0.0,
                "diagnostics": {
                    "grid_normalized": True,
                    "grid_row": (index - 1) // 2,
                    "grid_column": (index - 1) % 2,
                },
            }
        )
    panel.set_result(
        {
            "message": "Fitted grid",
            "grid": {"normalized": True, "columns": 2, "rows": 2},
            "detections": detections,
        }
    )

    assert panel.select_grid_button.isEnabled()
    assert panel.select_grid_button.text() == "Select complete 2 × 2 grid"
    assert len(panel.selected_ids()) == 3
    panel.select_grid_button.click()
    qt_application.processEvents()
    assert len(panel.selected_ids()) == 4

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_trace_result_labels_unselected_out_of_bounds_loose_grid_cell(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    panel.set_result(
        {
            "message": "WARNING: two cells extend outside the work area",
            "grid": {
                "normalized": True,
                "cells_snapped": False,
                "columns": 2,
                "rows": 8,
                "outside_cells": 2,
            },
            "detections": [
                {
                    "id": "outside-direct",
                    "index": 1,
                    "source": "direct",
                    "confidence": 0.99,
                    "selected_default": False,
                    "shape": "rounded_rectangle",
                    "width_mm": 80.5,
                    "height_mm": 21.5,
                    "corner_radius_mm": 3.5,
                    "rotation_deg": 0.5,
                    "diagnostics": {
                        "grid_normalized": True,
                        "grid_row": 0,
                        "grid_column": 1,
                        "within_work_area": False,
                        "work_area_overrun_mm": 0.51,
                        "observed_within_work_area": True,
                    },
                },
                {
                    "id": "outside-inferred",
                    "index": 2,
                    "source": "inferred",
                    "confidence": 0.99,
                    "selected_default": False,
                    "shape": "rounded_rectangle",
                    "width_mm": 80.5,
                    "height_mm": 21.5,
                    "corner_radius_mm": 3.5,
                    "rotation_deg": 0.5,
                    "diagnostics": {
                        "grid_normalized": True,
                        "grid_row": 1,
                        "grid_column": 1,
                        "within_work_area": False,
                        "work_area_overrun_mm": 0.42,
                    },
                },
                {
                    "id": "cropped-direct",
                    "index": 3,
                    "source": "direct",
                    "confidence": 0.99,
                    "selected_default": False,
                    "shape": "rounded_rectangle",
                    "width_mm": 80.5,
                    "height_mm": 21.5,
                    "corner_radius_mm": 3.5,
                    "rotation_deg": 0.5,
                    "diagnostics": {
                        "grid_normalized": True,
                        "grid_row": 2,
                        "grid_column": 0,
                        "within_work_area": True,
                        "touches_image_edge": True,
                        "image_edge_sides": ["right"],
                    },
                },
                {
                    "id": "inside-direct",
                    "index": 4,
                    "source": "direct",
                    "confidence": 0.99,
                    "selected_default": False,
                    "shape": "rounded_rectangle",
                    "width_mm": 80.5,
                    "height_mm": 21.5,
                    "corner_radius_mm": 3.5,
                    "rotation_deg": 0.5,
                    "diagnostics": {
                        "grid_normalized": True,
                        "grid_row": 3,
                        "grid_column": 0,
                        "within_work_area": True,
                    },
                },
            ],
        }
    )

    direct_item = panel.result_tree.topLevelItem(0)
    inferred_item = panel.result_tree.topLevelItem(1)
    cropped_item = panel.result_tree.topLevelItem(2)
    inside_item = panel.result_tree.topLevelItem(3)
    assert direct_item.checkState(0) == QtCore.Qt.CheckState.Unchecked
    assert "outside" in direct_item.text(2).lower()
    assert "observed center and rotation are retained" in direct_item.toolTip(4)
    assert "raw observed fit was inside" in direct_item.toolTip(4).lower()
    assert "shared grid sizing" in direct_item.toolTip(4).lower()
    assert "extend 0.51 mm outside" in direct_item.toolTip(4)
    assert "outside" in inferred_item.text(2).lower()
    assert "has no observed pose" in inferred_item.toolTip(4)
    assert inferred_item.foreground(2).color() == QtGui.QColor("#E06666")
    assert "cropped" in cropped_item.text(2).lower()
    assert "touches the right edge" in cropped_item.toolTip(4)
    assert cropped_item.foreground(2).color() == QtGui.QColor("#E06666")
    assert "Generation remains blocked" in panel.select_grid_button.toolTip()

    panel.select_direct_button.click()
    qt_application.processEvents()
    assert direct_item.checkState(0) == QtCore.Qt.CheckState.Unchecked
    assert inferred_item.checkState(0) == QtCore.Qt.CheckState.Unchecked
    assert cropped_item.checkState(0) == QtCore.Qt.CheckState.Unchecked
    assert inside_item.checkState(0) == QtCore.Qt.CheckState.Checked

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_non_grid_trace_names_the_guarded_output_boundary(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    panel.set_result(
        {
            "message": "WARNING: outline extends outside guarded output area",
            "camera_work_area": {
                "x_min": 0.0,
                "x_max": 100.0,
                "y_min": 0.0,
                "y_max": 100.0,
            },
            "output_work_area": {
                "x_min": 5.0,
                "x_max": 95.0,
                "y_min": 5.0,
                "y_max": 95.0,
            },
            "grid": None,
            "detections": [
                {
                    "id": "guarded-outside",
                    "index": 1,
                    "source": "direct",
                    "confidence": 0.99,
                    "selected_default": False,
                    "shape": "rounded_rectangle",
                    "width_mm": 10.0,
                    "height_mm": 10.0,
                    "corner_radius_mm": 1.0,
                    "rotation_deg": 0.0,
                    "diagnostics": {
                        "within_camera_work_area": True,
                        "within_work_area": False,
                        "work_area_overrun_mm": 1.25,
                    },
                }
            ],
        }
    )

    item = panel.result_tree.topLevelItem(0)
    assert "1.25 mm outside the guarded output area" in item.toolTip(4)

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_transform_summary_wraps_long_names_without_widening_inspector(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TransformPanel()
    summary = (
        "Extremely long customer label object name that must wrap without "
        "widening the inspector"
    )
    panel._set_summary(summary)
    scroll = _show_in_narrow_inspector(panel, qt_application)

    _assert_no_hidden_horizontal_content(scroll, panel)
    assert panel.summary.wordWrap()
    assert panel.summary.height() > panel.summary.fontMetrics().height()
    assert panel.summary.toolTip() == summary

    scroll.close()
    scroll.deleteLater()


def test_camera_test_source_does_not_widen_narrow_inspector(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = CameraPanel()
    long_source = (
        "Loaded: "
        "warehouse-label-sheet-red-rounded-rectangles-revision-2026-08-06.png"
    )
    panel.set_test_frame_source(True, long_source)
    scroll = _show_in_narrow_inspector(panel, qt_application)

    _assert_no_hidden_horizontal_content(scroll, panel)
    assert panel.image_state.toolTip() == long_source

    scroll.close()
    scroll.deleteLater()
