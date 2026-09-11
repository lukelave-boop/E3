from __future__ import annotations

# Qt must use the offscreen platform before desktop imports.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtTest, QtWidgets

from laser_aligner.desktop.context_bar import ContextPropertyBar
from laser_aligner.desktop.first_run import _CameraPage
from laser_aligner.desktop.panels import CameraPanel, LayerPanel
from laser_aligner.desktop.raster_vectorize_dialog import _slider_row
from laser_aligner.desktop.template_designer import GridTemplateDesignerDialog
from laser_aligner.desktop.text_dialog import VectorTextDialog
from laser_aligner.desktop.z_probe import ZProbePanel
from laser_aligner.project import ProjectDocument, SceneObject


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield app
    app.processEvents()


def _edit(
    app: QtWidgets.QApplication,
    owner: QtWidgets.QWidget,
    spin: QtWidgets.QAbstractSpinBox,
    text: str,
) -> None:
    owner.show()
    spin.setFocus()
    app.processEvents()
    spin.selectAll()
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Backspace)
    QtTest.QTest.keyClicks(spin, text)
    app.processEvents()


def _commit(app: QtWidgets.QApplication, spin: QtWidgets.QAbstractSpinBox) -> None:
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Tab)
    app.processEvents()


def _dispose(app: QtWidgets.QApplication, owner: QtWidgets.QWidget) -> None:
    owner.close()
    owner.deleteLater()
    app.processEvents()


def test_camera_resolution_can_be_replaced_below_minimum_prefix(
    qt_application: QtWidgets.QApplication,
) -> None:
    page = _CameraPage()
    changed: list[int] = []
    page.width.valueChanged.connect(changed.append)
    _edit(qt_application, page, page.width, "3")
    assert page.width.cleanText() == "3"
    assert page.width.value() == 1920
    assert changed == []
    QtTest.QTest.keyClicks(page.width, "840")
    _commit(qt_application, page.width)
    assert page.width.value() == 3840
    assert changed == [3840]
    _dispose(qt_application, page)


def test_probe_clearance_drafts_preserve_bounds_and_never_request_motion(
    qt_application: QtWidgets.QApplication,
) -> None:
    requests: list[object] = []
    panel = ZProbePanel(SimpleNamespace(), lambda *args, **kwargs: requests.append(args))
    panel.clearance.setValue(80)
    _edit(qt_application, panel, panel.clearance, "2")
    assert panel.clearance.cleanText() == "2"
    assert panel.clearance.value() == 80
    QtTest.QTest.keyClicks(panel.clearance, "0")
    _commit(qt_application, panel.clearance)
    assert panel.clearance.value() == 20

    _edit(qt_application, panel, panel.clearance, "100")
    assert panel.clearance.cleanText() == "100"
    _commit(qt_application, panel.clearance)
    assert panel.clearance.value() == 20
    assert (panel.clearance.minimum(), panel.clearance.maximum()) == (20, 80)
    assert requests == []
    _dispose(qt_application, panel)


def test_layer_settings_emit_only_committed_pass_count_and_bounded_power(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = LayerPanel()
    panel.set_document(ProjectDocument.new())
    changes: list[dict[str, object]] = []
    panel.layerEdited.connect(lambda _layer, payload: changes.append(payload))
    _edit(qt_application, panel, panel.passes_spin, "12")
    assert panel.passes_spin.value() == 1
    assert changes == []
    _commit(qt_application, panel.passes_spin)
    assert changes[-1]["passes"] == 12

    initial_power = panel.power_spin.value()
    _edit(qt_application, panel, panel.power_spin, "150")
    assert panel.power_spin.cleanText() == "150"
    assert panel.power_spin.value() == initial_power
    _commit(qt_application, panel.power_spin)
    assert panel.power_spin.value() == initial_power
    assert all(float(change["power_percent"]) <= 100 for change in changes)
    _dispose(qt_application, panel)


def test_template_dimension_draft_does_not_clamp_other_geometry(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = GridTemplateDesignerDialog(initial_spec={
        "name": "Draft dimensions", "rows": 2, "columns": 3,
        "width_mm": 300, "height_mm": 50, "corner_radius_mm": 10,
        "spacing_mode": "gap", "horizontal_spacing_mm": 1, "vertical_spacing_mm": 1,
    })
    _edit(qt_application, dialog, dialog.width_spin, "2")
    assert dialog.width_spin.value() == 300
    assert dialog.corner_radius_spin.value() == 10
    assert dialog.corner_radius_spin.maximum() == 25
    QtTest.QTest.keyClicks(dialog.width_spin, "0")
    _commit(qt_application, dialog.width_spin)
    assert dialog.spec()["width_mm"] == 20
    assert dialog.corner_radius_spin.value() == 10
    assert dialog.corner_radius_spin.maximum() == 10
    _dispose(qt_application, dialog)


def test_template_count_preview_waits_for_complete_integer(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = GridTemplateDesignerDialog()
    previous = dialog.spec()["cut_count"]
    _edit(qt_application, dialog, dialog.columns_spin, "12")
    assert dialog.spec()["cut_count"] == previous
    _commit(qt_application, dialog.columns_spin)
    assert dialog.spec()["cut_count"] == 12 * dialog.rows_spin.value()
    _dispose(qt_application, dialog)


def test_text_height_updates_auto_bridge_only_after_commit(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = VectorTextDialog()
    dialog.height_spin.setValue(100)
    old_bridge = dialog.bridge_spin.value()
    _edit(qt_application, dialog, dialog.height_spin, "25")
    assert dialog.options().height_mm == 100
    assert dialog.bridge_spin.value() == old_bridge
    _commit(qt_application, dialog.height_spin)
    assert dialog.options().height_mm == 25
    assert dialog.bridge_spin.value() < old_bridge
    _dispose(qt_application, dialog)


def test_raster_slider_does_not_rewrite_numeric_draft(
    qt_application: QtWidgets.QApplication,
) -> None:
    owner, slider, spin = _slider_row(0, 255, 128)
    _edit(qt_application, owner, spin, "200")
    assert spin.cleanText() == "200"
    assert slider.value() == 128
    _commit(qt_application, spin)
    assert spin.value() == slider.value() == 200
    slider.setValue(100)
    assert spin.value() == 100
    _dispose(qt_application, owner)


def test_context_scale_emits_transform_after_completed_percentage(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = ProjectDocument.new()
    shape = SceneObject.ellipse(document.active_layer_id, width_mm=20, height_mm=10)
    bar = ContextPropertyBar()
    bar.set_selection([shape], document)
    changes: list[object] = []
    bar.transformEdited.connect(lambda _object_id, transform: changes.append(transform))
    _edit(qt_application, bar, bar.scale_x_spin, "25")
    assert changes == []
    assert bar.width_spin.value() == 20
    _commit(qt_application, bar.scale_x_spin)
    assert changes[-1].width_mm == pytest.approx(5)
    _dispose(qt_application, bar)


@pytest.mark.parametrize("button_name", ["mirror_x", "mirror_y"])
def test_context_flip_commits_pending_position_before_action(
    qt_application: QtWidgets.QApplication,
    button_name: str,
) -> None:
    document = ProjectDocument.new()
    shape = SceneObject.ellipse(document.active_layer_id, width_mm=20, height_mm=10)
    bar = ContextPropertyBar()
    bar.set_selection([shape], document)
    changes: list[object] = []
    bar.transformEdited.connect(lambda _object_id, transform: changes.append(transform))
    _edit(qt_application, bar, bar.x_spin, "25")
    QtTest.QTest.mouseClick(getattr(bar, button_name), QtCore.Qt.MouseButton.LeftButton)
    assert changes[-1].x_mm == 25
    assert getattr(changes[-1], button_name) is True
    _dispose(qt_application, bar)


def test_context_aspect_lock_captures_completed_dimension(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = ProjectDocument.new()
    shape = SceneObject.ellipse(document.active_layer_id, width_mm=20, height_mm=10)
    bar = ContextPropertyBar()
    bar.set_selection([shape], document)
    _edit(qt_application, bar, bar.width_spin, "40")
    QtTest.QTest.mouseClick(bar.aspect_lock, QtCore.Qt.MouseButton.LeftButton)
    assert bar.width_spin.value() == 40
    assert bar._locked_aspect_ratio == 4
    _dispose(qt_application, bar)


def test_template_save_click_includes_pending_count(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = GridTemplateDesignerDialog()
    dialog.name_edit.setText("Save pending count")
    saved: list[dict[str, object]] = []
    dialog.saveRequested.connect(saved.append)
    _edit(qt_application, dialog, dialog.columns_spin, "12")
    QtTest.QTest.mouseClick(dialog.save_button, QtCore.Qt.MouseButton.LeftButton)
    assert saved[-1]["columns"] == 12
    _dispose(qt_application, dialog)


@pytest.mark.parametrize("button_name", ["apply_focus_button", "save_focus_button"])
def test_camera_action_click_includes_pending_focus(
    qt_application: QtWidgets.QApplication,
    button_name: str,
) -> None:
    panel = CameraPanel()
    panel.set_status({"connected": True})
    panel.set_focus_controls({"focus_absolute": 40, "focus_auto": 0})
    requested: list[tuple[bool, int]] = []
    panel.focusApplyRequested.connect(lambda automatic, value: requested.append((automatic, value)))
    panel.focusSaveRequested.connect(lambda automatic, value: requested.append((automatic, value)))
    _edit(qt_application, panel, panel.focus_spin, "85")
    QtTest.QTest.mouseClick(getattr(panel, button_name), QtCore.Qt.MouseButton.LeftButton)
    assert requested == [(False, 85)]
    _dispose(qt_application, panel)


def test_probe_action_click_includes_pending_clearance_without_hardware(
    qt_application: QtWidgets.QApplication,
) -> None:
    callbacks: list[object] = []
    requested: list[dict[str, object]] = []
    machine = SimpleNamespace(probe_z=lambda _operation, **kwargs: requested.append(kwargs))
    panel = ZProbePanel(
        SimpleNamespace(machine=machine),
        lambda _title, callback, *_args, **_kwargs: callbacks.append(callback),
    )
    panel.confirm.setChecked(True)
    _edit(qt_application, panel, panel.clearance, "40")
    QtTest.QTest.mouseClick(panel.reference, QtCore.Qt.MouseButton.LeftButton)
    callbacks[-1]()
    assert requested[-1]["clearance_z_mm"] == 40
    _dispose(qt_application, panel)
