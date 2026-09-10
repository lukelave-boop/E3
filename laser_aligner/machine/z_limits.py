"""Validated, atomic per-controller persistence for the operator Z ceiling."""

from __future__ import annotations

from pathlib import Path

from ..errors import MachineError
from ..storage import atomic_write_json, strict_json_loads
from .z_probe import finite_number

MIN_Z_MM = 20.0
HARD_MAX_Z_MM = 80.0
Z_LIMIT_CAPABILITY = "Cap:E3_Z_LIMIT_80_V1:1"
PI_Z_CAPABILITY = "pi-mainboard-z-v1"


def mainboard_limits_path(config_path: Path) -> Path:
    return config_path.with_name(config_path.name + ".z-limits.json")


def validate_max_z(value: object) -> float:
    return finite_number(value, "Maximum Z", MIN_Z_MM, HARD_MAX_Z_MM)


class MainboardZLimits:
    def __init__(self, path: Path | None, controller_port: str) -> None:
        self.path = path
        self.controller_port = controller_port

    def load(self, default: float) -> float:
        default = validate_max_z(default)
        if self.path is None:
            return default
        try:
            text = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return default
        except (OSError, UnicodeError) as exc:
            raise MachineError(f"Cannot read the saved mainboard Z ceiling: {exc}") from exc
        try:
            data = strict_json_loads(text)
            if (type(data) is not dict
                    or set(data) != {"schema_version", "controller_port", "max_z_mm"}
                    or type(data["schema_version"]) is not int
                    or data["schema_version"] != 1
                    or data["controller_port"] != self.controller_port):
                raise ValueError("Unknown format or different Ender controller binding")
            return validate_max_z(data["max_z_mm"])
        except (ValueError, RecursionError, MachineError) as exc:
            # An unreadable tighter ceiling must never silently become Z80.
            raise MachineError(f"Invalid saved mainboard Z ceiling: {exc}") from exc

    def save(self, maximum: float) -> None:
        maximum = validate_max_z(maximum)
        if self.path is None:
            raise MachineError("Persistent Z limits are unavailable in this process; update the E3 node")
        try:
            atomic_write_json(self.path, {
                "schema_version": 1, "controller_port": self.controller_port,
                "max_z_mm": maximum,
            })
        except OSError as exc:
            raise MachineError(f"Could not save the mainboard Z ceiling: {exc}") from exc
