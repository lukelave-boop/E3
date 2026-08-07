from __future__ import annotations

import logging
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass, field

LOGGER = logging.getLogger(__name__)
_CONTROL_RE = re.compile(r"^\s*([a-zA-Z0-9_]+)\s+0x[0-9a-fA-F]+\s+\([^)]*\)\s*:\s*(.*)$")
_CONTROL_VALUE_RE = re.compile(r"^\s*([a-zA-Z0-9_]+)\s*:\s*(-?\d+)\s*$")


@dataclass(slots=True)
class ControlResult:
    requested: dict[str, int | bool]
    applied: dict[str, int | bool]
    skipped: dict[str, str]
    verified: dict[str, int] = field(default_factory=dict)


def list_controls(device: str) -> dict[str, str]:
    """Return V4L2 control names and their raw descriptions."""
    if shutil.which("v4l2-ctl") is None:
        return {}
    proc = subprocess.run(
        ["v4l2-ctl", "-d", device, "--list-ctrls-menus"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    controls: dict[str, str] = {}
    if proc.returncode != 0:
        LOGGER.warning("Could not list controls for %s: %s", device, proc.stderr.strip())
        return controls
    for line in proc.stdout.splitlines():
        match = _CONTROL_RE.match(line)
        if match:
            controls[match.group(1)] = match.group(2).strip()
    return controls


def read_control_values(device: str, names: Mapping[str, object]) -> dict[str, int]:
    if not names or shutil.which("v4l2-ctl") is None:
        return {}
    proc = subprocess.run(
        ["v4l2-ctl", "-d", device, "--get-ctrl=" + ",".join(names)],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    if proc.returncode != 0:
        LOGGER.warning(
            "Could not read back controls for %s: %s",
            device,
            proc.stderr.strip(),
        )
        return {}
    values: dict[str, int] = {}
    for line in proc.stdout.splitlines():
        match = _CONTROL_VALUE_RE.match(line)
        if match:
            values[match.group(1)] = int(match.group(2))
    return values


def apply_controls(device: str, requested: Mapping[str, int | bool]) -> ControlResult:
    """Apply only controls actually exposed by the camera.

    Logitech C920 revisions and Linux kernels use slightly different names for
    automatic focus/white-balance controls. The configuration intentionally
    contains aliases; unavailable controls are skipped rather than treated as a
    fatal error.
    """
    requested_dict = dict(requested)
    if not requested_dict:
        return ControlResult(requested_dict, {}, {})
    if shutil.which("v4l2-ctl") is None:
        return ControlResult(requested_dict, {}, {key: "v4l2-ctl is not installed" for key in requested_dict})

    available = list_controls(device)
    applied: dict[str, int | bool] = {}
    skipped: dict[str, str] = {}

    for name, value in requested_dict.items():
        if name not in available:
            skipped[name] = "control not exposed by this device/kernel"
            continue
        normalized = 1 if value is True else 0 if value is False else int(value)
        proc = subprocess.run(
            ["v4l2-ctl", "-d", device, f"--set-ctrl={name}={normalized}"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if proc.returncode == 0:
            applied[name] = value
        else:
            skipped[name] = proc.stderr.strip() or "v4l2-ctl returned an error"

    verified = read_control_values(device, applied)
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
    return ControlResult(requested_dict, applied, skipped, verified)
