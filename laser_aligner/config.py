from __future__ import annotations

import copy
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .storage import default_user_data_dir, strict_json_loads


class ConfigError(ValueError):
    """Raised when a configuration file contains an invalid value."""


_SUPPORTED_HTTP_BIND_HOSTS = frozenset({"127.0.0.1", "localhost", "0.0.0.0"})
_MAX_ARM_TIMEOUT_SECONDS = 600


def validate_http_bind_host(value: object) -> str:
    """Return the normalized host supported by the IPv4 HTTP server."""

    if not isinstance(value, str):
        raise ConfigError(
            "app.host must be an IPv4 loopback or wildcard bind; "
            "the HTTP control surface is local-only"
        )
    host = value.strip().lower()
    if host not in _SUPPORTED_HTTP_BIND_HOSTS:
        raise ConfigError(
            "app.host must be an IPv4 loopback or wildcard bind; "
            "the HTTP control surface is local-only"
        )
    return host


def validate_http_port(value: object) -> int:
    """Return an integer TCP port within the valid bind range."""

    if type(value) is not int or not 1 <= value <= 65535:
        raise ConfigError("app.port must be an integer between 1 and 65535")
    return value


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(dict(result[key]), value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _validate_override_keys(
    override: Mapping[str, Any],
    schema: Mapping[str, Any],
    path: tuple[str, ...] = (),
) -> None:
    """Reject misspelled settings while preserving documented extension maps."""

    if path == ("camera", "controls"):
        return
    for key, value in override.items():
        label = ".".join((*path, str(key)))
        if key not in schema:
            raise ConfigError(f"Unknown configuration key: {label}")
        expected = schema[key]
        if isinstance(expected, Mapping):
            if not isinstance(value, Mapping):
                raise ConfigError(f"{label} must be a JSON object")
            _validate_override_keys(value, expected, (*path, str(key)))


def _path(value: str | Path, root: Path) -> Path:
    path = Path(value).expanduser()
    return path if path.is_absolute() else (root / path).resolve()


def _default_user_data_dir() -> Path:
    """Compatibility wrapper for the shared portable path policy."""

    return default_user_data_dir()


def _require_boolean(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ConfigError(f"{label} must be a JSON boolean")
    return value


def _require_integer(value: Any, label: str) -> int:
    if type(value) is not int:
        raise ConfigError(f"{label} must be a JSON integer")
    return value


def _require_number(value: Any, label: str) -> float:
    if type(value) not in {int, float}:
        raise ConfigError(f"{label} must be a finite JSON number")
    number = float(value)
    if not math.isfinite(number):
        raise ConfigError(f"{label} must be a finite JSON number")
    return number


def _require_finite_numbers(value: Any, path: str = "configuration") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ConfigError(f"{path} must be finite")
    if isinstance(value, Mapping):
        for key, child in value.items():
            _require_finite_numbers(child, f"{path}.{key}")
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            _require_finite_numbers(child, f"{path}[{index}]")


@dataclass(slots=True)
class AppSettings:
    host: str = "127.0.0.1"
    port: int = 8080
    data_dir: Path = Path("data")
    simulation: bool = True
    open_browser: bool = True
    allow_remote_control: bool = False
    max_request_bytes: int = 10_000_000


@dataclass(slots=True)
class PrecisionCaptureSettings:
    settle_seconds: float = 1.5
    discard_frames: int = 8
    sample_frames: int = 45
    timeout_seconds: float = 8.0
    minimum_valid_frames: int = 15
    mad_multiplier: float = 3.5
    outlier_floor_px: float = 0.25
    max_jitter_rms_px: float = 0.75
    coordinate_strategy: str = "stable_clarity_consensus"
    consensus_frames: int = 15


@dataclass(slots=True)
class CameraSettings:
    device: str = "/dev/video0"
    width: int = 1920
    height: int = 1080
    fps: int = 15
    fourcc: str = "MJPG"
    autostart: bool = True
    jpeg_quality: int = 90
    warmup_frames: int = 12
    view_rotation_degrees: int = 0
    controls: dict[str, int | bool] = field(default_factory=dict)
    precision_capture: PrecisionCaptureSettings = field(default_factory=PrecisionCaptureSettings)


@dataclass(slots=True)
class WorkArea:
    x_min: float = 0.0
    x_max: float = 220.0
    y_min: float = 0.0
    y_max: float = 220.0

    @property
    def width(self) -> float:
        return self.x_max - self.x_min

    @property
    def height(self) -> float:
        return self.y_max - self.y_min

    def contains(self, x: float, y: float, margin: float = 0.0) -> bool:
        return self.x_min + margin <= x <= self.x_max - margin and self.y_min + margin <= y <= self.y_max - margin


def effective_laser_output_area(
    work_area: WorkArea,
    boundary_margin_mm: float,
    spot_offset_x_mm: float = 0.0,
    spot_offset_y_mm: float = 0.0,
) -> WorkArea:
    """Return desired spot coordinates valid for both spot and controller."""

    values = (
        float(boundary_margin_mm),
        float(spot_offset_x_mm),
        float(spot_offset_y_mm),
        float(work_area.x_min),
        float(work_area.x_max),
        float(work_area.y_min),
        float(work_area.y_max),
    )
    if not all(math.isfinite(value) for value in values):
        raise ValueError("Laser output-area settings must be finite")
    margin, offset_x, offset_y = values[:3]
    if margin < 0.0:
        raise ValueError("Laser boundary margin must be non-negative")
    base_x_min = float(work_area.x_min) + margin
    base_x_max = float(work_area.x_max) - margin
    base_y_min = float(work_area.y_min) + margin
    base_y_max = float(work_area.y_max) - margin
    result = WorkArea(
        x_min=max(base_x_min, base_x_min + offset_x),
        x_max=min(base_x_max, base_x_max + offset_x),
        y_min=max(base_y_min, base_y_min + offset_y),
        y_max=min(base_y_max, base_y_max + offset_y),
    )
    if result.width <= 0.0 or result.height <= 0.0:
        raise ValueError(
            "Laser boundary margin and spot offset leave no guarded output area"
        )
    return result


@dataclass(slots=True)
class MachineSettings:
    backend: str = "simulator"
    protocol: str = "auto"
    port: str = "/dev/ttyUSB0"
    baudrate: int = 115200
    read_timeout: float = 2.0
    work_area: WorkArea = field(default_factory=WorkArea)
    photo_x: float = 110.0
    photo_y: float = 110.0
    photo_z: float | None = None
    home_before_photo: bool = True
    home_and_release_after_powered_job: bool = True
    grbl_step_idle_delay_ms: int = 250
    allow_motion: bool = False
    controller_startup_delay: float = 2.0
    max_travel_feed_mm_min: float = 6000.0
    max_work_feed_mm_min: float = 6000.0


@dataclass(slots=True)
class LensCalibrationSettings:
    columns: int = 9
    rows: int = 6
    square_size_mm: float = 20.0
    minimum_images: int = 10


@dataclass(slots=True)
class BedCalibrationSettings:
    pixels_per_mm: float = 4.0
    ransac_threshold_mm: float = 0.8
    minimum_points: int = 4


@dataclass(slots=True)
class CalibrationSettings:
    lens: LensCalibrationSettings = field(default_factory=LensCalibrationSettings)
    bed: BedCalibrationSettings = field(default_factory=BedCalibrationSettings)


@dataclass(slots=True)
class LaserSettings:
    power_mode: str = "M4"
    power_max: int = 1000
    default_power: int = 100
    frame_power: int = 0
    travel_feed_mm_min: float = 3000.0
    engrave_feed_mm_min: float = 1200.0
    curve_tolerance_mm: float = 0.15
    boundary_margin_mm: float = 0.0
    # Optional final guarded output authority for support-bound jobs. Unlike
    # machine.work_area, this polygon is not a camera crop or parking bound.
    guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None
    # Physical laser spot position relative to the controller's commanded
    # carriage/tool-reference position. To place the spot at a design point,
    # generated motion subtracts this vector from the design coordinates.
    spot_offset_x_mm: float = 0.0
    spot_offset_y_mm: float = 0.0
    arm_timeout_seconds: int = 60
    allow_low_power_frame: bool = False
    return_to_photo_position: bool = False
    preview_acceleration_mm_s2: float = 500.0
    preview_command_delay_ms: float = 0.0


@dataclass(slots=True)
class VisionSettings:
    workpiece_min_area_ratio: float = 0.03
    workpiece_canny_low: int = 40
    workpiece_canny_high: int = 130


@dataclass(slots=True)
class Settings:
    source_path: Path
    project_root: Path
    app: AppSettings
    camera: CameraSettings
    machine: MachineSettings
    calibration: CalibrationSettings
    laser: LaserSettings
    vision: VisionSettings

    def ensure_directories(self) -> None:
        self.app.data_dir.mkdir(parents=True, exist_ok=True)
        for name in ("captures", "lens_images", "generated", "logs"):
            (self.app.data_dir / name).mkdir(parents=True, exist_ok=True)

    def public_dict(self) -> dict[str, Any]:
        """Return settings safe for the local browser UI."""
        return {
            "app": {
                "simulation": self.app.simulation,
                "allow_remote_control": self.app.allow_remote_control,
            },
            "camera": {
                "device": self.camera.device,
                "width": self.camera.width,
                "height": self.camera.height,
                "fps": self.camera.fps,
                "view_rotation_degrees": self.camera.view_rotation_degrees,
                "precision_capture": {
                    "settle_seconds": self.camera.precision_capture.settle_seconds,
                    "discard_frames": self.camera.precision_capture.discard_frames,
                    "sample_frames": self.camera.precision_capture.sample_frames,
                    "timeout_seconds": self.camera.precision_capture.timeout_seconds,
                    "minimum_valid_frames": (self.camera.precision_capture.minimum_valid_frames),
                    "mad_multiplier": self.camera.precision_capture.mad_multiplier,
                    "outlier_floor_px": (self.camera.precision_capture.outlier_floor_px),
                    "max_jitter_rms_px": self.camera.precision_capture.max_jitter_rms_px,
                    "coordinate_strategy": self.camera.precision_capture.coordinate_strategy,
                    "consensus_frames": self.camera.precision_capture.consensus_frames,
                },
            },
            "machine": {
                "backend": self.machine.backend,
                "protocol": self.machine.protocol,
                "port": self.machine.port,
                "baudrate": self.machine.baudrate,
                "allow_motion": self.machine.allow_motion,
                "work_area": {
                    "x_min": self.machine.work_area.x_min,
                    "x_max": self.machine.work_area.x_max,
                    "y_min": self.machine.work_area.y_min,
                    "y_max": self.machine.work_area.y_max,
                },
                "photo_position": {
                    "x": self.machine.photo_x,
                    "y": self.machine.photo_y,
                    "z": self.machine.photo_z,
                },
                "home_and_release_after_powered_job": (
                    self.machine.home_and_release_after_powered_job
                ),
                "grbl_step_idle_delay_ms": self.machine.grbl_step_idle_delay_ms,
                "max_travel_feed_mm_min": self.machine.max_travel_feed_mm_min,
                "max_work_feed_mm_min": self.machine.max_work_feed_mm_min,
            },
            "calibration": {
                "lens": {
                    "columns": self.calibration.lens.columns,
                    "rows": self.calibration.lens.rows,
                    "square_size_mm": self.calibration.lens.square_size_mm,
                    "minimum_images": self.calibration.lens.minimum_images,
                },
                "bed": {
                    "pixels_per_mm": self.calibration.bed.pixels_per_mm,
                    "minimum_points": self.calibration.bed.minimum_points,
                },
            },
            "laser": {
                "power_mode": self.laser.power_mode,
                "power_max": self.laser.power_max,
                "default_power": self.laser.default_power,
                "frame_power": self.laser.frame_power,
                "travel_feed_mm_min": self.laser.travel_feed_mm_min,
                "engrave_feed_mm_min": self.laser.engrave_feed_mm_min,
                "curve_tolerance_mm": self.laser.curve_tolerance_mm,
                "guarded_output_polygon_mm": (
                    None
                    if self.laser.guarded_output_polygon_mm is None
                    else [list(point) for point in self.laser.guarded_output_polygon_mm]
                ),
                "spot_offset_x_mm": self.laser.spot_offset_x_mm,
                "spot_offset_y_mm": self.laser.spot_offset_y_mm,
                "allow_low_power_frame": self.laser.allow_low_power_frame,
                "preview_acceleration_mm_s2": self.laser.preview_acceleration_mm_s2,
                "preview_command_delay_ms": self.laser.preview_command_delay_ms,
            },
        }


DEFAULT_CONFIG: dict[str, Any] = {
    "app": {
        "host": "127.0.0.1",
        "port": 8080,
        "data_dir": "data",
        "simulation": True,
        "open_browser": True,
        "allow_remote_control": False,
        "max_request_bytes": 10_000_000,
    },
    "camera": {
        "device": "/dev/video0",
        "width": 1920,
        "height": 1080,
        "fps": 15,
        "fourcc": "MJPG",
        "autostart": True,
        "jpeg_quality": 90,
        "warmup_frames": 12,
        "view_rotation_degrees": 0,
        "controls": {
            "focus_automatic_continuous": 0,
            "focus_auto": 0,
            "focus_absolute": 40,
            "exposure_auto": 1,
            "auto_exposure": 1,
            "exposure_time_absolute": 250,
            "white_balance_automatic": 0,
            "white_balance_temperature": 4500,
            "gain": 0,
        },
        "precision_capture": {
            "settle_seconds": 1.5,
            "discard_frames": 8,
            "sample_frames": 45,
            "timeout_seconds": 8.0,
            "minimum_valid_frames": 15,
            "mad_multiplier": 3.5,
            "outlier_floor_px": 0.25,
            "max_jitter_rms_px": 0.75,
            "coordinate_strategy": "stable_clarity_consensus",
            "consensus_frames": 15,
        },
    },
    "machine": {
        "backend": "simulator",
        "protocol": "auto",
        "port": "/dev/ttyUSB0",
        "baudrate": 115200,
        "read_timeout": 2.0,
        "work_area": {"x_min": 0.0, "x_max": 220.0, "y_min": 0.0, "y_max": 220.0},
        "photo_position": {"x": 110.0, "y": 110.0, "z": None},
        "home_before_photo": True,
        "home_and_release_after_powered_job": True,
        "grbl_step_idle_delay_ms": 250,
        "allow_motion": False,
        "controller_startup_delay": 2.0,
        "max_travel_feed_mm_min": 6000.0,
        "max_work_feed_mm_min": 6000.0,
    },
    "calibration": {
        "lens": {
            "columns": 9,
            "rows": 6,
            "square_size_mm": 20.0,
            "minimum_images": 10,
        },
        "bed": {
            "pixels_per_mm": 4.0,
            "ransac_threshold_mm": 0.8,
            "minimum_points": 4,
        },
    },
    "laser": {
        "power_mode": "M4",
        "power_max": 1000,
        "default_power": 100,
        "frame_power": 0,
        "travel_feed_mm_min": 3000.0,
        "engrave_feed_mm_min": 1200.0,
        "curve_tolerance_mm": 0.15,
        "boundary_margin_mm": 0.0,
        "guarded_output_polygon_mm": None,
        "spot_offset_x_mm": 0.0,
        "spot_offset_y_mm": 0.0,
        "arm_timeout_seconds": 60,
        "allow_low_power_frame": False,
        "return_to_photo_position": False,
        "preview_acceleration_mm_s2": 500.0,
        "preview_command_delay_ms": 0.0,
    },
    "vision": {
        "workpiece_min_area_ratio": 0.03,
        "workpiece_canny_low": 40,
        "workpiece_canny_high": 130,
    },
}


def _validate(raw: Mapping[str, Any]) -> None:
    _require_finite_numbers(raw)
    precision = raw["camera"]["precision_capture"]
    area = raw["machine"]["work_area"]
    photo = raw["machine"]["photo_position"]
    lens = raw["calibration"]["lens"]
    bed = raw["calibration"]["bed"]
    laser = raw["laser"]
    vision = raw["vision"]
    integer_values = (
        ("app.max_request_bytes", raw["app"]["max_request_bytes"]),
        ("camera.width", raw["camera"]["width"]),
        ("camera.height", raw["camera"]["height"]),
        ("camera.fps", raw["camera"]["fps"]),
        ("camera.jpeg_quality", raw["camera"]["jpeg_quality"]),
        ("camera.warmup_frames", raw["camera"]["warmup_frames"]),
        ("camera.precision_capture.discard_frames", precision["discard_frames"]),
        ("camera.precision_capture.sample_frames", precision["sample_frames"]),
        (
            "camera.precision_capture.minimum_valid_frames",
            precision["minimum_valid_frames"],
        ),
        ("camera.precision_capture.consensus_frames", precision["consensus_frames"]),
        ("machine.baudrate", raw["machine"]["baudrate"]),
        (
            "machine.grbl_step_idle_delay_ms",
            raw["machine"]["grbl_step_idle_delay_ms"],
        ),
        ("calibration.lens.columns", lens["columns"]),
        ("calibration.lens.rows", lens["rows"]),
        ("calibration.lens.minimum_images", lens["minimum_images"]),
        ("calibration.bed.minimum_points", bed["minimum_points"]),
        ("laser.power_max", laser["power_max"]),
        ("laser.default_power", laser["default_power"]),
        ("laser.frame_power", laser["frame_power"]),
        ("laser.arm_timeout_seconds", laser["arm_timeout_seconds"]),
        ("vision.workpiece_canny_low", vision["workpiece_canny_low"]),
        ("vision.workpiece_canny_high", vision["workpiece_canny_high"]),
    )
    for label, value in integer_values:
        _require_integer(value, label)
    number_values = (
        ("camera.precision_capture.settle_seconds", precision["settle_seconds"]),
        ("camera.precision_capture.timeout_seconds", precision["timeout_seconds"]),
        ("camera.precision_capture.mad_multiplier", precision["mad_multiplier"]),
        ("camera.precision_capture.outlier_floor_px", precision["outlier_floor_px"]),
        (
            "camera.precision_capture.max_jitter_rms_px",
            precision["max_jitter_rms_px"],
        ),
        ("machine.read_timeout", raw["machine"]["read_timeout"]),
        ("machine.work_area.x_min", area["x_min"]),
        ("machine.work_area.x_max", area["x_max"]),
        ("machine.work_area.y_min", area["y_min"]),
        ("machine.work_area.y_max", area["y_max"]),
        ("machine.photo_position.x", photo["x"]),
        ("machine.photo_position.y", photo["y"]),
        (
            "machine.controller_startup_delay",
            raw["machine"]["controller_startup_delay"],
        ),
        (
            "machine.max_travel_feed_mm_min",
            raw["machine"]["max_travel_feed_mm_min"],
        ),
        ("machine.max_work_feed_mm_min", raw["machine"]["max_work_feed_mm_min"]),
        ("calibration.lens.square_size_mm", lens["square_size_mm"]),
        ("calibration.bed.pixels_per_mm", bed["pixels_per_mm"]),
        ("calibration.bed.ransac_threshold_mm", bed["ransac_threshold_mm"]),
        ("laser.travel_feed_mm_min", laser["travel_feed_mm_min"]),
        ("laser.engrave_feed_mm_min", laser["engrave_feed_mm_min"]),
        ("laser.curve_tolerance_mm", laser["curve_tolerance_mm"]),
        ("laser.boundary_margin_mm", laser["boundary_margin_mm"]),
        ("laser.spot_offset_x_mm", laser["spot_offset_x_mm"]),
        ("laser.spot_offset_y_mm", laser["spot_offset_y_mm"]),
        ("laser.preview_acceleration_mm_s2", laser["preview_acceleration_mm_s2"]),
        ("laser.preview_command_delay_ms", laser["preview_command_delay_ms"]),
        ("vision.workpiece_min_area_ratio", vision["workpiece_min_area_ratio"]),
    )
    for label, value in number_values:
        _require_number(value, label)
    guarded_polygon = laser.get("guarded_output_polygon_mm")
    if guarded_polygon is not None:
        if not isinstance(guarded_polygon, list) or len(guarded_polygon) != 4:
            raise ConfigError(
                "laser.guarded_output_polygon_mm must be null or four ordered XY points"
            )
        try:
            from .geometry.polygon import normalize_convex_polygon

            normalize_convex_polygon(
                guarded_polygon,
                label="laser.guarded_output_polygon_mm",
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(str(exc)) from exc
    if photo.get("z") is not None:
        _require_number(photo["z"], "machine.photo_position.z")
    controls = raw["camera"].get("controls", {})
    if not isinstance(controls, Mapping) or any(
        not isinstance(key, str) or type(value) not in {bool, int}
        for key, value in controls.items()
    ):
        raise ConfigError("camera.controls must map names to JSON integers or booleans")
    validate_http_bind_host(raw["app"]["host"])
    if _require_boolean(
        raw["app"]["allow_remote_control"],
        "app.allow_remote_control",
    ):
        raise ConfigError(
            "app.allow_remote_control is no longer supported; the HTTP control surface is local-only"
        )
    for section, key in (
        ("app", "simulation"),
        ("app", "open_browser"),
        ("app", "allow_remote_control"),
        ("camera", "autostart"),
        ("machine", "home_before_photo"),
        ("machine", "home_and_release_after_powered_job"),
        ("machine", "allow_motion"),
        ("laser", "allow_low_power_frame"),
        ("laser", "return_to_photo_position"),
    ):
        _require_boolean(raw[section][key], f"{section}.{key}")
    validate_http_port(raw["app"]["port"])
    if _require_integer(
        raw["app"]["max_request_bytes"],
        "app.max_request_bytes",
    ) <= 0:
        raise ConfigError("app.max_request_bytes must be positive")
    if int(raw["camera"]["width"]) <= 0 or int(raw["camera"]["height"]) <= 0:
        raise ConfigError("camera width and height must be positive")
    if int(raw["camera"]["fps"]) <= 0:
        raise ConfigError("camera.fps must be positive")
    if not 1 <= int(raw["camera"]["jpeg_quality"]) <= 100:
        raise ConfigError("camera.jpeg_quality must be between 1 and 100")
    if int(raw["camera"]["warmup_frames"]) < 0:
        raise ConfigError("camera.warmup_frames cannot be negative")
    view_rotation = _require_integer(
        raw["camera"]["view_rotation_degrees"],
        "camera.view_rotation_degrees",
    )
    if view_rotation not in {0, 90, 180, 270}:
        raise ConfigError(
            "camera.view_rotation_degrees must be 0, 90, 180, or 270"
        )
    if not 0 <= int(raw["machine"]["grbl_step_idle_delay_ms"]) < 255:
        raise ConfigError("machine.grbl_step_idle_delay_ms must be between 0 and 254")
    if float(precision["settle_seconds"]) < 0:
        raise ConfigError("camera.precision_capture.settle_seconds cannot be negative")
    if int(precision["discard_frames"]) < 0:
        raise ConfigError("camera.precision_capture.discard_frames cannot be negative")
    sample_frames = int(precision["sample_frames"])
    minimum_valid_frames = int(precision["minimum_valid_frames"])
    if sample_frames < 1:
        raise ConfigError("camera.precision_capture.sample_frames must be positive")
    if not 1 <= minimum_valid_frames <= sample_frames:
        raise ConfigError("camera.precision_capture.minimum_valid_frames must be between 1 and sample_frames")
    if float(precision["timeout_seconds"]) <= 0:
        raise ConfigError("camera.precision_capture.timeout_seconds must be positive")
    if float(precision["mad_multiplier"]) <= 0:
        raise ConfigError("camera.precision_capture.mad_multiplier must be positive")
    if float(precision["outlier_floor_px"]) < 0:
        raise ConfigError("camera.precision_capture.outlier_floor_px cannot be negative")
    if float(precision["max_jitter_rms_px"]) <= 0:
        raise ConfigError("camera.precision_capture.max_jitter_rms_px must be positive")
    if str(precision["coordinate_strategy"]) not in {
        "median",
        "sharpest_inlier_frame",
        "stable_clarity_consensus",
    }:
        raise ConfigError(
            "camera.precision_capture.coordinate_strategy must be median, "
            "sharpest_inlier_frame, or stable_clarity_consensus"
        )
    consensus_frames = int(precision["consensus_frames"])
    if consensus_frames < 1 or (
        precision["coordinate_strategy"] == "stable_clarity_consensus"
        and consensus_frames > sample_frames
    ):
        raise ConfigError(
            "camera.precision_capture.consensus_frames must be positive and cannot "
            "exceed sample_frames for stable_clarity_consensus"
        )
    if float(area["x_max"]) <= float(area["x_min"]):
        raise ConfigError("machine.work_area.x_max must be greater than x_min")
    if float(area["y_max"]) <= float(area["y_min"]):
        raise ConfigError("machine.work_area.y_max must be greater than y_min")
    if str(raw["machine"]["backend"]) not in {"simulator", "serial"}:
        raise ConfigError("machine.backend must be 'simulator' or 'serial'")
    if str(raw["machine"]["protocol"]) not in {"auto", "grbl", "marlin"}:
        raise ConfigError("machine.protocol must be auto, grbl, or marlin")
    if int(raw["machine"]["baudrate"]) <= 0:
        raise ConfigError("machine.baudrate must be positive")
    if float(raw["machine"]["read_timeout"]) <= 0:
        raise ConfigError("machine.read_timeout must be positive")
    if float(raw["machine"]["controller_startup_delay"]) < 0:
        raise ConfigError("machine.controller_startup_delay cannot be negative")
    for key in ("max_travel_feed_mm_min", "max_work_feed_mm_min"):
        if float(raw["machine"][key]) <= 0:
            raise ConfigError(f"machine.{key} must be positive")
    if str(raw["laser"]["power_mode"]).upper() not in {"M3", "M4"}:
        raise ConfigError("laser.power_mode must be M3 or M4")
    power_max = int(raw["laser"]["power_max"])
    if power_max <= 0:
        raise ConfigError("laser.power_max must be positive")
    for key in ("default_power", "frame_power"):
        value = int(raw["laser"][key])
        if not 0 <= value <= power_max:
            raise ConfigError(f"laser.{key} must be between 0 and laser.power_max")
    if int(lens["columns"]) < 3 or int(lens["rows"]) < 3:
        raise ConfigError("lens checkerboard needs at least 3 x 3 inner corners")
    if float(lens["square_size_mm"]) <= 0:
        raise ConfigError("lens square_size_mm must be positive")
    if int(lens["minimum_images"]) < 3:
        raise ConfigError("lens minimum_images must be at least 3")
    if float(bed["pixels_per_mm"]) <= 0:
        raise ConfigError("bed pixels_per_mm must be positive")
    if float(bed["ransac_threshold_mm"]) <= 0:
        raise ConfigError("bed ransac_threshold_mm must be positive")
    if int(bed["minimum_points"]) < 4:
        raise ConfigError("bed minimum_points must be at least 4")
    for key in ("travel_feed_mm_min", "engrave_feed_mm_min", "curve_tolerance_mm"):
        if float(laser[key]) <= 0:
            raise ConfigError(f"laser.{key} must be positive")
    if float(laser["travel_feed_mm_min"]) > float(raw["machine"]["max_travel_feed_mm_min"]):
        raise ConfigError(
            "laser.travel_feed_mm_min cannot exceed machine.max_travel_feed_mm_min"
        )
    if float(laser["engrave_feed_mm_min"]) > float(raw["machine"]["max_work_feed_mm_min"]):
        raise ConfigError(
            "laser.engrave_feed_mm_min cannot exceed machine.max_work_feed_mm_min"
        )
    margin = float(laser["boundary_margin_mm"])
    if margin < 0:
        raise ConfigError("laser.boundary_margin_mm cannot be negative")
    if margin * 2 >= min(float(area["x_max"]) - float(area["x_min"]), float(area["y_max"]) - float(area["y_min"])):
        raise ConfigError("laser.boundary_margin_mm leaves no usable work area")
    maximum_offset = max(
        float(area["x_max"]) - float(area["x_min"]),
        float(area["y_max"]) - float(area["y_min"]),
    )
    for key in ("spot_offset_x_mm", "spot_offset_y_mm"):
        value = float(laser[key])
        if not math.isfinite(value):
            raise ConfigError(f"laser.{key} must be finite")
        if abs(value) > maximum_offset:
            raise ConfigError(f"laser.{key} cannot exceed the configured work-area span")
    arm_timeout = int(laser["arm_timeout_seconds"])
    if not 1 <= arm_timeout <= _MAX_ARM_TIMEOUT_SECONDS:
        raise ConfigError(
            "laser.arm_timeout_seconds must be between 1 and "
            f"{_MAX_ARM_TIMEOUT_SECONDS} seconds"
        )
    if float(laser["preview_acceleration_mm_s2"]) <= 0:
        raise ConfigError("laser.preview_acceleration_mm_s2 must be positive")
    if float(laser["preview_command_delay_ms"]) < 0:
        raise ConfigError("laser.preview_command_delay_ms cannot be negative")
    if not 0 < float(vision["workpiece_min_area_ratio"]) < 1:
        raise ConfigError("vision.workpiece_min_area_ratio must be between 0 and 1")
    canny_low = int(vision["workpiece_canny_low"])
    canny_high = int(vision["workpiece_canny_high"])
    if not (0 <= canny_low < canny_high <= 255):
        raise ConfigError("vision Canny thresholds must satisfy 0 <= low < high <= 255")


def load_settings(config_path: str | Path | None = None) -> Settings:
    project_root = Path(__file__).resolve().parents[1]
    source_path = Path(config_path or os.environ.get("LASER_ALIGNER_CONFIG", project_root / "config" / "default.json"))
    source_path = source_path.expanduser().resolve()

    override: Mapping[str, Any] = {}
    if source_path.exists():
        try:
            loaded: Any = strict_json_loads(source_path.read_text(encoding="utf-8"))
        except UnicodeError as exc:
            raise ConfigError(f"Configuration file is not valid UTF-8: {source_path}") from exc
        except (ValueError, RecursionError) as exc:
            raise ConfigError(f"Invalid JSON in {source_path}: {exc}") from exc
        except OSError as exc:
            raise ConfigError(f"Could not read configuration file {source_path}: {exc}") from exc
        if not isinstance(loaded, Mapping):
            raise ConfigError("Configuration root must be a JSON object")
        override = loaded
    elif config_path is not None or os.environ.get("LASER_ALIGNER_CONFIG"):
        raise ConfigError(f"Configuration file does not exist: {source_path}")

    _validate_override_keys(override, DEFAULT_CONFIG)
    raw = _deep_merge(DEFAULT_CONFIG, override)
    if not source_path.exists():
        raw["app"]["data_dir"] = str(_default_user_data_dir())
    try:
        _validate(raw)
    except ConfigError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ConfigError(f"Configuration has an invalid structure or value: {exc}") from exc
    root = source_path.parent if source_path.exists() else project_root

    area_raw = raw["machine"]["work_area"]
    photo = raw["machine"]["photo_position"]

    settings = Settings(
        source_path=source_path,
        project_root=project_root,
        app=AppSettings(
            host=validate_http_bind_host(raw["app"]["host"]),
            port=validate_http_port(raw["app"]["port"]),
            data_dir=_path(raw["app"]["data_dir"], root),
            simulation=_require_boolean(raw["app"]["simulation"], "app.simulation"),
            open_browser=_require_boolean(raw["app"]["open_browser"], "app.open_browser"),
            allow_remote_control=_require_boolean(
                raw["app"]["allow_remote_control"],
                "app.allow_remote_control",
            ),
            max_request_bytes=_require_integer(
                raw["app"]["max_request_bytes"],
                "app.max_request_bytes",
            ),
        ),
        camera=CameraSettings(
            device=str(raw["camera"]["device"]),
            width=int(raw["camera"]["width"]),
            height=int(raw["camera"]["height"]),
            fps=int(raw["camera"]["fps"]),
            fourcc=str(raw["camera"]["fourcc"]),
            autostart=_require_boolean(raw["camera"]["autostart"], "camera.autostart"),
            jpeg_quality=int(raw["camera"]["jpeg_quality"]),
            warmup_frames=int(raw["camera"]["warmup_frames"]),
            view_rotation_degrees=int(raw["camera"]["view_rotation_degrees"]),
            controls=dict(raw["camera"].get("controls", {})),
            precision_capture=PrecisionCaptureSettings(
                settle_seconds=float(raw["camera"]["precision_capture"]["settle_seconds"]),
                discard_frames=int(raw["camera"]["precision_capture"]["discard_frames"]),
                sample_frames=int(raw["camera"]["precision_capture"]["sample_frames"]),
                timeout_seconds=float(raw["camera"]["precision_capture"]["timeout_seconds"]),
                minimum_valid_frames=int(raw["camera"]["precision_capture"]["minimum_valid_frames"]),
                mad_multiplier=float(raw["camera"]["precision_capture"]["mad_multiplier"]),
                outlier_floor_px=float(raw["camera"]["precision_capture"]["outlier_floor_px"]),
                max_jitter_rms_px=float(raw["camera"]["precision_capture"]["max_jitter_rms_px"]),
                coordinate_strategy=str(raw["camera"]["precision_capture"]["coordinate_strategy"]),
                consensus_frames=int(raw["camera"]["precision_capture"]["consensus_frames"]),
            ),
        ),
        machine=MachineSettings(
            backend=str(raw["machine"]["backend"]),
            protocol=str(raw["machine"]["protocol"]),
            port=str(raw["machine"]["port"]),
            baudrate=int(raw["machine"]["baudrate"]),
            read_timeout=float(raw["machine"]["read_timeout"]),
            work_area=WorkArea(
                x_min=float(area_raw["x_min"]),
                x_max=float(area_raw["x_max"]),
                y_min=float(area_raw["y_min"]),
                y_max=float(area_raw["y_max"]),
            ),
            photo_x=float(photo["x"]),
            photo_y=float(photo["y"]),
            photo_z=None if photo.get("z") is None else float(photo["z"]),
            home_before_photo=_require_boolean(
                raw["machine"]["home_before_photo"],
                "machine.home_before_photo",
            ),
            home_and_release_after_powered_job=_require_boolean(
                raw["machine"]["home_and_release_after_powered_job"],
                "machine.home_and_release_after_powered_job",
            ),
            grbl_step_idle_delay_ms=int(raw["machine"]["grbl_step_idle_delay_ms"]),
            allow_motion=_require_boolean(
                raw["machine"]["allow_motion"],
                "machine.allow_motion",
            ),
            controller_startup_delay=float(raw["machine"]["controller_startup_delay"]),
            max_travel_feed_mm_min=float(raw["machine"]["max_travel_feed_mm_min"]),
            max_work_feed_mm_min=float(raw["machine"]["max_work_feed_mm_min"]),
        ),
        calibration=CalibrationSettings(
            lens=LensCalibrationSettings(
                columns=int(raw["calibration"]["lens"]["columns"]),
                rows=int(raw["calibration"]["lens"]["rows"]),
                square_size_mm=float(raw["calibration"]["lens"]["square_size_mm"]),
                minimum_images=int(raw["calibration"]["lens"]["minimum_images"]),
            ),
            bed=BedCalibrationSettings(
                pixels_per_mm=float(raw["calibration"]["bed"]["pixels_per_mm"]),
                ransac_threshold_mm=float(raw["calibration"]["bed"]["ransac_threshold_mm"]),
                minimum_points=int(raw["calibration"]["bed"]["minimum_points"]),
            ),
        ),
        laser=LaserSettings(
            power_mode=str(raw["laser"]["power_mode"]).upper(),
            power_max=int(raw["laser"]["power_max"]),
            default_power=int(raw["laser"]["default_power"]),
            frame_power=int(raw["laser"]["frame_power"]),
            travel_feed_mm_min=float(raw["laser"]["travel_feed_mm_min"]),
            engrave_feed_mm_min=float(raw["laser"]["engrave_feed_mm_min"]),
            curve_tolerance_mm=float(raw["laser"]["curve_tolerance_mm"]),
            boundary_margin_mm=float(raw["laser"]["boundary_margin_mm"]),
            guarded_output_polygon_mm=(
                None
                if raw["laser"].get("guarded_output_polygon_mm") is None
                else tuple(
                    (float(point[0]), float(point[1]))
                    for point in raw["laser"]["guarded_output_polygon_mm"]
                )
            ),
            spot_offset_x_mm=float(raw["laser"]["spot_offset_x_mm"]),
            spot_offset_y_mm=float(raw["laser"]["spot_offset_y_mm"]),
            arm_timeout_seconds=int(raw["laser"]["arm_timeout_seconds"]),
            allow_low_power_frame=_require_boolean(
                raw["laser"]["allow_low_power_frame"],
                "laser.allow_low_power_frame",
            ),
            return_to_photo_position=_require_boolean(
                raw["laser"]["return_to_photo_position"],
                "laser.return_to_photo_position",
            ),
            preview_acceleration_mm_s2=float(raw["laser"]["preview_acceleration_mm_s2"]),
            preview_command_delay_ms=float(raw["laser"]["preview_command_delay_ms"]),
        ),
        vision=VisionSettings(
            workpiece_min_area_ratio=float(raw["vision"]["workpiece_min_area_ratio"]),
            workpiece_canny_low=int(raw["vision"]["workpiece_canny_low"]),
            workpiece_canny_high=int(raw["vision"]["workpiece_canny_high"]),
        ),
    )
    settings.ensure_directories()
    return settings
