from __future__ import annotations

# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from pathlib import Path

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for preview tests")

from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.desktop.job_preview import JobPreviewDialog
from laser_aligner.desktop.panels import JobPanel
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


def test_preview_scrubber_reports_explicit_power_and_coordinates(
    qt_application: QtWidgets.QApplication,
) -> None:
    plan = _plan()
    dialog = JobPreviewDialog(
        plan,
        (0.0, 100.0, 0.0, 100.0),
        "test.gcode",
    )
    dialog.show()
    qt_application.processEvents()

    dialog.set_elapsed(plan.moves[1].start_seconds + 0.1)

    assert "POWER 20.0% / S200" in dialog.move_label.text()
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
    )
    dialog.show()
    qt_application.processEvents()

    assert dialog.layer_tree.topLevelItemCount() == 1
    layer = dialog.layer_tree.topLevelItem(0)
    assert layer.text(1) == "Line 01 · Line"
    assert "20.0% / S200" in layer.text(4)
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

    panel.close()
    panel.deleteLater()
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
