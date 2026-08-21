from __future__ import annotations

import copy
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import (
    DEFAULT_CONFIG,
    UNCONFIGURED_CONTROLLER_PORT,
    ConfigError,
    Settings,
    load_settings,
)
from .deployment import bridge_token_path, user_config_path, user_state_root
from .machine.network_transport import parse_bridge_uri
from .machine.profiles import (
    MACHINE_REGISTRY_FILENAME,
    REMOVED_SIMULATOR_BACKUP_SUFFIX,
    MachineInstance,
    MachineRegistry,
    MachineRegistryRecoveryState,
    builtin_machine_profiles,
    builtin_tool_head_profiles,
)
from .storage import (
    atomic_write_bytes,
    atomic_write_bytes_if_absent,
    atomic_write_json,
    atomic_write_text,
    strict_json_loads,
)

_SETUP_STATE_FILENAME = "first-run.json"
_MINIMUM_BRIDGE_TOKEN_LENGTH = 24
_DEFAULT_HARDWARE_MACHINE_PROFILE_ID = "generic-grbl"
_DEFAULT_HARDWARE_TOOL_HEAD_PROFILE_ID = "custom-laser-head"


@dataclass(frozen=True, slots=True)
class SimulatorRecoveryPlan:
    """Immutable pre-runtime evidence for an explicit simulator recovery."""

    source_config_path: Path
    source_config_bytes: bytes
    replacement_config_path: Path
    replacement_config_bytes: bytes | None
    data_dir: Path
    registry_path: Path
    registry_bytes: bytes | None
    physical_machines: tuple[MachineInstance, ...]
    simulator_machine_ids: tuple[str, ...]
    original_active_machine_id: str | None
    config_simulation_enabled: bool
    config_simulator_backend: bool

    @property
    def active_simulator(self) -> bool:
        return self.original_active_machine_id in self.simulator_machine_ids

    @property
    def configured_physical_machines(self) -> tuple[MachineInstance, ...]:
        return tuple(
            machine
            for machine in self.physical_machines
            if isinstance(machine.machine.port, str)
            and machine.machine.port.strip()
            and machine.machine.port.strip() != UNCONFIGURED_CONTROLLER_PORT
        )


def _recovery_data_dir(
    source_path: Path,
    payload: Mapping[str, Any],
) -> Path:
    app = payload.get("app", {})
    if not isinstance(app, Mapping):
        raise ConfigError("app must be a JSON object")
    value = app.get("data_dir", DEFAULT_CONFIG["app"]["data_dir"])
    if type(value) is not str or not value.strip():
        raise ConfigError("app.data_dir must be a non-empty JSON string")
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (source_path.parent / path).resolve()


def inspect_simulator_recovery(
    config_path: str | Path,
    *,
    replacement_config_path: str | Path | None = None,
) -> SimulatorRecoveryPlan | None:
    """Recognize recoverable simulator state without constructing runtime."""

    source_path = Path(config_path).expanduser().resolve()
    replacement_path = (
        source_path
        if replacement_config_path is None
        else Path(replacement_config_path).expanduser().resolve()
    )
    try:
        source_bytes = source_path.read_bytes()
        payload = strict_json_loads(source_bytes.decode("utf-8"))
    except (OSError, UnicodeError, ValueError, RecursionError):
        return None
    if not isinstance(payload, Mapping):
        return None
    app = payload.get("app", {})
    if not isinstance(app, Mapping):
        return None
    legacy_simulation = app.get("simulation", False)
    if type(legacy_simulation) is not bool:
        return None
    machine = payload.get("machine", {})
    simulator_backend = bool(
        isinstance(machine, Mapping) and machine.get("backend") == "simulator"
    )
    data_dir = _recovery_data_dir(source_path, payload)
    registry_path = data_dir / MACHINE_REGISTRY_FILENAME
    registry_state = MachineRegistry.load_for_recovery(
        registry_path,
        machine_state_root=data_dir / "machine_state",
    )
    active_simulator = (
        registry_state.original_active_machine_id
        in registry_state.simulator_machine_ids
    )
    simulator_only = bool(
        registry_state.simulator_machine_ids
        and not registry_state.physical_machines
    )
    if not (
        legacy_simulation
        or simulator_backend
        or active_simulator
        or simulator_only
    ):
        return None
    if replacement_path == source_path:
        replacement_bytes = source_bytes
    else:
        try:
            replacement_bytes = replacement_path.read_bytes()
        except FileNotFoundError:
            replacement_bytes = None
        except OSError as exc:
            raise ConfigError(
                "Could not inspect the preserved replacement configuration: "
                f"{replacement_path}"
            ) from exc
    return SimulatorRecoveryPlan(
        source_config_path=source_path,
        source_config_bytes=source_bytes,
        replacement_config_path=replacement_path,
        replacement_config_bytes=replacement_bytes,
        data_dir=data_dir,
        registry_path=registry_path,
        registry_bytes=registry_state.original_bytes,
        physical_machines=registry_state.physical_machines,
        simulator_machine_ids=registry_state.simulator_machine_ids,
        original_active_machine_id=(
            registry_state.original_active_machine_id
        ),
        config_simulation_enabled=legacy_simulation,
        config_simulator_backend=simulator_backend,
    )


def setup_state_path() -> Path:
    return user_state_root() / _SETUP_STATE_FILENAME


def mark_setup_complete() -> None:
    atomic_write_json(
        setup_state_path(),
        {"schema_version": 1, "deferred": False, "configured": True},
    )


def _template_payload_bytes(source_bytes: bytes) -> dict[str, Any]:
    payload = strict_json_loads(source_bytes.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Default E3 configuration must be a JSON object")
    return payload


def _template_payload(template_path: Path) -> dict[str, Any]:
    return _template_payload_bytes(template_path.read_bytes())


def _profile_payload(
    machine_profile_id: str,
    tool_head_profile_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        machine_profile = builtin_machine_profiles()[machine_profile_id]
    except KeyError as exc:
        raise ValueError(
            f"Unknown machine profile: {machine_profile_id}"
        ) from exc
    try:
        tool_head_profile = builtin_tool_head_profiles()[tool_head_profile_id]
    except KeyError as exc:
        raise ValueError(
            f"Unknown tool-head profile: {tool_head_profile_id}"
        ) from exc
    snapshot = MachineInstance(
        id="first-run-profile-snapshot",
        name="First-run profile snapshot",
        machine_profile_id=machine_profile.id,
        tool_head_profile_id=tool_head_profile.id,
        machine=machine_profile.machine_defaults,
        laser=tool_head_profile.laser_defaults,
        created_from="profile",
    ).to_dict()
    return snapshot["machine"], snapshot["laser"]


def build_hardware_config(
    template_path: Path,
    *,
    host: str,
    controller_port: int,
    camera_port: int,
    width_mm: float,
    height_mm: float,
    camera_width: int = 1920,
    camera_height: int = 1080,
    autofocus: bool = False,
    focus_value: int = 40,
    machine_profile_id: str = _DEFAULT_HARDWARE_MACHINE_PROFILE_ID,
    tool_head_profile_id: str = _DEFAULT_HARDWARE_TOOL_HEAD_PROFILE_ID,
    _captured_template: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    host = str(host).strip()
    if not host:
        raise ValueError("Raspberry Pi address is required")
    if not 1 <= int(controller_port) <= 65535:
        raise ValueError("Controller port must be between 1 and 65535")
    if not 1 <= int(camera_port) <= 65535:
        raise ValueError("Camera port must be between 1 and 65535")
    if float(width_mm) <= 0.0 or float(height_mm) <= 0.0:
        raise ValueError("Machine width and height must be positive")
    if int(camera_width) <= 0 or int(camera_height) <= 0:
        raise ValueError("Camera resolution must be positive")
    if not 0 <= int(focus_value) <= 250:
        raise ValueError("Manual camera focus must be between 0 and 250")

    try:
        selected_machine_profile = builtin_machine_profiles()[
            machine_profile_id
        ]
    except KeyError as exc:
        raise ValueError(
            f"Unknown machine profile: {machine_profile_id}"
        ) from exc
    if selected_machine_profile.machine_defaults.backend != "serial":
        raise ValueError("Hardware setup requires a serial machine profile")

    payload = (
        copy.deepcopy(dict(_captured_template))
        if _captured_template is not None
        else _template_payload(template_path)
    )
    machine, laser = _profile_payload(
        machine_profile_id,
        tool_head_profile_id,
    )
    app_value = payload.setdefault("app", {})
    if not isinstance(app_value, dict):
        raise ValueError("app must be a JSON object")
    app = app_value
    app.pop("simulation", None)
    app["open_browser"] = False
    app["data_dir"] = "../data"

    camera = payload.setdefault("camera", {})
    camera["device"] = f"e3camera://{host}:{int(camera_port)}"
    camera["width"] = int(camera_width)
    camera["height"] = int(camera_height)
    camera["autostart"] = True
    controls = camera.setdefault("controls", {})
    automatic = 1 if autofocus else 0
    controls["focus_automatic_continuous"] = automatic
    controls["focus_auto"] = automatic
    if not autofocus:
        controls["focus_absolute"] = int(focus_value)

    machine["port"] = f"e3bridge://{host}:{int(controller_port)}"
    parse_bridge_uri(machine["port"])
    machine["allow_motion"] = False
    machine["work_area"] = {
        "x_min": 0.0,
        "x_max": float(width_mm),
        "y_min": 0.0,
        "y_max": float(height_mm),
    }
    machine["photo_position"] = {
        "x": float(width_mm) / 2.0,
        "y": float(height_mm) / 2.0,
        "z": None,
    }
    laser["default_power"] = 0
    laser["frame_power"] = 0
    laser["allow_low_power_frame"] = False
    payload["machine"] = machine
    payload["laser"] = laser
    return payload


def _new_registry(settings: Settings) -> MachineRegistry:
    return MachineRegistry(
        settings.app.data_dir / MACHINE_REGISTRY_FILENAME,
        machine_state_root=settings.app.data_dir / "machine_state",
    )


def _prepare_profile_snapshot(
    settings: Settings,
    *,
    machine_name: str,
    machine_profile_id: str,
    tool_head_profile_id: str,
    base_registry: MachineRegistry | None = None,
) -> MachineRegistry:
    registry_path = settings.app.data_dir / MACHINE_REGISTRY_FILENAME
    registry = (
        base_registry
        if base_registry is not None
        else (
            MachineRegistry.load_or_migrate(settings)
            if registry_path.exists()
            else _new_registry(settings)
        )
    )
    if registry.path != registry_path:
        raise RuntimeError(
            "Saved-machine recovery resolved a different registry path"
        )
    created = registry.create_machine(
        machine_name,
        machine_profile_id,
        tool_head_profile_id,
        persist=False,
    )
    created.machine = settings.machine
    created.machine.allow_motion = False
    created.laser = settings.laser
    created.laser.default_power = 0
    created.laser.frame_power = 0
    created.laser.allow_low_power_frame = False
    created.camera_profile_id = None
    created.calibration_profile_id = None
    registry.update_machine(created, persist=False)
    registry.set_active(created.id, persist=False)
    return registry


def _validate_settings_payload(
    payload: dict[str, Any],
    config_path: Path,
) -> Settings:
    """Validate complete settings before any canonical write."""

    config_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.validate-",
        suffix=".json",
        dir=config_path.parent,
    )
    os.close(descriptor)
    temporary_path = Path(temporary_name)
    try:
        atomic_write_json(temporary_path, payload)
        settings = load_settings(temporary_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    # The temporary file is a sibling of the canonical config, so relative
    # paths already resolved identically. Retain the actual source identity.
    settings.source_path = config_path
    return settings


def _validate_profile_setup(
    payload: dict[str, Any],
    config_path: Path,
    *,
    machine_name: str,
    machine_profile_id: str,
    tool_head_profile_id: str,
    base_registry: MachineRegistry | None = None,
) -> tuple[Settings, MachineRegistry]:
    """Validate complete settings and registry state before canonical writes."""

    settings = _validate_settings_payload(payload, config_path)
    registry = _prepare_profile_snapshot(
        settings,
        machine_name=machine_name,
        machine_profile_id=machine_profile_id,
        tool_head_profile_id=tool_head_profile_id,
        base_registry=base_registry,
    )
    return settings, registry


def _snapshot_file(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _restore_files(
    attempted_paths: list[Path],
    snapshots: dict[Path, bytes | None],
) -> list[tuple[Path, Exception]]:
    failures: list[tuple[Path, Exception]] = []
    restored: set[Path] = set()
    for path in reversed(attempted_paths):
        if path in restored:
            continue
        restored.add(path)
        try:
            previous = snapshots[path]
            if previous is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write_bytes(path, previous)
        except Exception as exc:
            failures.append((path, exc))
    return failures


def _persist_profile_setup(
    *,
    config_path: Path,
    payload: dict[str, Any],
    registry: MachineRegistry,
    token: str | None,
    registry_payload: dict[str, Any] | None = None,
    simulator_backup: tuple[Path, bytes] | None = None,
    expected_snapshots: Mapping[Path, bytes | None] | None = None,
) -> None:
    token_path = bridge_token_path() if token is not None else None
    state_path = setup_state_path()
    paths = [config_path]
    if simulator_backup is not None:
        paths.append(simulator_backup[0])
    paths.append(registry.path)
    if token_path is not None:
        paths.append(token_path)
    paths.append(state_path)
    snapshots = {path: _snapshot_file(path) for path in paths}
    if expected_snapshots is not None:
        for path, expected in expected_snapshots.items():
            actual = (
                snapshots[path]
                if path in snapshots
                else _snapshot_file(path)
            )
            if actual != expected:
                raise RuntimeError(
                    "Configuration or saved-machine data changed during "
                    "simulator recovery; restart E3"
                )
    attempted: list[Path] = []

    try:
        attempted.append(config_path)
        atomic_write_json(config_path, payload)
        if simulator_backup is not None:
            backup_path, backup_bytes = simulator_backup
            attempted.append(backup_path)
            atomic_write_bytes_if_absent(backup_path, backup_bytes)
        attempted.append(registry.path)
        if registry_payload is None:
            registry.save()
        else:
            atomic_write_json(registry.path, registry_payload)
        if token_path is not None:
            attempted.append(token_path)
            atomic_write_text(token_path, token)
        attempted.append(state_path)
        mark_setup_complete()
    except Exception as persistence_error:
        rollback_failures = _restore_files(attempted, snapshots)
        if rollback_failures:
            details = "; ".join(
                f"{path}: {error}" for path, error in rollback_failures
            )
            raise RuntimeError(
                "First-run persistence failed and rollback was incomplete: "
                + details
            ) from persistence_error
        raise


def _current_recovery_state(
    recovery: SimulatorRecoveryPlan,
) -> MachineRegistryRecoveryState:
    try:
        current_config = recovery.source_config_path.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            "The configuration changed during simulator recovery; restart E3"
        ) from exc
    if current_config != recovery.source_config_bytes:
        raise RuntimeError(
            "The configuration changed during simulator recovery; restart E3"
        )
    if recovery.replacement_config_path != recovery.source_config_path:
        if (
            _snapshot_file(recovery.replacement_config_path)
            != recovery.replacement_config_bytes
        ):
            raise RuntimeError(
                "The preserved replacement configuration changed during "
                "simulator recovery; restart E3"
            )
    state = MachineRegistry.load_for_recovery(
        recovery.registry_path,
        machine_state_root=recovery.data_dir / "machine_state",
    )
    if state.original_bytes != recovery.registry_bytes:
        raise RuntimeError(
            "The saved-machine registry changed during simulator recovery; "
            "restart E3"
        )
    return state


def _recovery_backup(
    recovery: SimulatorRecoveryPlan,
) -> tuple[Path, bytes] | None:
    if not recovery.simulator_machine_ids or recovery.registry_bytes is None:
        return None
    return (
        recovery.registry_path.with_name(
            recovery.registry_path.name + REMOVED_SIMULATOR_BACKUP_SUFFIX
        ),
        recovery.registry_bytes,
    )


def save_profile_setup(
    template_path: Path,
    *,
    machine_name: str,
    machine_profile_id: str,
    tool_head_profile_id: str,
    bridge_token: str | None = None,
    simulator_recovery: SimulatorRecoveryPlan | None = None,
    **configuration: Any,
) -> Path:
    """Save a safe first-run config and select its profile snapshot."""

    token = str(bridge_token or "").strip()
    if len(token) < _MINIMUM_BRIDGE_TOKEN_LENGTH:
        raise ValueError(
            "Bridge credential must contain at least "
            f"{_MINIMUM_BRIDGE_TOKEN_LENGTH} characters"
        )
    recovery_state = (
        _current_recovery_state(simulator_recovery)
        if simulator_recovery is not None
        else None
    )
    captured_template = (
        _template_payload_bytes(simulator_recovery.source_config_bytes)
        if simulator_recovery is not None
        else None
    )
    payload = build_hardware_config(
        template_path,
        machine_profile_id=machine_profile_id,
        tool_head_profile_id=tool_head_profile_id,
        _captured_template=captured_template,
        **configuration,
    )
    if simulator_recovery is not None:
        payload["app"]["data_dir"] = str(simulator_recovery.data_dir)

    config_path = (
        simulator_recovery.replacement_config_path
        if simulator_recovery is not None
        else user_config_path()
    )
    settings, registry = _validate_profile_setup(
        payload,
        config_path,
        machine_name=machine_name,
        machine_profile_id=machine_profile_id,
        tool_head_profile_id=tool_head_profile_id,
        base_registry=(
            recovery_state.registry
            if recovery_state is not None
            else None
        ),
    )
    if (
        simulator_recovery is not None
        and settings.app.data_dir != simulator_recovery.data_dir
    ):
        raise RuntimeError(
            "Simulator recovery did not preserve the saved-machine data directory"
        )
    recovery_registry_payload = (
        recovery_state.recovered_payload()
        if recovery_state is not None
        else None
    )
    _persist_profile_setup(
        config_path=config_path,
        payload=payload,
        registry=registry,
        token=token,
        registry_payload=recovery_registry_payload,
        simulator_backup=(
            _recovery_backup(simulator_recovery)
            if simulator_recovery is not None
            else None
        ),
        expected_snapshots=(
            {
                simulator_recovery.source_config_path: (
                    simulator_recovery.source_config_bytes
                ),
                simulator_recovery.replacement_config_path: (
                    simulator_recovery.replacement_config_bytes
                ),
                simulator_recovery.registry_path: (
                    simulator_recovery.registry_bytes
                ),
            }
            if simulator_recovery is not None
            else None
        ),
    )
    return config_path


def save_simulator_recovery_selection(
    recovery: SimulatorRecoveryPlan,
    machine_id: str,
) -> Path:
    """Explicitly select an existing physical machine and retire simulators."""

    state = _current_recovery_state(recovery)
    configured_ids = {
        machine.id for machine in recovery.configured_physical_machines
    }
    if machine_id not in configured_ids:
        raise ValueError(
            "Choose a saved physical machine with an explicit controller port"
        )
    selected = state.registry.get_machine(machine_id)
    state.registry.set_active(machine_id, persist=False)
    payload = _template_payload_bytes(recovery.source_config_bytes)
    app_value = payload.setdefault("app", {})
    if not isinstance(app_value, dict):
        raise ValueError("app must be a JSON object")
    app_value.pop("simulation", None)
    app_value["data_dir"] = str(recovery.data_dir)
    selected_payload = selected.to_dict()
    payload["machine"] = selected_payload["machine"]
    payload["laser"] = selected_payload["laser"]

    config_path = recovery.replacement_config_path
    settings = _validate_settings_payload(payload, config_path)
    if settings.app.data_dir != recovery.data_dir:
        raise RuntimeError(
            "Simulator recovery did not preserve the saved-machine data directory"
        )
    _persist_profile_setup(
        config_path=config_path,
        payload=payload,
        registry=state.registry,
        token=None,
        registry_payload=state.recovered_payload(),
        simulator_backup=_recovery_backup(recovery),
        expected_snapshots={
            recovery.source_config_path: recovery.source_config_bytes,
            recovery.replacement_config_path: (
                recovery.replacement_config_bytes
            ),
            recovery.registry_path: recovery.registry_bytes,
        },
    )
    return config_path


def save_hardware_setup(
    template_path: Path,
    *,
    bridge_token: str,
    machine_name: str = "Network laser",
    machine_profile_id: str = _DEFAULT_HARDWARE_MACHINE_PROFILE_ID,
    tool_head_profile_id: str = _DEFAULT_HARDWARE_TOOL_HEAD_PROFILE_ID,
    **configuration: Any,
) -> Path:
    """Compatibility wrapper for the profile-backed hardware setup path."""

    return save_profile_setup(
        template_path,
        bridge_token=bridge_token,
        machine_name=machine_name,
        machine_profile_id=machine_profile_id,
        tool_head_profile_id=tool_head_profile_id,
        **configuration,
    )
