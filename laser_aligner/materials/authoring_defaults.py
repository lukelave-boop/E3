from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..project.model import (
    DEFAULT_LAYER_COLORS,
    DEFAULT_OPERATION_PROFILES,
    LayerMode,
    OperationLayer,
)

_NEUTRAL_LINE_SPEED_MM_MIN = 1000.0
_CURATED_OPERATION_PROFILES = DEFAULT_OPERATION_PROFILES


class OperationDefaultSource(str, Enum):
    """Curated compatibility tier used for new-project operations."""

    EXACT_MACHINE_TOOL = "exact_machine_tool"
    TOOL_ONLY = "tool_only"
    UNIVERSAL = "universal"
    SAFE_NEUTRAL = "safe_neutral"

    @property
    def is_curated(self) -> bool:
        return self is not OperationDefaultSource.SAFE_NEUTRAL


@dataclass(frozen=True, slots=True)
class ResolvedOperationDefaults:
    """Detached authoring defaults for one new project."""

    layers: tuple[OperationLayer, ...]
    source: OperationDefaultSource
    notice: str | None = None


def _running_profile_id(value: object, label: str) -> str:
    if type(value) is not str or not value or value != value.strip():
        raise ValueError(f"{label} must be a non-empty stable profile ID")
    return value


def _curated_scope(
    profile: Mapping[str, Any],
    *,
    machine_profile_id: str,
    tool_head_profile_id: str,
) -> OperationDefaultSource | None:
    if "machine_profile_id" not in profile or "tool_head_profile_id" not in profile:
        raise ValueError(
            "Curated operation profiles must declare machine and tool-head scope"
        )
    machine_scope = profile["machine_profile_id"]
    tool_scope = profile["tool_head_profile_id"]
    for value, label in (
        (machine_scope, "machine_profile_id"),
        (tool_scope, "tool_head_profile_id"),
    ):
        if value is not None and (type(value) is not str or not value):
            raise ValueError(f"Curated {label} must be null or a stable profile ID")
    if machine_scope is not None and tool_scope is None:
        raise ValueError(
            "A machine-scoped curated operation profile must also specify a "
            "tool-head profile"
        )
    if machine_scope is None and tool_scope is None:
        return OperationDefaultSource.UNIVERSAL
    if machine_scope is None:
        return (
            OperationDefaultSource.TOOL_ONLY
            if tool_scope == tool_head_profile_id
            else None
        )
    if (
        machine_scope == machine_profile_id
        and tool_scope == tool_head_profile_id
    ):
        return OperationDefaultSource.EXACT_MACHINE_TOOL
    return None


def _curated_layers(
    profiles: tuple[Mapping[str, Any], ...],
) -> tuple[OperationLayer, ...]:
    layers: list[OperationLayer] = []
    for priority, profile in enumerate(profiles):
        layer = profile.get("layer")
        if not isinstance(layer, Mapping):
            raise ValueError("Curated operation profile layer must be an object")
        payload = dict(layer)
        payload["priority"] = priority
        layers.append(OperationLayer(**payload))
    return tuple(layers)


def _neutral_layer(max_work_feed_mm_min: float) -> OperationLayer:
    return OperationLayer(
        name="Line — configure material",
        color=DEFAULT_LAYER_COLORS[0],
        mode=LayerMode.LINE,
        speed_mm_min=min(
            _NEUTRAL_LINE_SPEED_MM_MIN,
            max_work_feed_mm_min,
        ),
        power_percent=0.0,
        passes=1,
        line_interval_mm=0.10,
        scan_angle_deg=0.0,
        overscan_percent=2.5,
        vector_power_correction=0.0,
        raster_power_correction=0.0,
        air_assist=False,
        output_enabled=False,
        visible=True,
        priority=0,
    )


def resolve_new_project_operation_defaults(
    *,
    machine_profile_id: str,
    tool_head_profile_id: str,
    max_work_feed_mm_min: float,
) -> ResolvedOperationDefaults:
    """Resolve curated built-ins or one neutral new-project operation.

    This resolver intentionally has no material-database input. Automatic new
    projects can therefore consume only built-ins reviewed with the source,
    never arbitrary user-created SQLite recipes.
    """

    machine_profile_id = _running_profile_id(
        machine_profile_id,
        "machine_profile_id",
    )
    tool_head_profile_id = _running_profile_id(
        tool_head_profile_id,
        "tool_head_profile_id",
    )
    if type(max_work_feed_mm_min) not in {int, float}:
        raise ValueError("max_work_feed_mm_min must be a positive finite number")
    feed_ceiling = float(max_work_feed_mm_min)
    if not math.isfinite(feed_ceiling) or feed_ceiling <= 0.0:
        raise ValueError("max_work_feed_mm_min must be a positive finite number")

    matches: dict[OperationDefaultSource, list[Mapping[str, Any]]] = {
        OperationDefaultSource.EXACT_MACHINE_TOOL: [],
        OperationDefaultSource.TOOL_ONLY: [],
        OperationDefaultSource.UNIVERSAL: [],
    }
    for profile in _CURATED_OPERATION_PROFILES:
        source = _curated_scope(
            profile,
            machine_profile_id=machine_profile_id,
            tool_head_profile_id=tool_head_profile_id,
        )
        if source is not None:
            matches[source].append(profile)

    for source in (
        OperationDefaultSource.EXACT_MACHINE_TOOL,
        OperationDefaultSource.TOOL_ONLY,
        OperationDefaultSource.UNIVERSAL,
    ):
        selected = matches[source]
        if selected:
            return ResolvedOperationDefaults(
                layers=_curated_layers(tuple(selected)),
                source=source,
            )

    return ResolvedOperationDefaults(
        layers=(_neutral_layer(feed_ceiling),),
        source=OperationDefaultSource.SAFE_NEUTRAL,
        notice=(
            "No curated material defaults exist for running profiles "
            f"{machine_profile_id} / {tool_head_profile_id}. Configure the "
            "0% power operation or apply a compatible Material Recipe before "
            "enabling output."
        ),
    )


__all__ = [
    "OperationDefaultSource",
    "ResolvedOperationDefaults",
    "resolve_new_project_operation_defaults",
]
