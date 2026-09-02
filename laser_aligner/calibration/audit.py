from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..config import Settings, effective_laser_output_area
from ..geometry.polygon import convex_polygon_contains, normalize_convex_polygon
from .support import HoneycombSupportReference

_SUPPORT_SPAN_EPSILON_MM = 1e-6


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


def honeycomb_support_validity(
    reference: HoneycombSupportReference | None,
    *,
    bed_calibration_created_at: float | None,
    expected_span_mm: float | None,
) -> dict[str, Any]:
    """Describe support evidence without inventing a physical ruler span."""

    if expected_span_mm is None:
        return {
            "state": "UNCONFIGURED",
            "reasons": [
                "The running machine has no configured physical honeycomb ruler span"
            ],
            "reason_codes": ["honeycomb.span_missing"],
            "expected_span_mm": None,
            "recorded_size_mm": (
                None
                if reference is None
                else [
                    float(reference.support_width_mm),
                    float(reference.support_height_mm),
                ]
            ),
            "execution_verifiable": False,
        }
    expected = float(expected_span_mm)
    if not math.isfinite(expected) or expected <= 0.0:
        raise ValueError("Expected honeycomb span must be finite and positive")
    if reference is None:
        return {
            "state": "MISSING",
            "reasons": ["No honeycomb support reference is recorded"],
            "reason_codes": ["honeycomb.reference_missing"],
            "expected_span_mm": expected,
            "recorded_size_mm": None,
            "execution_verifiable": False,
        }

    reasons: list[str] = []
    reason_codes: list[str] = []
    if bed_calibration_created_at is None:
        reasons.append("No active camera-to-machine bed map is installed")
        reason_codes.append("honeycomb.bed_map_missing")
    elif (
        abs(float(reference.bed_calibration_created_at) - bed_calibration_created_at)
        > 1e-9
    ):
        reasons.append(
            "The camera-to-machine bed map changed after this support was recorded"
        )
        reason_codes.append("honeycomb.bed_map_changed")
    width = float(reference.support_width_mm)
    height = float(reference.support_height_mm)
    if (
        abs(width - expected) > _SUPPORT_SPAN_EPSILON_MM
        or abs(height - expected) > _SUPPORT_SPAN_EPSILON_MM
    ):
        reasons.append(
            f"The saved support is {width:g} x {height:g} mm, but the running "
            f"machine is configured for {expected:g} x {expected:g} mm"
        )
        reason_codes.append("honeycomb.span_mismatch")
    if not reference.is_execution_verifiable:
        reasons.append(
            "The saved support does not contain accepted automatic four-edge evidence"
        )
        reason_codes.append("honeycomb.evidence_not_execution_verifiable")
    return {
        "state": "CURRENT" if not reasons else "STALE",
        "reasons": reasons,
        "reason_codes": reason_codes,
        "expected_span_mm": expected,
        "recorded_size_mm": [width, height],
        "execution_verifiable": bool(reference.is_execution_verifiable and not reasons),
    }


def source_to_display_pixel(
    source_x: float,
    source_y: float,
    *,
    source_width: int,
    source_height: int,
    rotation_degrees: int,
) -> tuple[float, float]:
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
    """Trace a corrected camera pixel without granting motion/output authority."""

    image_x = float(source_image_point[0])
    image_y = float(source_image_point[1])
    width, height = source_image_size
    display = source_to_display_pixel(
        image_x,
        image_y,
        source_width=int(width),
        source_height=int(height),
        rotation_degrees=int(settings.camera.view_rotation_degrees),
    )
    machine = tuple(float(value) for value in bed.image_to_mm(image_x, image_y))
    if len(machine) != 2 or not all(math.isfinite(value) for value in machine):
        raise ValueError("Bed mapping produced a non-finite machine coordinate")
    spot = (
        float(settings.laser.spot_offset_x_mm),
        float(settings.laser.spot_offset_y_mm),
    )
    carriage = (machine[0] - spot[0], machine[1] - spot[1])
    work = settings.machine.work_area
    beam_authority = normalize_convex_polygon(
        _output_polygon(settings), label="guarded beam authority"
    )
    carriage_authority = normalize_convex_polygon(
        _base_authority_polygon(settings), label="guarded carriage authority"
    )
    local = (
        None
        if support_reference is None
        else tuple(float(value) for value in support_reference.machine_to_local(*machine))
    )
    inside_support = (
        None
        if local is None
        else bool(
            -1e-6 <= local[0] <= float(support_reference.support_width_mm) + 1e-6
            and -1e-6
            <= local[1]
            <= float(support_reference.support_height_mm) + 1e-6
        )
    )
    return {
        "display_pixel": list(display),
        "lens_corrected_source_pixel": [image_x, image_y],
        "machine_mm": list(machine),
        "desired_beam_mm": list(machine),
        "honeycomb_local_mm": None if local is None else list(local),
        "spot_corrected_carriage_mm": list(carriage),
        "inside_machine_work_area": bool(work.contains(*machine)),
        "carriage_inside_machine_work_area": bool(work.contains(*carriage)),
        "inside_guarded_beam_authority": bool(
            convex_polygon_contains(machine, beam_authority)
        ),
        "inside_guarded_carriage_authority": bool(
            convex_polygon_contains(carriage, carriage_authority)
        ),
        "inside_honeycomb": inside_support,
    }


def build_coordinate_audit_status(
    settings: Settings,
    *,
    machine_identity: Mapping[str, Any],
    active_calibration_profile_id: str,
    active_bed_mapping_digest: str | None,
    machine_status: Mapping[str, Any],
    camera_status: Mapping[str, Any],
    camera_readiness: Mapping[str, Any],
    lens_status: Mapping[str, Any],
    bed_status: Mapping[str, Any],
    support_reference: HoneycombSupportReference | None,
    honeycomb_execution_signature: tuple[Any, ...] | None,
    capture_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-safe read-only view of the active coordinate frames."""

    calibration = bed_status.get("calibration")
    calibration_map = calibration if isinstance(calibration, Mapping) else {}
    validity = bed_status.get("validity")
    bed_validity = validity if isinstance(validity, Mapping) else {}
    created_raw = calibration_map.get("created_at")
    bed_created_at = (
        float(created_raw)
        if type(created_raw) in {int, float} and math.isfinite(float(created_raw))
        else None
    )
    support = honeycomb_support_validity(
        support_reference,
        bed_calibration_created_at=bed_created_at,
        expected_span_mm=settings.machine.honeycomb_span_mm,
    )
    output_polygon = _output_polygon(settings)
    support.update(
        {
            "origin_machine_mm": None,
            "rotation_degrees": None,
            "measured_spans_mm": None,
            "raw_corners_machine_mm": None,
            "rigid_corners_machine_mm": None,
            "output_polygon_local_mm": None,
        }
    )
    if support_reference is not None:
        frame = support_reference.coordinate_frame
        support.update(
            {
                "origin_machine_mm": list(frame.origin_machine_mm),
                "rotation_degrees": math.degrees(
                    math.atan2(frame.x_axis_machine[1], frame.x_axis_machine[0])
                ),
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

    capture = dict(capture_snapshot) if isinstance(capture_snapshot, Mapping) else {}
    captured_bed = capture.get("bed_calibration_created_at")
    captured_bed_digest = capture.get("bed_mapping_digest")
    capture_bed_current = bool(
        capture
        and bed_created_at is not None
        and type(captured_bed) in {int, float}
        and math.isfinite(float(captured_bed))
        and abs(float(captured_bed) - bed_created_at) <= 1e-9
        and type(captured_bed_digest) is str
        and captured_bed_digest == active_bed_mapping_digest
    )
    trusted_at_capture = bool(
        capture.get("trusted_at_capture") is True and capture_bed_current
    )
    capture_state = (
        "MISSING"
        if not capture
        else "STALE"
        if not capture_bed_current
        else "CURRENT"
        if trusted_at_capture
        else "UNTRUSTED"
    )
    capture.update(
        {
            "state": capture_state,
            "bed_map_current": capture_bed_current,
            "trusted_at_capture": trusted_at_capture,
        }
    )

    expected_calibration = machine_identity.get("expected_calibration_profile_id")
    binding_matches = bool(
        expected_calibration
        and str(expected_calibration) == str(active_calibration_profile_id)
    )
    blockers: list[str] = []

    def add(values: Any) -> None:
        for value in values or ():
            message = str(value).strip()
            if message and message not in blockers:
                blockers.append(message)

    if not expected_calibration:
        add(("The running machine has no expected calibration profile binding",))
    elif not binding_matches:
        add(
            (
                "Calibration binding mismatch: running machine expects "
                f"{expected_calibration!s}, but active profile is "
                f"{active_calibration_profile_id}",
            )
        )
    camera_state = str(camera_readiness.get("state") or "UNKNOWN")
    if camera_state != "READY":
        add(camera_readiness.get("reasons") or ("Camera is not calibration-ready",))
    lens_model = lens_status.get("model")
    if not isinstance(lens_model, Mapping):
        add(("No accepted lens model is active",))
    bed_state = str(bed_validity.get("state") or "MISSING")
    if bed_state != "VALID":
        add(bed_validity.get("reasons") or ("The bed map is not valid",))
    if support["state"] != "CURRENT":
        add(support["reasons"])
    elif not support.get("execution_verifiable"):
        add(("The support lacks accepted automatic four-edge evidence",))
    elif honeycomb_execution_signature is None:
        add(("The support teaching image or bed-map binding is not current",))
    if settings.machine.backend == "serial" and not machine_status.get("connected"):
        add(("The controller is not connected",))
    if settings.machine.backend == "serial" and capture_state != "CURRENT":
        add(("A trusted capture-time controller pose is not available",))

    if not expected_calibration or not binding_matches:
        next_action = "Select the calibration profile explicitly bound to this running machine."
    elif settings.machine.honeycomb_span_mm is None:
        next_action = (
            "Configure the measured physical honeycomb ruler span for this saved machine."
        )
    elif camera_state != "READY":
        next_action = "Restore the configured camera mode and resolve its readiness reasons."
    elif not isinstance(lens_model, Mapping):
        next_action = "Complete Lens calibration for the active camera profile."
    elif bed_state != "VALID":
        next_action = "Complete a fresh keyed camera-to-machine bed map."
    elif settings.machine.backend == "serial" and not machine_status.get("connected"):
        next_action = "Connect the controller, then use the explicit audit capture action."
    elif settings.machine.backend == "serial" and capture_state != "CURRENT":
        next_action = (
            "Run Home / park and capture audit view to record the controller pose at capture."
        )
    elif support["state"] != "CURRENT" or not support.get("execution_verifiable"):
        next_action = "Capture and accept a current automatic four-edge honeycomb reference."
    elif honeycomb_execution_signature is None:
        next_action = "Accept a fresh support teaching image bound to the current bed map."
    else:
        next_action = "All read-only Coordinate Audit dependencies are current."

    work = settings.machine.work_area
    return {
        "overall_state": "READY" if not blockers else "BLOCKED",
        "blockers": blockers,
        "required_next_action": next_action,
        "machine_identity": {
            **dict(machine_identity),
            "active_calibration_profile_id": active_calibration_profile_id,
            "calibration_binding_matches": binding_matches,
        },
        "machine": {
            **dict(machine_status),
            "work_area_mm": [work.x_min, work.x_max, work.y_min, work.y_max],
            "photo_position_mm": [
                settings.machine.photo_x,
                settings.machine.photo_y,
                settings.machine.photo_z,
            ],
            "capture_pose": capture,
        },
        "laser": {
            "output_authority_kind": (
                "configured convex polygon"
                if settings.laser.guarded_output_polygon_mm is not None
                else "work rectangle reduced by boundary margin and spot offset"
            ),
            "boundary_margin_mm": settings.laser.boundary_margin_mm,
            "spot_offset_mm": [
                settings.laser.spot_offset_x_mm,
                settings.laser.spot_offset_y_mm,
            ],
            "output_polygon_machine_mm": [list(point) for point in output_polygon],
            "carriage_authority_polygon_machine_mm": [
                list(point) for point in _base_authority_polygon(settings)
            ],
        },
        "camera": dict(camera_status),
        "camera_readiness": dict(camera_readiness),
        "lens": dict(lens_status),
        "bed_map": dict(bed_status),
        "honeycomb": support,
        "coordinate_frame_legend": [
            "Display pixel: rotated presentation only",
            "Corrected/source pixel: lens-corrected camera image",
            "Machine / desired beam: active bed-map millimetres",
            "Honeycomb local: accepted support origin and axes",
            "Carriage: desired beam minus configured laser spot offset",
        ],
    }
