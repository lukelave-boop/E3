from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402
import os
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.config import WorkArea
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.panels import TracePanel
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.project import Bounds


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def test_pick_color_button_drives_canvas_sample_and_updates_real_color(
    qt_application: QtWidgets.QApplication,
) -> None:
    area = Bounds(0.0, 0.0, 220.0, 220.0)
    workspace = WorkspaceView(area)
    workspace.resize(640, 520)
    workspace.show()
    workspace.fit_work_area()
    panel = TracePanel()
    panel.set_calibration_ready(True)
    panel.show()
    qt_application.processEvents()

    samples: list[tuple[float, float]] = []
    notices: list[str] = []
    select_action = QtGui.QAction()
    select_action.setCheckable(True)
    harness = SimpleNamespace(
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                bed=SimpleNamespace(calibration=object()),
            ),
            settings=SimpleNamespace(
                machine=SimpleNamespace(
                    work_area=WorkArea(0.0, 220.0, 0.0, 220.0)
                )
            ),
        ),
        document=SimpleNamespace(work_area=area),
        actions={"select_tool": select_action},
        workspace=workspace,
        trace_panel=panel,
        inspector_tabs=SimpleNamespace(select_panel=lambda _name: None),
        controller=SimpleNamespace(sample_trace_color=lambda x, y: samples.append((x, y))),
        show_notice=notices.append,
        show_error=lambda message: pytest.fail(message),
    )
    harness._activate_selection_tool = lambda show_message=False: (
        E3MainWindow._activate_selection_tool(harness, show_message=show_message)
    )
    harness._clear_template_preview = lambda show_message=False: None
    harness._work_area_signature = E3MainWindow._work_area_signature
    harness._reconcile_pristine_project_frame = lambda: False
    harness._require_project_machine_work_area_match = lambda: (
        E3MainWindow._require_project_machine_work_area_match(harness)
    )
    panel.pickColorRequested.connect(
        lambda: E3MainWindow._begin_trace_color_pick(harness)
    )
    workspace.pointPicked.connect(
        lambda x, y: E3MainWindow._trace_point_picked(harness, x, y)
    )

    panel.pick_color_button.click()
    qt_application.processEvents()

    assert workspace.point_pick_active
    assert panel.pick_color_button.text() == "Cancel color pick"
    assert "COLOR PICK ACTIVE" in panel.status_label.text()

    point = workspace.mapFromScene(
        workspace.workspace_scene.machine_to_scene(105.0, 115.0)
    )
    QtTest.QTest.mouseClick(
        workspace.viewport(),
        QtCore.Qt.MouseButton.LeftButton,
        QtCore.Qt.KeyboardModifier.NoModifier,
        point,
    )
    qt_application.processEvents()

    assert len(samples) == 1
    assert samples[0] == pytest.approx((105.0, 115.0), abs=0.5)
    assert panel.pick_color_button.text() == "Sampling…"
    assert not panel.pick_color_button.isEnabled()

    payload = {
        "hue": 91.0,
        "bgr": [153, 148, 142],
        "rgb": [142, 148, 153],
        "machine_x": samples[0][0],
        "machine_y": samples[0][1],
    }
    E3MainWindow._trace_color_ready(harness, payload)

    assert panel.pick_color_button.text() == "Pick color"
    assert panel.pick_color_button.isEnabled()
    assert panel.options()["target_bgr"] == [153, 148, 142]
    assert panel.mode_combo.currentData() == "color"
    assert "rgb(142,148,153)" in panel.color_swatch.styleSheet().replace(" ", "")

    panel.close()
    workspace.close()
    panel.deleteLater()
    workspace.deleteLater()
    qt_application.processEvents()
