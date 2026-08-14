from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..config import Settings, effective_laser_output_area
from ..geometry.polygon import convex_polygon_contains, normalize_convex_polygon
from .support import HoneycombSupportReference

_SUPPORT_SPAN_EPSILON_MM = 1e-6


def honeycomb_support_validity(
    reference: HoneycombSupportReference | None,
    *,
    bed_calibration_created_at: float | None,
    expected_span_mm: float,
) -> dict[str, Any]:
    """Describe whether one saved support belongs to the active machine profile.

    The expected physical ruler span is independent from output margins. A saved
    190 mm support therefore becomes stale when the active profile declares a
    191 mm physical surface; it is never silently stretched.
    """

    expected = float(expected_span_mm)
    if not math.isfinite(expected) or expected <= 0.0:
        raise ValueError("Expected honeycomb span must be finite and positive")
    if reference is None:
        return {
            "state": "MISSING",
            "reasons": ["No honeycomb support reference is recorded"],
            "expected_span_mm": expected,
            "recorded_size_mm": None,
            "execution_verifiable": False,
        }

    reasons: list[str] = []
    if bed_calibration_created_at is None:
        reasons.append("No active camera-to-machine bed map is installed")
    elif (
        abs(float(reference.bed_calibration_created_at) - float(bed_calibration_created_at))
        > 1e-9
    ):
        reasons.append("The camera-to-machine bed map changed after this support was recorded")

    recorded_width = float(reference.support_width_mm)
    recorded_height = float(reference.support_height_mm)
    if (
        abs(recorded_width - expected) > _SUPPORT_SPAN_EPSILON_MM
        or abs(recorded_height - expected) > _SUPPORT_SPAN_EPSILON_MM
    ):
        reasons.append(
            "The saved support is "
            f"{recorded_width:g} x {recorded_height:g} mm, but the active machine "
            f"profile requires {expected:g} x {expected:g} mm"
        )

    return {
        "state": "CURRENT" if not reasons else "STALE",
        "reasons": reasons,
        "expected_span_mm": expected,
        "recorded_size_mm": [recorded_width, recorded_height],
        "execution_verifiable": bool(reference.is_execution_verifiable and not reasons),
    }


def _rect_polygon(area: Any) -> tuple[tuple[float, float], ...]:
    return (
        (float(area.x_min), float(area.y_min)),
        (float(area.x_max), float(area.y_min)),
        (float(area.x_max), float(area.y_max)),
        (float(area.x_min), float(area.y_max)),
    )


def _base_authority_polygon(settings: Settings) -> tuple[tuple[float, float], ...]:
    configured = settings.laser.guarded_output_polygon_mm
    if configured is not None:
        return tuple((float(x), float(y)) for x, y in configured)
    work = settings.machine.work_area
    margin = float(settings.laser.boundary_margin_mm)
    return (
        (float(work.x_min) + margin, float(work.y_min) + margin),
        (float(work.x_max) - margin, float(work.y_min) + margin),
        (float(work.x_max) - margin, float(work.y_max) - margin),
        (float(work.x_min) + margin, float(work.y_max) - margin),
    )


def _output_polygon(settings: Settings) -> tuple[tuple[float, float], ...]:
    configured = settings.laser.guarded_output_polygon_mm
    if configured is not None:
        return tuple((float(x), float(y)) for x, y in configured)
    area = effective_laser_output_area(
        settings.machine.work_area,
        settings.laser.boundary_margin_mm,
        settings.laser.spot_offset_x_mm,
        settings.laser.spot_offset_y_mm,
    )
    return _rect_polygon(area)


def source_to_display_pixel(
    source_x: float,
    source_y: float,
    *,
    source_width: int,
    source_height: int,
    rotation_degrees: int,
) -> tuple[float, float]:
    """Map one source-image point into the configured presentation rotation."""

    x = float(source_x)
    y = float(source_y)
    if not math.isfinite(x) or not math.isfinite(y):
        raise ValueError("Image point must be finite")
    if type(source_width) is not int or source_width <= 0:
        raise ValueError("Source image width must be a positive integer")
    if type(source_height) is not int or source_height <= 0:
        raise ValueError("Source image height must be a positive integer")
    if rotation_degrees == 90:
        return float(source_height - 1) - y, x
    if rotation_degrees == 180:
        return float(source_width - 1) - x, float(source_height - 1) - y
    if rotation_degrees == 270:
        return y, float(source_width - 1) - x
    if rotation_degrees != 0:
        raise ValueError("Image view rotation must be 0, 90, 180, or 270 degrees")
    return x, y


def inspect_coordinate_point(
    settings: Settings,
    bed: Any,
    *,
    source_image_point: tuple[float, float],
    source_image_size: tuple[int, int],
    support_reference: HoneycombSupportReference | None,
) -> dict[str, Any]:
    """Trace one clicked corrected pixel through the active coordinate frames."""

    image_x = float(source_image_point[0])
    image_y = float(source_image_point[1])
    width, height = source_image_size
    display_x, display_y = source_to_display_pixel(
        image_x,
        image_y,
        source_width=int(width),
        source_height=int(height),
        rotation_degrees=int(settings.camera.view_rotation_degrees),
    )
    machine_x, machine_y = bed.image_to_mm(image_x, image_y)
    machine_point = (float(machine_x), float(machine_y))
    if not all(math.isfinite(value) for value in machine_point):
        raise ValueError("Bed mapping produced a non-finite machine coordinate")

    work = settings.machine.work_area
    output_polygon = normalize_convex_polygon(
        _output_polygon(settings),
        label="guarded laser-output polygon",
    )
    base_authority_polygon = normalize_convex_polygon(
        _base_authority_polygon(settings),
        label="base laser-output authority",
    )
    spot_x = float(settings.laser.spot_offset_x_mm)
    spot_y = float(settings.laser.spot_offset_y_mm)
    carriage_point = (machine_point[0] - spot_x, machine_point[1] - spot_y)
    beam_inside_base_authority = convex_polygon_contains(
        machine_point,
        base_authority_polygon,
    )
    carriage_inside_base_authority = convex_polygon_contains(
        carriage_point,
        base_authority_polygon,
    )
    local_point = (
        None
        if support_reference is None
        else tuple(
            float(value)
            for value in support_reference.machine_to_local(*machine_point)
        )
    )
    inside_support = (
        None
        if local_point is None
        else bool(
            -1e-6 <= local_point[0] <= float(support_reference.support_width_mm) + 1e-6
            and -1e-6
            <= local_point[1]
            <= float(support_reference.support_height_mm) + 1e-6
        )
    )
    return {
        "display_pixel": [display_x, display_y],
        "lens_corrected_source_pixel": [image_x, image_y],
        "machine_mm": list(machine_point),
        "honeycomb_local_mm": None if local_point is None else list(local_point),
        "spot_corrected_carriage_mm": list(carriage_point),
        "inside_machine_work_area": bool(
            float(work.x_min) - 1e-6 <= machine_point[0] <= float(work.x_max) + 1e-6
            and float(work.y_min) - 1e-6
            <= machine_point[1]
            <= float(work.y_max) + 1e-6
        ),
        "carriage_inside_machine_work_area": bool(
            float(work.x_min) - 1e-6 <= carriage_point[0] <= float(work.x_max) + 1e-6
            and float(work.y_min) - 1e-6
            <= carriage_point[1]
            <= float(work.y_max) + 1e-6
        ),
        "inside_guarded_beam_authority": beam_inside_base_authority,
        "inside_guarded_carriage_authority": carriage_inside_base_authority,
        "inside_guarded_laser_output": bool(
            convex_polygon_contains(machine_point, output_polygon)
            and beam_inside_base_authority
            and carriage_inside_base_authority
        ),
        "inside_honeycomb": inside_support,
    }


def build_coordinate_audit_status(
    settings: Settings,
    *,
    machine_status: Mapping[str, Any],
    camera_status: Mapping[str, Any],
    camera_readiness: Mapping[str, Any],
    lens_status: Mapping[str, Any],
    bed_status: Mapping[str, Any],
    support_reference: HoneycombSupportReference | None,
    honeycomb_execution_signature: tuple[Any, ...] | None,
) -> dict[str, Any]:
    """Build one JSON-safe, read-only view of every active coordinate frame."""

    calibration = bed_status.get("calibration")
    calibration_mapping = calibration if isinstance(calibration, Mapping) else {}
    bed_validity = bed_status.get("validity")
    bed_validity_mapping = bed_validity if isinstance(bed_validity, Mapping) else {}
    bed_created_at_raw = calibration_mapping.get("created_at")
    bed_created_at = (
        float(bed_created_at_raw)
        if type(bed_created_at_raw) in {int, float}
        and math.isfinite(float(bed_created_at_raw))
        else None
    )
    support_validity = honeycomb_support_validity(
        support_reference,
        bed_calibration_created_at=bed_created_at,
        expected_span_mm=settings.calibration.bed.honeycomb_span_mm,
    )

    output_polygon = _output_polygon(settings)
    support_payload: dict[str, Any] = {
        **support_validity,
        "origin_machine_mm": None,
        "rotation_degrees": None,
        "measured_spans_mm": None,
        "raw_corners_machine_mm": None,
        "rigid_corners_machine_mm": None,
        "output_polygon_local_mm": None,
    }
    if support_reference is not None:
        frame = support_reference.coordinate_frame
        rotation = math.degrees(
            math.atan2(float(frame.x_axis_machine[1]), float(frame.x_axis_machine[0]))
        )
        support_payload.update(
            {
                "origin_machine_mm": list(frame.origin_machine_mm),
                "rotation_degrees": float(rotation),
                "measured_spans_mm": list(support_reference.measured_ruler_span_mm),
                "raw_corners_machine_mm": [
                    list(point) for point in support_reference.support_corners_machine_mm
                ],
                "rigid_corners_machine_mm": [
                    list(point)
                    for point in support_reference.rigid_support_corners_machine_mm
                ],
                "output_polygon_local_mm": [
                    list(support_reference.machine_to_local(x, y))
                    for x, y in output_polygon
                ],
            }
        )

    coordinate_state_raw = machine_status.get("coordinate_state_reference")
    coordinate_state = (
        dict(coordinate_state_raw)
        if isinstance(coordinate_state_raw, Mapping)
        else None
    )
    lens_model_raw = lens_status.get("model")
    lens_model = lens_model_raw if isinstance(lens_model_raw, Mapping) else None
    model_id = None if lens_model is None else lens_model.get("model_id")
    camera_state = str(camera_readiness.get("state") or "UNKNOWN")
    bed_state = str(bed_validity_mapping.get("state") or "MISSING")

    blockers: list[str] = []

    def add_blockers(values: Any) -> None:
        for value in values or ():
            message = str(value).strip()
            if message and message not in blockers:
                blockers.append(message)

    if camera_state != "READY":
        add_blockers(camera_readiness.get("reasons", ()))
    if lens_model is None:
        add_blockers(("No accepted lens model is active",))
    if bed_state != "VALID":
        add_blockers(
            bed_validity_mapping.get("reasons")
            or ("The camera-to-machine bed map is not valid",)
        )
    if support_validity["state"] != "CURRENT":
        add_blockers(support_validity["reasons"])
    elif not support_reference or not support_reference.is_execution_verifiable:
        add_blockers(("The support lacks accepted automatic four-edge evidence",))
    elif honeycomb_execution_signature is None:
        add_blockers(
            ("The support teaching image or bed-map binding is not execution-current",)
        )

    if camera_state != "READY":
        required_next_action = (
            "Open Camera setup, restore the configured camera mode, and resolve the "
            "listed readiness reasons."
        )
    elif lens_model is None:
        required_next_action = (
            "Complete Lens calibration for the current resolution and locked focus."
        )
    elif bed_state != "VALID":
        required_next_action = (
            "Complete a fresh keyed 5 × 5 camera-to-machine base map, then capture "
            "the machine-grid overlay."
        )
    elif support_validity["state"] != "CURRENT":
        required_next_action = (
            "Capture view, run automatic four-edge honeycomb detection with the "
            f"configured {settings.calibration.bed.honeycomb_span_mm:g} mm span, "
            "review the inner cutting-surface outline, and accept it."
        )
    elif not support_reference or not support_reference.is_execution_verifiable:
        required_next_action = (
            "Replace the legacy or fallback support with an accepted automatic "
            "four-edge honeycomb reference."
        )
    elif honeycomb_execution_signature is None:
        required_next_action = (
            "Capture view and accept a fresh automatic four-edge teaching image "
            "bound to the current bed map."
        )
    else:
        required_next_action = (
            "No coordinate dependency is currently blocking support-bound work."
        )

    work = settings.machine.work_area
    trusted_position_raw = machine_status.get("jog_position_mm")
    trusted_position = (
        dict(trusted_position_raw)
        if isinstance(trusted_position_raw, Mapping)
        else None
    )
    return {
        "overall_state": "READY" if not blockers else "BLOCKED",
        "blockers": blockers,
        "required_next_action": required_next_action,
        "machine": {
            "connected": bool(machine_status.get("connected")),
            "backend": str(machine_status.get("backend") or settings.machine.backend),
            "protocol": str(machine_status.get("protocol") or ""),
            "coordinate_reference_ready": bool(
                machine_status.get("coordinate_reference_ready")
            ),
            "coordinate_state_reference": coordinate_state,
            "trusted_position_mm": trusted_position,
            "work_area_mm": [
                float(work.x_min),
                float(work.x_max),
                float(work.y_min),
                float(work.y_max),
            ],
            "photo_position_mm": [
                float(settings.machine.photo_x),
                float(settings.machine.photo_y),
                None
                if settings.machine.photo_z is None
                else float(settings.machine.photo_z),
            ],
        },
        "laser": {
            "output_authority_kind": (
                "explicit polygon"
                if settings.laser.guarded_output_polygon_mm is not None
                else "work-area rectangle after margin and spot offset"
            ),
            "output_polygon_machine_mm": [list(point) for point in output_polygon],
            "boundary_margin_mm": float(settings.laser.boundary_margin_mm),
            "spot_offset_mm": [
                float(settings.laser.spot_offset_x_mm),
                float(settings.laser.spot_offset_y_mm),
            ],
        },
        "camera": {
            "connected": bool(camera_status.get("connected")),
            "resolution": [
                int(camera_status.get("width") or 0),
                int(camera_status.get("height") or 0),
            ],
            "configured_resolution": [
                int(settings.camera.width),
                int(settings.camera.height),
            ],
            "display_rotation_degrees": int(settings.camera.view_rotation_degrees),
            "readiness_state": camera_state,
            "readiness_reasons": [
                str(reason) for reason in camera_readiness.get("reasons", ())
            ],
        },
        "lens": {
            "state": "CURRENT" if lens_model is not None else "MISSING",
            "model_id": None if model_id is None else str(model_id),
            "image_size": None if lens_model is None else lens_model.get("image_size"),
        },
        "bed_map": {
            "state": bed_state,
            "reasons": [
                str(reason) for reason in bed_validity_mapping.get("reasons", ())
            ],
            "created_at": bed_created_at,
            "rms_error_mm": calibration_mapping.get("rms_error_mm"),
            "max_error_mm": calibration_mapping.get("max_error_mm"),
            "point_count": calibration_mapping.get("point_count"),
            "inlier_count": calibration_mapping.get("inlier_count"),
            "axis_mapping": bed_status.get("axis_mapping"),
        },
        "honeycomb": support_payload,
    }


__all__ = [
    "build_coordinate_audit_status",
    "honeycomb_support_validity",
    "inspect_coordinate_point",
    "source_to_display_pixel",
]
