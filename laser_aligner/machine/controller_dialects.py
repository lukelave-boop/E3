from __future__ import annotations

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ..air_assist import (
    AirAssistCommands,
    AirAssistMode,
    AirAssistSettings,
    AirAssistTarget,
    coerce_air_assist_mode,
    validate_air_assist_settings,
)
from ..errors import MachineError

_GRBL_WORK_COORDINATE_CODES = frozenset(f"G{number}" for number in range(54, 60))
_GRBL_STATUS_VECTOR_KEYS = frozenset({"MPOS", "WPOS", "WCO"})
_GRBL_OFFSET_PATTERN = re.compile(
    r"^\[(G5[4-9]|G92):\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)),\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)),\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\]$",
    re.IGNORECASE,
)
_GRBL_STEP_IDLE_PATTERN = re.compile(
    r"^\s*\$1\s*=\s*(\d+)(?:\.0*)?(?:\s+\([^)]*\))?\s*$",
    re.IGNORECASE,
)


class CommandResponseKind(str, Enum):
    ACKNOWLEDGEMENT = "acknowledgement"
    ERROR = "error"
    ALARM = "alarm"
    PAYLOAD = "payload"
    # Compatibility alias for callers that historically treated every
    # non-terminal frame as an undifferentiated continuation.
    CONTINUE = "payload"
    REALTIME_STATUS = "realtime_status"
    STARTUP = "startup"
    MALFORMED = "malformed"


class HomingResponseKind(str, Enum):
    ACKNOWLEDGEMENT = "acknowledgement"
    ACTIVE = "active"
    IDLE = "idle"
    REJECTION = "rejection"
    STARTUP = "startup"
    MALFORMED = "malformed"
    CONTINUE = "continue"


@dataclass(frozen=True, slots=True)
class ProbeAttempt:
    dialect_id: str
    command: str
    timeout_seconds: float
    terminal_error_consumed: bool = True


@dataclass(frozen=True, slots=True)
class HomingPolicy:
    command: str
    timeout_floor_seconds: float
    realtime_status_query: bytes | None = None
    status_query_interval_seconds: float | None = None
    active_states: frozenset[str] = frozenset()
    accepts_active_to_idle_without_ack: bool = False


@dataclass(frozen=True, slots=True)
class EmergencyStopPolicy:
    raw_command: bytes | None = None
    line_command: str | None = None
    success_log: str = ""
    failure_label: str = ""

    def __post_init__(self) -> None:
        if (self.raw_command is None) == (self.line_command is None):
            raise ValueError("Emergency stop policy must define exactly one command")


@dataclass(frozen=True, slots=True)
class GrblSessionPolicy:
    settings_query_command: str = "$$"
    step_idle_setting_prefix: str = "$1="
    held_step_idle_delay_ms: int = 255
    unlock_command: str = "$X"
    motor_disable_command: str = "$MD"
    motor_sleep_command: str = "$SLP"
    soft_reset_command: bytes = b"\x18"
    sleep_before_reset_seconds: float = 0.1
    reset_startup_delay_min_seconds: float = 0.1
    reset_startup_delay_max_seconds: float = 5.0

    def format_step_idle_delay(self, milliseconds: int) -> str:
        return f"{self.step_idle_setting_prefix}{int(milliseconds)}"


@dataclass(frozen=True, slots=True)
class ControllerDialect:
    id: str
    display_name: str
    startup_markers: tuple[str, ...]
    identity_response_markers: tuple[str, ...]
    identity_query_command: str
    query_commands: frozenset[str]
    command_errors_are_consumed: bool
    homing: HomingPolicy
    motion_barrier_command: str
    laser_off_command: str
    motor_release_command: str
    emergency_stop: EmergencyStopPolicy
    coordinate_state_query_commands: tuple[str, ...] = ()
    realtime_status_query: bytes | None = None
    grbl_session: GrblSessionPolicy | None = None
    air_assist_mode: AirAssistMode | None = None

    def recognizes_startup(self, responses: Sequence[str]) -> bool:
        joined = "\n".join(responses).lower()
        return any(marker in joined for marker in self.startup_markers)

    def recognizes_identity(self, responses: Sequence[str]) -> bool:
        return any(
            marker in response.lower()
            for response in responses
            for marker in self.identity_response_markers
        )

    def classify_command_response(self, response: str) -> CommandResponseKind:
        if type(response) is not str or not response or response != response.strip():
            return CommandResponseKind.MALFORMED
        if any(ord(character) < 32 and character not in {"\t"} for character in response):
            return CommandResponseKind.MALFORMED
        lower = response.lower()
        if lower == "ok" or (self.id == "marlin" and lower.startswith("ok ")):
            return CommandResponseKind.ACKNOWLEDGEMENT
        if self.id == "grbl" and re.fullmatch(r"error:\d+", lower):
            return CommandResponseKind.ERROR
        if self.id == "marlin" and lower.startswith("error:") and len(response) > 6:
            return CommandResponseKind.ERROR
        if re.fullmatch(r"alarm:\d+", lower):
            return CommandResponseKind.ALARM
        if lower.startswith(("ok", "error", "alarm")):
            return CommandResponseKind.MALFORMED
        if response.startswith("<") or response.endswith(">"):
            if response.startswith("<") and response.endswith(">") and len(response) > 2:
                return CommandResponseKind.REALTIME_STATUS
            return CommandResponseKind.MALFORMED
        if lower.startswith("grbl ") or lower.startswith("start"):
            return CommandResponseKind.STARTUP
        return CommandResponseKind.PAYLOAD

    def classify_homing_response(self, response: str) -> HomingResponseKind:
        command_kind = self.classify_command_response(response)
        if command_kind is CommandResponseKind.STARTUP:
            return HomingResponseKind.STARTUP
        if command_kind is CommandResponseKind.MALFORMED:
            return HomingResponseKind.MALFORMED
        lower = response.strip().lower()
        if lower == "ok" or lower.startswith("ok "):
            return HomingResponseKind.ACKNOWLEDGEMENT
        if (
            lower.startswith("error")
            or lower.startswith("alarm")
            or lower.startswith("<alarm")
        ):
            return HomingResponseKind.REJECTION
        if lower.startswith("<") and lower.endswith(">"):
            state = lower[1:].split("|", 1)[0].split(":", 1)[0]
            if state in self.homing.active_states:
                return HomingResponseKind.ACTIVE
            if state == "idle":
                return HomingResponseKind.IDLE
        return HomingResponseKind.CONTINUE

    def resolve_air_assist_commands(
        self,
        settings: AirAssistSettings,
    ) -> AirAssistCommands | None:
        """Resolve only the fixed auxiliary-output mapping owned by this dialect."""

        validate_air_assist_settings(settings, protocol=self.id)
        mode = coerce_air_assist_mode(settings.mode)
        if mode is AirAssistMode.DISABLED:
            return None
        if mode is not self.air_assist_mode:
            raise ValueError(
                f"Air-assist mode {mode.value} is not supported by {self.display_name}"
            )
        if mode is AirAssistMode.GRBL_COOLANT:
            return AirAssistCommands(
                mode=mode,
                protocol=self.id,
                fan_index=None,
                on_commands=("M8",),
                off_commands=("M9",),
            )
        if mode is AirAssistMode.MARLIN_FAN:
            fan_index = settings.fan_index
            return AirAssistCommands(
                mode=mode,
                protocol=self.id,
                fan_index=fan_index,
                on_commands=(f"M106 P{fan_index} S255",),
                off_commands=(f"M107 P{fan_index}",),
            )
        raise ValueError(f"Unsupported air-assist mode: {mode.value}")


@dataclass(frozen=True, slots=True)
class ControllerDialectRegistry:
    dialects: tuple[ControllerDialect, ...]
    probe_attempts: tuple[ProbeAttempt, ...]

    def __post_init__(self) -> None:
        dialect_ids = tuple(dialect.id for dialect in self.dialects)
        if len(set(dialect_ids)) != len(dialect_ids):
            raise ValueError("Controller dialect IDs must be unique")
        if any(probe.dialect_id not in dialect_ids for probe in self.probe_attempts):
            raise ValueError("Every controller probe must reference a registered dialect")

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(dialect.id for dialect in self.dialects)

    @property
    def manual_query_commands(self) -> frozenset[str]:
        return frozenset(
            command
            for dialect in self.dialects
            for command in dialect.query_commands
        )

    def get(self, dialect_id: str) -> ControllerDialect:
        for dialect in self.dialects:
            if dialect.id == dialect_id:
                return dialect
        raise KeyError(dialect_id)

    def recognize_startup(
        self, responses: Sequence[str]
    ) -> ControllerDialect | None:
        for dialect in self.dialects:
            if dialect.recognizes_startup(responses):
                return dialect
        return None


GRBL_DIALECT = ControllerDialect(
    id="grbl",
    display_name="GRBL",
    startup_markers=("grbl",),
    identity_response_markers=("grbl", "[ver:"),
    identity_query_command="$I",
    query_commands=frozenset({"$I", "$$", "$G", "$#"}),
    command_errors_are_consumed=True,
    homing=HomingPolicy(
        command="$H",
        timeout_floor_seconds=120.0,
        realtime_status_query=b"?",
        status_query_interval_seconds=0.2,
        active_states=frozenset({"home", "homing", "run"}),
        accepts_active_to_idle_without_ack=True,
    ),
    motion_barrier_command="G4 P0.01",
    laser_off_command="M5",
    motor_release_command="$MD",
    emergency_stop=EmergencyStopPolicy(
        raw_command=b"!\x18",
        success_log="GRBL feed hold + soft reset",
        failure_label="GRBL realtime stop",
    ),
    coordinate_state_query_commands=("$G", "$#"),
    realtime_status_query=b"?",
    grbl_session=GrblSessionPolicy(),
    air_assist_mode=AirAssistMode.GRBL_COOLANT,
)

MARLIN_DIALECT = ControllerDialect(
    id="marlin",
    display_name="Marlin",
    # Current auto-detection does not accept a Marlin startup banner. It must
    # continue through the ordered $I and M115 probes.
    startup_markers=(),
    identity_response_markers=("firmware_name", "marlin"),
    identity_query_command="M115",
    query_commands=frozenset({"M105", "M114", "M115", "M503"}),
    command_errors_are_consumed=False,
    homing=HomingPolicy(
        command="G28",
        timeout_floor_seconds=120.0,
    ),
    motion_barrier_command="M400",
    laser_off_command="M5",
    motor_release_command="M84",
    emergency_stop=EmergencyStopPolicy(
        line_command="M112",
        success_log="M112",
        failure_label="Marlin emergency stop",
    ),
    air_assist_mode=AirAssistMode.MARLIN_FAN,
)

CONTROLLER_DIALECT_REGISTRY = ControllerDialectRegistry(
    dialects=(GRBL_DIALECT, MARLIN_DIALECT),
    probe_attempts=(
        ProbeAttempt("grbl", "$I", 1.0),
        ProbeAttempt("marlin", "M115", 1.5),
    ),
)

MANUAL_QUERY_COMMANDS = CONTROLLER_DIALECT_REGISTRY.manual_query_commands


def resolve_air_assist_commands(
    settings: AirAssistSettings,
    *,
    protocol: str,
) -> AirAssistCommands | None:
    """Resolve a validated machine mapping into immutable controller commands."""

    validate_air_assist_settings(settings, protocol=protocol)
    mode = coerce_air_assist_mode(settings.mode)
    if mode is AirAssistMode.DISABLED:
        return None
    if mode is AirAssistMode.SECONDARY_MARLIN_FAN:
        return AirAssistCommands(
            mode=mode,
            protocol="marlin",
            fan_index=None,
            on_commands=("M106 S255",),
            off_commands=("M106 S0",),
            target=AirAssistTarget.PI_SECONDARY,
            port=settings.port,
            baudrate=settings.baudrate,
        )
    try:
        dialect = CONTROLLER_DIALECT_REGISTRY.get(protocol)
    except KeyError as exc:
        raise ValueError(
            "Enabled air assist requires an explicit grbl or marlin protocol"
        ) from exc
    return dialect.resolve_air_assist_commands(settings)


def parse_grbl_step_idle_delay(responses: Sequence[str]) -> int | None:
    for response in responses:
        match = _GRBL_STEP_IDLE_PATTERN.match(response.strip())
        if match:
            return int(match.group(1))
    return None


def is_exact_grbl_locked_error_response(response: str) -> bool:
    return type(response) is str and response.strip().lower() == "error:9"


def parse_grbl_coordinate_state(
    modal_responses: Sequence[str],
    offset_responses: Sequence[str],
) -> dict[str, Any]:
    active_workspace: str | None = None
    for response in modal_responses:
        upper = response.strip().upper()
        if not upper.startswith("[GC:"):
            continue
        words = upper[4:-1].split() if upper.endswith("]") else upper[4:].split()
        active_workspace = next(
            (word for word in words if word in _GRBL_WORK_COORDINATE_CODES),
            None,
        )
        break

    offsets: dict[str, list[float]] = {}
    for response in offset_responses:
        match = _GRBL_OFFSET_PATTERN.match(response.strip())
        if match is None:
            continue
        values = [
            float(match.group(2)),
            float(match.group(3)),
            float(match.group(4)),
        ]
        if not all(math.isfinite(value) for value in values):
            raise MachineError("GRBL coordinate offsets must be finite")
        offsets[match.group(1).upper()] = values
    if active_workspace is None:
        raise MachineError("GRBL did not report an active G54-G59 work-coordinate system")
    if active_workspace not in offsets or "G92" not in offsets:
        raise MachineError(
            "GRBL did not report the active work offset and G92 offset in response to $#"
        )
    return {
        "active_workspace": active_workspace,
        "active_offset_mm": offsets[active_workspace],
        "g92_offset_mm": offsets["G92"],
    }


def _parse_grbl_status_vector(raw: str) -> list[float]:
    values: list[float] = []
    for component in raw.split(","):
        try:
            value = float(component.strip())
        except ValueError as exc:
            raise MachineError(
                f"GRBL realtime status contains an invalid coordinate vector: {raw!r}"
            ) from exc
        if not math.isfinite(value):
            raise MachineError("GRBL realtime status coordinates must be finite")
        values.append(value)
    if len(values) < 2:
        raise MachineError(
            "GRBL realtime status must report at least X and Y coordinates"
        )
    return values[:4]


def _vector_math(
    left: list[float | None],
    right: list[float | None],
    *,
    subtract: bool,
) -> list[float | None]:
    size = max(len(left), len(right), 3)
    output: list[float | None] = []
    for index in range(size):
        a = left[index] if index < len(left) else None
        b = right[index] if index < len(right) else None
        if a is None or b is None:
            output.append(None)
        else:
            output.append(float(a) - float(b) if subtract else float(a) + float(b))
    return output


def parse_grbl_realtime_status(
    response: str,
    *,
    coordinate_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    text = response.strip()
    if not (text.startswith("<") and text.endswith(">")):
        raise MachineError(f"Invalid GRBL realtime status frame: {response!r}")
    fields = text[1:-1].split("|")
    if not fields or not fields[0].strip():
        raise MachineError("GRBL realtime status did not report a machine state")
    state = fields[0].strip()
    vectors: dict[str, list[float | None]] = {}
    for field in fields[1:]:
        key, separator, raw = field.partition(":")
        key = key.strip().upper()
        if separator and key in _GRBL_STATUS_VECTOR_KEYS:
            vectors[key] = list(_parse_grbl_status_vector(raw))

    mpos = vectors.get("MPOS")
    wpos = vectors.get("WPOS")
    wco = vectors.get("WCO")
    wco_source = "reported" if wco is not None else None
    derived_fields: list[str] = []
    if wco is None and coordinate_state is not None:
        active = coordinate_state.get("active_offset_mm")
        g92 = coordinate_state.get("g92_offset_mm")
        if (
            isinstance(active, (list, tuple))
            and isinstance(g92, (list, tuple))
            and len(active) >= 2
            and len(g92) >= 2
        ):
            wco = [
                float(active[0]) + float(g92[0]),
                float(active[1]) + float(g92[1]),
                None,
            ]
            wco_source = "derived X/Y from active workspace and G92"
            derived_fields.append("WCO")
    if mpos is None and wpos is not None and wco is not None:
        mpos = _vector_math(wpos, wco, subtract=False)
        derived_fields.append("MPos")
    if wpos is None and mpos is not None and wco is not None:
        wpos = _vector_math(mpos, wco, subtract=True)
        derived_fields.append("WPos")
    if wco is None and mpos is not None and wpos is not None:
        wco = _vector_math(mpos, wpos, subtract=True)
        wco_source = "derived from MPos - WPos"
        derived_fields.append("WCO")
    if mpos is None and wpos is None:
        raise MachineError("GRBL realtime status did not report MPos or WPos")

    def xy_complete(value: list[float | None] | None) -> bool:
        return bool(
            value is not None
            and len(value) >= 2
            and all(
                type(component) in {int, float}
                and math.isfinite(float(component))
                for component in value[:2]
            )
        )

    return {
        "state": state,
        "mpos_mm": mpos,
        "wpos_mm": wpos,
        "wco_mm": wco,
        "wco_source": wco_source,
        "derived_fields": derived_fields,
        "xy_complete": bool(xy_complete(mpos) and xy_complete(wpos) and xy_complete(wco)),
        "raw_status": text,
    }
