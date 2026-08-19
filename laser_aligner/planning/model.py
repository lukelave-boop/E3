"""Typed, UI-neutral contracts for E3's staged planning pipeline.

This module intentionally contains data contracts only. Stage computation,
caching, and invalidation remain separate concerns and are introduced only after
byte-for-byte equivalence with the existing project planner is proven.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ..geometry.svg import Polyline
    from ..project.model import CoordinateSpace, OperationLayer
    from ..project.raster_asset import RasterAssetIdentity, RasterAssetMetadata

BoundsMm = tuple[float, float, float, float]
StatisticValue = int | float | str | bool
LayerPaths = tuple[tuple[str, tuple["Polyline", ...]], ...]


class PlanningStage(str, Enum):
    """Stable names for the behavior-preserving planning stages."""

    NORMALIZED_GEOMETRY = "normalized_geometry"
    OPERATIONS = "operations"
    PLACED_GEOMETRY = "placed_geometry"
    CONTROLLER_GEOMETRY = "controller_geometry"
    ENCODED_PROGRAM = "encoded_program"


class CoordinateDomain(str, Enum):
    """Coordinate authority carried by one planning artifact."""

    PROJECT = "project"
    MACHINE_BEAM = "machine_beam"
    CONTROLLER = "controller"
    PROGRAM = "program"


@dataclass(frozen=True, slots=True)
class SceneRevision:
    """Identity of the persistent project state entering planning."""

    project_id: str
    revision: int
    coordinate_space: CoordinateSpace
    source_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("Planning scene project_id must not be empty")
        if self.revision < 0:
            raise ValueError("Planning scene revision must not be negative")
        if self.source_digest is not None and not self.source_digest.strip():
            raise ValueError("Planning scene source_digest must not be blank")


@dataclass(frozen=True, slots=True)
class ArtifactMetadata:
    """Common provenance carried by every durable planning-stage artifact."""

    artifact_id: str
    scene_revision: SceneRevision
    stage: PlanningStage
    stage_version: int
    coordinate_domain: CoordinateDomain
    bounds_mm: BoundsMm | None = None
    warnings: tuple[str, ...] = ()
    statistics: tuple[tuple[str, StatisticValue], ...] = ()
    provenance: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.artifact_id.strip():
            raise ValueError("Planning artifact_id must not be empty")
        if self.stage_version < 1:
            raise ValueError("Planning stage_version must be at least 1")
        if self.bounds_mm is not None:
            if len(self.bounds_mm) != 4 or not all(
                math.isfinite(float(value)) for value in self.bounds_mm
            ):
                raise ValueError("Planning artifact bounds must contain four finite values")
            x_min, y_min, x_max, y_max = self.bounds_mm
            if x_min > x_max or y_min > y_max:
                raise ValueError("Planning artifact bounds are inverted")
        for key, value in self.statistics:
            if not key.strip():
                raise ValueError("Planning statistic names must not be empty")
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("Planning statistics must be finite")


def _require_stage(
    metadata: ArtifactMetadata,
    *,
    stage: PlanningStage,
    domain: CoordinateDomain,
) -> None:
    if metadata.stage is not stage:
        raise ValueError(
            f"Expected {stage.value} metadata, received {metadata.stage.value}"
        )
    if metadata.coordinate_domain is not domain:
        raise ValueError(
            f"Expected {domain.value} coordinates for {stage.value}, received "
            f"{metadata.coordinate_domain.value}"
        )


@dataclass(slots=True)
class RasterRow:
    """One constant-velocity scan row with zero or more powered spans."""

    points: np.ndarray
    spans: list[Polyline]
    source_tag: str


@dataclass(slots=True)
class RasterSource:
    """One bounded source shared by every image object in a generation."""

    metadata: RasterAssetMetadata
    image: np.ndarray | None = None
    identity: RasterAssetIdentity | None = None


@dataclass(slots=True)
class LayerOperation:
    """Current per-layer operation payload produced by project planning."""

    layer: OperationLayer
    paths: list[Polyline] = field(default_factory=list)
    raster_rows: list[RasterRow] = field(default_factory=list)
    dithered_image: bool = False
    raster_assets: tuple[RasterAssetIdentity, ...] = ()


@dataclass(frozen=True, slots=True)
class NormalizedGeometryArtifact:
    metadata: ArtifactMetadata
    layer_paths: LayerPaths = ()

    def __post_init__(self) -> None:
        _require_stage(
            self.metadata,
            stage=PlanningStage.NORMALIZED_GEOMETRY,
            domain=CoordinateDomain.PROJECT,
        )


@dataclass(frozen=True, slots=True)
class OperationArtifact:
    metadata: ArtifactMetadata
    layers: tuple[LayerOperation, ...] = ()

    def __post_init__(self) -> None:
        _require_stage(
            self.metadata,
            stage=PlanningStage.OPERATIONS,
            domain=CoordinateDomain.PROJECT,
        )


@dataclass(frozen=True, slots=True)
class PlacedGeometryArtifact:
    metadata: ArtifactMetadata
    layer_paths: LayerPaths = ()
    coordinate_frame_signature: tuple[str, int, str] | None = None

    def __post_init__(self) -> None:
        _require_stage(
            self.metadata,
            stage=PlanningStage.PLACED_GEOMETRY,
            domain=CoordinateDomain.MACHINE_BEAM,
        )


@dataclass(frozen=True, slots=True)
class ControllerGeometryArtifact:
    metadata: ArtifactMetadata
    layer_paths: LayerPaths = ()
    spot_offset_mm: tuple[float, float] = (0.0, 0.0)

    def __post_init__(self) -> None:
        _require_stage(
            self.metadata,
            stage=PlanningStage.CONTROLLER_GEOMETRY,
            domain=CoordinateDomain.CONTROLLER,
        )
        if not all(math.isfinite(float(value)) for value in self.spot_offset_mm):
            raise ValueError("Controller geometry spot offset must be finite")


@dataclass(frozen=True, slots=True)
class EncodedProgramArtifact:
    metadata: ArtifactMetadata
    text: str

    def __post_init__(self) -> None:
        _require_stage(
            self.metadata,
            stage=PlanningStage.ENCODED_PROGRAM,
            domain=CoordinateDomain.PROGRAM,
        )
        if not self.text:
            raise ValueError("Encoded planning program must not be empty")


__all__ = [
    "ArtifactMetadata",
    "ControllerGeometryArtifact",
    "CoordinateDomain",
    "EncodedProgramArtifact",
    "LayerOperation",
    "NormalizedGeometryArtifact",
    "OperationArtifact",
    "PlacedGeometryArtifact",
    "PlanningStage",
    "RasterRow",
    "RasterSource",
    "SceneRevision",
]
