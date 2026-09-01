from __future__ import annotations

# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for preview tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.air_assist import AirAssistCommands, AirAssistMode
from laser_aligner.desktop.job_preview import (
    JobPreviewDialog,
    JobPreviewPreparationCancelled,
    prepare_job_preview,
)
from laser_aligner.desktop.panels import JobProgressWidget
from laser_aligner.desktop.theme import DARK_STYLESHEET
from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.gcode.job_plan import build_job_plan, e3_metadata_line
from laser_aligner.project.job_preflight import (
    JobPreflightReport,
    PreflightFinding,
    PreflightSeverity,
)


GRBL_AIR = AirAssistCommands(
    mode=AirAssistMode.GRBL_COOLANT,
    protocol="grbl",
    fan_index=None,
    on_commands=("M8",),
    off_commands=("M9",),
)


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


def _air_assist_plan():
    text = "\n".join(
        [
            "G21",
            "G90",
            "M5",
            "M9",
            e3_metadata_line(
                "layer",
                {
                    "id": "air-01",
                    "name": "Air layer",
                    "color": "#185CFF",
                    "air_assist": True,
                },
            ),
            "G0 X20 Y20 F2000",
            "M8",
            "M4 S200",
            "G1 X80 Y20 F1000",
            "M5",
            "M9",
            "M5",
        ]
    )
    return build_job_plan(
        text,
        power_max=1000,
        start_position=(10.0, 10.0),
        air_assist_commands=GRBL_AIR,
    )


def test_preview_is_window_modal_and_blocks_parent_project_action(
    qt_application: QtWidgets.QApplication,
) -> None:
    parent = QtWidgets.QWidget()
    parent.resize(240, 120)
    edit_button = QtWidgets.QPushButton("Edit project", parent)
    edit_button.move(20, 20)
    mutations: list[bool] = []
    edit_button.clicked.connect(lambda: mutations.append(True))
    dialog = JobPreviewDialog(
        _plan(),
        (0.0, 100.0, 0.0, 100.0),
        "modal.gcode",
        parent,
    )
    parent.show()
    dialog.show()
    qt_application.processEvents()

    assert dialog.isModal()
    assert dialog.windowModality() == QtCore.Qt.WindowModality.WindowModal
    assert qt_application.activeModalWidget() is dialog

    click_position = edit_button.mapTo(parent, edit_button.rect().center())
    QtTest.QTest.mouseClick(
        parent.windowHandle(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        click_position,
    )
    qt_application.processEvents()
    assert mutations == []

    dialog.close()
    qt_application.processEvents()
    QtTest.QTest.mouseClick(
        parent.windowHandle(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        click_position,
    )
    qt_application.processEvents()
    assert mutations == [True]
    parent.close()
    parent.deleteLater()


def test_preview_start_job_is_distinct_from_timeline_start(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = JobPreviewDialog(
        _plan(),
        (0.0, 100.0, 0.0, 100.0),
        "start.gcode",
    )
    requests: list[bool] = []
    dialog.runRequested.connect(lambda: requests.append(True))
    dialog.show()
    qt_application.processEvents()

    assert dialog.run_button.text() == "START JOB"
    assert dialog.run_button.objectName() == "dangerButton"
    assert dialog.reset_button.text() == "⏮ Start"
    assert dialog.run_button is not dialog.reset_button
    dialog.run_button.click()
    assert requests == [True]

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_warning_preflight_is_retained_in_preview_without_blocking_controls(
    qt_application: QtWidgets.QApplication,
) -> None:
    report = JobPreflightReport(
        findings=(
            PreflightFinding(
                code="bounds.near_edge",
                severity=PreflightSeverity.WARNING,
                title="Near work-area edge",
                message="The exact job approaches the configured work-area edge.",
                detail="Inspect the outermost powered move in this exact preview.",
                context={"margin_mm": 0.25, "object": "Perimeter"},
            ),
        )
    )
    plan = _plan()
    dialog = JobPreviewDialog(
        plan,
        (0.0, 100.0, 0.0, 100.0),
        "warning-preflight.gcode",
        preflight_report=report,
    )
    run_requests: list[bool] = []
    dialog.runRequested.connect(lambda: run_requests.append(True))
    dialog.resize(900, 680)
    dialog.show()
    qt_application.processEvents()

    assert dialog.preflight_report is report
    assert dialog.preflight_view is not None
    assert dialog.preflight_view.report is report
    assert dialog.preflight_view.status_label.objectName() == "statusWarning"
    assert dialog.preflight_view.counts_label.text() == (
        "Blockers: 0  ·  Warnings: 1  ·  Info: 0  ·  Total: 1"
    )
    assert dialog.preflight_view.findings_tree.topLevelItemCount() == 1
    finding = dialog.preflight_view.findings_tree.topLevelItem(0)
    assert finding is not None
    assert tuple(finding.text(column) for column in range(6)) == (
        "Warning",
        "bounds.near_edge",
        "Near work-area edge",
        "The exact job approaches the configured work-area edge.",
        "Inspect the outermost powered move in this exact preview.",
        "margin_mm=0.25; object=Perimeter",
    )

    assert dialog.preflight_view.minimumWidth() == 0
    assert (
        dialog.preflight_view.sizePolicy().horizontalPolicy()
        == QtWidgets.QSizePolicy.Policy.Ignored
    )
    assert dialog.preflight_view.findings_tree.minimumWidth() == 0
    assert (
        dialog.preflight_view.findings_tree.horizontalScrollBarPolicy()
        == QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
    )
    assert dialog.sidebar.horizontalScrollBarPolicy() == (
        QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert dialog.canvas.width() >= 360
    assert dialog.sidebar.width() >= 300
    assert not dialog.canvas.geometry().intersects(dialog.sidebar.geometry())

    powered_move = next(move for move in plan.moves if move.laser_on)
    dialog.set_elapsed(powered_move.start_seconds + 0.01)
    assert all(
        control.isEnabled()
        for control in (
            dialog.slider,
            dialog.reset_button,
            dialog.play_button,
            dialog.speed_combo,
            dialog.start_here_button,
            dialog.run_button,
            dialog.close_button,
            dialog.travel_check,
            dialog.fit_button,
        )
    )
    dialog.run_button.click()
    assert run_requests == [True]

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_preview_without_preflight_preserves_existing_constructor_behavior(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = JobPreviewDialog(
        _plan(),
        (0.0, 100.0, 0.0, 100.0),
        "no-preflight.gcode",
    )
    dialog.show()
    qt_application.processEvents()

    assert dialog.preflight_report is None
    assert dialog.preflight_view is None
    assert dialog.layer_tree.topLevelItemCount() == 1
    assert dialog.run_button.isEnabled()
    assert dialog.close_button.isEnabled()
    assert dialog.travel_check.isChecked()
    assert dialog.legend_check.isChecked()

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


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
    assert "Machine X80.000 Y20.000" in dialog.move_label.full_text
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


def test_honeycomb_preview_renders_machine_plan_in_local_frame_without_reindexing(
    qt_application: QtWidgets.QApplication,
) -> None:
    plan = _plan()
    frame = HoneycombCoordinateFrame(
        origin_machine_mm=(20.0, 20.0),
        x_axis_machine=(0.0, 1.0),
        y_axis_machine=(-1.0, 0.0),
        width_mm=190.0,
        height_mm=190.0,
        provenance_digest="a" * 64,
    )
    dialog = JobPreviewDialog(
        plan,
        (0.0, 190.0, 0.0, 190.0),
        "local.gcode",
        coordinate_frame=frame,
    )
    dialog.show()
    qt_application.processEvents()

    powered_paths = [
        item.path()
        for key, item in dialog.canvas._items.items()
        if key[0] == "powered"
    ]
    assert powered_paths
    bounds = powered_paths[0].boundingRect()
    # Machine (20,20)->(80,20)->(80,40) becomes local
    # (0,0)->(0,-60)->(20,-60); scene Y is inverted for display.
    assert bounds.left() == pytest.approx(0.0)
    assert bounds.right() == pytest.approx(20.0)
    assert bounds.top() == pytest.approx(0.0)
    assert bounds.bottom() == pytest.approx(60.0)
    assert dialog.plan.moves is plan.moves
    assert [move.index for move in dialog.plan.moves] == [
        move.index for move in plan.moves
    ]
    dialog.set_elapsed(plan.moves[1].start_seconds + 0.1)
    assert "Honeycomb X0.000 Y-60.000" in dialog.move_label.full_text
    assert "Machine X80.000 Y20.000" in dialog.move_label.full_text

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_honeycomb_deferred_preview_keeps_start_here_move_identity(
    qt_application: QtWidgets.QApplication,
) -> None:
    plan = _plan()
    frame = HoneycombCoordinateFrame(
        origin_machine_mm=(20.0, 20.0),
        x_axis_machine=(0.0, 1.0),
        y_axis_machine=(-1.0, 0.0),
        width_mm=190.0,
        height_mm=190.0,
        provenance_digest="b" * 64,
    )
    dialog = JobPreviewDialog(
        plan,
        (0.0, 190.0, 0.0, 190.0),
        "local.gcode",
        defer_render=True,
        coordinate_frame=frame,
    )
    requested: list[int] = []
    dialog.startHereRequested.connect(requested.append)
    assert not dialog.run_button.isEnabled()

    # Drive the deferred slice directly so this exercises its separate path
    # builder without depending on event-loop timing.
    dialog.canvas.start_deferred_render()
    while dialog.canvas._building:
        dialog.canvas._build_slice()
    assert dialog.run_button.isEnabled()

    powered_path = next(
        item.path()
        for key, item in dialog.canvas._items.items()
        if key[0] == "powered"
    )
    bounds = powered_path.boundingRect()
    assert bounds.left() == pytest.approx(0.0)
    assert bounds.right() == pytest.approx(20.0)
    assert bounds.top() == pytest.approx(0.0)
    assert bounds.bottom() == pytest.approx(60.0)

    reviewed = plan.moves[1]
    elapsed = reviewed.start_seconds + 0.01
    dialog.set_elapsed(elapsed)
    assert dialog.canvas.move_at(elapsed) is reviewed
    fraction = 0.01 / reviewed.duration_seconds
    machine_x = reviewed.start_x + (reviewed.end_x - reviewed.start_x) * fraction
    machine_y = reviewed.start_y + (reviewed.end_y - reviewed.start_y) * fraction
    local_x, local_y = frame.machine_to_local(machine_x, machine_y)
    assert dialog.canvas._head_item.pos().x() == pytest.approx(local_x)
    assert dialog.canvas._head_item.pos().y() == pytest.approx(-local_y)
    dialog.start_here_button.click()

    assert requested == [reviewed.index]
    assert dialog.plan.moves is plan.moves

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
    assert layer.text(5) == "V +0 / R +0"
    assert "20.0% / S200" in layer.text(6)
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


def test_preview_shows_layer_air_status_and_exact_finalized_commands(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = JobPreviewDialog(
        _air_assist_plan(),
        (0.0, 100.0, 0.0, 100.0),
        "air.gcode",
    )
    dialog.show()
    qt_application.processEvents()

    assert dialog.layer_tree.topLevelItem(0).text(7) == "Air assist: On"
    assert dialog.air_assist_group is not None
    assert dialog.air_assist_list is not None
    command_rows = [
        dialog.air_assist_list.item(index).text()
        for index in range(dialog.air_assist_list.count())
    ]
    assert any(row.endswith("OFF · M9") for row in command_rows)
    assert any(row.endswith("ON · M8") for row in command_rows)

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_preview_exposes_every_air_command_when_sidebar_list_is_bounded(
    qt_application: QtWidgets.QApplication,
) -> None:
    plan = _air_assist_plan()
    seed = plan.air_assist_events[0]
    events = tuple(
        replace(
            seed,
            line_number=index + 1,
            enabled=bool(index % 2),
            command="M8" if index % 2 else "M9",
        )
        for index in range(102)
    )
    dialog = JobPreviewDialog(
        replace(plan, air_assist_events=events),
        (0.0, 100.0, 0.0, 100.0),
        "many-air-events.gcode",
    )
    dialog.show()
    qt_application.processEvents()

    assert dialog.air_assist_list is not None
    assert dialog.air_assist_list.count() == 101
    assert dialog.air_assist_list.item(100).text() == "… 2 more exact event(s)"
    assert dialog.air_assist_view_all_button is not None

    dialog.air_assist_view_all_button.click()
    qt_application.processEvents()

    assert dialog.air_assist_full_dialog is not None
    assert dialog.air_assist_full_text is not None
    rows = dialog.air_assist_full_text.toPlainText().splitlines()
    assert len(rows) == 102
    assert rows[0] == "Line 1 · OFF · M9"
    assert rows[-1] == "Line 102 · ON · M8"

    dialog.air_assist_full_dialog.close()
    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()


def test_preview_reports_layer_power_correction_metadata(
    qt_application: QtWidgets.QApplication,
) -> None:
    text = "\n".join(
        [
            "G21",
            "G90",
            "M5",
            e3_metadata_line(
                "layer",
                {
                    "id": "corrected",
                    "name": "Corrected",
                    "color": "#185CFF",
                    "vector_power_correction": -25,
                    "raster_power_correction": 40,
                },
            ),
            "G0 X10 Y10 F2000",
            "M4 S200",
            "G1 X40 Y10 F1000 S180",
            "M5",
        ]
    )
    dialog = JobPreviewDialog(
        build_job_plan(text, power_max=1000),
        (0.0, 100.0, 0.0, 100.0),
        "corrected.gcode",
    )
    dialog.show()
    qt_application.processEvents()

    row = dialog.layer_tree.topLevelItem(0)
    assert row.text(5) == "V -25 / R +40"
    assert row.text(6) == "18.0% / S180"

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
            header.sectionViewportPosition(7) + header.sectionSize(7)
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
    progress = JobProgressWidget()
    progress.set_prepared_job(
        "14 paths · estimated 42 s",
        power_percent=20.0,
        controller_power=200.0,
    )

    progress.set_job_status(None)

    assert "max power 20.0% / S200" in progress.toolTip()
    assert "Controller idle · no job started" in progress.toolTip()
    assert progress.progress.format() == "Execution 0%"
    progress.set_job_status(
        {
            "running": True,
            "name": "grid.gcode",
            "total_lines": 100,
            "completed_lines": 25,
        }
    )
    assert "max power 20.0% / S200" in progress.toolTip()
    assert progress.progress.format() == "Execution 25%"
    assert "25/100 lines" in progress.toolTip()

    progress.set_job_status(
        {
            "running": True,
            "phase": "draining",
            "name": "grid.gcode",
            "total_lines": 100,
            "completed_lines": 100,
        }
    )
    assert progress.progress.format() == "Finishing · motion"
    assert "waiting for queued motion to finish" in progress.toolTip()

    progress.set_job_status(
        {
            "running": True,
            "phase": "homing",
            "name": "grid.gcode",
            "total_lines": 100,
            "completed_lines": 100,
        }
    )
    assert progress.progress.format() == "Finishing · homing"
    assert "Toolpath complete · homing machine" in progress.toolTip()

    progress.close()
    progress.deleteLater()
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
    run_requests: list[bool] = []
    dialog.startHereRequested.connect(requested.append)
    dialog.runRequested.connect(lambda: run_requests.append(True))
    dialog.show()
    qt_application.processEvents()

    dialog.set_elapsed(plan.moves[1].start_seconds + 0.01)
    dialog.start_here_button.click()

    assert requested == [1]
    assert run_requests == []
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


def test_preview_index_polls_cooperative_cancellation() -> None:
    checks = 0

    def cancel_after_work_starts() -> bool:
        nonlocal checks
        checks += 1
        return checks >= 2

    with pytest.raises(JobPreviewPreparationCancelled, match="cancelled"):
        prepare_job_preview(
            _plan(),
            cancel_check=cancel_after_work_starts,
        )

    assert checks >= 2
    assert prepare_job_preview(
        _plan(),
        cancel_check=lambda: False,
    ) == prepare_job_preview(_plan())
