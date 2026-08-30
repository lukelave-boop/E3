from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.project import (
    Bounds,
    NativePathGeometry,
    PathAffineTransform,
    PathLineSegment,
    PathSubpath,
    native_path_bounds,
    transform_native_path,
)
from laser_aligner.vision.trace_orientation import (
    TraceOrientationEstimate,
    estimate_trace_orientation,
    trace_rotation_transform,
)


def _rectangle(
    center: tuple[float, float],
    width: float,
    height: float,
) -> NativePathGeometry:
    half_width = width / 2.0
    half_height = height / 2.0
    geometry = NativePathGeometry(
        (
            PathSubpath(
                (-half_width, -half_height),
                (
                    PathLineSegment((half_width, -half_height)),
                    PathLineSegment((half_width, half_height)),
                    PathLineSegment((-half_width, half_height)),
                    PathLineSegment((-half_width, -half_height)),
                ),
                closed=True,
            ),
        )
    )
    return transform_native_path(
        geometry,
        PathAffineTransform.from_components(
            translate_x=center[0],
            translate_y=center[1],
        ),
    )


def _normalize_detection(
    detection_id: str,
    index: int,
    geometry: NativePathGeometry,
) -> dict[str, object]:
    x_min, y_min, x_max, y_max = native_path_bounds(geometry)
    width = x_max - x_min
    height = y_max - y_min
    center = ((x_min + x_max) / 2.0, (y_min + y_max) / 2.0)
    normalized = transform_native_path(
        geometry,
        PathAffineTransform.from_components(
            scale_x=1.0 / width,
            scale_y=1.0 / height,
            translate_x=-center[0] / width,
            translate_y=-center[1] / height,
        ),
    )
    return {
        "id": detection_id,
        "index": index,
        "source": "direct",
        "confidence": 0.98,
        "selected_default": True,
        "shape": "contour",
        "center_mm": list(center),
        "width_mm": width,
        "height_mm": height,
        "area_mm2": width * height,
        "native_verified": True,
        "native_path": normalized.to_dict(),
        "native_center_mm": list(center),
        "native_width_mm": width,
        "native_height_mm": height,
        "diagnostics": {"native_fit_status": "verified"},
    }


def _label(prefix: str, x_offset: float, angle_deg: float) -> list[dict[str, object]]:
    geometries = [
        _rectangle((x_offset + 0.0, 25.0), 1.5, 10.0),
        _rectangle((x_offset + 8.0, 25.0), 1.5, 9.0),
        _rectangle((x_offset + 16.0, 25.0), 1.5, 11.0),
        _rectangle((x_offset + 8.0, 18.0), 27.0, 1.0),
    ]
    bounds = [native_path_bounds(geometry) for geometry in geometries]
    pivot = (
        (min(item[0] for item in bounds) + max(item[2] for item in bounds)) / 2.0,
        (min(item[1] for item in bounds) + max(item[3] for item in bounds)) / 2.0,
    )
    rotation = trace_rotation_transform(angle_deg, pivot)
    return [
        _normalize_detection(
            f"{prefix}-{index}",
            index,
            transform_native_path(geometry, rotation),
        )
        for index, geometry in enumerate(geometries, start=1)
    ]


class _Panel:
    def __init__(self, selected_ids: list[str]) -> None:
        self._selected_ids = list(selected_ids)
        self.context_current = True
        self.offer: TraceOrientationEstimate | None = None
        self.applied: TraceOrientationEstimate | None = None
        self.clear_count = 0
        self.failure: tuple[str, bool] | None = None
        self.result_clear_count = 0

    def selected_ids(self) -> list[str]:
        return list(self._selected_ids)

    def straighten_context_is_current(self) -> bool:
        return self.context_current

    def set_straighten_offer(self, estimate: TraceOrientationEstimate) -> None:
        self.offer = estimate
        self.applied = None

    def set_straighten_applied(self, estimate: TraceOrientationEstimate) -> None:
        self.offer = estimate
        self.applied = estimate

    def clear_straighten_review(self) -> None:
        self.offer = None
        self.applied = None
        self.clear_count += 1

    def set_detection_failed(self, message: str, *, retain_preview: bool) -> None:
        self.clear_straighten_review()
        self.failure = (message, retain_preview)

    def clear_result(self) -> None:
        self.clear_straighten_review()
        self.result_clear_count += 1


class _Workspace:
    def __init__(self) -> None:
        self.transforms: list[tuple[tuple[str, ...], object]] = []
        self.selected_ids: list[str] = []
        self.clear_count = 0

    def set_trace_straightening(self, selected_ids, transform) -> None:
        self.transforms.append((tuple(selected_ids), transform))

    def set_trace_selected_ids(self, selected_ids) -> None:
        self.selected_ids = list(selected_ids)

    def clear_trace_preview(self) -> None:
        self.clear_count += 1


class _Harness:
    def __init__(
        self,
        detections: list[dict[str, object]],
        selected_ids: list[str],
        *,
        grid: bool = False,
        options: dict[str, object] | None = None,
        diagnostics: dict[str, object] | None = None,
    ) -> None:
        result_options: dict[str, object] = {
            "detection_mode": "contrast",
            "regular_grid": grid,
            "output_mode": "native",
        }
        if options is not None:
            result_options.update(options)
        self._trace_result = {
            "detected": True,
            "detections": detections,
            "mode_used": result_options["detection_mode"],
            "grid": {"rows": 1, "columns": len(detections)} if grid else None,
            "options": result_options,
            "diagnostics": diagnostics or {},
        }
        self._trace_orientation_estimate = None
        self._trace_straightening = None
        self.trace_panel = _Panel(selected_ids)
        self.workspace = _Workspace()
        self.controller = SimpleNamespace(cancel_trace_detection=lambda: None)

    def _selected_trace_detections(self, selected_ids):
        return E3MainWindow._selected_trace_detections(self, selected_ids)

    def _clear_trace_straightening_preview(self) -> None:
        E3MainWindow._clear_trace_straightening_preview(self)

    def _update_trace_orientation(self, selected_ids) -> None:
        E3MainWindow._update_trace_orientation(self, selected_ids)

    def _invalidate_trace_orientation_review(self) -> None:
        E3MainWindow._invalidate_trace_orientation_review(self)


def test_selection_recomputes_without_detection_and_reset_reuses_original_estimate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _label("first", 20.0, 2.0)
    second = _label("second", 100.0, -3.0)
    first_ids = [str(item["id"]) for item in first]
    second_ids = [str(item["id"]) for item in second]
    harness = _Harness(first + second, first_ids)
    original_result = copy.deepcopy(harness._trace_result)
    calls: list[tuple[str, ...]] = []
    real_estimator = estimate_trace_orientation

    def recording_estimator(detections):
        calls.append(tuple(str(item["id"]) for item in detections))
        return real_estimator(detections)

    monkeypatch.setattr(
        main_window_module,
        "estimate_trace_orientation",
        recording_estimator,
    )

    E3MainWindow._update_trace_orientation(harness, first_ids)
    assert harness.trace_panel.offer is not None
    assert harness.trace_panel.offer.detected_skew_deg == pytest.approx(2.0, abs=0.08)
    assert calls == [tuple(first_ids)]

    E3MainWindow._straighten_trace_selection(harness)
    assert len(calls) == 1
    assert harness._trace_straightening is harness.trace_panel.applied
    assert harness.workspace.transforms[-1][0] == tuple(first_ids)

    E3MainWindow._reset_trace_straightening(harness)
    assert len(calls) == 1
    assert harness.workspace.transforms[-1] == ((), None)
    assert harness.trace_panel.offer is harness._trace_orientation_estimate
    assert harness._trace_straightening is None

    harness.trace_panel._selected_ids = second_ids
    E3MainWindow._trace_selection_changed(harness, second_ids)
    assert len(calls) == 2
    assert harness.trace_panel.offer is not None
    assert harness.trace_panel.offer.detected_skew_deg == pytest.approx(-3.0, abs=0.08)
    assert harness._trace_result == original_result


def test_grid_and_failed_native_selection_never_offer_or_run_extra_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detections = _label("grid", 20.0, 2.0)
    selected_ids = [str(item["id"]) for item in detections]
    grid_harness = _Harness(detections, selected_ids, grid=True)

    def unexpected_estimator(_detections):
        raise AssertionError("grid review must not run orientation estimation")

    monkeypatch.setattr(
        main_window_module,
        "estimate_trace_orientation",
        unexpected_estimator,
    )
    E3MainWindow._update_trace_orientation(grid_harness, selected_ids)
    assert grid_harness._trace_orientation_estimate is None
    assert grid_harness.trace_panel.offer is None

    incomplete_harness = _Harness(detections, selected_ids)
    incomplete_harness._trace_result.pop("detected")
    incomplete_harness._trace_result["options"].pop("output_mode")
    E3MainWindow._update_trace_orientation(incomplete_harness, selected_ids)
    assert incomplete_harness._trace_orientation_estimate is None
    assert incomplete_harness.trace_panel.offer is None

    failed = copy.deepcopy(detections)
    failed[0]["native_verified"] = False
    failed_harness = _Harness(failed, selected_ids)
    monkeypatch.setattr(
        main_window_module,
        "estimate_trace_orientation",
        unexpected_estimator,
    )
    E3MainWindow._update_trace_orientation(failed_harness, selected_ids)
    assert failed_harness._trace_orientation_estimate is None
    assert failed_harness.trace_panel.offer is None


@pytest.mark.parametrize(
    ("options", "diagnostics"),
    [
        (
            {
                "detection_mode": "auto",
                "contrast_threshold_mode": "auto",
            },
            {
                "auto": {
                    "selected_strategy": "raster_dark",
                    "attempts": [
                        {
                            "name": "raster_dark",
                            "status": "success",
                            "threshold": 170,
                        }
                    ],
                }
            },
        ),
        (
            {
                "detection_mode": "contrast",
                "contrast_threshold_mode": "manual",
                "contrast_threshold": 150,
            },
            {"strategy_metrics": {"threshold": 150}},
        ),
        (
            {
                "detection_mode": "color",
                "target_hue": 42.0,
            },
            {"strategy_metrics": {"target_hue": 42.0}},
        ),
    ],
)
def test_successful_auto_manual_and_color_results_share_geometry_orientation(
    options: dict[str, object],
    diagnostics: dict[str, object],
) -> None:
    detections = _label("strategy", 20.0, 2.0)
    selected_ids = [str(item["id"]) for item in detections]
    harness = _Harness(
        detections,
        selected_ids,
        options=options,
        diagnostics=diagnostics,
    )

    E3MainWindow._update_trace_orientation(harness, selected_ids)

    assert harness.trace_panel.offer is not None
    assert harness.trace_panel.offer.detected_skew_deg == pytest.approx(
        2.0,
        abs=0.08,
    )


def test_stale_or_stock_context_cannot_restore_or_apply_straighten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    detections = _label("context", 20.0, 2.0)
    selected_ids = [str(item["id"]) for item in detections]
    harness = _Harness(detections, selected_ids)
    E3MainWindow._update_trace_orientation(harness, selected_ids)
    assert harness.trace_panel.offer is not None
    E3MainWindow._straighten_trace_selection(harness)
    assert harness._trace_straightening is not None

    harness.trace_panel.context_current = False

    def unexpected_estimator(_detections):
        raise AssertionError("stale or Stock review must not estimate orientation")

    monkeypatch.setattr(
        main_window_module,
        "estimate_trace_orientation",
        unexpected_estimator,
    )
    E3MainWindow._trace_straighten_context_changed(harness)
    E3MainWindow._trace_selection_changed(harness, selected_ids[:-1])
    E3MainWindow._straighten_trace_selection(harness)

    assert harness._trace_orientation_estimate is None
    assert harness._trace_straightening is None
    assert harness.trace_panel.offer is None
    assert harness.trace_panel.applied is None
    assert harness.workspace.transforms[-1] == ((), None)


def test_failed_detection_and_clear_remove_stale_straighten_state() -> None:
    detections = _label("failure", 20.0, 2.0)
    selected_ids = [str(item["id"]) for item in detections]
    harness = _Harness(detections, selected_ids)
    estimate = estimate_trace_orientation(detections)
    assert estimate.offered
    harness._trace_orientation_estimate = estimate
    harness._trace_straightening = estimate
    harness._active_trace_request_id = 41
    harness._trace_raster_preview_images = {"mask": object()}
    harness._trace_raster_preview_area = Bounds(0.0, 0.0, 100.0, 50.0)
    harness._trace_raster_preview_signature = ("review", 1)
    harness.inspector_tabs = SimpleNamespace(select_panel=lambda _name: None)

    E3MainWindow._trace_detection_failed(
        harness,
        41,
        "Authoritative native-path topology remains ambiguous",
        True,
    )

    assert harness._trace_result is None
    assert harness._trace_orientation_estimate is None
    assert harness._trace_straightening is None
    assert harness.trace_panel.failure == (
        "Authoritative native-path topology remains ambiguous",
        True,
    )
    assert harness.workspace.clear_count == 1

    harness._trace_result = {"detections": detections}
    harness._trace_orientation_estimate = estimate
    harness._trace_straightening = estimate
    harness._trace_raster_preview_images = {}
    harness._trace_raster_preview_area = None
    harness._trace_raster_preview_signature = None
    E3MainWindow._clear_trace_preview(harness)

    assert harness._trace_result is None
    assert harness._trace_orientation_estimate is None
    assert harness._trace_straightening is None
    assert harness.trace_panel.result_clear_count == 1
    assert harness.workspace.clear_count == 2
