from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


class AirAssistMode(str, Enum):
    """Trusted machine-level mappings for binary air-assist output."""

    DISABLED = "disabled"
    GRBL_COOLANT = "grbl_coolant"
    MARLIN_FAN = "marlin_fan"


AIR_ASSIST_PROTOCOL_BY_MODE: Mapping[AirAssistMode, str] = MappingProxyType(
    {
        AirAssistMode.GRBL_COOLANT: "grbl",
        AirAssistMode.MARLIN_FAN: "marlin",
    }
)
MIN_FAN_INDEX = 0
MAX_FAN_INDEX = 255


@dataclass(slots=True)
class AirAssistSettings:
    """Persistent machine configuration for an explicitly selected output."""

    mode: AirAssistMode = AirAssistMode.DISABLED
    fan_index: int = 0

    def __post_init__(self) -> None:
        self.mode = coerce_air_assist_mode(self.mode)
        _validate_air_assist_shape(self)


@dataclass(frozen=True, slots=True)
class AirAssistCommands:
    """Resolved, immutable controller commands safe to bind into a job."""

    mode: AirAssistMode
    protocol: str
    fan_index: int | None
    on_commands: tuple[str, ...]
    off_commands: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.mode is AirAssistMode.GRBL_COOLANT:
            valid = (
                self.protocol == "grbl"
                and self.fan_index is None
                and self.on_commands == ("M8",)
                and self.off_commands == ("M9",)
            )
        elif self.mode is AirAssistMode.MARLIN_FAN:
            fan_index = self.fan_index
            valid = (
                self.protocol == "marlin"
                and type(fan_index) is int
                and MIN_FAN_INDEX <= fan_index <= MAX_FAN_INDEX
                and self.on_commands == (f"M106 P{fan_index} S255",)
                and self.off_commands == (f"M107 P{fan_index}",)
            )
        else:
            valid = False
        if not valid:
            raise ValueError(
                "AirAssistCommands must use one exact trusted controller mapping"
            )


def coerce_air_assist_mode(value: object) -> AirAssistMode:
    if isinstance(value, AirAssistMode):
        return value
    if not isinstance(value, str):
        raise ValueError(
            "machine.air_assist.mode must be disabled, grbl_coolant, or marlin_fan"
        )
    try:
        return AirAssistMode(value)
    except ValueError as exc:
        raise ValueError(
            "machine.air_assist.mode must be disabled, grbl_coolant, or marlin_fan"
        ) from exc


def validate_air_assist_settings(
    settings: AirAssistSettings,
    *,
    protocol: str,
) -> None:
    """Reject ambiguous or incompatible output mappings before execution."""

    mode = coerce_air_assist_mode(settings.mode)
    _validate_air_assist_shape(settings)
    required_protocol = AIR_ASSIST_PROTOCOL_BY_MODE.get(mode)
    if required_protocol is not None and protocol != required_protocol:
        raise ValueError(
            f"machine.air_assist.mode {mode.value} requires "
            f"machine.protocol {required_protocol}"
        )


def _validate_air_assist_shape(settings: AirAssistSettings) -> None:
    mode = coerce_air_assist_mode(settings.mode)
    if type(settings.fan_index) is not int:
        raise ValueError("machine.air_assist.fan_index must be a JSON integer")
    if not MIN_FAN_INDEX <= settings.fan_index <= MAX_FAN_INDEX:
        raise ValueError(
            f"machine.air_assist.fan_index must be between {MIN_FAN_INDEX} and "
            f"{MAX_FAN_INDEX}"
        )
    if mode is not AirAssistMode.MARLIN_FAN and settings.fan_index != 0:
        raise ValueError(
            "machine.air_assist.fan_index must be 0 unless mode is marlin_fan"
        )


__all__ = [
    "AIR_ASSIST_PROTOCOL_BY_MODE",
    "MAX_FAN_INDEX",
    "MIN_FAN_INDEX",
    "AirAssistCommands",
    "AirAssistMode",
    "AirAssistSettings",
    "coerce_air_assist_mode",
    "validate_air_assist_settings",
]
