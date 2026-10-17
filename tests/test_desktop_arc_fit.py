from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001
import copy
import math
import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtTest, QtWidgets

from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop import arc_fit_dialog as arc_fit_module
from laser_aligner.desktop.arc_fit_dialog import ArcFitDialog
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.panels import TransformPanel
from laser_aligner.project import (
    CommandStack,
    NativePathGeometry,
    ObjectKind,
    PathLineSegment,
    PathSubpath,
    ProjectDocument,
    SceneObject,
    Transform,
)
from laser_aligner.project.arc_fit import fit_arc_span


@pytest.fixture(scope="module")
def qt_application():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _source(document, *, closed=False, defect=False):
    points = []
    for index in range(41):
        angle = math.radians(10 + 150 * index / 40)
        radius = 20.18 if defect and index == 20 else 20.0
        points.append((radius * math.cos(angle) / 60, radius * math.sin(angle) / 40))
    arc = PathSubpath(points[0], tuple(PathLineSegment(point) for point in points[1:]), closed)
    hole = PathSubpath(
        (0.01, 0.01),
        (PathLineSegment((0.03, 0.01)), PathLineSegment((0.03, 0.03)), PathLineSegment((0.01, 0.03))),
        True,
    )
    source = SceneObject.native_path(
        document.active_layer_id,
        NativePathGeometry((arc, hole)),
        name="Curved edge",
        transform=Transform(80, 75, 60, 40, 31, True, False),
    )
    source.metadata["original_trace"] = {"keep": True}
    document.add_object(source)
    return source


def _harness(document, source):
    selected = [source.id]
    return SimpleNamespace(
        document=document,
        history=CommandStack(),
        workspace=SimpleNamespace(
            selected_object_ids=lambda: list(selected),
            select_objects=lambda values: selected.__setitem__(slice(None), values),
        ),
        show_notice=lambda text: notices.append(text),
        show_error=lambda text: errors.append(text),
        notices=(notices := []),
        errors=(errors := []),
    )


def test_shape_panel_fit_arc_selection_and_inherited_disabled_gates(qt_application):
    document = ProjectDocument.new()
    source = _source(document)
    image = SceneObject(kind=ObjectKind.IMAGE, layer_id=source.layer_id, geometry={"asset": "sample.png"})
    panel = TransformPanel()
    panel.set_document_layers(document)
    requests = []
    panel.fitArcRequested.connect(lambda: requests.append(True))
    panel.set_selection([source], document)
    assert panel.fit_arc_button.isEnabled()
    panel.fit_arc_button.click()
    assert requests == [True]
    panel.setEnabled(False)
    panel.set_selection([source], document)
    assert not panel.fit_arc_button.isEnabled()
    panel.setEnabled(True)
    for selected in ([], [image], [source, image]):
        panel.set_selection(selected, document)
        assert not panel.fit_arc_button.isEnabled()
    source.locked = True
    panel.set_selection([source], document)
    assert not panel.fit_arc_button.isEnabled()
    panel.close()


def test_preview_clicks_produce_physical_arc_and_cancel_does_not_edit(qt_application):
    document = ProjectDocument.new()
    source = _source(document)
    before = copy.deepcopy(source.to_dict())
    dialog = ArcFitDialog(source)
    dialog.show()
    qt_application.processEvents()
    assert not dialog.apply_button.isEnabled()
    assert dialog.start_spin.prefix() == "Start: "
    assert dialog.end_spin.prefix() == "End: "
    for vertex in (0, 40):
        position = dialog.view.mapFromScene(dialog.view._vertex_positions[0][vertex])
        QtTest.QTest.mouseClick(dialog.view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=position)
    assert dialog.start_spin.value() == 1
    assert dialog.end_spin.value() == 41
    assert dialog.apply_button.isEnabled()
    assert dialog.proposal.radius_mm == pytest.approx(20, abs=0.02)
    assert not dialog.view._selected.path().isEmpty()
    assert not dialog.view._proposal.path().isEmpty()
    # Scene transformation includes mirroring, anisotropic normalization, rotation,
    # and Y inversion, just as the committed workspace path does.
    point = dialog.view._vertex_positions[0][0]
    x = -60 * source.path_geometry().subpaths[0].start[0]
    y = -40 * source.path_geometry().subpaths[0].start[1]
    angle = math.radians(-31)
    assert point.x() == pytest.approx(x * math.cos(angle) - y * math.sin(angle))
    assert point.y() == pytest.approx(x * math.sin(angle) + y * math.cos(angle))
    assert source.to_dict() == before
    dialog.reject()
    assert dialog.accepted_fit is None
    assert source.to_dict() == before


def test_proposal_invalidated_by_invalid_endpoints_and_pending_edits(qt_application):
    document = ProjectDocument.new()
    dialog = ArcFitDialog(_source(document))
    dialog.start_spin.setValue(1)
    dialog.end_spin.setValue(41)
    assert dialog.apply_button.isEnabled()
    dialog.end_spin.setValue(1)
    assert dialog.proposal is None
    assert not dialog.apply_button.isEnabled()
    assert dialog.view._proposal.path().isEmpty()
    dialog.accept()
    assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted
    dialog.end_spin.setValue(41)
    dialog.end_spin.lineEdit().setText("1")
    dialog.accept()
    assert dialog.accepted_fit is None
    assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted
    dialog.reject()


def test_apply_accepts_current_preview_and_keeps_source_immutable(qt_application):
    document = ProjectDocument.new()
    source = _source(document)
    before = copy.deepcopy(source.to_dict())
    dialog = ArcFitDialog(source)
    dialog.start_spin.setValue(1)
    dialog.end_spin.setValue(41)
    dialog.apply_button.click()
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert dialog.accepted_fit is dialog.proposal
    assert source.to_dict() == before


def test_excessive_complexity_rejects_before_rendering(qt_application, monkeypatch):
    document = ProjectDocument.new()
    source = _source(document)
    monkeypatch.setattr(arc_fit_module, "MAX_ARC_FIT_SEGMENTS", 10)
    with pytest.raises(ValueError, match="too many edge segments"):
        ArcFitDialog(source)
    monkeypatch.setattr(main_window_module, "MAX_ARC_FIT_SEGMENTS", 10)
    monkeypatch.setattr(main_window_module, "ArcFitDialog", lambda *_args: pytest.fail("Complex path opened a dialog"))
    harness = _harness(document, source)
    E3MainWindow.fit_selected_arc(harness)
    assert harness.history.depth == 0
    assert "too many edge segments" in harness.notices[0]


def test_closed_outline_other_side_and_hole_choice(qt_application):
    document = ProjectDocument.new()
    dialog = ArcFitDialog(_source(document, closed=True))
    dialog.start_spin.setValue(1)
    dialog.end_spin.setValue(41)
    assert dialog.apply_button.isEnabled()
    assert dialog.proposal.geometry.subpaths[1] == dialog.geometry.subpaths[1]
    dialog.other_side.click()
    assert (dialog.start_spin.value(), dialog.end_spin.value()) == (41, 1)
    assert not dialog.apply_button.isEnabled()  # Opposite side is a straight closure.
    dialog.other_side.click()
    assert dialog.apply_button.isEnabled()
    dialog.outline_combo.setCurrentIndex(1)
    assert dialog.start_spin.value() == dialog.end_spin.value() == 0
    assert dialog.proposal is None
    assert not dialog.apply_button.isEnabled()
    dialog.reject()


def test_apply_one_history_entry_preserves_identity_hole_metadata_and_exact_undo(qt_application, monkeypatch):
    document = ProjectDocument.new()
    source = _source(document, closed=True)
    before = copy.deepcopy(source.to_dict())
    harness = _harness(document, source)
    result = fit_arc_span(source.path_geometry(), 0, 0, 40, width_mm=60, height_mm=40)

    class ReviewedDialog:
        def __init__(self, source, parent):
            self.accepted_fit = result

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(main_window_module, "ArcFitDialog", ReviewedDialog)
    E3MainWindow.fit_selected_arc(harness)
    after = copy.deepcopy(source.to_dict())
    assert harness.errors == harness.notices == []
    assert after != before
    assert harness.history.depth == 1
    assert harness.history.undo_text == "Fit arc"
    assert document.get_object(source.id) is source
    assert source.path_geometry().subpaths[1] == NativePathGeometry.from_dict(before["geometry"]).subpaths[1]
    assert source.metadata == before["metadata"]
    assert source.transform.to_dict() == before["transform"]
    assert harness.history.undo()
    assert source.to_dict() == before
    assert harness.history.redo()
    assert source.to_dict() == after


@pytest.mark.parametrize(
    "change", ["cancel", "source", "remove", "replace", "document", "selection", "freeze", "closing"]
)
def test_cancel_or_stale_review_cannot_commit(qt_application, monkeypatch, change):
    document = ProjectDocument.new()
    source = _source(document)
    harness = _harness(document, source)
    result = fit_arc_span(source.path_geometry(), 0, 0, 40, width_mm=60, height_mm=40)

    class ReviewedDialog:
        def __init__(self, source, parent):
            self.accepted_fit = result

        def exec(self):
            if change == "source":
                source.transform = source.transform.copy(width_mm=65)
            elif change == "remove":
                document.remove_object(source.id)
            elif change == "replace":
                document.remove_object(source.id)
                document.add_object(SceneObject.from_dict(source.to_dict()))
            elif change == "document":
                harness.document = ProjectDocument.new()
            elif change == "selection":
                harness.workspace.select_objects([])
            elif change == "freeze":
                harness._authoring_freeze_owner = 1
            elif change == "closing":
                harness._closing = True
            return (
                QtWidgets.QDialog.DialogCode.Rejected if change == "cancel" else QtWidgets.QDialog.DialogCode.Accepted
            )

    monkeypatch.setattr(main_window_module, "ArcFitDialog", ReviewedDialog)
    before_geometry = copy.deepcopy(source.geometry)
    E3MainWindow.fit_selected_arc(harness)
    assert harness.history.depth == 0
    assert source.geometry == before_geometry
    assert harness.errors == []
    assert bool(harness.notices) == (change != "cancel")


@pytest.mark.parametrize("gate", ["locked", "image", "multiple", "freeze", "closing"])
def test_action_rechecks_eligibility_before_opening(qt_application, monkeypatch, gate):
    document = ProjectDocument.new()
    source = _source(document)
    harness = _harness(document, source)
    if gate == "locked":
        source.locked = True
    elif gate == "image":
        source.kind = ObjectKind.IMAGE
    elif gate == "multiple":
        harness.workspace.select_objects([source.id, "another"])
    elif gate == "freeze":
        harness._authoring_freeze_owner = 1
    elif gate == "closing":
        harness._closing = True
    monkeypatch.setattr(
        main_window_module, "ArcFitDialog", lambda *_args: pytest.fail("Ineligible action opened a dialog")
    )
    E3MainWindow.fit_selected_arc(harness)
    assert harness.history.depth == 0
