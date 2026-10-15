"""Historical camera-placement provenance; never restored machine authority."""
from __future__ import annotations

import copy
import json
import math
from typing import Any


def validate_material_metadata(value: Any) -> dict[str, Any]:
    keys = {"schema_version", "model_id", "datum_id", "binding_id", "capture_pose", "plan_id",
            "measurement_id", "surface_elevation_mm", "honeycomb_height_mm", "reference", "session",
            "evidence_source_ids"}
    if type(value) is not dict or set(value) != keys or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported or malformed material placement metadata")
    for key in ("model_id", "binding_id"):
        if type(value[key]) is not str or len(value[key]) != 64 or any(c not in "0123456789abcdef" for c in value[key]):
            raise ValueError("Material placement model identity is invalid")
    for key in ("datum_id", "plan_id", "measurement_id"):
        if type(value[key]) is not str or not 1 <= len(value[key]) <= 512:
            raise ValueError("Material placement measurement identity is invalid")
    for key in ("surface_elevation_mm", "honeycomb_height_mm"):
        if type(value[key]) not in (int, float) or not math.isfinite(value[key]):
            raise ValueError("Material placement height must be finite")
    if not 0 <= value["surface_elevation_mm"] - value["honeycomb_height_mm"] <= 20:
        raise ValueError("Material placement is outside the 0–20 mm range")
    pose = value["capture_pose"]
    if type(pose) is not list or len(pose) != 3 or any(
        not (index == 2 and v is None) and (type(v) not in (int, float) or not math.isfinite(v))
        for index, v in enumerate(pose)
    ):
        raise ValueError("Material placement capture pose is invalid")
    ids = value["evidence_source_ids"]
    if (type(ids) is not list or len(ids) != 3 or any(type(s) is not str or len(s) != 64
            or any(c not in "0123456789abcdef" for c in s) for s in ids) or len(set(ids)) != 3):
        raise ValueError("Material placement requires three independent evidence identities")
    if (type(value["reference"]) is not dict or not value["reference"]
            or type(value["session"]) is not list or not value["session"]):
        raise ValueError("Material placement reference/session provenance is invalid")
    if len(json.dumps(value, allow_nan=False).encode()) > 8192:
        raise ValueError("Material placement metadata exceeds its size limit")
    return copy.deepcopy(value)
