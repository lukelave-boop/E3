from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType


class AirAssistMode(str, Enum):
    """Trusted machine-level mappings for binary air-assist output."""

    DISABLED = "disabled"
    GRBL_COOLANT = "grbl_coolant"
    MARLIN_FAN = "marlin_fan"
    SECONDARY_MARLIN_FAN = "secondary_marlin_fan"


class AirAssistTarget(str, Enum):
    """Controller route for a resolved, trusted air-assist mapping."""

    PRIMARY = "primary"
    PI_SECONDARY = "pi_secondary"


AIR_ASSIST_PROTOCOL_BY_MODE: Mapping[AirAssistMode, str] = MappingProxyType(
    {
        AirAssistMode.GRBL_COOLANT: "grbl",
        AirAssistMode.MARLIN_FAN: "marlin",
    }
)
MIN_FAN_INDEX = 0
MAX_FAN_INDEX = 255
AIR_ASSIST_DIRECTIVE_PREFIX = "E3AIRASSIST"
MAX_SECONDARY_RECOVERY_PORT_CHARACTERS = 4096
_SECONDARY_RECOVERY_BINDING_FIELDS = frozenset(
    {
        "baudrate",
        "mapping_digest",
        "mode",
        "port",
        "protocol",
        "schema",
        "target",
    }
)


@dataclass(slots=True)
class AirAssistSettings:
    """Persistent machine configuration for an explicitly selected output."""

    mode: AirAssistMode = AirAssistMode.DISABLED
    fan_index: int = 0
    port: str = ""
    baudrate: int = 115200

    def __post_init__(self) -> None:
        self.mode = coerce_air_assist_mode(self.mode)
        if not isinstance(self.port, str):
            raise ValueError("machine.air_assist.port must be a JSON string")
        self.port = self.port.strip()
        _validate_air_assist_shape(self)


@dataclass(frozen=True, slots=True)
class AirAssistCommands:
    """Resolved, immutable controller commands safe to bind into a job."""

    mode: AirAssistMode
    protocol: str
    fan_index: int | None
    on_commands: tuple[str, ...]
    off_commands: tuple[str, ...]
    target: AirAssistTarget = AirAssistTarget.PRIMARY
    port: str | None = None
    baudrate: int | None = None

    def __post_init__(self) -> None:
        if self.mode is AirAssistMode.GRBL_COOLANT:
            valid = (
                self.target is AirAssistTarget.PRIMARY
                and self.protocol == "grbl"
                and self.fan_index is None
                and self.port is None
                and self.baudrate is None
                and self.on_commands == ("M8",)
                and self.off_commands == ("M9",)
            )
        elif self.mode is AirAssistMode.MARLIN_FAN:
            fan_index = self.fan_index
            valid = (
                self.target is AirAssistTarget.PRIMARY
                and self.protocol == "marlin"
                and type(fan_index) is int
                and MIN_FAN_INDEX <= fan_index <= MAX_FAN_INDEX
                and self.port is None
                and self.baudrate is None
                and self.on_commands == (f"M106 P{fan_index} S255",)
                and self.off_commands == (f"M107 P{fan_index}",)
            )
        elif self.mode is AirAssistMode.SECONDARY_MARLIN_FAN:
            valid = (
                self.target is AirAssistTarget.PI_SECONDARY
                and self.protocol == "marlin"
                and self.fan_index is None
                and isinstance(self.port, str)
                and bool(self.port)
                and self.port == self.port.strip()
                and type(self.baudrate) is int
                and self.baudrate > 0
                and self.on_commands == ("M106 S255",)
                and self.off_commands == ("M106 S0",)
            )
        else:
            valid = False
        if not valid:
            raise ValueError(
                "AirAssistCommands must use one exact trusted controller mapping"
            )

    @property
    def mapping_digest(self) -> str:
        """Return a stable fingerprint of the complete physical output binding."""

        canonical = json.dumps(
            {
                "baudrate": self.baudrate,
                "mode": self.mode.value,
                "off_commands": self.off_commands,
                "on_commands": self.on_commands,
                "port": self.port,
                "protocol": self.protocol,
                "target": self.target.value,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        return hashlib.sha256(canonical).hexdigest()

    def program_lines(self, enabled: bool) -> tuple[str, ...]:
        """Return immutable program lines for one requested output state."""

        if type(enabled) is not bool:
            raise ValueError("Air-assist program state must be a boolean")
        if self.target is AirAssistTarget.PRIMARY:
            return self.on_commands if enabled else self.off_commands
        state = "ON" if enabled else "OFF"
        return (
            f"{AIR_ASSIST_DIRECTIVE_PREFIX} {self.mapping_digest} {state}",
        )

    def kind_for_program_line(self, line: str) -> str | None:
        """Classify an exact bound line and reject every malformed directive."""

        if not isinstance(line, str):
            raise ValueError("Air-assist program line must be a string")
        if self.target is AirAssistTarget.PRIMARY:
            normalized = _normalize_primary_program_line(line)
            if normalized in self.on_commands:
                return "on"
            if normalized in self.off_commands:
                return "off"
            return None
        text = line.strip()
        if text in self.program_lines(True):
            return "on"
        if text in self.program_lines(False):
            return "off"
        if text.startswith(AIR_ASSIST_DIRECTIVE_PREFIX):
            raise ValueError(
                "Air-assist directive does not exactly match the configured binding"
            )
        return None


def secondary_recovery_binding_payload(
    commands: AirAssistCommands | None,
) -> dict[str, object] | None:
    """Serialize one exact Pi-local binding for durable restart OFF recovery."""

    if commands is None:
        return None
    if not isinstance(commands, AirAssistCommands):
        raise ValueError("Secondary recovery binding must be AirAssistCommands or None")
    if (
        commands.mode is not AirAssistMode.SECONDARY_MARLIN_FAN
        or commands.target is not AirAssistTarget.PI_SECONDARY
    ):
        raise ValueError("Only a Pi-secondary Marlin fan binding may be persisted")
    port = commands.port
    if (
        type(port) is not str
        or not port
        or len(port) > MAX_SECONDARY_RECOVERY_PORT_CHARACTERS
        or any(ord(character) < 32 for character in port)
    ):
        raise ValueError("Secondary recovery port is not a bounded device path")
    return {
        "schema": 1,
        "mode": commands.mode.value,
        "target": commands.target.value,
        "protocol": commands.protocol,
        "port": port,
        "baudrate": commands.baudrate,
        "mapping_digest": commands.mapping_digest,
    }


def secondary_recovery_binding_from_payload(
    payload: object,
) -> AirAssistCommands | None:
    """Reconstruct only the fixed trusted secondary mapping from strict JSON."""

    if payload is None:
        return None
    if type(payload) is not dict or set(payload) != _SECONDARY_RECOVERY_BINDING_FIELDS:
        raise ValueError("Secondary recovery binding has an invalid schema")
    if type(payload.get("schema")) is not int or payload.get("schema") != 1:
        raise ValueError("Secondary recovery binding has an invalid schema")
    if (
        payload.get("mode") != AirAssistMode.SECONDARY_MARLIN_FAN.value
        or payload.get("target") != AirAssistTarget.PI_SECONDARY.value
        or payload.get("protocol") != "marlin"
    ):
        raise ValueError("Secondary recovery binding is not the exact FAN2 mapping")
    port = payload.get("port")
    baudrate = payload.get("baudrate")
    digest = payload.get("mapping_digest")
    if (
        type(port) is not str
        or not port
        or port != port.strip()
        or len(port) > MAX_SECONDARY_RECOVERY_PORT_CHARACTERS
        or any(ord(character) < 32 for character in port)
        or type(baudrate) is not int
        or baudrate <= 0
        or type(digest) is not str
        or len(digest) != 64
        or digest != digest.lower()
    ):
        raise ValueError("Secondary recovery binding contains invalid typed values")
    commands = AirAssistCommands(
        mode=AirAssistMode.SECONDARY_MARLIN_FAN,
        target=AirAssistTarget.PI_SECONDARY,
        protocol="marlin",
        fan_index=None,
        port=port,
        baudrate=baudrate,
        on_commands=("M106 S255",),
        off_commands=("M106 S0",),
    )
    if commands.mapping_digest != digest:
        raise ValueError("Secondary recovery binding digest does not match its mapping")
    return commands


def _normalize_primary_program_line(line: str) -> str:
    """Match existing controller-line normalization for trusted primary output."""

    line = line.split(";", 1)[0]
    while "(" in line and ")" in line:
        start = line.find("(")
        end = line.find(")", start)
        if end < 0:
            break
        line = line[:start] + line[end + 1 :]
    return " ".join(line.upper().split())


def coerce_air_assist_mode(value: object) -> AirAssistMode:
    if isinstance(value, AirAssistMode):
        return value
    if not isinstance(value, str):
        raise ValueError(
            "machine.air_assist.mode must be disabled, grbl_coolant, marlin_fan, "
            "or secondary_marlin_fan"
        )
    try:
        return AirAssistMode(value)
    except ValueError as exc:
        raise ValueError(
            "machine.air_assist.mode must be disabled, grbl_coolant, marlin_fan, "
            "or secondary_marlin_fan"
        ) from exc


def validate_air_assist_settings(
    settings: AirAssistSettings,
    *,
    protocol: str,
    primary_port: str | None = None,
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
    if mode is AirAssistMode.SECONDARY_MARLIN_FAN:
        if protocol not in {"grbl", "marlin"}:
            raise ValueError(
                "machine.air_assist.mode secondary_marlin_fan requires an "
                "explicit grbl or marlin machine.protocol"
            )
        if primary_port is not None and settings.port == primary_port.strip():
            raise ValueError(
                "machine.air_assist.port must differ from the primary "
                "machine.port"
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
    if not isinstance(settings.port, str):
        raise ValueError("machine.air_assist.port must be a JSON string")
    if type(settings.baudrate) is not int:
        raise ValueError("machine.air_assist.baudrate must be a JSON integer")
    if settings.baudrate <= 0:
        raise ValueError("machine.air_assist.baudrate must be positive")
    if mode is AirAssistMode.SECONDARY_MARLIN_FAN and not settings.port:
        raise ValueError(
            "machine.air_assist.port must be a nonempty Pi-local endpoint for "
            "secondary_marlin_fan"
        )


__all__ = [
    "AIR_ASSIST_PROTOCOL_BY_MODE",
    "AIR_ASSIST_DIRECTIVE_PREFIX",
    "MAX_SECONDARY_RECOVERY_PORT_CHARACTERS",
    "MAX_FAN_INDEX",
    "MIN_FAN_INDEX",
    "AirAssistCommands",
    "AirAssistMode",
    "AirAssistSettings",
    "AirAssistTarget",
    "coerce_air_assist_mode",
    "secondary_recovery_binding_from_payload",
    "secondary_recovery_binding_payload",
    "validate_air_assist_settings",
]
