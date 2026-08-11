from __future__ import annotations

# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for preview tests")

from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.desktop.job_preview import JobPreviewDialog
from laser_aligner.desktop.panels import JobPanel
from laser_aligner.desktop.theme import DARK_STYLESHEET
from laser_aligner.gcode.job_plan import build_job_plan, e3_metadata_line


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _plan():
    text = "\n".join(
        [
            "G21",
            "G90",
            "M5",
            e3_metadata_line(
                "layer",
                {"id": "line-01", "name": "Line 01", "color": "#185CFF"},
            ),
            e3_metadata_line("pass", {"index": 1, "count": 1}),
            "G0 X20 Y20 F2000",
            "M4 S200",
            "G1 X80 Y20 F1000",
            "G1 X80 Y40 F1000",
            "M5",
        ]
    )
    return build_job_plan(
        text,
        power_max=1000,
        start_position=(10.0, 10.0),
    )


def _variable_power_plan(
    *,
    layer_name: str = "Variable power",
    layer_color: str = "#185CFF",
):
    text = "\n".join(
        [
            "G21",
            "G90",
            "M5",
            e3_metadata_line(
                "layer",
                {
                    "id": "power-01",
                    "name": layer_name,
                    "color": layer_color,
                },
            ),
            e3_metadata_line("pass", {"index": 1, "count": 1}),
            "G0 X10 Y10 F2000",
            "M4 S100",
            "G1 X40 Y10 F1000",
            "S800",
            "G1 X70 Y10 F1000",
            "M5",
        ]
    )
    return build_job_plan(
        text,
        power_max=1000,
        start_position=(0.0, 0.0),
    )


def test_preview_scrubber_reports_explicit_power_and_coordinates(
    qt_application: QtWidgets.QApplication,
) -> None:
    plan = _plan()
    dialog = JobPreviewDialog(
        plan,
        (0.0, 100.0, 0.0, 100.0),
        "test.gcode",
        max_work_feed_mm_min=6000.0,
        max_travel_feed_mm_min=6000.0,
    )
    dialog.show()
    qt_application.processEvents()

    dialog.set_elapsed(plan.moves[1].start_seconds + 0.1)

    assert "POWER 20.0% / S200" in dialog.move_label.text()
    assert "16.67 mm/s · 16.7%" in dialog.move_label.text()
    assert "F1000" not in dialog.move_label.full_text
    assert "Line 01" in dialog.move_label.text()
    assert "X80.000 Y20.000" in dialog.move_label.text()
    dialog.travel_check.setChecked(False)
    travel_items = [
        item
        for key, item in dialog.canvas._items.items()
        if key[0] == "travel"
    ]
    assert travel_items and all(not item.isVisible() for item in travel_items)

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_preview_saves_current_frame(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    dialog = JobPreviewDialog(
        _plan(),
        (0.0, 100.0, 0.0, 100.0),
        "test.gcode",
    )
    destination = tmp_path / "preview.png"

    dialog.canvas.render_image(destination, width=600)

    assert destination.is_file()
    assert destination.stat().st_size > 1000
    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_preview_layer_table_controls_only_that_operation(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = JobPreviewDialog(
        _plan(),
        (0.0, 100.0, 0.0, 100.0),
        "test.gcode",
        max_work_feed_mm_min=6000.0,
        max_travel_feed_mm_min=6000.0,
    )
    dialog.show()
    qt_application.processEvents()

    assert dialog.layer_tree.topLevelItemCount() == 1
    layer = dialog.layer_tree.topLevelItem(0)
    assert layer.text(1) == "Line 01 · Line"
    assert layer.text(4) == "16.67 mm/s · 16.7%"
    assert "20.0% / S200" in layer.text(5)
    assert "16.67 mm/s · 16.7% of configured 100.00 mm/s work limit" in layer.toolTip(4)
    layer.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
    qt_application.processEvents()

    powered_items = [
        item
        for key, item in dialog.canvas._items.items()
        if key[0] == "powered"
    ]
    assert powered_items and all(not item.isVisible() for item in powered_items)
    assert "Line 01" in dialog.legend.text()

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_power_shading_keeps_distinct_s_values_within_one_layer(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = JobPreviewDialog(
        _variable_power_plan(),
        (0.0, 100.0, 0.0, 100.0),
        "variable-power.gcode",
    )
    dialog.show()
    qt_application.processEvents()

    dialog.power_check.setChecked(True)
    powered_colors = {
        key[2]: item.pen().color().name()
        for key, item in dialog.canvas._items.items()
        if key[0] == "powered"
    }

    assert set(powered_colors) == {100.0, 800.0}
    assert len(set(powered_colors.values())) == 2

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_preview_escapes_rich_metadata_and_uses_warning_theme_contract(
    qt_application: QtWidgets.QApplication,
) -> None:
    layer_name = '<b>Layer & "quoted"</b><img src="bad">'
    plan = replace(
        _variable_power_plan(
            layer_name=layer_name,
            layer_color="red; background-image: url(bad)",
        ),
        planner_mode="<i>nearest & unsafe</i>",
        warnings=("<b>warning & review</b>",),
    )
    dialog = JobPreviewDialog(
        plan,
        (0.0, 100.0, 0.0, 100.0),
        "<b>job & title</b>",
    )
    dialog.show()
    qt_application.processEvents()
    powered_move = next(move for move in plan.moves if move.laser_on)
    dialog.set_elapsed(powered_move.start_seconds + 0.01)

    assert dialog.heading.textFormat() == QtCore.Qt.TextFormat.PlainText
    assert "<i>nearest & unsafe</i>" in dialog.heading.text()
    assert "&lt;b&gt;Layer &amp; &quot;quoted&quot;&lt;/b&gt;" in dialog.legend.text()
    assert "<b>Layer" not in dialog.legend.text()
    assert "background-image" not in dialog.legend.text()
    assert dialog.move_label.textFormat() == QtCore.Qt.TextFormat.PlainText
    assert layer_name in dialog.move_label.full_text
    dialog.move_label.resize(80, dialog.move_label.height())
    dialog.move_label._refresh_text()
    assert "&lt;b&gt;Layer &amp; &quot;quoted&quot;&lt;/b&gt;" in dialog.move_label.toolTip()
    layer_tooltip = dialog.layer_tree.topLevelItem(0).toolTip(1)
    assert "&lt;b&gt;Layer &amp; &quot;quoted&quot;&lt;/b&gt;" in layer_tooltip
    assert "<img" not in layer_tooltip
    assert dialog.warning_label is not None
    assert dialog.warning_label.objectName() == "warningLabel"
    assert dialog.warning_label.textFormat() == QtCore.Qt.TextFormat.PlainText
    assert dialog.warning_label.text() == "Warnings: <b>warning & review</b>"
    assert "QLabel#warningLabel" in DARK_STYLESHEET

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_preview_remains_useful_at_compact_geometry_with_large_text(
    qt_application: QtWidgets.QApplication,
) -> None:
    original_font = QtGui.QFont(qt_application.font())
    large_font = QtGui.QFont(original_font)
    large_font.setPointSize(13)
    qt_application.setFont(large_font)
    dialog: JobPreviewDialog | None = None
    try:
        plan = _variable_power_plan(layer_name="Long operation " * 40)
        dialog = JobPreviewDialog(
            plan,
            (0.0, 100.0, 0.0, 100.0),
            "compact-preview.gcode",
        )
        screen = QtGui.QGuiApplication.primaryScreen()
        available = (
            screen.availableGeometry().size()
            if screen is not None
            else QtCore.QSize(900, 680)
        )
        target_width = min(900, available.width())
        target_height = min(680, available.height())
        dialog.resize(target_width, target_height)
        dialog.show()
        qt_application.processEvents()

        powered_move = next(move for move in plan.moves if move.laser_on)
        dialog.set_elapsed(powered_move.start_seconds + 0.01)
        qt_application.processEvents()

        assert dialog.width() <= target_width
        assert dialog.height() <= target_height
        assert dialog.canvas.width() >= 360
        assert dialog.canvas.height() >= 240
        assert dialog.sidebar.width() >= 300
        assert not dialog.canvas.geometry().intersects(dialog.sidebar.geometry())
        header = dialog.layer_tree.header()
        final_column_right = (
            header.sectionViewportPosition(5) + header.sectionSize(5)
        )
        assert final_column_right <= dialog.layer_tree.viewport().width()
        assert "…" in dialog.move_label.text()
        assert dialog.move_label.toolTip() == dialog.move_label.full_text
        assert dialog.move_label.width() <= dialog.contentsRect().width()
    finally:
        if dialog is not None:
            dialog.close()
            dialog.deleteLater()
            qt_application.processEvents()
        qt_application.setFont(original_font)


def test_controller_progress_does_not_overwrite_prepared_power(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = JobPanel()
    panel.set_prepared_job(
        "14 paths · estimated 42 s",
        power_percent=20.0,
        controller_power=200.0,
    )

    panel.set_job_status(None)

    assert "max power 20.0% / S200" in panel.summary.text()
    assert panel.execution_label.text() == "Controller idle · no job started"
    assert panel.progress.format() == "Execution 0%"
    panel.set_job_status(
        {
            "running": True,
            "name": "grid.gcode",
            "total_lines": 100,
            "completed_lines": 25,
        }
    )
    assert "max power 20.0% / S200" in panel.summary.text()
    assert panel.progress.format() == "Execution 25%"
    assert "25/100 lines" in panel.execution_label.text()

    panel.set_job_status(
        {
            "running": True,
            "phase": "homing",
            "name": "grid.gcode",
            "total_lines": 100,
            "completed_lines": 100,
        }
    )
    assert panel.progress.format() == "Finishing · homing"
    assert panel.execution_label.text() == "Toolpath complete · homing machine"

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_preview_explains_automatic_post_job_motion_is_not_drawn(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = JobPreviewDialog(
        _plan(),
        (0.0, 0.0, 220.0, 220.0),
        "preview.gcode",
    )

    notes = [
        label.text()
        for label in dialog.findChildren(QtWidgets.QLabel)
        if "end of the generated G-code stream" in label.text()
    ]
    assert len(notes) == 1
    assert "Home / park" in notes[0]
    assert "not drawn here" in notes[0]

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_preview_start_here_emits_reviewed_move_only(
    qt_application: QtWidgets.QApplication,
) -> None:
    plan = _plan()
    dialog = JobPreviewDialog(plan, (0.0, 100.0, 0.0, 100.0), "test.gcode")
    requested: list[int] = []
    dialog.startHereRequested.connect(requested.append)
    dialog.show()
    qt_application.processEvents()

    dialog.set_elapsed(plan.moves[1].start_seconds + 0.01)
    dialog.start_here_button.click()

    assert requested == [1]
    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_preview_keyboard_timeline_navigation(
    qt_application: QtWidgets.QApplication,
) -> None:
    plan = _plan()
    dialog = JobPreviewDialog(plan, (0.0, 100.0, 0.0, 100.0), "test.gcode")
    dialog.show()
    qt_application.processEvents()

    QtWidgets.QApplication.sendEvent(
        dialog,
        QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_Home,
            QtCore.Qt.KeyboardModifier.NoModifier,
        ),
    )
    assert dialog.slider.value() == 0
    QtWidgets.QApplication.sendEvent(
        dialog,
        QtGui.QKeyEvent(
            QtCore.QEvent.Type.KeyPress,
            QtCore.Qt.Key.Key_End,
            QtCore.Qt.KeyboardModifier.NoModifier,
        ),
    )
    assert dialog.slider.value() == 10_000

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()
