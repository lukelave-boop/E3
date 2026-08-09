from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import cv2
import numpy as np

from ..config import LaserSettings, WorkArea
from ..errors import CalibrationError
from ..gcode.generator import (
    DesignPlacement,
    GcodeProgram,
    ToolpathOptions,
    generate_vector_gcode,
)
from ..geometry.svg import Polyline, SvgGeometry

_TARGET_FRACTIONS = (
    (0.125, 0.125),
    (0.875, 0.125),
    (0.375, 0.375),
    (0.875, 0.375),
    (0.125, 0.625),
    (0.625, 0.625),
    (0.375, 0.875),
    (0.875, 0.875),
)
_VALIDATION_TARGET_FRACTIONS = (
    (0.25, 0.25),
    (0.75, 0.25),
    (0.50, 0.50),
    (0.25, 0.75),
    (0.75, 0.75),
)
_DENSE_MESH_FRACTIONS = (0.15, 0.325, 0.50, 0.675, 0.85)
_DENSE_VALIDATION_FRACTIONS = (0.2375, 0.4125, 0.5875, 0.7625)
_BASE_BED_GRID_FRACTIONS = (0.15, 0.325, 0.50, 0.675, 0.85)
_MAX_APPLIED_TRANSLATION_MM = 5.0
_MAX_MEASUREMENT_RESIDUAL_MM = 8.0
_TRANSLATION_SCATTER_RMS_MM = 0.8
_TRANSLATION_SCATTER_MAX_MM = 1.5
_ALIGNED_RMS_MM = 0.35
_MAX_REVIEW_EXCLUSIONS = 2
_MAX_REVIEWED_SEED_SHIFT_PX = 49.5
_HOMOGRAPHY_RANSAC_THRESHOLD_MM = 0.8
_HOMOGRAPHY_MIN_INLIERS = 7
_HOMOGRAPHY_MAX_RMS_MM = 0.6
_HOMOGRAPHY_MAX_ERROR_MM = 1.0
_HOMOGRAPHY_MIN_COVERAGE_RATIO = 0.35
_HOMOGRAPHY_MIN_AXIS_SPAN_RATIO = 0.70
_HOMOGRAPHY_MAX_CORRECTION_MM = 8.0
_HOMOGRAPHY_MIN_LOCAL_SCALE = 0.90
_HOMOGRAPHY_MAX_LOCAL_SCALE = 1.10
_VALIDATION_RMS_LIMIT_MM = 0.5
_VALIDATION_MAX_LIMIT_MM = 1.0


@dataclass(frozen=True, slots=True)
class RegistrationTarget:
    id: int
    machine_x: float
    machine_y: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class FineRegistrationJob:
    program: GcodeProgram
    filename: str
    targets: tuple[RegistrationTarget, ...]
    powered: bool
    power_percent: float
    mark_size_mm: float


@dataclass(frozen=True, slots=True)
class BaseBedCalibrationJob:
    program: GcodeProgram
    filename: str
    targets: tuple[RegistrationTarget, ...]
    powered: bool
    power_percent: float
    mark_size_mm: float
    display_name: str = "Base bed mapping"


@dataclass(frozen=True, slots=True)
class AccuracyValidationJob:
    program: GcodeProgram
    filename: str
    targets: tuple[RegistrationTarget, ...]
    powered: bool
    power_percent: float
    mark_size_mm: float
    display_name: str = "Accuracy validation"


def registration_targets(
    work_area: WorkArea,
    *,
    mark_size_mm: float = 5.0,
    boundary_margin_mm: float = 0.0,
) -> tuple[RegistrationTarget, ...]:
    if not math.isfinite(mark_size_mm) or not 2.0 <= mark_size_mm <= 10.0:
        raise CalibrationError("Registration mark size must be between 2 and 10 mm")
    if work_area.width <= 0 or work_area.height <= 0:
        raise CalibrationError("Configured work area is invalid")
    clearance = boundary_margin_mm + mark_size_mm * 0.5
    targets = tuple(
        RegistrationTarget(
            id=index,
            machine_x=work_area.x_min + fraction_x * work_area.width,
            machine_y=work_area.y_min + fraction_y * work_area.height,
        )
        for index, (fraction_x, fraction_y) in enumerate(_TARGET_FRACTIONS, start=1)
    )
    if any(not work_area.contains(target.machine_x, target.machine_y, clearance) for target in targets):
        raise CalibrationError("Work area is too small for the fine-registration marks and configured boundary margin")
    return targets


def accuracy_validation_targets(
    work_area: WorkArea,
    *,
    mark_size_mm: float = 5.0,
    boundary_margin_mm: float = 0.0,
) -> tuple[RegistrationTarget, ...]:
    if not math.isfinite(mark_size_mm) or not 2.0 <= mark_size_mm <= 10.0:
        raise CalibrationError("Validation mark size must be between 2 and 10 mm")
    if work_area.width <= 0 or work_area.height <= 0:
        raise CalibrationError("Configured work area is invalid")
    clearance = boundary_margin_mm + mark_size_mm * 0.5
    targets = tuple(
        RegistrationTarget(
            id=index,
            machine_x=work_area.x_min + fraction_x * work_area.width,
            machine_y=work_area.y_min + fraction_y * work_area.height,
        )
        for index, (fraction_x, fraction_y) in enumerate(_VALIDATION_TARGET_FRACTIONS, start=1)
    )
    if any(not work_area.contains(target.machine_x, target.machine_y, clearance) for target in targets):
        raise CalibrationError("Work area is too small for the validation marks and configured boundary margin")
    return targets


def regular_grid_targets(
    work_area: WorkArea,
    fractions: tuple[float, ...],
    *,
    mark_size_mm: float = 5.0,
    boundary_margin_mm: float = 0.0,
) -> tuple[RegistrationTarget, ...]:
    if not math.isfinite(mark_size_mm) or not 2.0 <= mark_size_mm <= 10.0:
        raise CalibrationError("Grid mark size must be between 2 and 10 mm")
    clearance = boundary_margin_mm + mark_size_mm * 0.5
    targets = tuple(
        RegistrationTarget(
            id=row * len(fractions) + column + 1,
            machine_x=work_area.x_min + fx * work_area.width,
            machine_y=work_area.y_min + fy * work_area.height,
        )
        for row, fy in enumerate(fractions)
        for column, fx in enumerate(fractions)
    )
    if any(not work_area.contains(t.machine_x, t.machine_y, clearance) for t in targets):
        raise CalibrationError("Work area is too small for the regular calibration grid")
    return targets


def dense_mesh_targets(
    work_area: WorkArea, *, mark_size_mm: float = 5.0, boundary_margin_mm: float = 0.0
) -> tuple[RegistrationTarget, ...]:
    return regular_grid_targets(
        work_area,
        _DENSE_MESH_FRACTIONS,
        mark_size_mm=mark_size_mm,
        boundary_margin_mm=boundary_margin_mm,
    )


def base_bed_grid_targets(
    work_area: WorkArea,
    *,
    mark_size_mm: float = 5.0,
    boundary_margin_mm: float = 0.0,
) -> tuple[RegistrationTarget, ...]:
    """Return a broad 5x5 grid for solving a new camera-to-bed homography."""
    return regular_grid_targets(
        work_area,
        _BASE_BED_GRID_FRACTIONS,
        mark_size_mm=mark_size_mm,
        boundary_margin_mm=boundary_margin_mm,
    )


def base_bed_grid_mark_sizes(mark_size_mm: float) -> dict[int, float]:
    if not math.isfinite(mark_size_mm) or not 2.0 <= mark_size_mm <= 5.0:
        raise CalibrationError("Base-grid cross size must be between 2 and 5 mm")
    return {
        7: mark_size_mm * 2.0,
        8: mark_size_mm * 1.5,
    }


def dense_validation_targets(
    work_area: WorkArea, *, mark_size_mm: float = 5.0, boundary_margin_mm: float = 0.0
) -> tuple[RegistrationTarget, ...]:
    return regular_grid_targets(
        work_area,
        _DENSE_VALIDATION_FRACTIONS,
        mark_size_mm=mark_size_mm,
        boundary_margin_mm=boundary_margin_mm,
    )


def dense_confirmation_targets(
    work_area: WorkArea,
    *,
    mark_size_mm: float = 5.0,
    boundary_margin_mm: float = 0.0,
) -> tuple[RegistrationTarget, ...]:
    """Sixteen shifted cell-interior targets distinct from fit and refinement marks."""
    nodes = _DENSE_MESH_FRACTIONS
    fractions: list[tuple[float, float]] = []
    for row in range(4):
        for column in range(4):
            x_weight = 0.32 if (row + column) % 2 == 0 else 0.68
            y_weight = 0.68 if (row + column) % 2 == 0 else 0.32
            fractions.append(
                (
                    nodes[column] + (nodes[column + 1] - nodes[column]) * x_weight,
                    nodes[row] + (nodes[row + 1] - nodes[row]) * y_weight,
                )
            )
    clearance = boundary_margin_mm + mark_size_mm * 0.5
    targets = tuple(
        RegistrationTarget(
            id=index,
            machine_x=work_area.x_min + fx * work_area.width,
            machine_y=work_area.y_min + fy * work_area.height,
        )
        for index, (fx, fy) in enumerate(fractions, start=1)
    )
    if any(not work_area.contains(t.machine_x, t.machine_y, clearance) for t in targets):
        raise CalibrationError("Work area is too small for the shifted confirmation grid")
    return targets


def analyze_dense_validation_refinement(
    measurements: list[dict[str, Any]],
    x_nodes_mm: np.ndarray,
    y_nodes_mm: np.ndarray,
) -> dict[str, Any]:
    """Solve a bounded mesh update from 16 reviewed cell-center residuals."""
    if len(measurements) != 16:
        raise CalibrationError("Validation refinement requires all 16 measurements")
    x_nodes = np.asarray(x_nodes_mm, dtype=np.float64)
    y_nodes = np.asarray(y_nodes_mm, dtype=np.float64)
    if len(x_nodes) != 5 or len(y_nodes) != 5:
        raise CalibrationError("Validation refinement requires the active 5×5 mesh")
    matrix = np.zeros((16, 25), dtype=np.float64)
    residuals = np.zeros((16, 2), dtype=np.float64)
    for row, item in enumerate(measurements):
        x, y = float(item["machine_x"]), float(item["machine_y"])
        ix = int(np.clip(np.searchsorted(x_nodes, x) - 1, 0, 3))
        iy = int(np.clip(np.searchsorted(y_nodes, y) - 1, 0, 3))
        tx = (x - x_nodes[ix]) / (x_nodes[ix + 1] - x_nodes[ix])
        ty = (y - y_nodes[iy]) / (y_nodes[iy + 1] - y_nodes[iy])
        matrix[row, iy * 5 + ix] = (1 - tx) * (1 - ty)
        matrix[row, iy * 5 + ix + 1] = tx * (1 - ty)
        matrix[row, (iy + 1) * 5 + ix] = (1 - tx) * ty
        matrix[row, (iy + 1) * 5 + ix + 1] = tx * ty
        residuals[row] = (
            float(item["observed_x"]) - x,
            float(item["observed_y"]) - y,
        )
    scores = np.asarray([float(item.get("score", 1.0)) for item in measurements])
    shifts = np.asarray([float(item.get("seed_shift_px", 0.0)) for item in measurements])
    confidence_ok = bool(
        np.median(scores) > 0
        and np.min(scores) >= np.median(scores) * 0.22
        and np.max(shifts) <= _MAX_REVIEWED_SEED_SHIFT_PX
    )
    regularizer = np.eye(25) * math.sqrt(0.04)
    solve_matrix = np.vstack((matrix, regularizer))
    solve_rhs = np.vstack((-residuals, np.zeros((25, 2))))
    delta = np.linalg.lstsq(solve_matrix, solve_rhs, rcond=None)[0]
    predicted = residuals + matrix @ delta
    errors = np.linalg.norm(predicted, axis=1)
    update_max = float(np.max(np.linalg.norm(delta, axis=1)))
    predicted_rms = float(np.sqrt(np.mean(np.square(errors))))
    predicted_max = float(np.max(errors))
    can_apply = bool(confidence_ok and update_max <= 1.5 and predicted_rms <= 0.20 and predicted_max <= 0.40)
    return {
        "can_refine": can_apply,
        "reason": (
            "The reviewed validation residual supports one bounded mesh refinement"
            if can_apply
            else "The validation detections or proposed refinement failed the update gates"
        ),
        "delta_corrections_mm": delta.reshape(5, 5, 2).tolist(),
        "update_max_mm": update_max,
        "predicted_rms_mm": predicted_rms,
        "predicted_max_mm": predicted_max,
    }


def analyze_dense_mesh_measurements(
    measurements: list[dict[str, Any]],
) -> dict[str, Any]:
    """Fit a regularized 5x5 field, allowing one visibly unreliable grid cell."""
    if len(measurements) != 25:
        raise CalibrationError("Dense local correction requires all 25 grid marks")
    ordered = sorted(measurements, key=lambda item: int(item["id"]))
    if [int(item["id"]) for item in ordered] != list(range(1, 26)):
        raise CalibrationError("Dense local correction mark identities are incomplete")
    commanded = np.asarray([[item["machine_x"], item["machine_y"]] for item in ordered], dtype=np.float64)
    observed = np.asarray([[item["observed_x"], item["observed_y"]] for item in ordered], dtype=np.float64)
    measured = commanded - observed
    if not np.isfinite(measured).all():
        raise CalibrationError("Dense-grid measurements must be finite")
    lengths = np.linalg.norm(measured, axis=1)
    scores = np.asarray([float(item.get("score", 1.0)) for item in ordered])
    shifts = np.asarray([float(item.get("seed_shift_px", 0.0)) for item in ordered])
    median_score = float(np.median(scores))
    over_bound_ids = [int(item["id"]) for item, length in zip(ordered, lengths, strict=True) if float(length) > 3.0]
    low_confidence_ids = [
        int(item["id"])
        for item, score, shift in zip(ordered, scores, shifts, strict=True)
        if median_score <= 0 or float(score) < median_score * 0.22 or float(shift) > _MAX_REVIEWED_SEED_SHIFT_PX
    ]
    unreliable_ids = sorted(set(over_bound_ids) | set(low_confidence_ids))
    included = [index for index, item in enumerate(ordered) if int(item["id"]) not in unreliable_ids]

    rows: list[np.ndarray] = [np.eye(25)[included]]
    for axis in (0, 1):
        penalties = []
        for row in range(5):
            for column in range(1, 4):
                indices = (
                    (row, column - 1, row, column, row, column + 1)
                    if axis == 0
                    else (column - 1, row, column, row, column + 1, row)
                )
                vector = np.zeros(25)
                vector[indices[0] * 5 + indices[1]] = 1.0
                vector[indices[2] * 5 + indices[3]] = -2.0
                vector[indices[4] * 5 + indices[5]] = 1.0
                penalties.append(vector)
        rows.append(np.asarray(penalties) * math.sqrt(0.12))
    matrix = np.vstack(rows)
    rhs = np.vstack((measured[included], np.zeros((len(matrix) - len(included), 2))))
    fitted = np.linalg.lstsq(matrix, rhs, rcond=None)[0]
    fit_error = np.linalg.norm(fitted[included] - measured[included], axis=1)
    rms = float(np.sqrt(np.mean(np.square(fit_error))))
    maximum = float(np.max(fit_error))
    fitted_grid = fitted.reshape(5, 5, 2)
    correction_maximum = float(np.max(np.linalg.norm(fitted_grid, axis=2)))
    x_nodes = np.asarray(sorted(set(float(value) for value in commanded[:, 0])))
    y_nodes = np.asarray(sorted(set(float(value) for value in commanded[:, 1])))
    dx = np.diff(fitted_grid, axis=1) / np.diff(x_nodes)[None, :, None]
    dy = np.diff(fitted_grid, axis=0) / np.diff(y_nodes)[:, None, None]
    gradient_ok = bool(float(np.max(np.abs(dx))) <= 0.08 and float(np.max(np.abs(dy))) <= 0.08)
    can_apply = bool(
        len(unreliable_ids) <= 1
        and rms <= 0.35
        and maximum <= 0.75
        and correction_maximum <= 3.0
        and gradient_ok
    )
    inferred_ids = unreliable_ids if can_apply and len(unreliable_ids) == 1 else []
    rejected_ids = unreliable_ids if not can_apply else []
    output_measurements = [
        {
            **item,
            "error_x_mm": float(-correction[0]),
            "error_y_mm": float(-correction[1]),
            "error_mm": float(length),
            "over_correction_bound": float(length) > 3.0,
            "inferred": int(item["id"]) in inferred_ids,
            "rejected": int(item["id"]) in rejected_ids,
            "unreliable": int(item["id"]) in unreliable_ids,
        }
        for item, correction, length in zip(ordered, measured, lengths, strict=True)
    ]
    if len(unreliable_ids) > 1:
        reason = (
            "More than one grid cell is unreliable ("
            + ", ".join(f"#{identifier}" for identifier in unreliable_ids)
            + "); recapture rather than inferring multiple corrections"
        )
    elif inferred_ids:
        reason = (
            f"Grid cell #{inferred_ids[0]} was excluded as unreliable; its correction "
            "was inferred from the other 24 cells and requires the independent holdout check"
        )
    elif unreliable_ids:
        reason = (
            f"Grid cell #{unreliable_ids[0]} was rejected, and the remaining fit failed "
            "the correction or smoothness gates"
        )
    elif rms > 0.35 or maximum > 0.75:
        reason = "The 25-mark fit residual exceeds the dense-map gates; inspect and recapture"
    elif correction_maximum > 3.0 or not gradient_ok:
        reason = "The proposed mesh exceeds the displacement or local-distortion gates"
    else:
        reason = "All 25 marks support a bounded local correction mesh"
    return {
        "classification": (
            "ready_with_inference" if can_apply and inferred_ids else "ready" if can_apply else "invalid"
        ),
        "can_apply": can_apply,
        "reason": reason,
        "over_bound_ids": over_bound_ids,
        "low_confidence_ids": low_confidence_ids,
        "unreliable_ids": unreliable_ids,
        "inferred_ids": inferred_ids,
        "rejected_ids": rejected_ids,
        "x_nodes_mm": x_nodes.tolist(),
        "y_nodes_mm": y_nodes.tolist(),
        "corrections_mm": fitted_grid.tolist(),
        "fit_rms_mm": rms,
        "fit_max_mm": maximum,
        "measurement_max_mm": float(np.max(lengths)),
        "included_measurement_max_mm": float(np.max(lengths[included])),
        "correction_max_mm": correction_maximum,
        "measurements": output_measurements,
    }


def generate_registration_program(
    targets: tuple[RegistrationTarget, ...],
    laser: LaserSettings,
    work_area: WorkArea,
    *,
    mark_size_mm: float,
    power_percent: float,
    powered: bool,
    speed_mm_min: float,
    design_name: str = "fine-registration-crosses",
    mark_sizes_mm: dict[int, float] | None = None,
) -> GcodeProgram:
    if len(targets) < 4:
        raise CalibrationError("Fine registration requires at least four target marks")
    if not math.isfinite(power_percent) or not 0.0 <= power_percent <= 100.0:
        raise CalibrationError("Registration power must be between 0 and 100 percent")
    if not math.isfinite(speed_mm_min) or speed_mm_min <= 0:
        raise CalibrationError("Registration speed must be positive")
    controller_power = int(round(laser.power_max * power_percent / 100.0))
    if powered and controller_power <= 0:
        raise CalibrationError(
            "Set a previously verified visible-marking power before preparing a powered registration job"
        )

    target_mark_sizes = {
        target.id: float((mark_sizes_mm or {}).get(target.id, mark_size_mm))
        for target in targets
    }
    if any(
        not math.isfinite(size) or not 2.0 <= size <= 10.0
        for size in target_mark_sizes.values()
    ):
        raise CalibrationError("Registration mark sizes must be between 2 and 10 mm")
    paths: list[Polyline] = []
    for target in targets:
        half = target_mark_sizes[target.id] * 0.5
        # Source Y is negated because SVG geometry grows down while machine Y grows up.
        paths.extend(
            [
                Polyline(
                    np.asarray(
                        [
                            [target.machine_x - half, -target.machine_y],
                            [target.machine_x + half, -target.machine_y],
                        ]
                    ),
                    source_tag=f"registration {target.id} horizontal",
                ),
                Polyline(
                    np.asarray(
                        [
                            [target.machine_x, -(target.machine_y - half)],
                            [target.machine_x, -(target.machine_y + half)],
                        ]
                    ),
                    source_tag=f"registration {target.id} vertical",
                ),
            ]
        )
    points = np.vstack([path.points for path in paths])
    source_bounds = (
        float(points[:, 0].min()),
        float(points[:, 1].min()),
        float(points[:, 0].max()),
        float(points[:, 1].max()),
    )
    machine_min_x = source_bounds[0]
    machine_max_x = source_bounds[2]
    machine_min_y = -source_bounds[3]
    machine_max_y = -source_bounds[1]
    geometry = SvgGeometry(polylines=paths, bounds=source_bounds)
    placement = DesignPlacement(
        center_x_mm=(machine_min_x + machine_max_x) * 0.5,
        center_y_mm=(machine_min_y + machine_max_y) * 0.5,
        width_mm=machine_max_x - machine_min_x,
        height_mm=machine_max_y - machine_min_y,
    )
    options = ToolpathOptions(
        power_mode=laser.power_mode,
        power=controller_power if powered else 0,
        power_max=laser.power_max,
        travel_feed_mm_min=laser.travel_feed_mm_min,
        engrave_feed_mm_min=speed_mm_min,
        boundary_margin_mm=laser.boundary_margin_mm,
        spot_offset_x_mm=laser.spot_offset_x_mm,
        spot_offset_y_mm=laser.spot_offset_y_mm,
        optimize_order=True,
        # Capture performs a fresh guarded Home / park; do not guess a return
        # coordinate from the laser-only settings object.
        include_return_move=False,
    )
    return generate_vector_gcode(
        geometry,
        placement,
        options,
        work_area,
        design_name=design_name,
    )


def analyze_accuracy_measurements(
    measurements: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(measurements) != len(_VALIDATION_TARGET_FRACTIONS):
        raise CalibrationError("Accuracy validation requires all five holdout marks")
    commanded = np.asarray(
        [[item["machine_x"], item["machine_y"]] for item in measurements],
        dtype=np.float64,
    )
    observed = np.asarray(
        [[item["observed_x"], item["observed_y"]] for item in measurements],
        dtype=np.float64,
    )
    if not np.isfinite(commanded).all() or not np.isfinite(observed).all():
        raise CalibrationError("Accuracy-validation measurements must be finite")
    residuals = observed - commanded
    lengths = np.linalg.norm(residuals, axis=1)
    rms = float(np.sqrt(np.mean(np.square(lengths))))
    maximum = float(np.max(lengths))
    mean = residuals.mean(axis=0)
    seed_shift_ids = [
        int(item["id"]) for item in measurements if float(item.get("seed_shift_px", 0.0)) > _MAX_REVIEWED_SEED_SHIFT_PX
    ]
    scores = np.asarray([float(item.get("score", 1.0)) for item in measurements], dtype=np.float64)
    low_score = bool(
        len(scores) and (float(np.median(scores)) <= 0 or float(np.min(scores)) < float(np.median(scores)) * 0.22)
    )
    confidence_ok = not seed_shift_ids and not low_score
    passed = bool(confidence_ok and rms <= _VALIDATION_RMS_LIMIT_MM and maximum <= _VALIDATION_MAX_LIMIT_MM)
    if not confidence_ok:
        classification = "invalid"
        reason = (
            "One or more holdout crosses were detected with low confidence; use a clean "
            "surface and recapture instead of accepting this result"
        )
    elif passed:
        classification = "pass"
        reason = "Independent holdout marks pass the configured camera-to-laser accuracy limits"
    else:
        classification = "fail"
        reason = (
            "Independent holdout marks exceed the configured accuracy limits; do not "
            "treat the camera map as physically verified"
        )

    output_measurements = []
    for item, residual, error in zip(measurements, residuals, lengths, strict=True):
        output = dict(item)
        output["error_x_mm"] = float(residual[0])
        output["error_y_mm"] = float(residual[1])
        output["error_mm"] = float(error)
        output_measurements.append(output)
    return {
        "classification": classification,
        "passed": passed,
        "reason": reason,
        "point_count": len(measurements),
        "rms_error_mm": rms,
        "max_error_mm": maximum,
        "mean_error_x_mm": float(mean[0]),
        "mean_error_y_mm": float(mean[1]),
        "rms_limit_mm": _VALIDATION_RMS_LIMIT_MM,
        "max_limit_mm": _VALIDATION_MAX_LIMIT_MM,
        "low_confidence_ids": seed_shift_ids,
        "measurements": output_measurements,
    }


def analyze_registration_measurements(
    measurements: list[dict[str, Any]],
) -> dict[str, Any]:
    if len(measurements) < 4:
        raise CalibrationError("At least four measured registration marks are required")
    commanded = np.asarray(
        [[item["machine_x"], item["machine_y"]] for item in measurements],
        dtype=np.float64,
    )
    observed = np.asarray(
        [[item["observed_x"], item["observed_y"]] for item in measurements],
        dtype=np.float64,
    )
    if not np.isfinite(commanded).all() or not np.isfinite(observed).all():
        raise CalibrationError("Registration measurements must be finite")
    residuals = observed - commanded
    mean = residuals.mean(axis=0)
    centered = residuals - mean
    lengths = np.linalg.norm(residuals, axis=1)
    scatter_lengths = np.linalg.norm(centered, axis=1)
    rms = float(np.sqrt(np.mean(np.square(lengths))))
    maximum = float(np.max(lengths))
    scatter_rms = float(np.sqrt(np.mean(np.square(scatter_lengths))))
    scatter_max = float(np.max(scatter_lengths))
    correction = -mean
    correction_magnitude = float(np.linalg.norm(correction))

    if maximum > _MAX_MEASUREMENT_RESIDUAL_MM:
        classification = "invalid"
        reason = (
            "At least one mark is more than 8 mm from its commanded location; "
            "check mark identity, bed pose, restraint, and axis direction"
        )
    elif rms <= _ALIGNED_RMS_MM:
        classification = "aligned"
        reason = "Residual is already below the fine-registration threshold"
    elif (
        scatter_rms <= _TRANSLATION_SCATTER_RMS_MM
        and scatter_max <= _TRANSLATION_SCATTER_MAX_MM
        and correction_magnitude <= _MAX_APPLIED_TRANSLATION_MM
    ):
        classification = "translation"
        reason = "The mark errors are consistent with one global X/Y translation"
    else:
        classification = "position_dependent"
        reason = (
            "The residual changes across the bed; redo the full bed mapping or check "
            "camera/bed rigidity instead of applying a global offset"
        )

    output_measurements = []
    for item, residual in zip(measurements, residuals, strict=True):
        output = dict(item)
        output["error_x_mm"] = float(residual[0])
        output["error_y_mm"] = float(residual[1])
        output["error_mm"] = float(np.linalg.norm(residual))
        output_measurements.append(output)
    return {
        "classification": classification,
        "reason": reason,
        "can_apply_translation": classification == "translation",
        "point_count": len(measurements),
        "mean_error_x_mm": float(mean[0]),
        "mean_error_y_mm": float(mean[1]),
        "correction_x_mm": float(correction[0]),
        "correction_y_mm": float(correction[1]),
        "rms_error_mm": rms,
        "max_error_mm": maximum,
        "scatter_rms_mm": scatter_rms,
        "scatter_max_mm": scatter_max,
        "measurements": output_measurements,
    }


def suggested_registration_exclusions(
    measurements: list[dict[str, Any]],
) -> list[int]:
    suggested = []
    for item in measurements:
        residual = math.hypot(
            float(item["observed_x"]) - float(item["machine_x"]),
            float(item["observed_y"]) - float(item["machine_y"]),
        )
        seed_shift = float(item.get("seed_shift_px", 0.0))
        if residual > _MAX_MEASUREMENT_RESIDUAL_MM or seed_shift > _MAX_REVIEWED_SEED_SHIFT_PX:
            suggested.append(int(item["id"]))
    return suggested


def review_registration_measurements(
    measurements: list[dict[str, Any]],
    excluded_ids: list[int] | tuple[int, ...] | set[int],
) -> dict[str, Any]:
    available_ids = {int(item["id"]) for item in measurements}
    excluded = {int(value) for value in excluded_ids}
    unknown = excluded - available_ids
    if unknown:
        raise CalibrationError(
            "Unknown fine-registration point(s): " + ", ".join(str(value) for value in sorted(unknown))
        )
    if len(excluded) > _MAX_REVIEW_EXCLUSIONS:
        raise CalibrationError("At most two of the eight registration marks may be excluded")
    included = [item for item in measurements if int(item["id"]) not in excluded]
    if len(included) < max(4, len(measurements) - _MAX_REVIEW_EXCLUSIONS):
        raise CalibrationError("At least six of eight registration marks must remain in use")

    analysis = analyze_registration_measurements(included)
    bad_seed_ids = [
        int(item["id"]) for item in included if float(item.get("seed_shift_px", 0.0)) > _MAX_REVIEWED_SEED_SHIFT_PX
    ]
    scores = np.asarray([float(item.get("score", 1.0)) for item in included], dtype=np.float64)
    low_score = bool(
        len(scores) and (float(np.median(scores)) <= 0 or float(np.min(scores)) < float(np.median(scores)) * 0.22)
    )
    if bad_seed_ids or low_score:
        analysis.update(
            {
                "classification": "invalid",
                "can_apply_translation": False,
                "reason": (
                    "One or more included cross detections remain low confidence; "
                    "exclude only a visibly incorrect detection or recapture the marks"
                ),
            }
        )
    analysis["available_point_count"] = len(measurements)
    analysis["excluded_ids"] = sorted(excluded)
    if excluded:
        analysis["reason"] += " Reviewed exclusion(s): " + ", ".join(f"#{value}" for value in sorted(excluded)) + "."
    return analysis


def analyze_homography_refinement(
    measurements: list[dict[str, Any]],
    excluded_ids: list[int] | tuple[int, ...] | set[int],
    current_image_to_machine: np.ndarray,
    work_area: WorkArea,
) -> dict[str, Any]:
    """Fit and strictly gate a reviewed replacement camera-to-bed homography."""
    reviewed = review_registration_measurements(measurements, excluded_ids)
    excluded = {int(value) for value in reviewed["excluded_ids"]}
    selected = [item for item in measurements if int(item["id"]) not in excluded]
    if any("image_x" not in item or "image_y" not in item for item in selected):
        raise CalibrationError("Full-map refinement requires the original detected image coordinates")

    image_points = np.asarray([[item["image_x"], item["image_y"]] for item in selected], dtype=np.float64)
    target_points = np.asarray(
        [[item["machine_x"], item["machine_y"]] for item in selected],
        dtype=np.float64,
    )
    current = np.asarray(current_image_to_machine, dtype=np.float64)
    if (
        image_points.shape[0] < 4
        or not np.isfinite(image_points).all()
        or not np.isfinite(target_points).all()
        or current.shape != (3, 3)
        or not np.isfinite(current).all()
        or abs(float(np.linalg.det(current))) < 1e-12
    ):
        raise CalibrationError("Fine-registration homography inputs are invalid")

    homography, mask = cv2.findHomography(
        image_points,
        target_points,
        method=cv2.RANSAC,
        ransacReprojThreshold=_HOMOGRAPHY_RANSAC_THRESHOLD_MM,
        maxIters=5000,
        confidence=0.999,
    )
    if homography is None or mask is None:
        raise CalibrationError("OpenCV could not fit the reviewed full-bed refinement")
    if not np.isfinite(homography).all() or abs(float(np.linalg.det(homography))) < 1e-12:
        raise CalibrationError("The reviewed full-bed refinement is singular")

    predicted = cv2.perspectiveTransform(image_points.astype(np.float64).reshape(-1, 1, 2), homography).reshape(-1, 2)
    errors = np.linalg.norm(predicted - target_points, axis=1)
    inlier_mask = mask.reshape(-1).astype(bool)
    inlier_errors = errors[inlier_mask]
    inlier_ids = [int(item["id"]) for item, is_inlier in zip(selected, inlier_mask, strict=True) if is_inlier]
    ransac_outlier_ids = [
        int(item["id"]) for item, is_inlier in zip(selected, inlier_mask, strict=True) if not is_inlier
    ]
    rms = float(np.sqrt(np.mean(np.square(inlier_errors)))) if len(inlier_errors) else float("inf")
    maximum = float(np.max(inlier_errors)) if len(inlier_errors) else float("inf")

    inlier_targets = target_points[inlier_mask].astype(np.float32)
    hull = cv2.convexHull(inlier_targets) if len(inlier_targets) >= 3 else None
    coverage = (
        float(cv2.contourArea(hull)) / float(work_area.width * work_area.height)
        if hull is not None and work_area.width > 0 and work_area.height > 0
        else 0.0
    )
    span_x = (
        float(np.ptp(inlier_targets[:, 0])) / float(work_area.width)
        if len(inlier_targets) and work_area.width > 0
        else 0.0
    )
    span_y = (
        float(np.ptp(inlier_targets[:, 1])) / float(work_area.height)
        if len(inlier_targets) and work_area.height > 0
        else 0.0
    )

    # Compare the proposed map with the currently solved map over the whole bed,
    # not only at the registration marks. This catches flips, implausible scale,
    # and large extrapolated movement near a corner.
    relative = homography @ np.linalg.inv(current)
    xs = np.linspace(work_area.x_min, work_area.x_max, 5)
    ys = np.linspace(work_area.y_min, work_area.y_max, 5)
    grid = np.asarray([(x, y) for y in ys for x in xs], dtype=np.float64)
    refined_grid = cv2.perspectiveTransform(grid.reshape(-1, 1, 2), relative).reshape(-1, 2)
    correction_lengths = np.linalg.norm(refined_grid - grid, axis=1)
    correction_rms = float(np.sqrt(np.mean(np.square(correction_lengths))))
    correction_max = float(np.max(correction_lengths))

    step = min(work_area.width, work_area.height) * 0.005
    basis_points = np.vstack((grid, grid + (step, 0.0), grid + (0.0, step)))
    mapped_basis = cv2.perspectiveTransform(basis_points.reshape(-1, 1, 2), relative).reshape(-1, 2)
    count = len(grid)
    dx = (mapped_basis[count : count * 2] - mapped_basis[:count]) / step
    dy = (mapped_basis[count * 2 :] - mapped_basis[:count]) / step
    local_scale_x = np.linalg.norm(dx, axis=1)
    local_scale_y = np.linalg.norm(dy, axis=1)
    local_determinants = dx[:, 0] * dy[:, 1] - dx[:, 1] * dy[:, 0]

    failures: list[str] = []
    if reviewed["classification"] == "invalid" and "low confidence" in str(reviewed["reason"]).lower():
        failures.append("one or more selected cross detections are low confidence")
    if len(inlier_ids) < _HOMOGRAPHY_MIN_INLIERS:
        failures.append(f"needs at least {_HOMOGRAPHY_MIN_INLIERS} geometric inliers; found {len(inlier_ids)}")
    if len(excluded) + len(ransac_outlier_ids) > 1:
        failures.append("rejects more than one of the eight physical marks")
    if rms > _HOMOGRAPHY_MAX_RMS_MM or maximum > _HOMOGRAPHY_MAX_ERROR_MM:
        failures.append(f"fit error is too high ({rms:.3f} mm RMS, {maximum:.3f} mm maximum)")
    if coverage < _HOMOGRAPHY_MIN_COVERAGE_RATIO:
        failures.append(f"bed coverage is too small ({coverage:.0%})")
    if span_x < _HOMOGRAPHY_MIN_AXIS_SPAN_RATIO or span_y < _HOMOGRAPHY_MIN_AXIS_SPAN_RATIO:
        failures.append(f"mark span is too small (X {span_x:.0%}, Y {span_y:.0%})")
    if not np.isfinite(refined_grid).all() or correction_max > _HOMOGRAPHY_MAX_CORRECTION_MM:
        failures.append(f"proposed full-bed movement is too large ({correction_max:.3f} mm maximum)")
    if (
        np.any(local_determinants <= 0)
        or np.any(local_scale_x < _HOMOGRAPHY_MIN_LOCAL_SCALE)
        or np.any(local_scale_x > _HOMOGRAPHY_MAX_LOCAL_SCALE)
        or np.any(local_scale_y < _HOMOGRAPHY_MIN_LOCAL_SCALE)
        or np.any(local_scale_y > _HOMOGRAPHY_MAX_LOCAL_SCALE)
    ):
        failures.append("proposed map flips or excessively scales part of the bed")

    can_apply = not failures
    return {
        "classification": "full_map" if can_apply else "invalid",
        "can_apply_full_map": can_apply,
        "reason": (
            "The reviewed marks support a bounded full-bed homography refinement"
            if can_apply
            else "Full-map refinement refused: " + "; ".join(failures)
        ),
        "selected_count": len(selected),
        "inlier_count": len(inlier_ids),
        "inlier_ids": inlier_ids,
        "ransac_outlier_ids": ransac_outlier_ids,
        "excluded_ids": sorted(excluded),
        "rms_error_mm": rms,
        "max_error_mm": maximum,
        "coverage_ratio": coverage,
        "span_x_ratio": span_x,
        "span_y_ratio": span_y,
        "correction_rms_mm": correction_rms,
        "correction_max_mm": correction_max,
        "local_scale_min": float(min(local_scale_x.min(), local_scale_y.min())),
        "local_scale_max": float(max(local_scale_x.max(), local_scale_y.max())),
        "base_image_to_machine": current.tolist(),
        "image_to_machine": homography.tolist(),
        "machine_to_image": np.linalg.inv(homography).tolist(),
    }
