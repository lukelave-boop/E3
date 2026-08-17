from __future__ import annotations

from pathlib import Path
from typing import Any

from .deployment import bridge_token_path, user_config_path, user_state_root
from .storage import atomic_write_json, atomic_write_text, read_json, strict_json_loads

_SETUP_STATE_FILENAME = "first-run.json"
_MINIMUM_BRIDGE_TOKEN_LENGTH = 24


def setup_state_path() -> Path:
    return user_state_root() / _SETUP_STATE_FILENAME


def setup_deferred() -> bool:
    state = read_json(setup_state_path(), {})
    return isinstance(state, dict) and state.get("deferred") is True


def mark_setup_deferred() -> None:
    atomic_write_json(
        setup_state_path(),
        {"schema_version": 1, "deferred": True, "configured": False},
    )


def mark_setup_complete() -> None:
    atomic_write_json(
        setup_state_path(),
        {"schema_version": 1, "deferred": False, "configured": True},
    )


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
    allow_motion: bool = True,
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

    payload = strict_json_loads(template_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Default E3 configuration must be a JSON object")

    app = payload.setdefault("app", {})
    app["simulation"] = False
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

    machine = payload.setdefault("machine", {})
    machine["backend"] = "serial"
    machine["protocol"] = "auto"
    machine["port"] = f"e3bridge://{host}:{int(controller_port)}"
    machine["allow_motion"] = bool(allow_motion)
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
    return payload


def save_hardware_setup(
    template_path: Path,
    *,
    bridge_token: str,
    **configuration: Any,
) -> Path:
    token = str(bridge_token).strip()
    if len(token) < _MINIMUM_BRIDGE_TOKEN_LENGTH:
        raise ValueError(
            f"Bridge credential must contain at least {_MINIMUM_BRIDGE_TOKEN_LENGTH} characters"
        )
    payload = build_hardware_config(template_path, **configuration)
    config_path = user_config_path()
    atomic_write_json(config_path, payload)
    atomic_write_text(bridge_token_path(), token)
    mark_setup_complete()
    return config_path
