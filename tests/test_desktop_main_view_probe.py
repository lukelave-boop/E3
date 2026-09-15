from __future__ import annotations

import copy
import os
from dataclasses import asdict, replace
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.calibration.bed import BedResidualMesh
from laser_aligner.calibration.support import HoneycombCoordinateFrame
from laser_aligner.desktop import main_view_probe
from laser_aligner.desktop.main_view_probe import MainViewProbe
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.errors import CalibrationError
from laser_aligner.project import Bounds
from tests.test_focus_click_mapping import mapped_context as mapped_context


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


class Controller:
    def __init__(self, context):
        self.runtime = SimpleNamespace(context=context, settings=context.settings)
        self._camera_source_generation = 4
        self.coordinate_space = "machine"
        self.review = False
        self.frame = None

    def signature(self):
        return (self.coordinate_space,
                None if self.frame is None else self.frame.provenance_signature,
                self.runtime.context.bed_mapping_digest())

    def review_signature_is_current(self, signature):
        return signature == self.signature()

    def _camera_review_active(self):
        return self.review


@pytest.fixture
def harness(app, mapped_context, monkeypatch):
    context, _ = mapped_context
    controller = Controller(context)
    monkeypatch.setattr(context, "current_honeycomb_coordinate_frame", lambda: controller.frame)
    clock = SimpleNamespace(value=100.0)
    monkeypatch.setattr(main_view_probe, "time", SimpleNamespace(monotonic=lambda: clock.value))
    workspace = WorkspaceView(Bounds(0.0, 0.0, 200.0, 200.0))
    workspace.resize(900, 700)
    workspace.show()
    app.processEvents()
    probe = MainViewProbe(controller, workspace, workspace)
    probe.begin()
    probe.set_selection_enabled(True)
    result = SimpleNamespace(context=context, controller=controller, workspace=workspace,
                             probe=probe, clock=clock, app=app)
    publish(result)
    yield result
    probe.end()
    workspace.close()
    workspace.deleteLater()
    app.processEvents()


def publish(harness, *, evidence=True, age=0.1, dpr=1.0, **changes):
    view, context = harness.workspace, harness.context
    area = view.workspace_scene.work_area
    ppm = context.settings.calibration.bed.pixels_per_mm
    width, height = max(1, round(area.width * ppm)), max(1, round(area.height * ppm))
    image = QtGui.QImage(width, height, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("#a09080"))
    image.setDevicePixelRatio(dpr)
    metadata = {
        "width": 440, "height": 440, "source_width": 440, "source_height": 440,
        "source_mode": None, "received_monotonic": harness.clock.value,
        "frame_age_seconds": age, "camera_settings": asdict(context.camera.settings),
        "review_signature": harness.controller.signature(),
        "source_generation": harness.controller._camera_source_generation,
        "lens_model_id": context.lens.model.model_id, "pixels_per_mm": ppm,
        "camera_image_area": {key: float(getattr(area, key)) for key in ("x_min", "x_max", "y_min", "y_max")},
        "corrected_width": width, "corrected_height": height,
        **changes,
    }
    view.set_camera_image(image, image_area=area, pixels_per_mm=ppm,
                          focus_frame_metadata=metadata if evidence else None)
    return metadata


def select(harness, x=100.0, y=100.0):
    point = harness.workspace.viewportTransform().map(QtCore.QPointF(x, -y))
    harness.probe._select_point(point)
    return harness.probe.selection_snapshot()


def map_selection(harness, selection):
    return harness.context.focus_probe_target(
        selection["image_x"], selection["image_y"],
        source_image_size=[selection["width"], selection["height"]],
        frame_age_seconds=selection["frame_age_seconds"], frame_metadata=selection,
    )


def test_main_view_click_reuses_guarded_raw_mapper_without_camera_or_machine_access(harness):
    selected = []
    harness.probe.pointSelected.connect(selected.append)
    result = select(harness, 40.0, 140.0)
    assert result["main_view"] is True and result["fresh"] is True
    assert (result["image_x"], result["image_y"]) == pytest.approx((100.0, 140.0))
    assert map_selection(harness, result)["target_machine_xy_mm"] == pytest.approx((40.0, 140.0))
    assert len(selected) == 1
    assert not harness.context.machine.status()["connected"]
    assert not hasattr(harness.probe, "_worker")


def test_inverse_mapping_matches_lens_registration_and_residual_mesh(harness):
    context = harness.context
    context.lens._model = replace(context.lens.model, distortion=np.array([0.08, 0.01, 0, 0, 0]), model_id="")
    context.bed.calibration.provenance = context._bed_provenance()
    context.bed.apply_registration_translation(2.0, -3.0)
    context.bed.calibration.residual_mesh = BedResidualMesh(
        np.array([0.0, 200.0]), np.array([0.0, 200.0]),
        np.array([[[0.2, -0.1], [0.7, -0.1]], [[0.2, -0.5], [0.7, -0.5]]]),
        2.0, 0.05, 0.1,
    )
    publish(harness)
    selection = select(harness, 45.25, 132.5)
    target = map_selection(harness, selection)
    assert target["target_machine_xy_mm"] == pytest.approx((45.25, 132.5), abs=1e-5)


def test_inverse_click_uses_the_material_mapper_that_rendered_the_view(harness, monkeypatch):
    context = harness.context
    material = copy.copy(context.bed)
    material._calibration = copy.deepcopy(context.bed.calibration)
    material.calibration.registration_x_mm = 2.25
    material.calibration.registration_y_mm = -3.5
    monkeypatch.setattr(context, "material_preview_mapper", lambda: material)
    publish(harness)
    selection = select(harness, 45.25, 132.5)
    expected = context.lens.model.distort_points(np.asarray([material.mm_to_image(45.25, 132.5)]))[0]
    assert (selection["image_x"], selection["image_y"]) == pytest.approx(expected)
    assert map_selection(harness, selection)["target_machine_xy_mm"] == pytest.approx((45.25, 132.5), abs=1e-5)
    assert context.bed.calibration.registration_x_mm == 0


def test_honeycomb_local_selection_uses_displayed_rigid_pose(harness):
    harness.controller.coordinate_space = "honeycomb_local"
    harness.controller.frame = HoneycombCoordinateFrame(
        (150.0, 20.0), (0.0, 1.0), (-1.0, 0.0), 100.0, 100.0, "a" * 64,
    )
    harness.workspace.set_work_area(Bounds(0.0, 0.0, 100.0, 100.0))
    publish(harness)
    selection = select(harness, 40.0, 50.0)
    assert map_selection(harness, selection)["target_machine_xy_mm"] == pytest.approx((100.0, 60.0))


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
def test_pan_zoom_dpi_and_pixel_center_registration_preserve_target(harness, dpr):
    harness.workspace.set_work_area(Bounds(20.0, 30.0, 170.0, 160.0))
    publish(harness, dpr=dpr)
    view = harness.workspace
    view.zoom_by(1.7)
    view.horizontalScrollBar().setValue(view.horizontalScrollBar().value() + 53)
    view.verticalScrollBar().setValue(view.verticalScrollBar().value() - 31)
    selection = select(harness, 80.25, 111.75)
    assert map_selection(harness, selection)["target_machine_xy_mm"] == pytest.approx((80.25, 111.75))
    assert view._camera_item.pixmap().devicePixelRatio() == 1.0
    assert view._camera_item.offset() == QtCore.QPointF(-0.5, -0.5)
    assert harness.probe._marker.pos() == QtCore.QPointF(80.25, -111.75)
    marker_rect = harness.probe._marker.boundingRect()
    view.zoom_by(1.3)
    assert harness.probe._marker.boundingRect() == marker_rect
    assert harness.probe.selection_snapshot()["image_x"] == selection["image_x"]


def test_selection_consumes_left_click_without_editing_objects_or_starting_other_pick(harness):
    view = harness.workspace
    view.set_creation_tool("rectangle")
    committed = []
    picked = []
    view.rectangleDrawCommitted.connect(lambda *args: committed.append(args))
    view.pointPicked.connect(lambda *args: picked.append(args))
    start = view.mapFromScene(QtCore.QPointF(80.0, -100.0))
    end = view.mapFromScene(QtCore.QPointF(120.0, -80.0))
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
    QtTest.QTest.mouseMove(view.viewport(), end)
    QtTest.QTest.mouseRelease(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=end)
    assert harness.probe.selection_snapshot() is not None
    assert view.creation_tool == "rectangle"
    assert committed == [] and picked == []
    assert view._rectangle_anchor_mm is None
    harness.probe.set_selection_enabled(False)
    QtTest.QTest.mousePress(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=start)
    QtTest.QTest.mouseRelease(view.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=end)
    assert len(committed) == 1


@pytest.mark.parametrize("x,y", [(-10.0, 100.0), (210.0, 100.0), (100.0, -10.0), (100.0, 210.0)])
def test_black_margins_do_not_select_or_replace_a_point(harness, x, y):
    original = select(harness)
    assert select(harness, x, y) == original


def test_identical_live_frames_refresh_selection_but_frozen_image_expires(harness):
    original = select(harness)
    harness.clock.value += 1.0
    publish(harness)
    current = harness.probe.selection_snapshot()
    assert current["fresh"] is True
    assert current["image_x"] == original["image_x"]
    assert current["received_monotonic"] == harness.clock.value
    harness.clock.value += 2.0
    assert harness.probe.current_frame_fresh() is False
    invalidated = []
    harness.probe.selectionInvalidated.connect(invalidated.append)
    harness.probe._check_selection()
    assert harness.probe.selection_snapshot() is None
    assert invalidated and not harness.probe._marker.isVisible()


@pytest.mark.parametrize("change", [
    "clear_image", "no_evidence", "hidden", "transparent", "generation", "review", "camera_settings",
    "lens", "mapping", "image_transform", "ppm", "suspended",
])
def test_changed_or_unavailable_display_cannot_retain_a_target(harness, change):
    select(harness)
    probe, view, context, controller = harness.probe, harness.workspace, harness.context, harness.controller
    if change == "clear_image":
        view.set_camera_image(None)
    elif change == "no_evidence":
        publish(harness, evidence=False)
    elif change == "hidden":
        view.hide()
    elif change == "transparent":
        view.set_camera_opacity(0.0)
    elif change == "generation":
        controller._camera_source_generation += 1
    elif change == "review":
        controller.review = True
    elif change == "camera_settings":
        context.camera.settings.controls["focus_absolute"] = 20
    elif change == "lens":
        context.lens._model = replace(context.lens.model, distortion=np.array([0.01, 0, 0, 0, 0]), model_id="")
    elif change == "mapping":
        context.bed.apply_registration_translation(0.5, 0.0)
    elif change == "image_transform":
        view._camera_item.setPos(5.0, -200.0)
    elif change == "ppm":
        context.settings.calibration.bed.pixels_per_mm += 1
    elif change == "suspended":
        probe.end()
    assert probe.current_frame_fresh() is False
    probe._check_selection()
    assert probe.selection_snapshot() is None
    assert select(harness) is None


@pytest.mark.parametrize("field,value", [
    ("frame_age_seconds", None), ("frame_age_seconds", -0.1), ("frame_age_seconds", float("nan")),
    ("received_monotonic", 101.0), ("width", True), ("width", 441), ("source_width", 220),
    ("source_mode", "transcoded"), ("review_signature", None), ("review_signature", ()),
    ("corrected_width", 1), ("pixels_per_mm", 0.0), ("camera_image_area", {}),
])
def test_incomplete_or_mismatched_evidence_is_not_selectable(harness, field, value):
    publish(harness, **{field: value})
    assert not harness.probe.current_frame_fresh()
    assert select(harness) is None


def test_metadata_and_selection_are_defensive_copies(harness):
    metadata = publish(harness)
    metadata["source_generation"] = -1
    selection = select(harness)
    selection["camera_settings"]["controls"]["focus_absolute"] = -1
    assert harness.probe.current_frame_fresh()
    assert harness.probe.selection_snapshot()["camera_settings"] == asdict(harness.context.camera.settings)


def test_rejected_marker_remains_red_until_replaced_or_invalidated(harness):
    select(harness)
    harness.probe.reject_selection()
    assert harness.probe._marker.isVisible()
    assert harness.probe._marker.pen().color().name() == "#ef5350"
    publish(harness)
    assert harness.probe._marker.pen().color().name() == "#ef5350"
    select(harness, 110.0, 100.0)
    assert harness.probe._marker.pen().color().name() == "#ffcf40"
    harness.probe.clear_selection()
    assert not harness.probe._marker.isVisible()


@pytest.mark.parametrize("failure", ["calibration", "roundtrip", "outside_source"])
def test_inverse_mapping_failure_clears_previous_target_without_crashing(harness, monkeypatch, failure):
    select(harness)
    invalidated = []
    harness.probe.selectionInvalidated.connect(invalidated.append)
    if failure == "calibration":
        def fail(*args):
            raise CalibrationError("Bed map unavailable")
        monkeypatch.setattr(harness.context.bed, "mm_to_image", fail)
    elif failure == "roundtrip":
        original = harness.context.bed.image_to_mm
        monkeypatch.setattr(harness.context.bed, "image_to_mm", lambda *args: np.asarray(original(*args)) + 0.1)
    else:
        monkeypatch.setattr(type(harness.context.lens.model), "distort_points", lambda *args: np.array([[-1.0, 20.0]]))
    assert select(harness) is None
    assert invalidated


def test_honeycomb_pose_change_invalidates_selection_even_before_next_frame(harness):
    harness.controller.coordinate_space = "honeycomb_local"
    frame = HoneycombCoordinateFrame((0.0, 0.0), (1.0, 0.0), (0.0, 1.0), 200.0, 200.0, "a" * 64)
    harness.controller.frame = frame
    publish(harness)
    select(harness)
    harness.controller.frame = replace(frame, origin_machine_mm=(1.0, 2.0), provenance_digest="b" * 64)
    assert harness.probe.selection_snapshot()["fresh"] is False


def test_new_evidence_does_not_resurrect_an_invalidated_selection(harness):
    select(harness)
    original = copy.deepcopy(harness.probe._frame_metadata)
    publish(harness, evidence=False)
    harness.probe.update_frame(original)
    assert harness.probe.current_frame_fresh()
    assert harness.probe.selection_snapshot() is None


@pytest.mark.parametrize("change", ["no_evidence", "stale", "transparent", "end"])
def test_frame_invalidation_revokes_queued_authority_even_after_marker_cleared(harness, change):
    select(harness)
    harness.probe.clear_selection()
    invalidated = []
    harness.probe.selectionInvalidated.connect(invalidated.append)
    if change == "no_evidence":
        publish(harness, evidence=False)
    elif change == "stale":
        harness.clock.value += 2.0
        harness.probe._check_selection()
    elif change == "transparent":
        harness.workspace.set_camera_opacity(0.0)
        harness.probe._check_selection()
    else:
        harness.probe.end()
    assert len(invalidated) == 1


def test_matching_fresh_frame_does_not_revoke_queued_authority_without_marker(harness):
    select(harness)
    harness.probe.clear_selection()
    invalidated = []
    harness.probe.selectionInvalidated.connect(invalidated.append)
    harness.clock.value += 0.1
    publish(harness)
    assert invalidated == []


@pytest.mark.parametrize("replacement", ["unchanged", "fresh_frame", "plain_image"])
def test_daily_panel_real_viewport_click_requires_separate_guarded_move(harness, monkeypatch, replacement):
    from laser_aligner.desktop import laser_focus
    from laser_aligner.desktop.laser_focus import LaserFocusWorkspace
    from tests.test_desktop_laser_focus import FakeController, camera_result, status

    harness.probe.end()
    monkeypatch.setattr(laser_focus, "time", SimpleNamespace(monotonic=lambda: harness.clock.value))
    controller = FakeController()
    controller.runtime = SimpleNamespace(context=harness.context, settings=harness.context.settings)
    harness.context.machine = controller.machine
    controller._camera_source_generation = harness.controller._camera_source_generation
    controller.review_signature_is_current = harness.controller.review_signature_is_current
    controller._camera_review_active = harness.controller._camera_review_active
    controller.machine.payload = camera_result(surface=None)
    daily = LaserFocusWorkspace(controller, main_view=harness.workspace)
    try:
        daily.show()
        harness.app.processEvents()
        daily.coordinator._timer.stop()
        daily._camera_timer.stop()
        daily.set_machine_status(status())
        while controller.work:
            controller.complete()
        daily.panel.set_result(camera_result(surface=None))
        daily.panel.path_clear.setChecked(True)
        publish(harness)
        daily._camera_tick()
        assert daily.panel.position_probe.isEnabled()
        harness.workspace.set_creation_tool("rectangle")
        daily.panel.position_probe.click()
        point = harness.workspace.mapFromScene(QtCore.QPointF(100.0, -100.0))
        expected = harness.workspace.workspace_scene.scene_to_machine(harness.workspace.mapToScene(point))
        QtTest.QTest.mouseClick(harness.workspace.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=point)
        assert daily.panel.can_move_probe()
        assert daily.panel._camera_target["target_machine_xy_mm"] == pytest.approx(expected)
        assert harness.workspace._rectangle_anchor_mm is None
        assert not any(action == "position_probe" for action, _ in controller.machine.calls)
        daily.panel.position_probe.click()
        assert daily.bed_view.selection_snapshot() is None
        assert len(controller.work) == 1
        if replacement == "fresh_frame":
            publish(harness)
        elif replacement == "plain_image":
            publish(harness, evidence=False)
        controller.complete()
        moves = [arguments for action, arguments in controller.machine.calls if action == "position_probe"]
        if replacement == "plain_image":
            assert moves == []
            assert "Camera target changed" in daily.panel.message.text()
        else:
            assert len(moves) == 1
            assert moves[0]["value"] == pytest.approx(expected)
            assert moves[0]["confirmed"] is True
    finally:
        daily.shutdown(force=True)
        daily.close()
        daily.deleteLater()
        harness.app.processEvents()
