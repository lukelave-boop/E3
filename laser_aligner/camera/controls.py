from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

LOGGER = logging.getLogger(__name__)
_CONTROL_RE = re.compile(r"^\s*([a-zA-Z0-9_]+)\s+0x[0-9a-fA-F]+\s+\([^)]*\)\s*:\s*(.*)$")
_CONTROL_VALUE_RE = re.compile(
    r"^\s*([a-zA-Z0-9_]+)\s*:\s*(-?\d+)(?:\s+\([^\r\n]*\))?\s*$"
)
_LIMIT_RE = re.compile(r"\b(min|max|step)=(-?\d+)\b")
_ALIAS_GROUPS = {
    "focus_mode": ("focus_automatic_continuous", "focus_auto"),
    "exposure_mode": ("auto_exposure", "exposure_auto"),
    "white_balance_mode": (
        "white_balance_automatic",
        "white_balance_temperature_auto",
    ),
}
_MODE_CONTROLS = frozenset(name for names in _ALIAS_GROUPS.values() for name in names)
_CRITICAL_DIRECT_CONTROLS = {
    "focus_absolute": "focus_value",
    "exposure_time_absolute": "exposure_value",
    "white_balance_temperature": "white_balance_value",
}
_COMMAND_TIMEOUT_SECONDS = 5.0
_CONTROL_NAME_RE = re.compile(r"^[a-zA-Z0-9_]+$")


def validate_control_request(
    requested: Mapping[str, int | bool],
) -> dict[str, int | bool]:
    if not isinstance(requested, Mapping):
        raise ValueError("Camera controls must be a mapping")
    result: dict[str, int | bool] = {}
    for name, value in requested.items():
        if not isinstance(name, str) or not _CONTROL_NAME_RE.fullmatch(name):
            raise ValueError("Camera control names may contain only letters, digits, and underscores")
        if type(value) not in {bool, int}:
            raise ValueError(f"Camera control {name} must have an integer or boolean value")
        result[name] = value
    return result


def _critical_requirements(
    requested: Mapping[str, int | bool],
    reason: str,
) -> dict[str, str]:
    requirements = {
        group: reason
        for group, names in _ALIAS_GROUPS.items()
        if any(name in requested for name in names)
    }
    requirements.update(
        {
            group: reason
            for name, group in _CRITICAL_DIRECT_CONTROLS.items()
            if name in requested
        }
    )
    return requirements


@dataclass(slots=True)
class ControlResult:
    requested: dict[str, int | bool]
    applied: dict[str, int | bool]
    skipped: dict[str, str]
    verified: dict[str, int] = field(default_factory=dict)
    satisfied: dict[str, str] = field(default_factory=dict)
    critical_unverified: dict[str, str] = field(default_factory=dict)


def _run_v4l2(
    command: Sequence[str],
    *,
    deadline: float | None = None,
) -> tuple[subprocess.CompletedProcess[str] | None, str | None]:
    timeout = _COMMAND_TIMEOUT_SECONDS
    if deadline is not None:
        timeout = min(timeout, deadline - time.monotonic())
        if timeout <= 0:
            return None, "camera-control deadline expired"
    try:
        return (
            subprocess.run(
                list(command),
                check=False,
                capture_output=True,
                text=True,
                timeout=timeout,
            ),
            None,
        )
    except subprocess.TimeoutExpired:
        return None, f"v4l2-ctl timed out after {timeout:g} seconds"
    except OSError as exc:
        return None, f"could not run v4l2-ctl: {exc}"


def _list_controls(
    device: str,
    *,
    deadline: float | None = None,
) -> tuple[dict[str, str], str | None]:
    proc, error = _run_v4l2(
        ["v4l2-ctl", "-d", device, "--list-ctrls-menus"],
        deadline=deadline,
    )
    if error is not None:
        LOGGER.warning("Could not list controls for %s: %s", device, error)
        return {}, error
    assert proc is not None
    if proc.returncode != 0:
        error = proc.stderr.strip() or "v4l2-ctl returned an error"
        LOGGER.warning("Could not list controls for %s: %s", device, error)
        return {}, error
    controls: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        match = _CONTROL_RE.match(line)
        if match:
            controls[match.group(1)] = match.group(2).strip()
    return controls, None


def list_controls(device: str) -> dict[str, str]:
    """Return V4L2 control names and their raw descriptions."""
    if shutil.which("v4l2-ctl") is None:
        return {}
    controls, _ = _list_controls(device)
    return controls


def _read_control_values(
    device: str,
    names: Mapping[str, object],
    *,
    deadline: float | None = None,
) -> tuple[dict[str, int], str | None]:
    if not names or shutil.which("v4l2-ctl") is None:
        return {}, None
    proc, error = _run_v4l2(
        ["v4l2-ctl", "-d", device, "--get-ctrl=" + ",".join(names)],
        deadline=deadline,
    )
    if error is not None:
        LOGGER.warning("Could not read back controls for %s: %s", device, error)
        return {}, error
    assert proc is not None
    if proc.returncode != 0:
        error = proc.stderr.strip() or "v4l2-ctl returned an error"
        LOGGER.warning(
            "Could not read back controls for %s: %s",
            device,
            error,
        )
        return {}, error
    values: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        match = _CONTROL_VALUE_RE.match(line)
        if match:
            values[match.group(1)] = int(match.group(2))
    return values, None


def read_control_values(device: str, names: Mapping[str, object]) -> dict[str, int]:
    values, _ = _read_control_values(device, names)
    return values


def apply_controls(
    device: str,
    requested: Mapping[str, int | bool],
    *,
    timeout_seconds: float = _COMMAND_TIMEOUT_SECONDS,
    cancelled: Callable[[], bool] | None = None,
) -> ControlResult:
    """Apply only controls actually exposed by the camera.

    Logitech C920 revisions and Linux kernels use slightly different names for
    automatic focus/white-balance controls. The configuration intentionally
    contains aliases; unavailable controls are skipped rather than treated as a
    fatal error.
    """
    requested_dict = validate_control_request(requested)
    if type(timeout_seconds) is bool:
        raise ValueError("Camera-control timeout must be a positive finite number")
    try:
        timeout = float(timeout_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "Camera-control timeout must be a positive finite number"
        ) from exc
    if not math.isfinite(timeout) or timeout <= 0.0:
        raise ValueError("Camera-control timeout must be a positive finite number")
    if not requested_dict:
        return ControlResult(requested_dict, {}, {})
    deadline = time.monotonic() + timeout
    if cancelled is not None and cancelled():
        reason = "camera-control operation was cancelled"
        return ControlResult(
            requested_dict,
            {},
            {key: reason for key in requested_dict},
            critical_unverified=_critical_requirements(requested_dict, reason),
        )
    if shutil.which("v4l2-ctl") is None:
        reason = "v4l2-ctl is not installed"
        return ControlResult(
            requested_dict,
            {},
            {key: reason for key in requested_dict},
            critical_unverified=_critical_requirements(requested_dict, reason),
        )

    available, probe_error = _list_controls(device, deadline=deadline)
    if probe_error is not None:
        return ControlResult(
            requested_dict,
            {},
            {key: probe_error for key in requested_dict},
            critical_unverified=_critical_requirements(requested_dict, probe_error),
        )
    applied: dict[str, int | bool] = {}
    skipped: dict[str, str] = {}
    satisfied: dict[str, str] = {}
    critical_unverified: dict[str, str] = {}
    selected: dict[str, int | bool] = {}
    selected_groups: dict[str, str] = {}
    aliases = {name for names in _ALIAS_GROUPS.values() for name in names}

    for group, names in _ALIAS_GROUPS.items():
        configured = [(name, requested_dict[name]) for name in names if name in requested_dict]
        if not configured:
            continue
        values = {
            1 if value is True else 0 if value is False else int(value)
            for _, value in configured
        }
        if len(values) != 1:
            reason = "configured aliases disagree"
            critical_unverified[group] = reason
            for name, _ in configured:
                skipped[name] = reason
            continue
        exposed = next((name for name in names if name in available), None)
        if exposed is None:
            reason = "control not exposed; no supported alias is available"
            critical_unverified[group] = reason
            for name, _ in configured:
                skipped[name] = reason
            continue
        value = configured[0][1]
        selected[exposed] = value
        selected_groups[group] = exposed
        for name, _ in configured:
            if name != exposed:
                skipped[name] = f"alias resolved through {exposed}"

    for name, value in requested_dict.items():
        if name in aliases:
            continue
        if name not in available:
            skipped[name] = "control not exposed by this device/kernel"
            critical = _CRITICAL_DIRECT_CONTROLS.get(name)
            if critical:
                critical_unverified[critical] = skipped[name]
            continue
        selected[name] = value

    ordered = sorted(
        selected.items(),
        key=lambda item: (0 if item[0] in _MODE_CONTROLS else 1),
    )
    for name, value in ordered:
        if cancelled is not None and cancelled():
            skipped[name] = "camera-control operation was cancelled"
            continue
        normalized = 1 if value is True else 0 if value is False else int(value)
        limits = {
            key: int(limit)
            for key, limit in _LIMIT_RE.findall(available.get(name, ""))
        }
        minimum = limits.get("min")
        maximum = limits.get("max")
        step = max(1, limits.get("step", 1))
        if (
            (minimum is not None and normalized < minimum)
            or (maximum is not None and normalized > maximum)
            or (minimum is not None and (normalized - minimum) % step != 0)
        ):
            skipped[name] = (
                f"requested value {normalized} is outside the exposed range/step "
                f"({available[name]})"
            )
            critical = _CRITICAL_DIRECT_CONTROLS.get(name)
            if critical:
                critical_unverified[critical] = skipped[name]
            continue
        proc, command_error = _run_v4l2(
            ["v4l2-ctl", "-d", device, f"--set-ctrl={name}={normalized}"],
            deadline=deadline,
        )
        if command_error is not None:
            skipped[name] = command_error
        elif proc is not None and proc.returncode == 0:
            applied[name] = value
        else:
            assert proc is not None
            skipped[name] = proc.stderr.strip() or "v4l2-ctl returned an error"

    if cancelled is not None and cancelled():
        verified: dict[str, int] = {}
        readback_error = "camera-control operation was cancelled"
    else:
        verified, readback_error = _read_control_values(
            device,
            applied,
            deadline=deadline,
        )
    for name, actual in verified.items():
        requested_value = applied.get(name)
        normalized = (
            1
            if requested_value is True
            else 0
            if requested_value is False
            else int(requested_value)
        )
        if actual != normalized:
            applied.pop(name, None)
            skipped[name] = (
                f"readback returned {actual} after requesting {normalized}"
            )
    for group, name in selected_groups.items():
        if name in applied and name in verified:
            satisfied[group] = name
            critical_unverified.pop(group, None)
        else:
            critical_unverified[group] = skipped.get(
                name,
                readback_error or "control write was not verified by readback",
            )
    for name, group in _CRITICAL_DIRECT_CONTROLS.items():
        if name not in requested_dict:
            continue
        if name in applied and name in verified:
            satisfied[group] = name
            critical_unverified.pop(group, None)
        else:
            critical_unverified[group] = skipped.get(
                name,
                readback_error or "control write was not verified by readback",
            )
    return ControlResult(
        requested_dict,
        applied,
        skipped,
        verified,
        satisfied,
        critical_unverified,
    )
