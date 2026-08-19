"""Explicit bounded caches for deterministic planning-stage payloads.

The cache is caller-owned and in-memory only. It stores reusable computation
payloads, never run-oriented artifact metadata or authorization state.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from threading import RLock

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
    placed_entries: int
    placed_hits: int
    placed_misses: int
    placed_evictions: int
    controller_entries: int
    controller_hits: int
    controller_misses: int
    controller_evictions: int


@dataclass(frozen=True, slots=True)
class _NormalizedGeometryCacheValue:
    paths: tuple[Polyline, ...]
    bounds_mm: BoundsMm | None


@dataclass(frozen=True, slots=True)
class _PlacedGeometryCacheValue:
    paths: tuple[Polyline, ...]
    bounds_mm: BoundsMm | None


@dataclass(frozen=True, slots=True)
class _ControllerGeometryCacheValue:
    paths: tuple[Polyline, ...]
    bounds_mm: BoundsMm | None


class PlanningCache:
    """Caller-owned bounded cache for reusable staged-planning payloads."""

    def __init__(
        self,
        *,
        max_normalized_entries: int = 128,
        max_placed_entries: int = 128,
        max_controller_entries: int = 128,
    ) -> None:
        if type(max_normalized_entries) is not int or max_normalized_entries < 1:
            raise ValueError("max_normalized_entries must be a positive integer")
        if type(max_placed_entries) is not int or max_placed_entries < 1:
            raise ValueError("max_placed_entries must be a positive integer")
        if type(max_controller_entries) is not int or max_controller_entries < 1:
            raise ValueError("max_controller_entries must be a positive integer")
        self._max_normalized_entries = max_normalized_entries
        self._max_placed_entries = max_placed_entries
        self._max_controller_entries = max_controller_entries
        self._lock = RLock()
        self._normalized: OrderedDict[str, _NormalizedGeometryCacheValue] = OrderedDict()
        self._normalized_hits = 0
        self._normalized_misses = 0
        self._normalized_evictions = 0
        self._placed: OrderedDict[str, _PlacedGeometryCacheValue] = OrderedDict()
        self._placed_hits = 0
        self._placed_misses = 0
        self._placed_evictions = 0
        self._controller: OrderedDict[
            str, _ControllerGeometryCacheValue
        ] = OrderedDict()
        self._controller_hits = 0
        self._controller_misses = 0
        self._controller_evictions = 0

    def get_normalized(
        self,
        dependency_digest: str,
    ) -> tuple[tuple[Polyline, ...], BoundsMm | None] | None:
        """Return an isolated copy of one cached normalized payload."""

        key = _require_sha256(dependency_digest)
        with self._lock:
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
        with self._lock:
            self._normalized.pop(key, None)
            self._normalized[key] = value
            while len(self._normalized) > self._max_normalized_entries:
                self._normalized.popitem(last=False)
                self._normalized_evictions += 1

    def get_placed(
        self,
        dependency_digest: str,
    ) -> tuple[tuple[Polyline, ...], BoundsMm | None] | None:
        """Return an isolated copy of one cached placed-geometry payload."""

        key = _require_sha256(dependency_digest)
        with self._lock:
            value = self._placed.pop(key, None)
            if value is None:
                self._placed_misses += 1
                return None
            self._placed[key] = value
            self._placed_hits += 1
            return _clone_paths(value.paths), value.bounds_mm

    def put_placed(
        self,
        dependency_digest: str,
        paths: tuple[Polyline, ...],
        bounds_mm: BoundsMm | None,
    ) -> None:
        """Store one placed-geometry payload by deterministic dependency digest."""

        key = _require_sha256(dependency_digest)
        value = _PlacedGeometryCacheValue(
            paths=_clone_paths(paths),
            bounds_mm=bounds_mm,
        )
        with self._lock:
            self._placed.pop(key, None)
            self._placed[key] = value
            while len(self._placed) > self._max_placed_entries:
                self._placed.popitem(last=False)
                self._placed_evictions += 1

    def get_controller(
        self,
        dependency_digest: str,
    ) -> tuple[tuple[Polyline, ...], BoundsMm | None] | None:
        """Return an isolated copy of one cached controller-geometry payload."""

        key = _require_sha256(dependency_digest)
        with self._lock:
            value = self._controller.pop(key, None)
            if value is None:
                self._controller_misses += 1
                return None
            self._controller[key] = value
            self._controller_hits += 1
            return _clone_paths(value.paths), value.bounds_mm

    def put_controller(
        self,
        dependency_digest: str,
        paths: tuple[Polyline, ...],
        bounds_mm: BoundsMm | None,
    ) -> None:
        """Store controller geometry by deterministic dependency digest."""

        key = _require_sha256(dependency_digest)
        value = _ControllerGeometryCacheValue(
            paths=_clone_paths(paths),
            bounds_mm=bounds_mm,
        )
        with self._lock:
            self._controller.pop(key, None)
            self._controller[key] = value
            while len(self._controller) > self._max_controller_entries:
                self._controller.popitem(last=False)
                self._controller_evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._normalized.clear()
            self._placed.clear()
            self._controller.clear()

    @property
    def stats(self) -> PlanningCacheStats:
        with self._lock:
            return PlanningCacheStats(
                normalized_entries=len(self._normalized),
                normalized_hits=self._normalized_hits,
                normalized_misses=self._normalized_misses,
                normalized_evictions=self._normalized_evictions,
                placed_entries=len(self._placed),
                placed_hits=self._placed_hits,
                placed_misses=self._placed_misses,
                placed_evictions=self._placed_evictions,
                controller_entries=len(self._controller),
                controller_hits=self._controller_hits,
                controller_misses=self._controller_misses,
                controller_evictions=self._controller_evictions,
            )


__all__ = ["PlanningCache", "PlanningCacheStats"]
