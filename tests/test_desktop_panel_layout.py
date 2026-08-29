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


@pytest.fixture(autouse=True)
def isolated_trace_preferences() -> Iterator[None]:
    """Prevent persistent Trace settings from leaking between tests.

    Preserve any real developer/user settings that existed before the test.
    """
    settings = QtCore.QSettings("E3", "PositioningSystem")
    settings.beginGroup("trace")
    previous = {key: settings.value(key) for key in settings.allKeys()}
    settings.remove("")
    settings.endGroup()
    settings.sync()

    try:
        yield
    finally:
        settings.beginGroup("trace")
        settings.remove("")
        for key, value in previous.items():
            settings.setValue(key, value)
        settings.endGroup()
        settings.sync()


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
    assert panel.output_mode.currentText() == "Best-fit analytic shapes"
    assert panel.border_offset_mode.currentData() == "uniform"
    assert panel.border_offset_mode.isEnabled()
    assert panel.border_offset.singleStep() == pytest.approx(0.1)
    assert all(
        field.singleStep() == pytest.approx(0.1)
        for field in panel.edge_offset_fields.values()
    )
    assert panel.border_offset.isVisibleTo(panel)
    assert not panel.edge_offsets.isVisibleTo(panel)
    assert not panel.smoothing.isEnabled()
    assert not panel.smoothing_label.isEnabled()
    assert panel.repair_grid_edges.isEnabled()
    assert panel.repair_grid_edges.isChecked()
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
    assert not panel.repair_grid_edges.isEnabled()
    assert not panel.border_offset_mode.isEnabled()

    panel.output_mode.setCurrentIndex(panel.output_mode.findData("rounded"))
    panel.border_offset_mode.setCurrentIndex(
        panel.border_offset_mode.findData("custom")
    )
    panel.edge_offset_fields["top"].setValue(-1.25)
    qt_application.processEvents()
    assert not panel.border_offset.isVisibleTo(panel)
    assert panel.edge_offsets.isVisibleTo(panel)
    assert panel.options()["border_offset_mode"] == "custom"
    assert panel.options()["border_offset_top_mm"] == pytest.approx(-1.25)

    panel.output_mode.setCurrentIndex(panel.output_mode.findData("exact"))
    qt_application.processEvents()
    assert panel.border_offset_mode.currentData() == "uniform"
    assert not panel.border_offset_mode.isEnabled()

    assert not panel.smoothing.isEnabled()
    assert not panel.smoothing_label.isEnabled()
    assert not panel.normalize_grid.isEnabled()
    assert not panel.snap_grid_cells.isEnabled()
    assert not panel.repair_grid_edges.isEnabled()

    panel.output_mode.setCurrentIndex(panel.output_mode.findData("rounded"))
    panel.regular_grid.setChecked(False)
    qt_application.processEvents()
    assert not panel.infer_missing.isEnabled()
    assert not panel.repair_grid_edges.isEnabled()
    assert not panel.normalize_grid.isEnabled()
    assert not panel.snap_grid_cells.isEnabled()

    panel.regular_grid.setChecked(True)
    panel.output_mode.setCurrentIndex(panel.output_mode.findData("rounded"))
    panel.normalize_grid.setChecked(False)
    qt_application.processEvents()
    assert panel.repair_grid_edges.isEnabled()
    assert not panel.snap_grid_cells.isEnabled()
    assert panel.options()["repair_grid_edges"] is True

    legend_text = " ".join(
        label.text() for label in panel.findChildren(QtWidgets.QLabel)
    )
    assert "Green outlines are included" in legend_text

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


def test_trace_raster_selector_is_diagnostic_and_does_not_stale_result(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    emitted: list[str] = []
    panel.rasterPreviewModeChanged.connect(emitted.append)

    assert panel.raster_preview_mode() == "camera"
    assert not panel.raster_preview_combo.isEnabled()
    panel.begin_detection()
    assert "preparing" in panel.status_label.text().lower()

    panel.set_raster_preview_available(
        "raster_dark",
        selected_strategy=False,
    )
    assert panel.raster_preview_combo.isEnabled()
    assert panel.raster_preview_mode() == "mask"
    assert "Auto evaluation is still running" in panel.status_label.text()

    panel.set_raster_preview_available(
        "raster_dark",
        selected_strategy=True,
        native_fitting_completed=False,
    )
    assert "native fitting is still running" in panel.status_label.text()

    panel.set_raster_preview_available(
        "raster_dark",
        selected_strategy=True,
        native_fitting_completed=True,
    )
    assert "native fitting completed" in panel.status_label.text()

    panel.set_result(
        {
            "message": "One fitted object",
            "detections": [
                {
                    "id": "trace-preview",
                    "index": 1,
                    "source": "direct",
                    "confidence": 0.95,
                    "selected_default": True,
                    "shape": "rounded_rectangle",
                    "width_mm": 20.0,
                    "height_mm": 10.0,
                    "corner_radius_mm": 1.0,
                    "rotation_deg": 0.0,
                }
            ],
        }
    )
    assert panel.create_button.isEnabled()

    panel.raster_preview_combo.setCurrentIndex(
        panel.raster_preview_combo.findData("normalized")
    )
    qt_application.processEvents()
    assert emitted == ["normalized"]
    assert panel.create_button.isEnabled()
    assert panel.status_label.text() == "One fitted object"

    panel.set_detection_failed("native fit did not converge", retain_preview=True)
    assert not panel.create_button.isEnabled()
    assert not panel.create_combined_button.isEnabled()
    assert panel.raster_preview_combo.isEnabled()
    assert panel.raster_preview_mode() == "normalized"
    assert "fitting failed" in panel.status_label.text().lower()
    assert "diagnostic views are retained" in panel.status_label.text()

    panel.clear_result()
    assert panel.raster_preview_mode() == "camera"
    assert not panel.raster_preview_combo.isEnabled()

    panel.begin_detection()
    panel.set_detection_failed("normalization failed", retain_preview=False)
    assert not panel.raster_preview_combo.isEnabled()
    assert panel.status_label.text() == "Trace detection failed: normalization failed"
    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_trace_result_identifies_washer_dimensions(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    panel.set_result(
        {
            "message": "One washer",
            "detections": [
                {
                    "id": "trace-washer",
                    "index": 1,
                    "source": "direct",
                    "confidence": 0.97,
                    "selected_default": True,
                    "shape": "washer",
                    "width_mm": 20.0,
                    "height_mm": 20.0,
                    "rotation_deg": 0.0,
                    "diagnostics": {"hole_ratio": 0.4, "center_offset_mm": 0.02},
                }
            ],
        }
    )

    item = panel.result_tree.topLevelItem(0)
    assert "Washer · OD 20.00 mm · ID 8.00 mm" == item.text(4)
    assert "Best-fit washer" in item.toolTip(4)

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_trace_result_labels_damaged_and_already_open_cells(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    panel.set_result(
        {
            "message": "Review flags",
            "detections": [
                {
                    "id": "damaged",
                    "index": 1,
                    "source": "direct",
                    "confidence": 0.9,
                    "selected_default": False,
                    "shape": "rounded_rectangle",
                    "width_mm": 20,
                    "height_mm": 10,
                    "corner_radius_mm": 1,
                    "rotation_deg": 0,
                    "diagnostics": {
                        "damage_suspected": True,
                        "damage_reasons": ["rotation differs by 3.00°"],
                    },
                },
                {
                    "id": "open",
                    "index": 2,
                    "source": "direct",
                    "confidence": 0.9,
                    "selected_default": False,
                    "shape": "rounded_rectangle",
                    "width_mm": 20,
                    "height_mm": 10,
                    "corner_radius_mm": 1,
                    "rotation_deg": 0,
                    "diagnostics": {"likely_open_cell": True},
                },
            ],
        }
    )

    damaged = panel.result_tree.topLevelItem(0)
    opened = panel.result_tree.topLevelItem(1)
    assert "damaged?" in damaged.text(2)
    assert "DAMAGE SUSPECTED" in damaged.toolTip(4)
    assert "likely cut/open" in opened.text(2)
    assert "LIKELY ALREADY CUT / OPEN" in opened.toolTip(4)
    assert damaged.checkState(0) == QtCore.Qt.CheckState.Unchecked
    assert opened.checkState(0) == QtCore.Qt.CheckState.Unchecked

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_trace_preferences_persist_but_replace_previous_resets(
    qt_application: QtWidgets.QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = QtCore.QSettings(
        str(tmp_path / "trace-preferences.ini"),
        QtCore.QSettings.Format.IniFormat,
    )
    monkeypatch.setattr(QtCore, "QSettings", lambda *_args, **_kwargs: settings)
    first = TracePanel()
    first.snap_grid_cells.setChecked(False)
    first.replace_previous.setChecked(False)
    qt_application.processEvents()
    first.close()
    first.deleteLater()
    qt_application.processEvents()

    second = TracePanel()

    assert not second.snap_grid_cells.isChecked()
    assert second.replace_previous.isChecked()

    second.close()
    second.deleteLater()
    qt_application.processEvents()


def test_trace_create_payload_can_keep_earlier_trace_batches(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    panel.set_result(
        {
            "message": "One fitted object",
            "detections": [
                {
                    "id": "trace-new",
                    "index": 1,
                    "source": "direct",
                    "confidence": 0.95,
                    "selected_default": True,
                    "shape": "rounded_rectangle",
                    "width_mm": 80.0,
                    "height_mm": 20.0,
                    "corner_radius_mm": 3.0,
                    "rotation_deg": 4.0,
                }
            ],
        }
    )
    payloads: list[dict[str, object]] = []
    panel.createRequested.connect(payloads.append)

    assert panel.replace_previous.isChecked()
    panel.replace_previous.setChecked(False)
    panel.create_button.click()
    qt_application.processEvents()

    assert payloads == [
        {
            "selected_ids": ["trace-new"],
            "output_mode": "rounded",
            "purpose": "cut",
            "replace_previous": False,
            "combine": False,
        }
    ]
    panel.close()
    panel.deleteLater()


def test_trace_generate_follows_create_and_emits_once(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    result_layout = panel.create_button.parentWidget().layout()
    requests: list[bool] = []
    panel.generateRequested.connect(lambda: requests.append(True))

    assert result_layout is not None
    assert result_layout.indexOf(panel.generate_button) == (
        result_layout.indexOf(panel.create_combined_button) + 1
    )
    panel.generate_button.click()
    qt_application.processEvents()
    assert requests == [True]

    panel.set_generate_enabled(False)
    assert not panel.generate_button.isEnabled()
    panel.set_generate_enabled(True)
    assert panel.generate_button.isEnabled()

    panel.close()
    panel.deleteLater()


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


def test_trace_select_all_checkbox_tracks_and_controls_mixed_selection(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    panel.set_result(
        {
            "message": "Three objects",
            "detections": [
                {
                    "id": f"trace-{index}",
                    "index": index,
                    "source": "direct",
                    "confidence": 0.9,
                    "selected_default": index == 1,
                    "shape": "rounded_rectangle",
                    "width_mm": 80.0,
                    "height_mm": 20.0,
                    "corner_radius_mm": 3.0,
                    "rotation_deg": 0.0,
                }
                for index in range(1, 4)
            ],
        }
    )

    assert panel.select_all_checkbox.isEnabled()
    assert (
        panel.select_all_checkbox.checkState()
        == QtCore.Qt.CheckState.PartiallyChecked
    )

    panel.select_all_checkbox.setCheckState(QtCore.Qt.CheckState.Checked)
    qt_application.processEvents()
    assert panel.selected_ids() == ["trace-1", "trace-2", "trace-3"]
    assert panel.select_all_checkbox.checkState() == QtCore.Qt.CheckState.Checked

    panel.result_tree.topLevelItem(1).setCheckState(
        0, QtCore.Qt.CheckState.Unchecked
    )
    qt_application.processEvents()
    assert (
        panel.select_all_checkbox.checkState()
        == QtCore.Qt.CheckState.PartiallyChecked
    )

    panel.select_all_checkbox.setCheckState(QtCore.Qt.CheckState.Unchecked)
    qt_application.processEvents()
    assert panel.selected_ids() == []
    assert panel.select_all_checkbox.checkState() == QtCore.Qt.CheckState.Unchecked

    panel.clear_result()
    assert not panel.select_all_checkbox.isEnabled()
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


def test_trace_modes_share_filters_and_native_output_creation_controls(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    panel.set_calibration_ready(True)
    assert panel.mode_combo.findData("cutout") == -1
    for mode in ("auto", "color", "contrast"):
        panel.mode_combo.setCurrentIndex(panel.mode_combo.findData(mode))
        panel.output_mode.setCurrentIndex(panel.output_mode.findData("native"))
        qt_application.processEvents()
        assert panel.detect_button.text() == "Detect objects"
        assert panel.min_area.isEnabled()
        assert panel.max_area.isEnabled()
        assert panel.regular_grid.isEnabled()
        assert panel.native_fitting_tolerance.isEnabled()

    detection = {
        "id": "contrast-candidate",
        "index": 1,
        "source": "direct",
        "confidence": 0.9,
        "selected_default": True,
        "shape": "contour",
        "width_mm": 25.0,
        "height_mm": 18.0,
        "rotation_deg": 0.0,
        "native_verified": True,
        "diagnostics": {
            "within_work_area": True,
            "native_fit_status": "verified",
            "native_sequences": ["LLCC"],
        },
    }
    panel.set_result(
        {
            "mode_used": "contrast",
            "message": "Detected contrast candidate",
            "detections": [detection],
            "grid": None,
        }
    )
    assert panel.selected_ids() == ["contrast-candidate"]
    assert panel.create_button.isEnabled()
    assert panel.create_combined_button.isEnabled()
    assert "Verified native sequence" in panel.result_tree.topLevelItem(0).toolTip(4)

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_trace_mode_controls_match_auto_raster_color_and_grid_paths(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    panel.set_calibration_ready(True)

    assert "correcting broad lighting variation" in (
        panel.contrast_threshold_mode.toolTip()
    )
    assert "illumination-normalized" in panel.contrast_threshold.toolTip()
    assert "does not threshold raw camera brightness" in (
        panel.contrast_invert.toolTip()
    )

    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("color"))
    panel.output_mode.setCurrentIndex(panel.output_mode.findData("rounded"))
    panel.border_offset.setValue(1.25)
    panel.regular_grid.setChecked(False)
    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("auto"))
    qt_application.processEvents()

    assert not panel.target_hue.isEnabled()
    assert not panel.hue_tolerance.isEnabled()
    assert not panel.min_saturation.isEnabled()
    assert not panel.pick_color_button.isEnabled()
    assert not panel.contrast_threshold_mode.isEnabled()
    assert not panel.contrast_threshold.isEnabled()
    assert not panel.contrast_invert.isEnabled()
    assert panel.output_mode.currentData() == "native"
    assert not panel.output_mode.isEnabled()
    assert panel.border_offset.value() == pytest.approx(0.0)
    assert not panel.border_offset.isEnabled()
    assert panel.native_fitting_tolerance.isEnabled()

    panel.regular_grid.setChecked(True)
    panel.output_mode.setCurrentIndex(panel.output_mode.findData("rounded"))
    qt_application.processEvents()

    assert panel.output_mode.isEnabled()
    assert panel.output_mode.currentData() == "rounded"
    assert panel.border_offset.isEnabled()
    assert panel.infer_missing.isEnabled()
    assert panel.normalize_grid.isEnabled()
    assert not panel.target_hue.isEnabled()
    assert not panel.pick_color_button.isEnabled()
    assert not panel.contrast_threshold_mode.isEnabled()
    assert not panel.contrast_invert.isEnabled()

    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("contrast"))
    panel.regular_grid.setChecked(False)
    qt_application.processEvents()

    assert not panel.target_hue.isEnabled()
    assert not panel.hue_tolerance.isEnabled()
    assert not panel.min_saturation.isEnabled()
    assert not panel.pick_color_button.isEnabled()
    assert panel.contrast_threshold_mode.isEnabled()
    assert not panel.contrast_threshold.isEnabled()
    assert panel.contrast_invert.isEnabled()
    assert panel.output_mode.currentData() == "native"
    assert not panel.output_mode.isEnabled()
    panel.contrast_threshold_mode.setCurrentIndex(
        panel.contrast_threshold_mode.findData("manual")
    )
    panel.contrast_threshold.setValue(137)
    panel.contrast_invert.setChecked(True)
    qt_application.processEvents()
    assert panel.contrast_threshold.isEnabled()
    assert panel.options()["contrast_threshold_mode"] == "manual"
    assert panel.options()["contrast_threshold"] == 137
    assert panel.options()["contrast_invert"] is True

    panel.regular_grid.setChecked(True)
    qt_application.processEvents()
    assert not panel.contrast_threshold_mode.isEnabled()
    assert not panel.contrast_threshold.isEnabled()
    assert not panel.contrast_invert.isEnabled()
    assert panel.output_mode.isEnabled()

    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("color"))
    qt_application.processEvents()
    assert panel.target_hue.isEnabled()
    assert panel.hue_tolerance.isEnabled()
    assert panel.min_saturation.isEnabled()
    assert panel.pick_color_button.isEnabled()
    assert not panel.contrast_threshold_mode.isEnabled()
    assert not panel.contrast_threshold.isEnabled()
    assert not panel.contrast_invert.isEnabled()

    panel.set_color_pick_active(True, sampling=True)
    panel.set_calibration_ready(True)
    qt_application.processEvents()
    assert not panel.pick_color_button.isEnabled()

    panel.set_color_pick_active(False)
    qt_application.processEvents()
    assert panel.pick_color_button.isEnabled()

    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("auto"))
    panel.set_calibration_ready(False)
    panel.set_calibration_ready(True)
    panel.set_color_pick_active(False)
    qt_application.processEvents()
    assert not panel.pick_color_button.isEnabled()

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_trace_preferences_migrate_removed_cutout_mode_to_contrast(
    qt_application: QtWidgets.QApplication,
) -> None:
    settings = QtCore.QSettings("E3", "PositioningSystem")
    settings.beginGroup("trace")
    settings.setValue("detection_mode", "cutout")
    settings.endGroup()
    settings.sync()

    panel = TracePanel()

    assert panel.mode_combo.currentData() == "contrast"
    settings.beginGroup("trace")
    assert settings.value("detection_mode") == "contrast"
    settings.endGroup()

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
