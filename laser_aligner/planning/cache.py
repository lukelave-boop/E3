"""Explicit bounded caches for deterministic planning-stage payloads.

The cache is caller-owned and in-memory only. It stores reusable computation
payloads, never run-oriented artifact metadata or authorization state.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass

from ..geometry.svg import Polyline
from .model import BoundsMm


def _require_sha256(digest: str) -> str:
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        raise ValueError("Planning cache key must be a lowercase SHA-256 digest")
    return digest


def _clone_paths(paths: tuple[Polyline, ...]) -> tuple[Polyline, ...]:
    return tuple(
        Polyline(
            path.points.copy(),
            closed=path.closed,
            source_tag=path.source_tag,
        )
        for path in paths
    )


@dataclass(frozen=True, slots=True)
class PlanningCacheStats:
    normalized_entries: int
    normalized_hits: int
    normalized_misses: int
    normalized_evictions: int


@dataclass(frozen=True, slots=True)
class _NormalizedGeometryCacheValue:
    paths: tuple[Polyline, ...]
    bounds_mm: BoundsMm | None


class PlanningCache:
    """Caller-owned bounded cache for reusable staged-planning payloads."""

    def __init__(self, *, max_normalized_entries: int = 128) -> None:
        if type(max_normalized_entries) is not int or max_normalized_entries < 1:
            raise ValueError("max_normalized_entries must be a positive integer")
        self._max_normalized_entries = max_normalized_entries
        self._normalized: OrderedDict[str, _NormalizedGeometryCacheValue] = OrderedDict()
        self._normalized_hits = 0
        self._normalized_misses = 0
        self._normalized_evictions = 0

    def get_normalized(
        self,
        dependency_digest: str,
    ) -> tuple[tuple[Polyline, ...], BoundsMm | None] | None:
        """Return an isolated copy of one cached normalized payload."""

        key = _require_sha256(dependency_digest)
        value = self._normalized.pop(key, None)
        if value is None:
            self._normalized_misses += 1
            return None
        self._normalized[key] = value
        self._normalized_hits += 1
        return _clone_paths(value.paths), value.bounds_mm

    def put_normalized(
        self,
        dependency_digest: str,
        paths: tuple[Polyline, ...],
        bounds_mm: BoundsMm | None,
    ) -> None:
        """Store one normalized payload by deterministic dependency digest."""

        key = _require_sha256(dependency_digest)
        value = _NormalizedGeometryCacheValue(
            paths=_clone_paths(paths),
            bounds_mm=bounds_mm,
        )
        self._normalized.pop(key, None)
        self._normalized[key] = value
        while len(self._normalized) > self._max_normalized_entries:
            self._normalized.popitem(last=False)
            self._normalized_evictions += 1

    def clear(self) -> None:
        self._normalized.clear()

    @property
    def stats(self) -> PlanningCacheStats:
        return PlanningCacheStats(
            normalized_entries=len(self._normalized),
            normalized_hits=self._normalized_hits,
            normalized_misses=self._normalized_misses,
            normalized_evictions=self._normalized_evictions,
        )


__all__ = ["PlanningCache", "PlanningCacheStats"]
