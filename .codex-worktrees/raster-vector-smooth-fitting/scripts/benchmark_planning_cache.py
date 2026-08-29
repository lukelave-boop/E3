from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from laser_aligner.config import LaserSettings  # noqa: E402
from laser_aligner.planning import PlanningCache, PlanningCacheStats  # noqa: E402
from laser_aligner.project.model import (  # noqa: E402
    Bounds,
    LayerMode,
    OperationLayer,
    ProjectDocument,
    SceneObject,
)
from laser_aligner.project.toolpath import generate_project_gcode  # noqa: E402


@dataclass(frozen=True, slots=True)
class BenchmarkResult:
    scenario: str
    median_ms: float
    minimum_ms: float
    maximum_ms: float
    speedup_vs_uncached: float
    cache_delta: dict[str, int]


def _build_document(*, layer_count: int, objects_per_layer: int) -> ProjectDocument:
    layers: list[OperationLayer] = []
    objects: list[SceneObject] = []
    columns = 8
    for layer_index in range(layer_count):
        layer = OperationLayer(
            id=f"bench-layer-{layer_index}",
            name=f"Benchmark {layer_index + 1}",
            color=f"#{(0x335577 + layer_index * 0x10101) & 0xFFFFFF:06X}",
            mode=LayerMode.LINE,
            speed_mm_min=1200.0 + layer_index * 25.0,
            power_percent=20.0 + layer_index,
            passes=1,
            vector_power_correction=0.0,
            output_enabled=True,
            visible=True,
            priority=layer_index,
        )
        layers.append(layer)
        for object_index in range(objects_per_layer):
            column = object_index % columns
            row = object_index // columns
            center = (
                18.0 + column * 22.0,
                18.0 + row * 22.0,
            )
            name = f"L{layer_index:02d}-O{object_index:03d}"
            if object_index % 2:
                item = SceneObject.rectangle(
                    layer.id,
                    name=name,
                    center=center,
                    width_mm=10.0,
                    height_mm=8.0,
                    corner_radius_mm=2.0,
                )
            else:
                item = SceneObject.ellipse(
                    layer.id,
                    name=name,
                    center=center,
                    width_mm=10.0,
                    height_mm=8.0,
                )
            objects.append(item)
    document = ProjectDocument(
        id="project-planning-cache-benchmark",
        name="Planning cache benchmark",
        work_area=Bounds(0.0, 0.0, 220.0, 220.0),
        layers=layers,
        objects=objects,
        created_at="2026-08-19T00:00:00+00:00",
        modified_at="2026-08-19T00:00:00+00:00",
        revision=0,
    )
    return document


def _stats_delta(
    before: PlanningCacheStats,
    after: PlanningCacheStats,
) -> dict[str, int]:
    return {
        field.name: int(getattr(after, field.name) - getattr(before, field.name))
        for field in fields(PlanningCacheStats)
    }


def _zero_delta() -> dict[str, int]:
    return {field.name: 0 for field in fields(PlanningCacheStats)}


def _add_delta(total: dict[str, int], delta: dict[str, int]) -> None:
    for key, value in delta.items():
        total[key] += value


def _time_call(operation: Callable[[], Any]) -> float:
    started = time.perf_counter_ns()
    operation()
    return (time.perf_counter_ns() - started) / 1_000_000.0


def _summarize(
    scenario: str,
    samples: list[float],
    cache_delta: dict[str, int],
    uncached_median_ms: float,
) -> BenchmarkResult:
    median_ms = statistics.median(samples)
    return BenchmarkResult(
        scenario=scenario,
        median_ms=median_ms,
        minimum_ms=min(samples),
        maximum_ms=max(samples),
        speedup_vs_uncached=(
            uncached_median_ms / median_ms if median_ms > 0.0 else float("inf")
        ),
        cache_delta=cache_delta,
    )


def _generate(
    document: ProjectDocument,
    laser: LaserSettings,
    cache: PlanningCache | None,
) -> None:
    job = generate_project_gcode(
        document,
        laser,
        optimize_order=True,
        start_position=(0.0, 0.0),
        planning_cache=cache,
    )
    if job.plan is None:
        raise RuntimeError("Benchmark generation did not produce an immutable JobPlan")


def _measure_uncached(
    document: ProjectDocument,
    laser: LaserSettings,
    iterations: int,
) -> tuple[list[float], dict[str, int]]:
    samples = [
        _time_call(lambda: _generate(document, laser, None))
        for _ in range(iterations)
    ]
    return samples, _zero_delta()


def _measure_cold_cache(
    document: ProjectDocument,
    laser: LaserSettings,
    iterations: int,
) -> tuple[list[float], dict[str, int]]:
    samples: list[float] = []
    total = _zero_delta()
    for _ in range(iterations):
        cache = PlanningCache()
        before = cache.stats
        samples.append(
            _time_call(lambda cache=cache: _generate(document, laser, cache))
        )
        _add_delta(total, _stats_delta(before, cache.stats))
    return samples, total


def _measure_with_session_cache(
    *,
    document: ProjectDocument,
    base_laser: LaserSettings,
    cache: PlanningCache,
    iterations: int,
    mutate: Callable[[int], LaserSettings],
) -> tuple[list[float], dict[str, int]]:
    before = cache.stats
    samples: list[float] = []
    for index in range(iterations):
        laser = mutate(index)
        samples.append(_time_call(lambda laser=laser: _generate(document, laser, cache)))
    return samples, _stats_delta(before, cache.stats)


def _verify_delta(
    scenario: str,
    delta: dict[str, int],
    *,
    layer_count: int,
    iterations: int,
    normalized_hits: int = 0,
    normalized_misses: int = 0,
    placed_hits: int = 0,
    placed_misses: int = 0,
    controller_hits: int = 0,
    controller_misses: int = 0,
) -> None:
    expected = {
        "normalized_hits": normalized_hits,
        "normalized_misses": normalized_misses,
        "placed_hits": placed_hits,
        "placed_misses": placed_misses,
        "controller_hits": controller_hits,
        "controller_misses": controller_misses,
    }
    for key, expected_value in expected.items():
        actual = delta[key]
        if actual != expected_value:
            raise RuntimeError(
                f"{scenario}: expected {key}={expected_value}, received {actual}; "
                f"layer_count={layer_count}, iterations={iterations}"
            )


def run_benchmark(
    *,
    iterations: int,
    warmup: int,
    layer_count: int,
    objects_per_layer: int,
    verify: bool,
) -> dict[str, Any]:
    if iterations < 1:
        raise ValueError("iterations must be at least 1")
    if warmup < 0:
        raise ValueError("warmup must not be negative")
    if layer_count < 2:
        raise ValueError("layer_count must be at least 2")
    if objects_per_layer < 2:
        raise ValueError("objects_per_layer must be at least 2")

    document = _build_document(
        layer_count=layer_count,
        objects_per_layer=objects_per_layer,
    )
    laser = LaserSettings(
        power_max=1000,
        travel_feed_mm_min=3000.0,
        boundary_margin_mm=0.0,
        spot_offset_x_mm=0.0,
        spot_offset_y_mm=0.0,
        preview_acceleration_mm_s2=500.0,
        preview_command_delay_ms=0.0,
    )

    for _ in range(warmup):
        _generate(document, laser, None)
    gc.collect()

    uncached_samples, uncached_delta = _measure_uncached(
        document,
        laser,
        iterations,
    )
    uncached_median = statistics.median(uncached_samples)
    results = [
        _summarize(
            "uncached identical",
            uncached_samples,
            uncached_delta,
            uncached_median,
        )
    ]

    cold_samples, cold_delta = _measure_cold_cache(
        document,
        laser,
        iterations,
    )
    if verify:
        count = iterations * layer_count
        _verify_delta(
            "cold cache",
            cold_delta,
            layer_count=layer_count,
            iterations=iterations,
            normalized_misses=count,
            placed_misses=count,
            controller_misses=count,
        )
    results.append(
        _summarize(
            "cold cache",
            cold_samples,
            cold_delta,
            uncached_median,
        )
    )

    cache = PlanningCache()
    _generate(document, laser, cache)

    warm_samples, warm_delta = _measure_with_session_cache(
        document=document,
        base_laser=laser,
        cache=cache,
        iterations=iterations,
        mutate=lambda _index: laser,
    )
    if verify:
        count = iterations * layer_count
        _verify_delta(
            "warm identical",
            warm_delta,
            layer_count=layer_count,
            iterations=iterations,
            normalized_hits=count,
            placed_hits=count,
            controller_hits=count,
        )
    results.append(
        _summarize(
            "warm identical",
            warm_samples,
            warm_delta,
            uncached_median,
        )
    )

    def mutate_speed(index: int) -> LaserSettings:
        document.layers[0].speed_mm_min = 1200.0 + float(index + 1) * 5.0
        document.touch()
        return laser

    speed_samples, speed_delta = _measure_with_session_cache(
        document=document,
        base_laser=laser,
        cache=cache,
        iterations=iterations,
        mutate=mutate_speed,
    )
    if verify:
        count = iterations * layer_count
        _verify_delta(
            "speed-only edit",
            speed_delta,
            layer_count=layer_count,
            iterations=iterations,
            normalized_hits=count,
            placed_hits=count,
            controller_hits=count,
        )
    results.append(
        _summarize(
            "speed-only edit",
            speed_samples,
            speed_delta,
            uncached_median,
        )
    )

    def mutate_power(index: int) -> LaserSettings:
        document.layers[0].power_percent = 20.0 + float(index + 1) * 0.5
        document.touch()
        return laser

    power_samples, power_delta = _measure_with_session_cache(
        document=document,
        base_laser=laser,
        cache=cache,
        iterations=iterations,
        mutate=mutate_power,
    )
    if verify:
        count = iterations * layer_count
        _verify_delta(
            "power-only edit",
            power_delta,
            layer_count=layer_count,
            iterations=iterations,
            normalized_hits=count,
            placed_hits=count,
            controller_hits=count,
        )
    results.append(
        _summarize(
            "power-only edit",
            power_samples,
            power_delta,
            uncached_median,
        )
    )

    def mutate_spot(index: int) -> LaserSettings:
        return replace(
            laser,
            spot_offset_x_mm=0.01 * float(index + 1),
        )

    spot_samples, spot_delta = _measure_with_session_cache(
        document=document,
        base_laser=laser,
        cache=cache,
        iterations=iterations,
        mutate=mutate_spot,
    )
    if verify:
        count = iterations * layer_count
        _verify_delta(
            "spot-offset edit",
            spot_delta,
            layer_count=layer_count,
            iterations=iterations,
            normalized_hits=count,
            placed_hits=count,
            controller_misses=count,
        )
    results.append(
        _summarize(
            "spot-offset edit",
            spot_samples,
            spot_delta,
            uncached_median,
        )
    )

    target = next(
        item
        for item in document.objects
        if item.layer_id == document.layers[0].id
    )

    def mutate_geometry(index: int) -> LaserSettings:
        target.transform.x_mm += 0.01 * float(index + 1)
        document.touch()
        return laser

    geometry_samples, geometry_delta = _measure_with_session_cache(
        document=document,
        base_laser=laser,
        cache=cache,
        iterations=iterations,
        mutate=mutate_geometry,
    )
    if verify:
        changed = iterations
        unchanged = iterations * (layer_count - 1)
        _verify_delta(
            "geometry edit",
            geometry_delta,
            layer_count=layer_count,
            iterations=iterations,
            normalized_hits=unchanged,
            normalized_misses=changed,
            placed_hits=unchanged,
            placed_misses=changed,
            controller_hits=unchanged,
            controller_misses=changed,
        )
    results.append(
        _summarize(
            "geometry edit",
            geometry_samples,
            geometry_delta,
            uncached_median,
        )
    )

    return {
        "parameters": {
            "iterations": iterations,
            "warmup": warmup,
            "layers": layer_count,
            "objects_per_layer": objects_per_layer,
            "total_objects": layer_count * objects_per_layer,
            "verify_cache_pattern": verify,
        },
        "results": [asdict(result) for result in results],
        "final_cache_stats": asdict(cache.stats),
    }


def _compact_cache(delta: dict[str, int]) -> str:
    return (
        f"N {delta['normalized_hits']}/{delta['normalized_misses']}  "
        f"P {delta['placed_hits']}/{delta['placed_misses']}  "
        f"C {delta['controller_hits']}/{delta['controller_misses']}"
    )


def _print_report(report: dict[str, Any]) -> None:
    parameters = report["parameters"]
    print(
        "E3 planning-cache benchmark "
        f"({parameters['layers']} layers, "
        f"{parameters['objects_per_layer']} objects/layer, "
        f"{parameters['iterations']} measured runs)"
    )
    print("Cache columns are hits/misses accumulated across measured runs.")
    print()
    print(
        f"{'Scenario':<20} {'Median ms':>10} {'Min':>9} {'Max':>9} "
        f"{'vs uncached':>12}  Cache N/P/C"
    )
    print("-" * 96)
    for result in report["results"]:
        print(
            f"{result['scenario']:<20} "
            f"{result['median_ms']:>10.3f} "
            f"{result['minimum_ms']:>9.3f} "
            f"{result['maximum_ms']:>9.3f} "
            f"{result['speedup_vs_uncached']:>11.2f}x  "
            f"{_compact_cache(result['cache_delta'])}"
        )
    print()
    print("N=normalized, P=placed, C=controller.")
    print(
        "No timing threshold is enforced; use the medians to decide the next "
        "optimization target."
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Benchmark E3 LINE planning regeneration and verify selective-cache behavior."
        )
    )
    parser.add_argument("--iterations", type=int, default=7)
    parser.add_argument("--warmup", type=int, default=2)
    parser.add_argument("--layers", type=int, default=8)
    parser.add_argument("--objects-per-layer", type=int, default=32)
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Do not assert the expected per-stage cache hit/miss pattern.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of the console table.",
    )
    args = parser.parse_args()

    report = run_benchmark(
        iterations=args.iterations,
        warmup=args.warmup,
        layer_count=args.layers,
        objects_per_layer=args.objects_per_layer,
        verify=not args.no_verify,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
