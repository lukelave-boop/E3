from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

from .config import Settings, load_settings
from .deployment import bridge_token_path, user_config_path, user_state_root
from .machine.network_transport import parse_bridge_uri
from .machine.profiles import (
    MACHINE_REGISTRY_FILENAME,
    MachineInstance,
    MachineRegistry,
    builtin_machine_profiles,
    builtin_tool_head_profiles,
)
from .storage import (
    atomic_write_bytes,
    atomic_write_json,
    atomic_write_text,
    strict_json_loads,
)

_SETUP_STATE_FILENAME = "first-run.json"
_MINIMUM_BRIDGE_TOKEN_LENGTH = 24
_DEFAULT_HARDWARE_MACHINE_PROFILE_ID = "generic-grbl"
_DEFAULT_HARDWARE_TOOL_HEAD_PROFILE_ID = "custom-laser-head"


def setup_state_path() -> Path:
    return user_state_root() / _SETUP_STATE_FILENAME


def mark_setup_complete() -> None:
    atomic_write_json(
        setup_state_path(),
        {"schema_version": 1, "deferred": False, "configured": True},
    )


def _template_payload(template_path: Path) -> dict[str, Any]:
    payload = strict_json_loads(template_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Default E3 configuration must be a JSON object")
    return payload


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

    payload = _template_payload(template_path)
    machine, laser = _profile_payload(
        machine_profile_id,
        tool_head_profile_id,
    )
    app = payload.setdefault("app", {})
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
) -> MachineRegistry:
    registry_path = settings.app.data_dir / MACHINE_REGISTRY_FILENAME
    registry = (
        MachineRegistry.load_or_migrate(settings)
        if registry_path.exists()
        else _new_registry(settings)
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


def _validate_profile_setup(
    payload: dict[str, Any],
    config_path: Path,
    *,
    machine_name: str,
    machine_profile_id: str,
    tool_head_profile_id: str,
) -> tuple[Settings, MachineRegistry]:
    """Validate complete settings and registry state before canonical writes."""

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
    registry = _prepare_profile_snapshot(
        settings,
        machine_name=machine_name,
        machine_profile_id=machine_profile_id,
        tool_head_profile_id=tool_head_profile_id,
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
) -> None:
    token_path = bridge_token_path() if token is not None else None
    state_path = setup_state_path()
    paths = [config_path, registry.path]
    if token_path is not None:
        paths.append(token_path)
    paths.append(state_path)
    snapshots = {path: _snapshot_file(path) for path in paths}
    attempted: list[Path] = []

    try:
        attempted.append(config_path)
        atomic_write_json(config_path, payload)
        attempted.append(registry.path)
        registry.save()
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


def save_profile_setup(
    template_path: Path,
    *,
    machine_name: str,
    machine_profile_id: str,
    tool_head_profile_id: str,
    bridge_token: str | None = None,
    **configuration: Any,
) -> Path:
    """Save a safe first-run config and select its profile snapshot."""

    token = str(bridge_token or "").strip()
    if len(token) < _MINIMUM_BRIDGE_TOKEN_LENGTH:
        raise ValueError(
            "Bridge credential must contain at least "
            f"{_MINIMUM_BRIDGE_TOKEN_LENGTH} characters"
        )
    payload = build_hardware_config(
        template_path,
        machine_profile_id=machine_profile_id,
        tool_head_profile_id=tool_head_profile_id,
        **configuration,
    )

    config_path = user_config_path()
    _settings, registry = _validate_profile_setup(
        payload,
        config_path,
        machine_name=machine_name,
        machine_profile_id=machine_profile_id,
        tool_head_profile_id=tool_head_profile_id,
    )
    _persist_profile_setup(
        config_path=config_path,
        payload=payload,
        registry=registry,
        token=token,
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
