"""Strict material-plane provenance embedded in job bytes, never controller G-code."""

from __future__ import annotations

import base64
import binascii
import copy
import json
import math
import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..errors import SafetyError
from ..storage import strict_json_loads

PREFIX = "E3SURFACE"
CAPABILITY = "pi-material-surface-v1"
_MAX_ENCODED = 16_384
_DIGEST = re.compile(r"[0-9a-f]{64}")
_TOKEN = re.compile(r"[A-Za-z0-9_-]+={0,2}")
_FIELDS = {
    "schema_version", "model_id", "datum_id", "binding_id", "capture_pose",
    "plan_id", "measurement_id", "surface_elevation_mm", "honeycomb_height_mm",
    "reference", "session", "evidence_source_ids",
}


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SafetyError(f"Material surface {label} must be finite")
    return float(value)


def _identity(value: Any, label: str) -> str:
    if type(value) is not str or not 1 <= len(value) <= 512 or not value.strip():
        raise SafetyError(f"Material surface {label} identity is missing")
    return value


def _json(value: Any) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise SafetyError("Material surface binding must be finite JSON") from exc


def validate_metadata(raw: Any) -> dict[str, Any]:
    if type(raw) is not dict or set(raw) != _FIELDS or type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise SafetyError("Invalid material surface binding schema")
    for field in ("model_id", "binding_id"):
        if type(raw[field]) is not str or _DIGEST.fullmatch(raw[field]) is None:
            raise SafetyError(f"Invalid material surface {field}")
    sources = raw["evidence_source_ids"]
    if (
        type(sources) is not list or len(sources) != 3
        or any(type(value) is not str or _DIGEST.fullmatch(value) is None for value in sources)
        or len(set(sources)) != 3
    ):
        raise SafetyError("Material surface requires three distinct evidence identities")
    for field in ("datum_id", "plan_id", "measurement_id"):
        _identity(raw[field], field)
    pose = raw["capture_pose"]
    if type(pose) is not list or len(pose) != 3:
        raise SafetyError("Invalid material surface capture pose")
    _number(pose[0], "capture X")
    _number(pose[1], "capture Y")
    if pose[2] is not None:
        _number(pose[2], "capture Z")
    elevation = _number(raw["surface_elevation_mm"], "elevation")
    honeycomb = _number(raw["honeycomb_height_mm"], "honeycomb height")
    if not 0 <= elevation - honeycomb <= 20:
        raise SafetyError("Material surface must be 0 to 20 mm above the saved honeycomb, including supports")
    if type(raw["reference"]) is not dict or not raw["reference"]:
        raise SafetyError("Material surface border reference is missing")
    if type(raw["session"]) is not list or len(raw["session"]) != 3:
        raise SafetyError("Material surface controller session is missing")
    if any(type(value) is not int or value < 0 for value in raw["session"]):
        raise SafetyError("Invalid material surface controller session")
    encoded = _json(raw).encode("utf-8")
    if len(base64.urlsafe_b64encode(encoded)) > _MAX_ENCODED:
        raise SafetyError("Material surface binding exceeds its size limit")
    return copy.deepcopy(raw)


def parse(line: str) -> dict[str, Any] | None:
    if not line.startswith(PREFIX):
        return None
    parts = line.split(" ")
    if len(parts) != 3 or parts[:2] != [PREFIX, "1"] or not 1 <= len(parts[2]) <= _MAX_ENCODED:
        raise SafetyError("Invalid material surface instruction")
    if _TOKEN.fullmatch(parts[2]) is None:
        raise SafetyError("Invalid material surface encoding")
    try:
        decoded = base64.b64decode(parts[2], altchars=b"-_", validate=True)
        raw = strict_json_loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, binascii.Error, RecursionError) as exc:
        raise SafetyError("Invalid material surface encoding") from exc
    result = validate_metadata(raw)
    if base64.urlsafe_b64encode(_json(result).encode("utf-8")).decode("ascii") != parts[2]:
        raise SafetyError("Material surface binding must use canonical encoding")
    return result


def program_binding(lines: Iterable[str]) -> dict[str, Any] | None:
    # Callers pass the canonical executable sequence (comments already removed).
    sequence = tuple(lines)
    found = None
    for index, line in enumerate(sequence):
        binding = parse(line)
        if binding is None:
            continue
        allowed_index = 1 if sequence and sequence[0].startswith("E3FOCUS ") else 0
        if found is not None or index != allowed_index:
            raise SafetyError("Material surface binding must occur once, after optional measured job focus")
        if allowed_index == 1 and sequence[0] != "E3FOCUS " + binding["plan_id"]:
            raise SafetyError("Material surface and measured job focus select different plans")
        found = binding
    return found


def attach(text: str, metadata: dict[str, Any] | None) -> str:
    if metadata is None:
        return text
    validated = validate_metadata(metadata)
    # Local import keeps the pure wire format independent of MachineService.
    from ..gcode.preview import strip_comment
    raw_lines = text.splitlines()
    executable = [strip_comment(line) for line in raw_lines if strip_comment(line)]
    existing = program_binding(executable)
    if existing is not None:
        if existing != validated:
            raise SafetyError("Existing material surface binding differs; regenerate the job")
        return text
    encoded = base64.urlsafe_b64encode(_json(validated).encode("utf-8")).decode("ascii")
    directive = f"{PREFIX} 1 {encoded}"
    insertion = 0
    if executable and executable[0].startswith("E3FOCUS "):
        insertion = next(index + 1 for index, line in enumerate(raw_lines) if strip_comment(line))
    raw_lines.insert(insertion, directive)
    result = "\n".join(raw_lines)
    program_binding([strip_comment(line) for line in raw_lines if strip_comment(line)])
    return result


def snapshot_from_plan(plan: Mapping[str, Any], *, honeycomb_height_mm: float | None = None) -> dict[str, Any]:
    """Normalize an already validated selected-focus snapshot without hardware I/O."""
    result = copy.deepcopy(dict(plan))
    _identity(result.get("id"), "plan")
    _identity(result.get("measurement_id"), "measurement")
    if result.get("reusable") is not True or not isinstance(result.get("reference"), Mapping):
        raise SafetyError("A current reusable measured surface is required")
    reference = result["reference"]
    contact = _number(result.get("contact_z_mm"), "contact Z")
    border = _number(reference.get("border_z_mm"), "border Z")
    elevation = contact - border
    if "surface_elevation_mm" in result and _number(result["surface_elevation_mm"], "elevation") != elevation:
        raise SafetyError("Measured surface elevation differs from its border contact reference")
    result["surface_elevation_mm"] = elevation
    saved = result.get("honeycomb_height_mm", honeycomb_height_mm)
    result["honeycomb_height_mm"] = _number(saved, "honeycomb height")
    if honeycomb_height_mm is not None and result["honeycomb_height_mm"] != honeycomb_height_mm:
        raise SafetyError("Saved honeycomb height differs from the selected measured surface")
    if not 0 <= elevation - result["honeycomb_height_mm"] <= 20:
        raise SafetyError("Material surface must be 0 to 20 mm above the saved honeycomb, including supports")
    session = result.get("session")
    if not isinstance(session, (list, tuple)) or len(session) != 3 or any(type(v) is not int or v < 0 for v in session):
        raise SafetyError("Current material surface controller session is missing")
    _json(result)
    return result


def validate_program_binding(lines: Iterable[str], snapshot: Mapping[str, Any] | None) -> dict[str, Any] | None:
    binding = program_binding(lines)
    if binding is None:
        return None
    if snapshot is None:
        raise SafetyError("Material surface measurement is no longer current; measure and regenerate the job")
    current = snapshot_from_plan(snapshot)
    for field, current_field in (
        ("plan_id", "id"), ("measurement_id", "measurement_id"),
        ("surface_elevation_mm", "surface_elevation_mm"), ("honeycomb_height_mm", "honeycomb_height_mm"),
        ("reference", "reference"), ("session", "session"),
    ):
        if _json(binding[field]) != _json(current[current_field]):
            raise SafetyError(f"Material surface {field} changed; recapture and regenerate the job")
    return binding
