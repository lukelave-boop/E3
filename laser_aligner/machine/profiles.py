from __future__ import annotations

import copy
import json
import math
import re
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..calibration.profiles import signature_from_camera_settings
from ..config import (
    DEFAULT_CONFIG,
    UNCONFIGURED_CONTROLLER_PORT,
    LaserSettings,
    MachineSettings,
    Settings,
    WorkArea,
    _deep_merge,
    _validate,
    _validate_override_keys,
)
from ..errors import RealMachineSetupRequired
from ..storage import (
    atomic_write_bytes_if_absent,
    atomic_write_json,
    strict_json_loads,
)

MACHINE_REGISTRY_FILENAME = "machines.json"
MACHINE_REGISTRY_SCHEMA_VERSION = 1
REMOVED_SIMULATOR_BACKUP_SUFFIX = ".before-simulator-removal.bak"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


class MachineRegistryError(ValueError):
    """Raised when saved machine data is malformed or inconsistent."""


class MachineSetupRequired(MachineRegistryError, RealMachineSetupRequired):
    """Saved-machine authority requires explicit real-machine setup."""


def _is_removed_simulator_entry(value: object) -> bool:
    if not isinstance(value, Mapping):
        return False
    machine = value.get("machine")
    return bool(
        value.get("machine_profile_id") == "simulator"
        or value.get("tool_head_profile_id") == "simulated-laser-head"
        or (isinstance(machine, Mapping) and machine.get("backend") == "simulator")
    )


def _identifier(value: object, label: str) -> str:
    if type(value) is not str:
        raise MachineRegistryError(f"{label} must be a JSON string")
    normalized = value.strip().lower()
    if _ID_RE.fullmatch(normalized) is None:
        raise MachineRegistryError(
            f"{label} must use 1-80 lowercase letters, digits, dots, "
            "underscores, or hyphens"
        )
    return normalized


def _required_text(value: object, label: str, *, limit: int = 120) -> str:
    if type(value) is not str or not value.strip():
        raise MachineRegistryError(f"{label} must be a non-empty JSON string")
    normalized = value.strip()
    if len(normalized) > limit:
        raise MachineRegistryError(f"{label} must be {limit} characters or fewer")
    return normalized


def _optional_text(
    value: object,
    label: str,
    *,
    limit: int = 240,
) -> str:
    if type(value) is not str:
        raise MachineRegistryError(f"{label} must be a JSON string")
    normalized = value.strip()
    if len(normalized) > limit:
        raise MachineRegistryError(f"{label} must be {limit} characters or fewer")
    return normalized


def _optional_reference(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _required_text(value, label)


def _capabilities(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise MachineRegistryError(f"{label} must be a JSON array")
    output = tuple(
        _required_text(item, f"{label}[]").lower() for item in value
    )
    if len(set(output)) != len(output):
        raise MachineRegistryError(f"{label} must not contain duplicates")
    return output


def _machine_dict(settings: MachineSettings) -> dict[str, Any]:
    payload = asdict(settings)
    payload["photo_position"] = {
        "x": payload.pop("photo_x"),
        "y": payload.pop("photo_y"),
        "z": payload.pop("photo_z"),
    }
    return payload


def _laser_dict(settings: LaserSettings) -> dict[str, Any]:
    payload = asdict(settings)
    polygon = payload.get("guarded_output_polygon_mm")
    if polygon is not None:
        payload["guarded_output_polygon_mm"] = [
            list(point) for point in polygon
        ]
    return payload


def _validated_pair(
    machine_value: object,
    laser_value: object,
) -> tuple[MachineSettings, LaserSettings]:
    if not isinstance(machine_value, Mapping):
        raise MachineRegistryError("machine must be a JSON object")
    if not isinstance(laser_value, Mapping):
        raise MachineRegistryError("laser must be a JSON object")
    override = {
        "machine": copy.deepcopy(dict(machine_value)),
        "laser": copy.deepcopy(dict(laser_value)),
    }
    try:
        _validate_override_keys(override, DEFAULT_CONFIG)
        raw = _deep_merge(DEFAULT_CONFIG, override)
        _validate(raw)
        machine_raw = raw["machine"]
        area = machine_raw["work_area"]
        photo = machine_raw["photo_position"]
        machine = MachineSettings(
            backend=str(machine_raw["backend"]),
            protocol=str(machine_raw["protocol"]),
            port=str(machine_raw["port"]),
            baudrate=int(machine_raw["baudrate"]),
            read_timeout=float(machine_raw["read_timeout"]),
            work_area=WorkArea(
                x_min=float(area["x_min"]),
                x_max=float(area["x_max"]),
                y_min=float(area["y_min"]),
                y_max=float(area["y_max"]),
            ),
            honeycomb_span_mm=(
                None
                if machine_raw["honeycomb_span_mm"] is None
                else float(machine_raw["honeycomb_span_mm"])
            ),
            photo_x=float(photo["x"]),
            photo_y=float(photo["y"]),
            photo_z=(
                None if photo.get("z") is None else float(photo["z"])
            ),
            home_before_photo=machine_raw["home_before_photo"],
            home_and_release_after_powered_job=(
                machine_raw["home_and_release_after_powered_job"]
            ),
            grbl_step_idle_delay_ms=int(
                machine_raw["grbl_step_idle_delay_ms"]
            ),
            allow_motion=machine_raw["allow_motion"],
            controller_startup_delay=float(
                machine_raw["controller_startup_delay"]
            ),
            max_travel_feed_mm_min=float(
                machine_raw["max_travel_feed_mm_min"]
            ),
            max_work_feed_mm_min=float(
                machine_raw["max_work_feed_mm_min"]
            ),
        )
        laser_raw = raw["laser"]
        polygon = laser_raw.get("guarded_output_polygon_mm")
        laser = LaserSettings(
            power_mode=str(laser_raw["power_mode"]).upper(),
            power_max=int(laser_raw["power_max"]),
            default_power=int(laser_raw["default_power"]),
            frame_power=int(laser_raw["frame_power"]),
            travel_feed_mm_min=float(laser_raw["travel_feed_mm_min"]),
            engrave_feed_mm_min=float(laser_raw["engrave_feed_mm_min"]),
            curve_tolerance_mm=float(laser_raw["curve_tolerance_mm"]),
            boundary_margin_mm=float(laser_raw["boundary_margin_mm"]),
            guarded_output_polygon_mm=(
                None
                if polygon is None
                else tuple(
                    (float(point[0]), float(point[1]))
                    for point in polygon
                )
            ),
            spot_offset_x_mm=float(laser_raw["spot_offset_x_mm"]),
            spot_offset_y_mm=float(laser_raw["spot_offset_y_mm"]),
            arm_timeout_seconds=int(laser_raw["arm_timeout_seconds"]),
            allow_low_power_frame=laser_raw["allow_low_power_frame"],
            return_to_photo_position=laser_raw["return_to_photo_position"],
            preview_acceleration_mm_s2=float(
                laser_raw["preview_acceleration_mm_s2"]
            ),
            preview_command_delay_ms=float(
                laser_raw["preview_command_delay_ms"]
            ),
        )
        return machine, laser
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise MachineRegistryError(
            f"Invalid saved machine configuration: {exc}"
        ) from exc


def _copy_pair(
    machine: MachineSettings,
    laser: LaserSettings,
) -> tuple[MachineSettings, LaserSettings]:
    return _validated_pair(_machine_dict(machine), _laser_dict(laser))


def _safe_laser(machine: MachineSettings) -> LaserSettings:
    return LaserSettings(
        power_mode="M4",
        power_max=1000,
        default_power=0,
        frame_power=0,
        travel_feed_mm_min=min(
            3000.0,
            machine.max_travel_feed_mm_min,
        ),
        engrave_feed_mm_min=min(
            1200.0,
            machine.max_work_feed_mm_min,
        ),
        allow_low_power_frame=False,
    )


def _validation_machine(laser: LaserSettings) -> MachineSettings:
    maximum = max(
        6000.0,
        laser.travel_feed_mm_min,
        laser.engrave_feed_mm_min,
    )
    return MachineSettings(
        backend="serial",
        protocol="auto",
        port="SELECT_CONTROLLER_PORT",
        work_area=WorkArea(0.0, 1000.0, 0.0, 1000.0),
        photo_x=500.0,
        photo_y=500.0,
        controller_startup_delay=0.0,
        allow_motion=False,
        max_travel_feed_mm_min=maximum,
        max_work_feed_mm_min=maximum,
    )


@dataclass(slots=True)
class MachineProfile:
    id: str
    name: str
    machine_defaults: MachineSettings
    manufacturer: str = ""
    model: str = ""
    description: str = ""
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.id = _identifier(self.id, "machine_profile.id")
        self.name = _required_text(self.name, "machine_profile.name")
        self.manufacturer = _optional_text(
            self.manufacturer,
            "machine_profile.manufacturer",
            limit=120,
        )
        self.model = _optional_text(
            self.model,
            "machine_profile.model",
            limit=120,
        )
        self.description = _optional_text(
            self.description,
            "machine_profile.description",
        )
        self.capabilities = _capabilities(
            self.capabilities,
            "machine_profile.capabilities",
        )
        machine, _laser = _copy_pair(
            self.machine_defaults,
            _safe_laser(self.machine_defaults),
        )
        machine.allow_motion = False
        self.machine_defaults = machine


@dataclass(slots=True)
class ToolHeadProfile:
    id: str
    name: str
    laser_defaults: LaserSettings
    manufacturer: str = ""
    model: str = ""
    description: str = ""
    nominal_output_watts: float | None = None
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self.id = _identifier(self.id, "tool_head_profile.id")
        self.name = _required_text(self.name, "tool_head_profile.name")
        self.manufacturer = _optional_text(
            self.manufacturer,
            "tool_head_profile.manufacturer",
            limit=120,
        )
        self.model = _optional_text(
            self.model,
            "tool_head_profile.model",
            limit=120,
        )
        self.description = _optional_text(
            self.description,
            "tool_head_profile.description",
        )
        self.capabilities = _capabilities(
            self.capabilities,
            "tool_head_profile.capabilities",
        )
        if self.nominal_output_watts is not None:
            if isinstance(self.nominal_output_watts, bool) or not isinstance(
                self.nominal_output_watts,
                (int, float),
            ):
                raise MachineRegistryError(
                    "tool_head_profile.nominal_output_watts must be a finite "
                    "number"
                )
            self.nominal_output_watts = float(self.nominal_output_watts)
            if (
                not math.isfinite(self.nominal_output_watts)
                or self.nominal_output_watts <= 0
            ):
                raise MachineRegistryError(
                    "tool_head_profile.nominal_output_watts must be positive"
                )
        _machine, laser = _copy_pair(
            _validation_machine(self.laser_defaults),
            self.laser_defaults,
        )
        laser.default_power = 0
        laser.frame_power = 0
        laser.allow_low_power_frame = False
        self.laser_defaults = laser


@dataclass(slots=True)
class MachineInstance:
    id: str
    name: str
    machine_profile_id: str
    tool_head_profile_id: str
    machine: MachineSettings
    laser: LaserSettings
    calibration_profile_id: str | None = None
    camera_profile_id: str | None = None
    created_from: str = "user"

    def __post_init__(self) -> None:
        self.id = _identifier(self.id, "machine.id")
        self.name = _required_text(self.name, "machine.name")
        self.machine_profile_id = _identifier(
            self.machine_profile_id,
            "machine.machine_profile_id",
        )
        self.tool_head_profile_id = _identifier(
            self.tool_head_profile_id,
            "machine.tool_head_profile_id",
        )
        self.calibration_profile_id = _optional_reference(
            self.calibration_profile_id,
            "machine.calibration_profile_id",
        )
        self.camera_profile_id = _optional_reference(
            self.camera_profile_id,
            "machine.camera_profile_id",
        )
        self.created_from = _required_text(
            self.created_from,
            "machine.created_from",
        )
        self.machine, self.laser = _copy_pair(self.machine, self.laser)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "machine_profile_id": self.machine_profile_id,
            "tool_head_profile_id": self.tool_head_profile_id,
            "calibration_profile_id": self.calibration_profile_id,
            "camera_profile_id": self.camera_profile_id,
            "created_from": self.created_from,
            "machine": _machine_dict(self.machine),
            "laser": _laser_dict(self.laser),
        }

    @classmethod
    def from_dict(cls, value: object) -> MachineInstance:
        if not isinstance(value, Mapping):
            raise MachineRegistryError("machine must be a JSON object")
        allowed = {
            "id",
            "name",
            "machine_profile_id",
            "tool_head_profile_id",
            "calibration_profile_id",
            "camera_profile_id",
            "created_from",
            "machine",
            "laser",
        }
        unknown = sorted(str(key) for key in value if key not in allowed)
        if unknown:
            raise MachineRegistryError(
                f"Unknown machine key(s): {', '.join(unknown)}"
            )
        machine, laser = _validated_pair(
            value.get("machine"),
            value.get("laser"),
        )
        return cls(
            id=value.get("id"),
            name=value.get("name"),
            machine_profile_id=value.get("machine_profile_id"),
            tool_head_profile_id=value.get("tool_head_profile_id"),
            calibration_profile_id=value.get("calibration_profile_id"),
            camera_profile_id=value.get("camera_profile_id"),
            created_from=value.get("created_from", "user"),
            machine=machine,
            laser=laser,
        )


@dataclass(slots=True)
class ResolvedMachineConfig:
    machine_id: str
    machine_name: str
    created_from: str
    machine_profile: MachineProfile
    tool_head_profile: ToolHeadProfile
    machine: MachineSettings
    laser: LaserSettings
    calibration_profile_id: str | None = None
    camera_profile_id: str | None = None


def _machine_defaults(
    backend: str,
    protocol: str,
    *,
    maximum_feed: float = 6000.0,
) -> MachineSettings:
    return MachineSettings(
        backend=backend,
        protocol=protocol,
        port=UNCONFIGURED_CONTROLLER_PORT,
        work_area=WorkArea(0.0, 220.0, 0.0, 220.0),
        photo_x=110.0,
        photo_y=110.0,
        allow_motion=False,
        max_travel_feed_mm_min=maximum_feed,
        max_work_feed_mm_min=maximum_feed,
    )


def builtin_machine_profiles() -> dict[str, MachineProfile]:
    profiles = (
        MachineProfile(
            "generic-grbl",
            "Generic GRBL laser",
            _machine_defaults("serial", "grbl"),
            description=(
                "Generic serial GRBL/FluidNC-compatible Cartesian laser."
            ),
            capabilities=("gcode", "homing", "xy-motion", "dynamic-power"),
        ),
        MachineProfile(
            "generic-marlin",
            "Generic Marlin laser",
            _machine_defaults("serial", "marlin"),
            description=(
                "Generic serial Marlin Cartesian motion platform with a "
                "laser head."
            ),
            capabilities=("gcode", "homing", "xy-motion"),
        ),
        MachineProfile(
            "ender-3-s1-pro",
            "Creality Ender-3 S1 Pro",
            _machine_defaults("serial", "auto", maximum_feed=3000.0),
            manufacturer="Creality",
            model="Ender-3 S1 Pro",
            description=(
                "Starting profile for the existing E3-mounted Cartesian "
                "setup."
            ),
            capabilities=("gcode", "homing", "xy-motion"),
        ),
        MachineProfile(
            "custom-machine",
            "Custom machine",
            _machine_defaults("serial", "auto"),
            description=(
                "Unclassified machine retained without inferring a make or "
                "model."
            ),
            capabilities=("gcode", "homing", "xy-motion"),
        ),
    )
    return {profile.id: profile for profile in profiles}


def builtin_tool_head_profiles() -> dict[str, ToolHeadProfile]:
    defaults = _safe_laser(_machine_defaults("serial", "auto"))
    profiles = (
        ToolHeadProfile(
            "generic-diode-10w",
            "Generic 10 W diode laser",
            defaults,
            nominal_output_watts=10.0,
            description=(
                "Safe starting defaults for a nominal 10 W diode module."
            ),
            capabilities=("diode", "dynamic-power"),
        ),
        ToolHeadProfile(
            "custom-laser-head",
            "Custom laser head",
            defaults,
            description=(
                "Unclassified laser head retained without inferring optical "
                "wattage."
            ),
            capabilities=("laser",),
        ),
    )
    return {profile.id: profile for profile in profiles}


def _copy_machine(machine: MachineInstance) -> MachineInstance:
    return MachineInstance.from_dict(machine.to_dict())


def _slug(value: str) -> str:
    slug = _SLUG_RE.sub("-", value.lower()).strip("-") or "machine"
    return slug[:72].rstrip("-") or "machine"


def _migrated_machine(settings: Settings) -> MachineInstance:
    optical_profile_id = signature_from_camera_settings(settings.camera).key
    profile_id = {
        "grbl": "generic-grbl",
        "marlin": "generic-marlin",
    }.get(settings.machine.protocol, "custom-machine")
    head_id = "custom-laser-head"
    name = "Current configured machine"
    return MachineInstance(
        id="existing-machine",
        name=name,
        machine_profile_id=profile_id,
        tool_head_profile_id=head_id,
        machine=settings.machine,
        laser=settings.laser,
        calibration_profile_id=optical_profile_id,
        camera_profile_id=optical_profile_id,
        created_from="legacy-config",
    )


@dataclass(slots=True)
class MachineRegistryRecoveryState:
    """Read-only legacy-registry state used before a product runtime exists."""

    registry: MachineRegistry
    original_bytes: bytes | None
    raw_physical_machines: tuple[dict[str, Any], ...]
    simulator_machine_ids: tuple[str, ...]
    original_active_machine_id: str | None

    @property
    def physical_machines(self) -> tuple[MachineInstance, ...]:
        return self.registry.machines()

    def recovered_payload(self) -> dict[str, Any]:
        """Filter simulators while preserving raw physical machine values."""

        current = {machine.id: machine for machine in self.registry.machines()}
        original_ids: list[str] = []
        machines: list[dict[str, Any]] = []
        for raw in self.raw_physical_machines:
            machine_id = _identifier(raw.get("id"), "machine.id")
            original_ids.append(machine_id)
            if machine_id not in current:
                raise MachineRegistryError(
                    "Simulator recovery cannot remove a physical saved machine"
                )
            original = MachineInstance.from_dict(raw)
            if current[machine_id].to_dict() != original.to_dict():
                raise MachineRegistryError(
                    "Simulator recovery cannot modify an existing physical saved machine"
                )
            machines.append(copy.deepcopy(raw))
        for machine_id in sorted(set(current) - set(original_ids)):
            machines.append(current[machine_id].to_dict())
        return {
            "schema_version": MACHINE_REGISTRY_SCHEMA_VERSION,
            "active_machine_id": self.registry.active_machine_id,
            "machines": machines,
        }


class MachineRegistry:
    """Versioned saved-machine data with no controller authority."""

    def __init__(
        self,
        path: Path,
        *,
        machine_state_root: Path | None = None,
    ) -> None:
        self.path = Path(path)
        self._machine_state_root = (
            Path(machine_state_root)
            if machine_state_root is not None
            else self.path.parent / "machine_state"
        )
        self._profiles = builtin_machine_profiles()
        self._heads = builtin_tool_head_profiles()
        self._machines: dict[str, MachineInstance] = {}
        self._active_machine_id: str | None = None
        self._reserved_machine_ids: set[str] = set()
        self._lock = threading.RLock()

    @classmethod
    def load_or_migrate(
        cls,
        settings: Settings,
        path: Path | None = None,
    ) -> MachineRegistry:
        registry = cls(
            path or settings.app.data_dir / MACHINE_REGISTRY_FILENAME,
            machine_state_root=settings.app.data_dir / "machine_state",
        )
        if registry.path.exists():
            registry.load()
            return registry
        migrated = _migrated_machine(settings)
        registry._machines[migrated.id] = migrated
        registry._active_machine_id = migrated.id
        if atomic_write_bytes_if_absent(
            registry.path,
            registry._encoded_payload(),
        ):
            return registry
        registry.load()
        return registry

    @classmethod
    def load_for_recovery(
        cls,
        path: Path,
        *,
        machine_state_root: Path | None = None,
    ) -> MachineRegistryRecoveryState:
        """Read physical candidates without accepting or rewriting simulators."""

        registry = cls(path, machine_state_root=machine_state_root)
        if not registry.path.exists():
            return MachineRegistryRecoveryState(
                registry=registry,
                original_bytes=None,
                raw_physical_machines=(),
                simulator_machine_ids=(),
                original_active_machine_id=None,
            )
        try:
            original_bytes = registry.path.read_bytes()
            value = strict_json_loads(original_bytes.decode("utf-8"))
        except UnicodeError as exc:
            raise MachineRegistryError(
                f"Machine registry is not valid UTF-8: {registry.path}"
            ) from exc
        except (OSError, ValueError, RecursionError) as exc:
            raise MachineRegistryError(
                f"Invalid machine registry {registry.path}: {exc}"
            ) from exc
        if not isinstance(value, Mapping):
            raise MachineRegistryError("Machine registry must be a JSON object")
        allowed = {"schema_version", "active_machine_id", "machines"}
        unknown = sorted(str(key) for key in value if key not in allowed)
        if unknown:
            raise MachineRegistryError(
                f"Unknown machine registry key(s): {', '.join(unknown)}"
            )
        version = value.get("schema_version")
        if type(version) is not int:
            raise MachineRegistryError(
                "machine_registry.schema_version must be a JSON integer"
            )
        if version != MACHINE_REGISTRY_SCHEMA_VERSION:
            raise MachineRegistryError(
                f"Unsupported machine registry schema {version}; expected "
                f"{MACHINE_REGISTRY_SCHEMA_VERSION}"
            )
        items = value.get("machines")
        if not isinstance(items, list):
            raise MachineRegistryError(
                "machine_registry.machines must be a JSON array"
            )
        if not items:
            raise MachineRegistryError(
                "Machine registry must contain at least one saved machine"
            )

        all_ids: set[str] = set()
        simulator_ids: set[str] = set()
        raw_physical: list[dict[str, Any]] = []
        physical: dict[str, MachineInstance] = {}
        for item in items:
            if not isinstance(item, Mapping):
                raise MachineRegistryError("machine must be a JSON object")
            machine_id = _identifier(item.get("id"), "machine.id")
            if machine_id in all_ids:
                raise MachineRegistryError(
                    f"Duplicate saved machine ID: {machine_id}"
                )
            all_ids.add(machine_id)
            if _is_removed_simulator_entry(item):
                simulator_ids.add(machine_id)
                continue
            machine = MachineInstance.from_dict(item)
            registry._validate_references(machine)
            physical[machine.id] = machine
            raw_physical.append(copy.deepcopy(dict(item)))
        active = _identifier(
            value.get("active_machine_id"),
            "machine_registry.active_machine_id",
        )
        if active not in all_ids:
            raise MachineRegistryError(
                "machine_registry.active_machine_id does not reference a "
                "saved machine"
            )
        registry._machines = physical
        # Deliberately require the recovery UI to make a new explicit choice.
        registry._active_machine_id = None
        registry._reserved_machine_ids = set(simulator_ids)
        return MachineRegistryRecoveryState(
            registry=registry,
            original_bytes=original_bytes,
            raw_physical_machines=tuple(raw_physical),
            simulator_machine_ids=tuple(sorted(simulator_ids)),
            original_active_machine_id=active,
        )

    @property
    def active_machine_id(self) -> str:
        with self._lock:
            if self._active_machine_id is None:
                raise MachineRegistryError(
                    "The registry has no active machine"
                )
            return self._active_machine_id

    @property
    def active_machine(self) -> MachineInstance:
        return self.get_machine(self.active_machine_id)

    def machines(self) -> tuple[MachineInstance, ...]:
        with self._lock:
            return tuple(
                _copy_machine(self._machines[key])
                for key in sorted(self._machines)
            )

    def machine_profiles(self) -> tuple[MachineProfile, ...]:
        with self._lock:
            return tuple(
                copy.deepcopy(self._profiles[key])
                for key in sorted(self._profiles)
            )

    def tool_head_profiles(self) -> tuple[ToolHeadProfile, ...]:
        with self._lock:
            return tuple(
                copy.deepcopy(self._heads[key])
                for key in sorted(self._heads)
            )

    def get_machine(self, machine_id: str) -> MachineInstance:
        normalized = _identifier(machine_id, "machine.id")
        with self._lock:
            try:
                return _copy_machine(self._machines[normalized])
            except KeyError as exc:
                raise MachineRegistryError(
                    f"Unknown saved machine: {normalized}"
                ) from exc

    def get_machine_profile(self, profile_id: str) -> MachineProfile:
        normalized = _identifier(profile_id, "machine_profile.id")
        with self._lock:
            try:
                return copy.deepcopy(self._profiles[normalized])
            except KeyError as exc:
                raise MachineRegistryError(
                    f"Unknown machine profile: {normalized}"
                ) from exc

    def get_tool_head_profile(self, profile_id: str) -> ToolHeadProfile:
        normalized = _identifier(profile_id, "tool_head_profile.id")
        with self._lock:
            try:
                return copy.deepcopy(self._heads[normalized])
            except KeyError as exc:
                raise MachineRegistryError(
                    f"Unknown tool-head profile: {normalized}"
                ) from exc

    def resolve_machine(
        self,
        machine_id: str | None = None,
    ) -> ResolvedMachineConfig:
        instance = self.get_machine(machine_id or self.active_machine_id)
        machine, laser = _copy_pair(instance.machine, instance.laser)
        return ResolvedMachineConfig(
            machine_id=instance.id,
            machine_name=instance.name,
            created_from=instance.created_from,
            machine_profile=self.get_machine_profile(
                instance.machine_profile_id
            ),
            tool_head_profile=self.get_tool_head_profile(
                instance.tool_head_profile_id
            ),
            machine=machine,
            laser=laser,
            calibration_profile_id=instance.calibration_profile_id,
            camera_profile_id=instance.camera_profile_id,
        )

    def _machine_state_scope_exists(self, machine_id: str) -> bool:
        return (self._machine_state_root / machine_id).exists()

    def _machine_id_is_available(self, machine_id: str) -> bool:
        return (
            machine_id not in self._machines
            and machine_id not in self._reserved_machine_ids
            and not self._machine_state_scope_exists(machine_id)
        )

    def _next_available_machine_id(self, name: str) -> str:
        base = _slug(name)
        candidate_id = base
        suffix = 2
        while not self._machine_id_is_available(candidate_id):
            candidate_id = f"{base[:72]}-{suffix}"
            suffix += 1
        return candidate_id

    def create_machine(
        self,
        name: str,
        machine_profile_id: str,
        tool_head_profile_id: str,
        *,
        machine_id: str | None = None,
        persist: bool = True,
    ) -> MachineInstance:
        profile = self.get_machine_profile(machine_profile_id)
        head = self.get_tool_head_profile(tool_head_profile_id)
        validated_name = _required_text(name, "machine.name")
        with self._lock:
            if machine_id is None:
                candidate_id = self._next_available_machine_id(validated_name)
            else:
                candidate_id = _identifier(machine_id, "machine.id")
                if candidate_id in self._machines:
                    raise MachineRegistryError(
                        f"Saved machine ID already exists: {candidate_id}"
                    )
                if candidate_id in self._reserved_machine_ids:
                    raise MachineRegistryError(
                        "Saved machine ID belongs to a retired simulator: "
                        f"{candidate_id}; choose a different ID"
                    )
                if self._machine_state_scope_exists(candidate_id):
                    raise MachineRegistryError(
                        "Saved machine ID has preserved machine-state evidence: "
                        f"{candidate_id}; choose a different ID"
                    )
            candidate = MachineInstance(
                id=candidate_id,
                name=validated_name,
                machine_profile_id=profile.id,
                tool_head_profile_id=head.id,
                machine=profile.machine_defaults,
                laser=head.laser_defaults,
                created_from="profile",
            )
            if candidate.id in self._machines:
                raise MachineRegistryError(
                    f"Saved machine ID already exists: {candidate.id}"
                )
            previous_active = self._active_machine_id
            self._machines[candidate.id] = candidate
            if self._active_machine_id is None:
                self._active_machine_id = candidate.id
            if persist:
                try:
                    self.save()
                except Exception:
                    del self._machines[candidate.id]
                    self._active_machine_id = previous_active
                    raise
            return _copy_machine(candidate)


    def duplicate_machine(
        self,
        machine_id: str,
        *,
        name: str | None = None,
        persist: bool = True,
    ) -> MachineInstance:
        source = self.get_machine(machine_id)
        duplicated_name = _required_text(
            name or f"{source.name} copy",
            "machine.name",
        )
        with self._lock:
            candidate_id = self._next_available_machine_id(duplicated_name)
            candidate = _copy_machine(source)
            candidate.id = candidate_id
            candidate.name = duplicated_name
            candidate.created_from = f"duplicate:{source.id}"
            previous_active = self._active_machine_id
            self._machines[candidate.id] = candidate
            if self._active_machine_id is None:
                self._active_machine_id = candidate.id
            if persist:
                try:
                    self.save()
                except Exception:
                    del self._machines[candidate.id]
                    self._active_machine_id = previous_active
                    raise
            return _copy_machine(candidate)

    def update_machine(
        self,
        machine: MachineInstance,
        *,
        persist: bool = True,
    ) -> MachineInstance:
        candidate = _copy_machine(machine)
        with self._lock:
            if candidate.id not in self._machines:
                raise MachineRegistryError(
                    f"Unknown saved machine: {candidate.id}"
                )
            self._validate_references(candidate)
            previous = self._machines[candidate.id]
            self._machines[candidate.id] = candidate
            if persist:
                try:
                    self.save()
                except Exception:
                    self._machines[candidate.id] = previous
                    raise
            return _copy_machine(candidate)

    def set_active(
        self,
        machine_id: str,
        *,
        persist: bool = True,
    ) -> None:
        normalized = _identifier(machine_id, "machine.id")
        with self._lock:
            if normalized not in self._machines:
                raise MachineRegistryError(
                    f"Unknown saved machine: {normalized}"
                )
            previous = self._active_machine_id
            self._active_machine_id = normalized
            if persist:
                try:
                    self.save()
                except Exception:
                    self._active_machine_id = previous
                    raise

    def remove_machine(
        self,
        machine_id: str,
        *,
        persist: bool = True,
    ) -> None:
        normalized = _identifier(machine_id, "machine.id")
        with self._lock:
            if normalized not in self._machines:
                raise MachineRegistryError(
                    f"Unknown saved machine: {normalized}"
                )
            if len(self._machines) == 1:
                raise MachineRegistryError(
                    "Cannot remove the only saved machine"
                )
            if normalized == self._active_machine_id:
                raise MachineRegistryError(
                    "Cannot remove the active machine; select another "
                    "machine first"
                )
            removed = self._machines.pop(normalized)
            if persist:
                try:
                    self.save()
                except Exception:
                    self._machines[normalized] = removed
                    raise

    def load(self) -> None:
        try:
            original_bytes = self.path.read_bytes()
            value = strict_json_loads(original_bytes.decode("utf-8"))
        except FileNotFoundError as exc:
            raise MachineRegistryError(
                f"Machine registry does not exist: {self.path}"
            ) from exc
        except UnicodeError as exc:
            raise MachineRegistryError(
                f"Machine registry is not valid UTF-8: {self.path}"
            ) from exc
        except (OSError, ValueError, RecursionError) as exc:
            raise MachineRegistryError(
                f"Invalid machine registry {self.path}: {exc}"
            ) from exc
        if not isinstance(value, Mapping):
            raise MachineRegistryError(
                "Machine registry must be a JSON object"
            )
        allowed = {"schema_version", "active_machine_id", "machines"}
        unknown = sorted(str(key) for key in value if key not in allowed)
        if unknown:
            raise MachineRegistryError(
                f"Unknown machine registry key(s): {', '.join(unknown)}"
            )
        version = value.get("schema_version")
        if type(version) is not int:
            raise MachineRegistryError(
                "machine_registry.schema_version must be a JSON integer"
            )
        if version != MACHINE_REGISTRY_SCHEMA_VERSION:
            raise MachineRegistryError(
                f"Unsupported machine registry schema {version}; expected "
                f"{MACHINE_REGISTRY_SCHEMA_VERSION}"
            )
        items = value.get("machines")
        if not isinstance(items, list):
            raise MachineRegistryError(
                "machine_registry.machines must be a JSON array"
            )
        removed_ids = {
            str(item.get("id"))
            for item in items
            if isinstance(item, Mapping) and _is_removed_simulator_entry(item)
        }
        active_value = value.get("active_machine_id")
        if removed_ids and active_value in removed_ids:
            raise MachineSetupRequired(
                "The active saved machine used the removed E3 simulator. "
                "Configure and explicitly select a real machine before continuing."
            )
        migrated_items = [
            item for item in items if not _is_removed_simulator_entry(item)
        ]
        if removed_ids:
            if not migrated_items:
                raise MachineSetupRequired(
                    "The saved-machine registry contains only the removed E3 "
                    "simulator. Configure a real machine before continuing."
                )
            items = migrated_items
        machines: dict[str, MachineInstance] = {}
        for item in items:
            machine = MachineInstance.from_dict(item)
            if machine.id in machines:
                raise MachineRegistryError(
                    f"Duplicate saved machine ID: {machine.id}"
                )
            self._validate_references(machine)
            machines[machine.id] = machine
        if not machines:
            raise MachineRegistryError(
                "Machine registry must contain at least one saved machine"
            )
        active = _identifier(
            value.get("active_machine_id"),
            "machine_registry.active_machine_id",
        )
        if active not in machines:
            raise MachineRegistryError(
                "machine_registry.active_machine_id does not reference a "
                "saved machine"
            )
        with self._lock:
            self._machines = machines
            self._active_machine_id = active
        if removed_ids:
            backup_path = self.path.with_name(
                self.path.name + REMOVED_SIMULATOR_BACKUP_SUFFIX
            )
            atomic_write_bytes_if_absent(backup_path, original_bytes)
            self.save()

    def save(self) -> None:
        with self._lock:
            atomic_write_json(self.path, self._payload())

    def _validate_references(self, machine: MachineInstance) -> None:
        if machine.machine_profile_id not in self._profiles:
            raise MachineRegistryError(
                f"Machine {machine.id} references unknown machine profile "
                f"{machine.machine_profile_id}"
            )
        if machine.tool_head_profile_id not in self._heads:
            raise MachineRegistryError(
                f"Machine {machine.id} references unknown tool-head profile "
                f"{machine.tool_head_profile_id}"
            )

    def _payload(self) -> dict[str, Any]:
        if self._active_machine_id is None:
            raise MachineRegistryError("The registry has no active machine")
        if self._active_machine_id not in self._machines:
            raise MachineRegistryError(
                "The active machine does not reference a saved machine"
            )
        for machine in self._machines.values():
            self._validate_references(machine)
        return {
            "schema_version": MACHINE_REGISTRY_SCHEMA_VERSION,
            "active_machine_id": self._active_machine_id,
            "machines": [
                self._machines[key].to_dict()
                for key in sorted(self._machines)
            ],
        }

    def _encoded_payload(self) -> bytes:
        return (
            json.dumps(
                self._payload(),
                indent=2,
                sort_keys=True,
                allow_nan=False,
            )
            + "\n"
        ).encode("utf-8")


__all__ = [
    "MACHINE_REGISTRY_FILENAME",
    "MACHINE_REGISTRY_SCHEMA_VERSION",
    "REMOVED_SIMULATOR_BACKUP_SUFFIX",
    "MachineInstance",
    "MachineProfile",
    "MachineRegistry",
    "MachineRegistryError",
    "MachineRegistryRecoveryState",
    "MachineSetupRequired",
    "ResolvedMachineConfig",
    "ToolHeadProfile",
    "builtin_machine_profiles",
    "builtin_tool_head_profiles",
]
