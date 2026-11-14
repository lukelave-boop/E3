"""Material settings in a clean Z checkpoint; never persisted job authority."""
from __future__ import annotations

import copy
import uuid

from ..errors import SafetyError
from .laser_focus import THICKNESS_POLICY, thickness_focus
from .z_probe import finite_number

CAPABILITY = "pi-saved-workpiece-v1"
FIELDS = {"schema_version", "surface_elevation_mm", "spacer_thickness_mm",
          "honeycomb_height_mm", "calibration_id", "focus_offset_mm", "focus_policy",
          "max_z_mm", "clearance_z_mm", "height_source"}


def validate_saved_workpiece(data, reference, clearance, maximum):
    if (type(data) is not dict or set(data) != FIELDS
            or type(data["schema_version"]) is not int or data["schema_version"] != 1
            or data["focus_policy"] != THICKNESS_POLICY
            or data["height_source"] not in ("measured", "manual")):
        raise SafetyError("Invalid saved material setup")
    try:
        if type(data["calibration_id"]) is not str or str(uuid.UUID(data["calibration_id"])) != data["calibration_id"]:
            raise ValueError("invalid calibration ID")
        offset = finite_number(data["focus_offset_mm"], "Saved focus offset", -100, 100)
        saved_max = finite_number(data["max_z_mm"], "Saved material maximum", 20, 80)
        saved_clearance = finite_number(data["clearance_z_mm"], "Saved material clearance", 20, saved_max)
        if saved_max != maximum or saved_clearance != clearance:
            raise SafetyError("Saved material Z limits changed")
        derived = thickness_focus(data["surface_elevation_mm"], data["spacer_thickness_mm"],
                                  data["honeycomb_height_mm"])
        contact = data["surface_elevation_mm"] + reference["border_z_mm"]
        finite_number(contact, "Saved contact", reference["geometry"]["min_mm"], clearance - 15)
        for gap in (derived["gap_mm"], 7.0):
            target = finite_number(contact + offset + gap - 7, "Saved focus target", 0, maximum)
            if target >= clearance:
                raise SafetyError("Saved material focus must be below clearance")
    except (ValueError, TypeError, KeyError, OverflowError) as exc:
        raise SafetyError("Invalid saved material setup") from exc
    return copy.deepcopy(data)


def capture_workpiece(focus, reference, maximum):
    plan = focus.job_plan
    if plan is None:
        data = focus.saved_workpiece
    elif plan.get("focus_policy") == THICKNESS_POLICY:
        if (plan["reference"] != reference or plan["calibration"] != focus.calibration
                or plan["session"] != focus.session or plan["maximum"] != maximum
                or plan["clearance_z_mm"] != focus.selected_clearance):
            raise SafetyError("Workpiece focus changed before shutdown")
        data = {key: plan[key] for key in ("surface_elevation_mm", "spacer_thickness_mm",
                                         "honeycomb_height_mm", "focus_policy")}
        data.update(schema_version=1, calibration_id=focus.calibration["id"],
                    focus_offset_mm=focus.calibration["focus_offset_mm"],
                    max_z_mm=maximum, clearance_z_mm=focus.selected_clearance,
                    height_source=plan.get("height_source", "measured"))
    else:
        return None  # Manual gauge previews are not saved material setups.
    if data is None:
        return None
    data = validate_saved_workpiece(data, reference, focus.selected_clearance, maximum)
    if not compatible(data, focus):
        raise SafetyError("Saved material calibration or honeycomb height changed")
    return data


def compatible(data, focus):
    calibration = focus.calibration
    return (calibration is not None and data["calibration_id"] == calibration["id"]
            and data["focus_offset_mm"] == calibration["focus_offset_mm"]
            and data["honeycomb_height_mm"] == focus.honeycomb_height_mm)


def reuse_workpiece(focus, *, firmware, geometry, maximum, session, xy, current, clearance):
    data = focus.saved_workpiece
    if data is None or focus.reference is None:
        raise SafetyError("No restored material setup; measure the workpiece")
    validate_saved_workpiece(data, focus.reference, clearance, maximum)
    if (not compatible(data, focus) or focus.calibration["firmware"] != firmware
            or focus.calibration["geometry"] != geometry or focus.requires_clearance
            or focus.xy_recovery_pending_reference or abs(current - clearance) > .05
            or clearance != focus.selected_clearance):
        raise SafetyError("Saved material setup changed or Z is not at saved clearance")
    derived = thickness_focus(data["surface_elevation_mm"], data["spacer_thickness_mm"],
                              data["honeycomb_height_mm"])
    contact = data["surface_elevation_mm"] + focus.reference["border_z_mm"]
    # A fresh job binding at the verified current carriage. No old session,
    # camera measurement, preview, XY target or job ID is restored.
    focus.job_plan = copy.deepcopy(dict(
        **derived, id=str(uuid.uuid4()), measurement_id=str(uuid.uuid4()),
        calibration_id=focus.calibration["id"], contact_z_mm=contact,
        target_z_mm=contact + data["focus_offset_mm"] + derived["gap_mm"] - 7,
        current_z_mm=current, clearance_z_mm=clearance, session=session,
        firmware=firmware, geometry=geometry, maximum=maximum,
        xy=list(xy), measurement_xy_mm=list(xy), laser_target_xy_mm=list(xy),
        calibration=focus.calibration, reference=focus.reference,
        reusable=True, parked_at_clearance=True, source="saved_workpiece",
        height_source=data["height_source"],
    ))
    if data["height_source"] == "manual":
        focus.job_plan["entered_height"] = {
            key: derived[key] for key in ("material_thickness_mm", "spacer_thickness_mm")
        }
    focus.surface = focus.preview = focus.xy_sequence = None
    focus.saved_workpiece = None
    focus.job_focus_required = True
