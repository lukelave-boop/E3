from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator
from pathlib import Path
from types import MethodType, SimpleNamespace

import cv2
import numpy as np
import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtWidgets

from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.panels import ObjectPanel
from laser_aligner.project import (
    CommandStack,
    LayerMode,
    ObjectKind,
    OperationLayer,
    PathCubicSegment,
    PathFillRule,
    ProjectDocument,
    RasterDetectionMode,
    RasterVectorizationOptions,
    SceneObject,
    Transform,
    load_project,
    object_polylines,
    read_raster_asset_payload,
    save_project,
    vectorize_raster_payload,
)


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _write_donut(path: Path) -> None:
    image = np.full((72, 96, 4), 255, dtype=np.uint8)
    cv2.circle(image, (48, 36), 26, (0, 0, 0, 255), -1)
    cv2.circle(image, (48, 36), 11, (255, 255, 255, 255), -1)
    assert cv2.imwrite(str(path), image)


def _result(path: Path):
    _write_donut(path)
    payload = read_raster_asset_payload(path)
    options = RasterVectorizationOptions(
        detection_mode=RasterDetectionMode.MANUAL_THRESHOLD,
        threshold=128,
        minimum_feature_area_mm2=0.01,
        smoothing_mm=0.05,
        simplification_tolerance_mm=0.08,
    )
    result = vectorize_raster_payload(
        payload,
        options,
        displayed_width_mm=48.0,
        displayed_height_mm=36.0,
    )
    return payload, options, result


class _WorkspaceHarness:
    def __init__(
        self,
        selected: list[str],
        identity: tuple[str, str] | None = None,
    ) -> None:
        self.selection = list(selected)
        self.identity = identity

    def selected_object_ids(self) -> list[str]:
        return list(self.selection)

    def select_objects(self, object_ids: list[str]) -> None:
        self.selection = list(object_ids)

    def raster_preview_identity_for_object(
        self,
        _object_id: str,
    ) -> tuple[str, str] | None:
        return self.identity


def _harness(
    document: ProjectDocument,
    source: SceneObject,
    *,
    identity: tuple[str, str] | None = None,
) -> SimpleNamespace:
    notices: list[str] = []
    errors: list[str] = []
    harness = SimpleNamespace(
        document=document,
        active_layer_id=source.layer_id,
        history=CommandStack(),
        workspace=_WorkspaceHarness([source.id], identity),
        show_notice=notices.append,
        show_error=errors.append,
        notices=notices,
        errors=errors,
    )
    harness._commit_raster_vectorization = MethodType(
        E3MainWindow._commit_raster_vectorization,
        harness,
    )
    return harness


def _source(document: ProjectDocument, path: Path) -> SceneObject:
    item = SceneObject(
        name="Donut logo",
        kind=ObjectKind.IMAGE,
        layer_id=document.active_layer_id,
        transform=Transform(
            87.5,
            64.25,
            48.0,
            36.0,
            rotation_deg=31.0,
            mirror_x=True,
            mirror_y=True,
        ),
        geometry={"asset": str(path.resolve())},
    )
    document.add_object(item)
    return item


def test_objects_panel_exposes_action_only_for_one_image(
    qt_application: QtWidgets.QApplication,
) -> None:
    document = ProjectDocument.new()
    image = SceneObject(
        name="Logo",
        kind=ObjectKind.IMAGE,
        layer_id=document.active_layer_id,
        geometry={"asset": "logo.png"},
    )
    rectangle = SceneObject.rectangle(document.active_layer_id)
    document.add_object(image)
    document.add_object(rectangle)
    panel = ObjectPanel()
    requested: list[str] = []
    panel.rasterVectorizeRequested.connect(requested.append)

    panel.set_document(document, [image.id])
    assert not panel.image_group.isHidden()
    assert panel.raster_vectorize_button.isEnabled()
    assert panel.raster_vectorize_button.text() == "Trace image to vectors…"
    panel.raster_vectorize_button.click()
    assert requested == [image.id]

    for selected in ([], [rectangle.id], [image.id, rectangle.id]):
        panel.set_selection(selected)
        assert panel.image_group.isHidden()
        assert not panel.raster_vectorize_button.isEnabled()

    panel.close()
    panel.deleteLater()


def test_dialog_cancellation_leaves_project_history_and_selection_unchanged(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "cancel-donut.png"
    payload, _options, _vector_result = _result(image_path)
    document = ProjectDocument.new()
    source = _source(document, image_path)
    harness = _harness(
        document,
        source,
        identity=(payload.identity.path, payload.identity.sha256),
    )
    before = document.to_dict()
    before_selection = list(harness.workspace.selection)
    before_layer = harness.active_layer_id

    class CancelDialog:
        def __init__(self, *_args, **_kwargs) -> None:
            pass

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Rejected

    monkeypatch.setattr(main_window_module, "RasterVectorizationDialog", CancelDialog)

    E3MainWindow.vectorize_raster_image(harness, source.id)

    assert document.to_dict() == before
    assert harness.history.depth == 0
    assert harness.workspace.selection == before_selection
    assert harness.active_layer_id == before_layer
    assert harness.errors == []
    assert harness.notices == []


def test_source_identity_is_rechecked_after_dialog_before_project_mutation(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "changed-during-dialog.png"
    payload, options, result = _result(image_path)
    document = ProjectDocument.new()
    source = _source(document, image_path)
    harness = _harness(
        document,
        source,
        identity=(payload.identity.path, payload.identity.sha256),
    )

    class ChangingDialog:
        def __init__(self, *_args) -> None:
            self.vectorization_result = result
            self.accepted_options = options
            self.source_handling = "replace"
            self.hide_source_after = False

        def exec(self):
            replacement = np.full((72, 96, 3), 127, dtype=np.uint8)
            assert cv2.imwrite(str(image_path), replacement)
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        main_window_module,
        "RasterVectorizationDialog",
        ChangingDialog,
    )

    E3MainWindow.vectorize_raster_image(harness, source.id)

    assert [item.id for item in document.objects] == [source.id]
    assert harness.history.depth == 0
    assert not harness.notices
    assert len(harness.errors) == 1
    assert "source changed while the dialog was open" in harness.errors[0]


def test_replace_workflow_preserves_frame_transform_holes_and_is_one_undo_step(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "replace-donut.png"
    _payload, options, result = _result(image_path)
    document = ProjectDocument.new()
    source = _source(document, image_path)
    source_transform = source.transform.to_dict()
    source_bounds = source.bounds().to_dict()
    original_layer_ids = [layer.id for layer in document.layers]
    harness = _harness(document, source)

    vector = E3MainWindow._commit_raster_vectorization(
        harness,
        source,
        result,
        options,
        source_handling="replace",
        hide_source_after=False,
    )

    assert harness.history.depth == 1
    assert harness.history.undo_text == "Vectorize raster image"
    assert [item.id for item in document.objects] == [vector.id]
    assert vector.kind is ObjectKind.PATH
    assert vector.transform.to_dict() == source_transform
    assert vector.bounds().to_dict() == source_bounds
    assert vector.transform.rotation_deg == pytest.approx(31.0)
    assert vector.transform.mirror_x is True
    assert vector.transform.mirror_y is True
    assert "polylines" not in vector.geometry
    native_path = vector.path_geometry()
    assert native_path.fill_rule is PathFillRule.EVENODD
    assert len(native_path.subpaths) == 2
    assert all(subpath.closed for subpath in native_path.subpaths)
    assert any(
        isinstance(segment, PathCubicSegment)
        for subpath in native_path.subpaths
        for segment in subpath.segments
    )
    assert native_path == result.project_path_geometry()
    assert vector.metadata["raster_vectorization_preview_flattened_points"] == (
        result.preview_flattened_point_count
    )
    assert vector.metadata["raster_vectorization_hierarchy"][1] == {
        "parent_index": 0,
        "depth": 1,
        "is_hole": True,
    }
    world_paths = object_polylines(vector)
    assert len(world_paths) == 2
    local_first = np.asarray(
        result.contours[0].native_subpath.start,
        dtype=np.float64,
    )
    scaled = local_first * np.asarray([-48.0, -36.0])
    angle = np.deg2rad(31.0)
    expected_first = np.asarray(
        [
            87.5 + scaled[0] * np.cos(angle) - scaled[1] * np.sin(angle),
            64.25 + scaled[0] * np.sin(angle) + scaled[1] * np.cos(angle),
        ]
    )
    assert world_paths[0].points[0] == pytest.approx(expected_first)
    assert cv2.pointPolygonTest(
        world_paths[0].points.astype(np.float32),
        tuple(float(value) for value in world_paths[1].points[0]),
        False,
    ) > 0
    output_layer = document.get_layer(vector.layer_id)
    assert output_layer.mode is LayerMode.LINE
    assert output_layer.power_percent == 0.0
    assert output_layer.output_enabled is False
    assert output_layer.name == "Donut logo trace"
    assert harness.active_layer_id == output_layer.id
    assert harness.workspace.selection == [vector.id]

    assert harness.history.undo()
    assert [item.id for item in document.objects] == [source.id]
    assert [layer.id for layer in document.layers] == original_layer_ids
    assert harness.active_layer_id == source.layer_id

    assert harness.history.redo()
    assert [item.id for item in document.objects] == [vector.id]
    restored_vector = document.get_object(vector.id)
    assert restored_vector.geometry == vector.geometry
    assert restored_vector.path_geometry() == native_path
    assert document.get_layer(vector.layer_id).output_enabled is False


@pytest.mark.parametrize(
    ("hide_source", "expected_visible"),
    [(False, True), (True, False)],
)
def test_keep_source_visibility_choice_and_undo_redo(
    tmp_path: Path,
    hide_source: bool,
    expected_visible: bool,
) -> None:
    image_path = tmp_path / f"keep-{hide_source}.png"
    _payload, options, result = _result(image_path)
    document = ProjectDocument.new()
    source = _source(document, image_path)
    harness = _harness(document, source)
    initial_layer_count = len(document.layers)

    vector = E3MainWindow._commit_raster_vectorization(
        harness,
        source,
        result,
        options,
        source_handling="keep",
        hide_source_after=hide_source,
    )

    assert [item.id for item in document.objects] == [source.id, vector.id]
    assert vector.transform.to_dict() == source.transform.to_dict()
    assert source.visible is expected_visible
    assert len(document.layers) == initial_layer_count + 1
    assert harness.active_layer_id == vector.layer_id
    assert harness.workspace.selection == [vector.id]
    assert harness.history.depth == 1

    assert harness.history.undo()
    assert [item.id for item in document.objects] == [source.id]
    assert source.visible is True
    assert len(document.layers) == initial_layer_count

    assert harness.history.redo()
    assert [item.id for item in document.objects] == [source.id, vector.id]
    assert source.visible is expected_visible


def test_safe_active_line_layer_is_reused_without_changing_its_authority(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "safe-layer.png"
    _payload, options, result = _result(image_path)
    safe_layer = OperationLayer(
        name="Reviewed zero-power line",
        mode=LayerMode.LINE,
        power_percent=0.0,
        output_enabled=False,
    )
    document = ProjectDocument(layers=[safe_layer])
    source = _source(document, image_path)
    harness = _harness(document, source)

    vector = E3MainWindow._commit_raster_vectorization(
        harness,
        source,
        result,
        options,
        source_handling="keep",
        hide_source_after=False,
    )

    assert len(document.layers) == 1
    assert vector.layer_id == safe_layer.id
    assert safe_layer.power_percent == 0.0
    assert safe_layer.output_enabled is False
    assert harness.active_layer_id == safe_layer.id
    assert harness.workspace.selection == [vector.id]


def test_saved_and_reopened_project_preserves_compound_vector_result(
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "saved-donut.png"
    _payload, options, result = _result(image_path)
    document = ProjectDocument.new(name="Raster vector project")
    source = _source(document, image_path)
    harness = _harness(document, source)
    vector = E3MainWindow._commit_raster_vectorization(
        harness,
        source,
        result,
        options,
        source_handling="replace",
        hide_source_after=False,
    )

    project_path = save_project(
        document,
        tmp_path / "raster-vector-result.e3laser",
        create_backup=False,
    )
    reopened = load_project(project_path)
    restored = reopened.get_object(vector.id)

    assert restored.kind is ObjectKind.PATH
    assert restored.transform.to_dict() == vector.transform.to_dict()
    assert restored.geometry == vector.geometry
    assert restored.metadata == vector.metadata
    layer = reopened.get_layer(restored.layer_id)
    assert (layer.mode, layer.power_percent, layer.output_enabled) == (
        LayerMode.LINE,
        0.0,
        False,
    )


def test_accepted_objects_workflow_is_offline_and_calls_no_runtime_service(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    image_path = tmp_path / "offline-donut.png"
    payload, options, result = _result(image_path)
    document = ProjectDocument.new()
    source = _source(document, image_path)
    harness = _harness(
        document,
        source,
        identity=(payload.identity.path, payload.identity.sha256),
    )

    class ForbiddenService:
        def __getattr__(self, name: str):
            raise AssertionError(f"Runtime or hardware method accessed: {name}")

    harness.runtime = ForbiddenService()
    harness.controller = ForbiddenService()
    harness.machine = ForbiddenService()

    class AcceptedDialog:
        def __init__(self, supplied_payload, width_mm, height_mm, _parent) -> None:
            assert supplied_payload.identity == payload.identity
            assert (width_mm, height_mm) == pytest.approx((48.0, 36.0))
            self.vectorization_result = result
            self.accepted_options = options
            self.source_handling = "keep"
            self.hide_source_after = True

        def exec(self):
            return QtWidgets.QDialog.DialogCode.Accepted

    monkeypatch.setattr(
        main_window_module,
        "RasterVectorizationDialog",
        AcceptedDialog,
    )

    E3MainWindow.vectorize_raster_image(harness, source.id)

    assert harness.errors == []
    assert harness.history.depth == 1
    assert source.visible is False
    vectors = [item for item in document.objects if item.kind is ObjectKind.PATH]
    assert len(vectors) == 1
    assert harness.workspace.selection == [vectors[0].id]
