"""Deterministic fingerprints for staged planning inputs.

Digests are observational in this phase: they identify content but do not yet
control cache lookup, reuse, or selective recomputation.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

import numpy as np

from .model import PlanningStage, SceneRevision

if TYPE_CHECKING:
    from ..geometry.svg import Polyline
    from ..project.model import ProjectDocument


def canonical_json_digest(payload: Any) -> str:
    """Hash one JSON-safe payload using a stable canonical representation."""

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def polyline_sequence_digest(paths: Iterable[Polyline]) -> str:
    """Hash ordered polyline geometry, closure state, and source tags exactly."""

    digest = hashlib.sha256()
    digest.update(b"e3-polyline-sequence-v1\0")
    count = 0
    for path in paths:
        points = np.ascontiguousarray(
            np.asarray(path.points, dtype="<f8").reshape(-1, 2)
        )
        if not np.isfinite(points).all():
            raise ValueError("Planning polyline digest requires finite coordinates")
        source = str(path.source_tag).encode("utf-8")
        digest.update(len(source).to_bytes(8, "big"))
        digest.update(source)
        digest.update(b"\x01" if path.closed else b"\x00")
        digest.update(len(points).to_bytes(8, "big"))
        digest.update(points.tobytes(order="C"))
        count += 1
    digest.update(count.to_bytes(8, "big"))
    return digest.hexdigest()


def stage_dependency_digest(
    stage: PlanningStage,
    stage_version: int,
    payload: Any,
) -> str:
    """Hash effective inputs for one versioned planning-stage computation."""

    if stage_version < 1:
        raise ValueError("Planning stage_version must be at least 1")
    return canonical_json_digest(
        {
            "digest_schema": "e3-planning-stage-dependency-v1",
            "stage": stage.value,
            "stage_version": stage_version,
            "payload": payload,
        }
    )


def project_source_payload(document: ProjectDocument) -> dict[str, Any]:
    """Return persisted project content that can affect planning.

    Project identity and mutation bookkeeping are carried separately by
    ``SceneRevision`` and therefore do not participate in the content digest.
    """

    payload = document.to_dict()
    for key in ("id", "created_at", "modified_at", "revision"):
        payload.pop(key, None)
    return payload


def project_source_digest(document: ProjectDocument) -> str:
    """Return a deterministic SHA-256 fingerprint of planning source content."""

    return canonical_json_digest(project_source_payload(document))


def project_scene_revision(document: ProjectDocument) -> SceneRevision:
    """Capture project identity, mutation generation, coordinates, and content."""

    return SceneRevision(
        project_id=document.id,
        revision=document.revision,
        coordinate_space=document.coordinate_space,
        source_digest=project_source_digest(document),
    )


__all__ = [
    "canonical_json_digest",
    "polyline_sequence_digest",
    "project_scene_revision",
    "project_source_digest",
    "project_source_payload",
    "stage_dependency_digest",
]
