from __future__ import annotations

import numpy as np
import pytest

from laser_aligner.geometry.svg import Polyline
from laser_aligner.planning import (
    ArtifactMetadata,
    ControllerGeometryArtifact,
    CoordinateDomain,
    EncodedProgramArtifact,
    LayerOperation,
    NormalizedGeometryArtifact,
    OperationArtifact,
    PlacedGeometryArtifact,
    PlanningStage,
    RasterRow,
    SceneRevision,
)
from laser_aligner.project.model import CoordinateSpace, LayerMode, OperationLayer


def _scene() -> SceneRevision:
    return SceneRevision(
        project_id="project-stage-model",
        revision=7,
        coordinate_space=CoordinateSpace.MACHINE,
    )


def _metadata(
    stage: PlanningStage,
    domain: CoordinateDomain,
) -> ArtifactMetadata:
    return ArtifactMetadata(
        artifact_id=f"artifact-{stage.value}",
        scene_revision=_scene(),
        stage=stage,
        stage_version=1,
        coordinate_domain=domain,
        bounds_mm=(1.0, 2.0, 3.0, 4.0),
        statistics=(("path_count", 1),),
        provenance=("project.toolpath",),
    )


def _layer() -> OperationLayer:
    return OperationLayer(
        id="layer-stage-model",
        name="Stage Model",
        color="#123456",
        mode=LayerMode.LINE,
        speed_mm_min=1200.0,
        power_percent=20.0,
        passes=1,
        line_interval_mm=0.1,
        scan_angle_deg=0.0,
        overscan_percent=0.0,
        vector_power_correction=0.0,
        raster_power_correction=0.0,
        air_assist=False,
        output_enabled=True,
        visible=True,
        priority=0,
    )


def test_stage_artifact_requires_its_declared_coordinate_domain() -> None:
    metadata = _metadata(
        PlanningStage.NORMALIZED_GEOMETRY,
        CoordinateDomain.PROJECT,
    )
    artifact = NormalizedGeometryArtifact(metadata=metadata)

    assert artifact.metadata.coordinate_domain is CoordinateDomain.PROJECT

    wrong_domain = _metadata(
        PlanningStage.NORMALIZED_GEOMETRY,
        CoordinateDomain.CONTROLLER,
    )
    with pytest.raises(ValueError, match="Expected project coordinates"):
        NormalizedGeometryArtifact(metadata=wrong_domain)


def test_normalized_geometry_artifact_indexes_real_layer_paths() -> None:
    path = Polyline(
        np.asarray([[1.0, 2.0], [3.0, 4.0]], dtype=np.float64),
        closed=False,
        source_tag="normalized-line",
    )
    artifact = NormalizedGeometryArtifact(
        metadata=_metadata(
            PlanningStage.NORMALIZED_GEOMETRY,
            CoordinateDomain.PROJECT,
        ),
        layer_paths=(("layer-a", (path,)),),
    )

    assert artifact.paths_for_layer("layer-a")[0] is path
    assert artifact.paths_for_layer("missing") == ()


def test_normalized_geometry_artifact_rejects_duplicate_layer_ids() -> None:
    metadata = _metadata(
        PlanningStage.NORMALIZED_GEOMETRY,
        CoordinateDomain.PROJECT,
    )
    with pytest.raises(ValueError, match="layer IDs must be unique"):
        NormalizedGeometryArtifact(
            metadata=metadata,
            layer_paths=(("layer-a", ()), ("layer-a", ())),
        )


def test_operation_artifact_indexes_existing_layer_payload() -> None:
    path = Polyline(
        np.asarray([[0.0, 0.0], [4.0, 0.0]], dtype=np.float64),
        closed=False,
        source_tag="operation-line",
    )
    layer = _layer()
    operation = LayerOperation(layer=layer, paths=[path])
    artifact = OperationArtifact(
        metadata=_metadata(
            PlanningStage.OPERATIONS,
            CoordinateDomain.PROJECT,
        ),
        layers=(operation,),
    )

    selected = artifact.layer_for_id(layer.id)
    assert selected is operation
    assert selected.paths[0] is path
    assert artifact.layer_for_id("missing") is None


def test_operation_artifact_rejects_duplicate_layer_ids() -> None:
    layer = _layer()
    metadata = _metadata(
        PlanningStage.OPERATIONS,
        CoordinateDomain.PROJECT,
    )
    with pytest.raises(ValueError, match="layer IDs must be unique"):
        OperationArtifact(
            metadata=metadata,
            layers=(LayerOperation(layer=layer), LayerOperation(layer=layer)),
        )


def test_placed_geometry_artifact_indexes_machine_beam_paths() -> None:
    path = Polyline(
        np.asarray([[10.0, 20.0], [14.0, 20.0]], dtype=np.float64),
        closed=False,
        source_tag="placed-line",
    )
    signature = ("support-stage-model", 3, "a" * 64)
    artifact = PlacedGeometryArtifact(
        metadata=_metadata(
            PlanningStage.PLACED_GEOMETRY,
            CoordinateDomain.MACHINE_BEAM,
        ),
        layer_paths=(("layer-a", (path,)),),
        coordinate_frame_signature=signature,
    )

    assert artifact.paths_for_layer("layer-a")[0] is path
    assert artifact.paths_for_layer("missing") == ()
    assert artifact.coordinate_frame_signature == signature


def test_placed_geometry_artifact_rejects_duplicate_layer_ids() -> None:
    metadata = _metadata(
        PlanningStage.PLACED_GEOMETRY,
        CoordinateDomain.MACHINE_BEAM,
    )
    with pytest.raises(ValueError, match="layer IDs must be unique"):
        PlacedGeometryArtifact(
            metadata=metadata,
            layer_paths=(("layer-a", ()), ("layer-a", ())),
        )


def test_controller_geometry_artifact_indexes_corrected_paths_and_offset() -> None:
    path = Polyline(
        np.asarray([[8.0, 23.0], [12.0, 23.0]], dtype=np.float64),
        closed=False,
        source_tag="controller-line",
    )
    artifact = ControllerGeometryArtifact(
        metadata=_metadata(
            PlanningStage.CONTROLLER_GEOMETRY,
            CoordinateDomain.CONTROLLER,
        ),
        layer_paths=(("layer-a", (path,)),),
        spot_offset_mm=(2.0, -3.0),
    )

    assert artifact.paths_for_layer("layer-a")[0] is path
    assert artifact.paths_for_layer("missing") == ()
    assert artifact.spot_offset_mm == (2.0, -3.0)


def test_controller_geometry_artifact_rejects_duplicate_layer_ids() -> None:
    metadata = _metadata(
        PlanningStage.CONTROLLER_GEOMETRY,
        CoordinateDomain.CONTROLLER,
    )
    with pytest.raises(ValueError, match="layer IDs must be unique"):
        ControllerGeometryArtifact(
            metadata=metadata,
            layer_paths=(("layer-a", ()), ("layer-a", ())),
        )


def test_artifact_metadata_rejects_invalid_versions_and_bounds() -> None:
    with pytest.raises(ValueError, match="stage_version"):
        ArtifactMetadata(
            artifact_id="artifact-invalid-version",
            scene_revision=_scene(),
            stage=PlanningStage.OPERATIONS,
            stage_version=0,
            coordinate_domain=CoordinateDomain.PROJECT,
        )

    with pytest.raises(ValueError, match="inverted"):
        ArtifactMetadata(
            artifact_id="artifact-invalid-bounds",
            scene_revision=_scene(),
            stage=PlanningStage.OPERATIONS,
            stage_version=1,
            coordinate_domain=CoordinateDomain.PROJECT,
            bounds_mm=(5.0, 0.0, 4.0, 1.0),
        )


def test_layer_operation_preserves_existing_mutable_planner_payload() -> None:
    path = Polyline(
        np.asarray([[0.0, 0.0], [2.0, 0.0]], dtype=np.float64),
        closed=False,
        source_tag="line",
    )
    row = RasterRow(
        points=np.asarray([[0.0, 1.0], [2.0, 1.0]], dtype=np.float64),
        spans=[path],
        source_tag="row",
    )
    operation = LayerOperation(layer=_layer())

    operation.paths.append(path)
    operation.raster_rows.append(row)

    assert operation.paths[0] is path
    assert operation.raster_rows[0] is row
    assert operation.dithered_image is False
    assert operation.raster_assets == ()


def test_encoded_program_artifact_keeps_exact_program_text() -> None:
    text = "G21\nG90\nM5\nM4 S200\nG1 X1 Y1 F1000\nM5\n"
    artifact = EncodedProgramArtifact(
        metadata=_metadata(
            PlanningStage.ENCODED_PROGRAM,
            CoordinateDomain.PROGRAM,
        ),
        text=text,
    )

    assert artifact.text == text



def test_encoded_program_artifact_requires_program_domain() -> None:
    with pytest.raises(ValueError, match="Expected program coordinates"):
        EncodedProgramArtifact(
            metadata=_metadata(
                PlanningStage.ENCODED_PROGRAM,
                CoordinateDomain.CONTROLLER,
            ),
            text="M5\n",
        )


def test_encoded_program_artifact_rejects_empty_text() -> None:
    with pytest.raises(ValueError, match="must not be empty"):
        EncodedProgramArtifact(
            metadata=_metadata(
                PlanningStage.ENCODED_PROGRAM,
                CoordinateDomain.PROGRAM,
            ),
            text="",
        )
