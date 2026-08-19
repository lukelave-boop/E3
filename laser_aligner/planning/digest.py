"""Deterministic fingerprints for staged planning inputs.

Digests are observational in this phase: they identify content but do not yet
control cache lookup, reuse, or selective recomputation.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

from .model import SceneRevision

if TYPE_CHECKING:
    from ..project.model import ProjectDocument


def _canonical_json_digest(payload: Any) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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

    return _canonical_json_digest(project_source_payload(document))


def project_scene_revision(document: ProjectDocument) -> SceneRevision:
    """Capture project identity, mutation generation, coordinates, and content."""

    return SceneRevision(
        project_id=document.id,
        revision=document.revision,
        coordinate_space=document.coordinate_space,
        source_digest=project_source_digest(document),
    )


__all__ = [
    "project_scene_revision",
    "project_source_digest",
    "project_source_payload",
]
