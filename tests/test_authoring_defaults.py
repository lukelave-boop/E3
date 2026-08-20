from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from laser_aligner.materials import (
    MaterialDatabase,
    MaterialPreset,
    OperationDefaultSource,
    resolve_new_project_operation_defaults,
)
from laser_aligner.materials import authoring_defaults as defaults_module


def _layer_payload_digest(layers: tuple[Any, ...]) -> str:
    payload: list[dict[str, Any]] = []
    for layer in layers:
        item = layer.to_dict()
        item.pop("id")
        payload.append(item)
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _curated_profile(
    name: str,
    *,
    machine_profile_id: str | None,
    tool_head_profile_id: str | None,
    speed_mm_min: float,
) -> dict[str, Any]:
    return {
        "machine_profile_id": machine_profile_id,
        "tool_head_profile_id": tool_head_profile_id,
        "layer": {
            "name": name,
            "speed_mm_min": speed_mm_min,
            "power_percent": 0.0,
            "output_enabled": False,
        },
    }


def test_ender_10w_resolver_retains_independent_historical_layer_digest() -> None:
    resolved = resolve_new_project_operation_defaults(
        machine_profile_id="ender-3-s1-pro",
        tool_head_profile_id="generic-diode-10w",
        max_work_feed_mm_min=3000.0,
    )

    assert resolved.source is OperationDefaultSource.EXACT_MACHINE_TOOL
    assert resolved.notice is None
    assert len(resolved.layers) == 13
    # This digest is an independent lock over every persisted OperationLayer
    # field except the intentionally fresh layer IDs.
    assert _layer_payload_digest(resolved.layers) == (
        "56f51341c6679d0573b6edf01a82f7f46b2f371d2877a9b04221ccaff5a11e6b"
    )


def test_resolver_returns_fresh_curated_layer_objects() -> None:
    first = resolve_new_project_operation_defaults(
        machine_profile_id="ender-3-s1-pro",
        tool_head_profile_id="generic-diode-10w",
        max_work_feed_mm_min=6000.0,
    )
    second = resolve_new_project_operation_defaults(
        machine_profile_id="ender-3-s1-pro",
        tool_head_profile_id="generic-diode-10w",
        max_work_feed_mm_min=6000.0,
    )

    first.layers[0].name = "Changed"

    assert first.layers[0].id != second.layers[0].id
    assert second.layers[0].name == "Copy / Printer Paper — CUT"


def test_exact_curated_tier_wins_without_mixing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    profiles = (
        _curated_profile(
            "Universal",
            machine_profile_id=None,
            tool_head_profile_id=None,
            speed_mm_min=100.0,
        ),
        _curated_profile(
            "Exact A",
            machine_profile_id="machine-a",
            tool_head_profile_id="tool-a",
            speed_mm_min=200.0,
        ),
        _curated_profile(
            "Tool only",
            machine_profile_id=None,
            tool_head_profile_id="tool-a",
            speed_mm_min=300.0,
        ),
        _curated_profile(
            "Exact B",
            machine_profile_id="machine-a",
            tool_head_profile_id="tool-a",
            speed_mm_min=400.0,
        ),
    )
    monkeypatch.setattr(defaults_module, "_CURATED_OPERATION_PROFILES", profiles)

    resolved = resolve_new_project_operation_defaults(
        machine_profile_id="machine-a",
        tool_head_profile_id="tool-a",
        max_work_feed_mm_min=500.0,
    )

    assert resolved.source is OperationDefaultSource.EXACT_MACHINE_TOOL
    assert [layer.name for layer in resolved.layers] == ["Exact A", "Exact B"]
    assert [layer.priority for layer in resolved.layers] == [0, 1]


@pytest.mark.parametrize(
    ("profiles", "expected_source", "expected_name"),
    [
        (
            (
                _curated_profile(
                    "Universal",
                    machine_profile_id=None,
                    tool_head_profile_id=None,
                    speed_mm_min=100.0,
                ),
                _curated_profile(
                    "Tool only",
                    machine_profile_id=None,
                    tool_head_profile_id="tool-a",
                    speed_mm_min=200.0,
                ),
            ),
            OperationDefaultSource.TOOL_ONLY,
            "Tool only",
        ),
        (
            (
                _curated_profile(
                    "Universal",
                    machine_profile_id=None,
                    tool_head_profile_id=None,
                    speed_mm_min=100.0,
                ),
                _curated_profile(
                    "Other tool",
                    machine_profile_id=None,
                    tool_head_profile_id="tool-b",
                    speed_mm_min=200.0,
                ),
            ),
            OperationDefaultSource.UNIVERSAL,
            "Universal",
        ),
    ],
)
def test_resolver_uses_highest_available_non_exact_tier(
    monkeypatch: pytest.MonkeyPatch,
    profiles: tuple[dict[str, Any], ...],
    expected_source: OperationDefaultSource,
    expected_name: str,
) -> None:
    monkeypatch.setattr(defaults_module, "_CURATED_OPERATION_PROFILES", profiles)

    resolved = resolve_new_project_operation_defaults(
        machine_profile_id="machine-a",
        tool_head_profile_id="tool-a",
        max_work_feed_mm_min=500.0,
    )

    assert resolved.source is expected_source
    assert [layer.name for layer in resolved.layers] == [expected_name]


@pytest.mark.parametrize("feed_ceiling", [250.0, 10_000.0])
def test_unmatched_profiles_receive_safe_neutral_fallback(
    feed_ceiling: float,
) -> None:
    resolved = resolve_new_project_operation_defaults(
        machine_profile_id="generic-grbl",
        tool_head_profile_id="custom-laser-head",
        max_work_feed_mm_min=feed_ceiling,
    )

    assert resolved.source is OperationDefaultSource.SAFE_NEUTRAL
    assert resolved.source.is_curated is False
    assert resolved.notice is not None
    assert "No curated material defaults" in resolved.notice
    assert len(resolved.layers) == 1
    layer = resolved.layers[0]
    assert layer.name == "Line — configure material"
    assert layer.speed_mm_min == min(1000.0, feed_ceiling)
    assert layer.power_percent == 0.0
    assert layer.passes == 1
    assert layer.output_enabled is False
    assert layer.visible is True
    assert layer.vector_power_correction == 0.0
    assert layer.raster_power_correction == 0.0
    assert layer.air_assist is False


def test_user_database_recipe_is_not_an_automatic_default(tmp_path: Path) -> None:
    database = MaterialDatabase(tmp_path / "materials.sqlite")
    database.save(
        MaterialPreset(
            material="User material",
            name="User exact recipe",
            machine_profile_id="generic-grbl",
            tool_head_profile_id="custom-laser-head",
            speed_mm_min=321.0,
            power_percent=99.0,
        )
    )

    resolved = resolve_new_project_operation_defaults(
        machine_profile_id="generic-grbl",
        tool_head_profile_id="custom-laser-head",
        max_work_feed_mm_min=6000.0,
    )

    assert resolved.source is OperationDefaultSource.SAFE_NEUTRAL
    assert resolved.layers[0].speed_mm_min == 1000.0
    assert resolved.layers[0].power_percent == 0.0


@pytest.mark.parametrize(
    "feed_ceiling",
    [True, "1000", 0.0, -1.0, float("nan"), float("inf")],
)
def test_resolver_rejects_invalid_work_feed_ceiling(feed_ceiling: object) -> None:
    with pytest.raises(ValueError, match="positive finite"):
        resolve_new_project_operation_defaults(
            machine_profile_id="generic-grbl",
            tool_head_profile_id="custom-laser-head",
            max_work_feed_mm_min=feed_ceiling,  # type: ignore[arg-type]
        )
