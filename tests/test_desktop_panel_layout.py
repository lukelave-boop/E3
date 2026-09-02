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


def test_trace_detail_layout_copy_and_grid_effective_full(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    form = panel.trace_detail.parentWidget().layout()
    assert isinstance(form, QtWidgets.QFormLayout)
    detail_row = form.getWidgetPosition(panel.trace_detail)[0]
    purpose_row = form.getWidgetPosition(panel.trace_purpose)[0]

    assert detail_row + 1 == purpose_row
    assert form.labelForField(panel.trace_detail).text() == "Trace detail"
    assert [panel.trace_detail.itemText(index) for index in range(2)] == [
        "Full detail",
        "Outer silhouette",
    ]
    assert [panel.trace_detail.itemData(index) for index in range(2)] == [
        "full",
        "outer_silhouette",
    ]
    assert panel.trace_detail.currentData() == "full"
    assert panel.options()["trace_detail"] == "full"
    assert not panel.trace_detail.isEnabled()
    assert "Mask display" in panel.trace_detail.toolTip()
    assert "Minimum/Maximum hole area" in panel.trace_detail.toolTip()
    assert "Grid tracing always uses Full detail" in panel.trace_detail.toolTip()
    assert "exact cleaned detection evidence" in panel.raster_preview_combo.toolTip()
    assert "need not become Outer silhouette" in panel.raster_preview_combo.toolTip()

    panel.regular_grid.setChecked(False)
    panel.set_result(
        {
            "message": "One current exterior",
            "detections": [
                {
                    "id": "outer-candidate",
                    "index": 1,
                    "source": "direct",
                    "confidence": 0.95,
                    "selected_default": True,
                    "shape": "contour",
                    "width_mm": 20.0,
                    "height_mm": 10.0,
                    "rotation_deg": 0.0,
                    "diagnostics": {
                        "within_work_area": True,
                        "trace_detail": "outer_silhouette",
                        "outer_only": True,
                    },
                }
            ],
        }
    )
    assert panel.create_button.isEnabled()
    assert "only this top-level exterior" in panel.result_tree.topLevelItem(0).toolTip(0)
    assert "Mask-only holes and descendants" in panel.result_tree.topLevelItem(0).toolTip(0)
    panel.trace_detail.setCurrentIndex(
        panel.trace_detail.findData("outer_silhouette")
    )
    qt_application.processEvents()
    assert panel.trace_detail.isEnabled()
    assert panel.options()["trace_detail"] == "outer_silhouette"
    assert not panel.create_button.isEnabled()

    panel.regular_grid.setChecked(True)
    qt_application.processEvents()
    assert not panel.trace_detail.isEnabled()
    assert panel.trace_detail.currentData() == "outer_silhouette"
    assert panel.options()["trace_detail"] == "full"

    panel.regular_grid.setChecked(False)
    qt_application.processEvents()
    assert panel.trace_detail.currentData() == "outer_silhouette"
    assert panel.options()["trace_detail"] == "outer_silhouette"

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_trace_detail_preference_missing_invalid_and_round_trip(
    qt_application: QtWidgets.QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = QtCore.QSettings(
        str(tmp_path / "trace-detail-preferences.ini"),
        QtCore.QSettings.Format.IniFormat,
    )
    monkeypatch.setattr(QtCore, "QSettings", lambda *_args, **_kwargs: settings)

    missing = TracePanel()
    assert missing.trace_detail.currentData() == "full"
    missing.close()
    missing.deleteLater()
    qt_application.processEvents()

    settings.beginGroup("trace")
    settings.setValue("trace_detail", "obsolete")
    settings.endGroup()
    settings.sync()
    invalid = TracePanel()
    assert invalid.trace_detail.currentData() == "full"
    invalid.regular_grid.setChecked(False)
    invalid.trace_detail.setCurrentIndex(
        invalid.trace_detail.findData("outer_silhouette")
    )
    qt_application.processEvents()
    invalid.close()
    invalid.deleteLater()
    qt_application.processEvents()

    restored = TracePanel()
    assert not restored.regular_grid.isChecked()
    assert restored.trace_detail.currentData() == "outer_silhouette"
    assert restored.options()["trace_detail"] == "outer_silhouette"

    restored.close()
    restored.deleteLater()
    qt_application.processEvents()


def test_trace_hole_filter_preferences_migrate_and_round_trip_independently(
    qt_application: QtWidgets.QApplication,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = QtCore.QSettings(
        str(tmp_path / "legacy-trace-preferences.ini"),
        QtCore.QSettings.Format.IniFormat,
    )
    settings.beginGroup("trace")
    settings.setValue("min_area_mm2", 50.0)
    settings.setValue("max_area_mm2", 8_000.0)
    settings.endGroup()
    settings.sync()
    monkeypatch.setattr(QtCore, "QSettings", lambda *_args, **_kwargs: settings)

    migrated = TracePanel()
    assert migrated.min_hole_area.value() == pytest.approx(50.0)
    assert migrated.options()["max_hole_area_mm2"] is None
    settings.beginGroup("trace")
    assert float(settings.value("min_hole_area_mm2")) == pytest.approx(50.0)
    assert float(settings.value("max_hole_area_mm2")) == pytest.approx(-0.01)
    settings.endGroup()

    migrated.min_area.setValue(75.0)
    qt_application.processEvents()
    assert migrated.min_hole_area.value() == pytest.approx(50.0)
    migrated.min_hole_area.setValue(2.0)
    migrated.max_hole_area.setValue(30.0)
    qt_application.processEvents()
    migrated.close()
    migrated.deleteLater()
    qt_application.processEvents()

    restored = TracePanel()
    options = restored.options()
    assert options["min_area_mm2"] == pytest.approx(75.0)
    assert options["min_hole_area_mm2"] == pytest.approx(2.0)
    assert options["max_hole_area_mm2"] == pytest.approx(30.0)

    restored.close()
    restored.deleteLater()
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


def test_trace_hole_filters_have_distinct_layout_copy_and_raster_applicability(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    form = panel.min_area.parentWidget().layout()
    assert isinstance(form, QtWidgets.QFormLayout)
    controls = (
        panel.min_area,
        panel.max_area,
        panel.min_hole_area,
        panel.max_hole_area,
        panel.min_width,
        panel.min_height,
    )
    assert [form.getWidgetPosition(control)[0] for control in controls] == list(
        range(6)
    )
    assert panel.min_hole_area.suffix() == " mm²"
    assert panel.max_hole_area.suffix() == " mm²"
    assert panel.max_hole_area.specialValueText() == "No maximum"
    assert panel.options()["max_hole_area_mm2"] is None
    panel.min_hole_area.setValue(0.0)
    panel.max_hole_area.setValue(0.0)
    assert panel.options()["max_hole_area_mm2"] == pytest.approx(0.0)
    assert "Fill enclosed holes smaller" in panel.min_hole_area.toolTip()
    assert "Fill enclosed holes larger" in panel.max_hole_area.toolTip()
    assert "selected area range are preserved" in panel.max_hole_area.toolTip()
    assert "cleaned Mask" in panel.min_hole_area.toolTip()
    assert "cleaned Mask" in panel.max_hole_area.toolTip()
    assert "Outer silhouette ignores" in panel.min_hole_area.toolTip()
    assert "Outer silhouette ignores" in panel.max_hole_area.toolTip()
    assert "objects" not in panel.min_hole_area.toolTip().lower()
    assert "objects" not in panel.max_hole_area.toolTip().lower()

    assert not panel.min_hole_area.isEnabled()
    assert not panel.max_hole_area.isEnabled()
    panel.regular_grid.setChecked(False)
    qt_application.processEvents()
    assert panel.min_hole_area.isEnabled()
    assert panel.max_hole_area.isEnabled()

    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("color"))
    qt_application.processEvents()
    assert not panel.min_hole_area.isEnabled()
    assert not panel.max_hole_area.isEnabled()

    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("contrast"))
    qt_application.processEvents()
    assert panel.min_hole_area.isEnabled()
    assert panel.max_hole_area.isEnabled()

    panel.regular_grid.setChecked(True)
    qt_application.processEvents()
    assert not panel.min_hole_area.isEnabled()
    assert not panel.max_hole_area.isEnabled()
    assert panel.min_area.isEnabled()
    assert panel.max_area.isEnabled()

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_trace_mode_controls_match_auto_raster_color_and_grid_paths(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    panel.set_calibration_ready(True)

    assert panel.min_area.value() == pytest.approx(30.0)
    assert panel.min_width.value() == pytest.approx(4.0)
    assert panel.min_height.value() == pytest.approx(3.0)
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


def test_trace_panel_shows_only_current_successful_auto_threshold(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()
    panel.set_calibration_ready(True)
    detection = {
        "id": "threshold-candidate",
        "index": 1,
        "source": "direct",
        "confidence": 1.0,
        "selected_default": True,
        "shape": "contour",
        "width_mm": 20.0,
        "height_mm": 10.0,
        "rotation_deg": 0.0,
        "native_verified": True,
        "diagnostics": {
            "within_work_area": True,
            "native_fit_status": "verified",
            "native_sequences": ["LLLL"],
        },
    }

    def auto_result(strategy: str, threshold: int | None) -> dict[str, object]:
        attempt: dict[str, object] = {
            "name": strategy,
            "status": "success",
        }
        if threshold is not None:
            attempt["threshold"] = threshold
        return {
            "mode_used": "contrast" if strategy.startswith("raster_") else "color",
            "message": "Detection complete",
            "detections": [detection],
            "grid": None,
            "options": {
                "detection_mode": "auto",
                "regular_grid": False,
                "contrast_threshold_mode": "auto",
            },
            "diagnostics": {
                "auto": {
                    "selected_strategy": strategy,
                    "attempts": [attempt],
                }
            },
        }

    assert panel.chosen_threshold_value.text() == "—"
    panel.set_result(auto_result("raster_dark", 158))
    assert panel.chosen_threshold_value.text() == "158"
    panel.set_result(auto_result("raster_light", 171))
    assert panel.chosen_threshold_value.text() == "171"

    panel.begin_detection()
    assert panel.chosen_threshold_value.text() == "—"
    panel.set_result(auto_result("raster_dark", 149))
    panel.set_detection_failed("native fitting failed", retain_preview=True)
    assert panel.chosen_threshold_value.text() == "—"

    panel.set_result(auto_result("color", None))
    assert panel.chosen_threshold_value.text() == "N/A"
    panel.clear_result()
    assert panel.chosen_threshold_value.text() == "—"

    contrast_result = {
        "mode_used": "contrast",
        "message": "Detected contrast candidate",
        "detections": [detection],
        "grid": None,
        "options": {
            "detection_mode": "contrast",
            "regular_grid": False,
            "contrast_threshold_mode": "auto",
        },
        "diagnostics": {"strategy_metrics": {"threshold": 146}},
    }
    panel.set_result(contrast_result)
    assert panel.chosen_threshold_value.text() == "146"
    panel.min_area.setValue(panel.min_area.value() + 1.0)
    qt_application.processEvents()
    assert panel.chosen_threshold_value.text() == "—"

    panel.mode_combo.setCurrentIndex(panel.mode_combo.findData("contrast"))
    panel.regular_grid.setChecked(False)
    panel.contrast_threshold_mode.setCurrentIndex(
        panel.contrast_threshold_mode.findData("manual")
    )
    panel.contrast_threshold.setValue(133)
    qt_application.processEvents()
    manual_result = {
        **contrast_result,
        "options": {
            "detection_mode": "contrast",
            "regular_grid": False,
            "contrast_threshold_mode": "manual",
        },
    }
    panel.set_result(manual_result)
    assert panel.chosen_threshold_value.text() == "—"
    assert panel.contrast_threshold.isEnabled()
    assert panel.contrast_threshold.value() == 133

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_trace_panel_has_no_pre_create_straighten_control(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TracePanel()

    assert not hasattr(panel, "straightenRequested")
    assert not hasattr(panel, "straightenResetRequested")
    assert not hasattr(panel, "straightenContextChanged")
    assert not hasattr(panel, "reviewInvalidated")
    assert not hasattr(panel, "straighten_review")
    assert not hasattr(panel, "set_straighten_offer")
    assert not hasattr(panel, "set_straighten_applied")
    assert not hasattr(panel, "clear_straighten_review")
    assert panel.findChild(QtWidgets.QWidget, "traceStraightenReview") is None

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_transform_straighten_review_uses_direction_words_and_has_no_reset(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TransformPanel()
    straighten_events: list[bool] = []
    panel.straightenRequested.connect(lambda: straighten_events.append(True))
    estimate = {
        "offered": True,
        "detected_skew_deg": 1.6,
        "correction_deg": -1.6,
    }

    assert panel.straighten_review.isHidden()
    panel.set_straighten_review(estimate)
    assert not panel.straighten_review.isHidden()
    assert panel.straighten_status.text() == "Detected skew: 1.6° counterclockwise"
    assert "clockwise correction" in panel.straighten_button.toolTip()
    assert "selected project geometry" in panel.straighten_button.toolTip()
    assert "undoable transform" in panel.straighten_button.toolTip()
    assert not panel.straighten_button.isHidden()
    assert panel.straighten_unavailable.isHidden()
    assert not hasattr(panel, "straighten_reset_button")
    panel.straighten_button.click()
    assert straighten_events == [True]

    panel.set_straighten_review(
        {
            "offered": True,
            "detected_skew_deg": -3.0,
            "correction_deg": 3.0,
        }
    )
    assert panel.straighten_status.text() == "Detected skew: 3.0° clockwise"
    assert "counterclockwise correction" in panel.straighten_button.toolTip()

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (
            "insufficient_orientation_evidence",
            "not enough reliable orientation evidence",
        ),
        ("conflicting_candidate_orientations", "conflicting orientation evidence"),
        ("trivial_skew", "already straight"),
        ("outside_skew_correction_range", "outside the supported correction range"),
    ],
)
def test_transform_straighten_review_shows_muted_unavailable_reason(
    qt_application: QtWidgets.QApplication,
    reason: str,
    expected: str,
) -> None:
    panel = TransformPanel()

    panel.set_straighten_review(
        {"offered": False, "suppression_reason": reason}
    )

    assert not panel.straighten_review.isHidden()
    assert panel.straighten_status.isHidden()
    assert panel.straighten_button.isHidden()
    assert not panel.straighten_unavailable.isHidden()
    assert panel.straighten_unavailable.objectName() == "mutedLabel"
    assert expected in panel.straighten_unavailable.text()

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_transform_straighten_review_hides_for_ineligible_selection(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TransformPanel()
    offer = {
        "offered": True,
        "detected_skew_deg": 2.0,
        "correction_deg": -2.0,
    }

    panel.set_straighten_review(offer)
    assert not panel.straighten_review.isHidden()

    panel.set_straighten_review(offer, eligible=False)
    assert panel.straighten_review.isHidden()
    assert panel.straighten_status.text() == ""
    assert panel.straighten_unavailable.text() == ""

    panel.set_straighten_review(
        {"offered": False, "suppression_reason": "trivial_skew"}
    )
    panel.clear_straighten_review()
    assert panel.straighten_review.isHidden()


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
