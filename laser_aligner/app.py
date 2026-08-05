from __future__ import annotations

import logging
import re
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import __version__
from .calibration.bed import BedMapper, BedPoint
from .calibration.lens import LensCalibrator
from .camera.service import CameraService, SyntheticCameraService, list_video_devices
from .config import Settings
from .errors import CalibrationError
from .gcode.generator import (
    DesignPlacement,
    ToolpathOptions,
    generate_frame_gcode,
    generate_vector_gcode,
)
from .geometry.svg import parse_svg
from .machine.service import MachineService, list_serial_ports
from .vision.fiducials import detect_aruco_markers
from .vision.workpiece import detect_workpiece

LOGGER = logging.getLogger(__name__)
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class AppContext:
    def __init__(self, settings: Settings, hardware_enabled: bool = False):
        self.settings = settings
        settings.app.data_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("captures", "calibration", "generated", "logs"):
            (settings.app.data_dir / directory).mkdir(parents=True, exist_ok=True)
        if settings.app.simulation:
            self.camera: CameraService = SyntheticCameraService(settings.camera, settings.machine.work_area)
        else:
            self.camera = CameraService(settings.camera)
        self.lens = LensCalibrator(settings.app.data_dir, settings.calibration.lens)
        self.bed = BedMapper(settings.app.data_dir, settings.calibration.bed, settings.machine.work_area)
        self.machine = MachineService(settings.machine, settings.laser, hardware_enabled=hardware_enabled)
        self.bed_reference_path = settings.app.data_dir / "bed_reference.jpg"
        self.workspace_path = settings.app.data_dir / "captures" / "workspace.jpg"
        self._camera_start_error: str | None = None

    def start(self) -> None:
        if self.settings.camera.autostart:
            try:
                self.camera.start()
                if isinstance(self.camera, SyntheticCameraService) and self.bed.calibration is None:
                    self.capture_bed_reference()
                    points = [
                        BedPoint(image_x=u, image_y=v, machine_x=x, machine_y=y, label=label)
                        for u, v, x, y, label in self.camera.calibration_correspondences()
                    ]
                    self.bed.replace_points(points)
                    image = self.bed_reference()
                    self.bed.solve(image.shape[1], image.shape[0])
            except Exception as exc:
                self._camera_start_error = str(exc)
                LOGGER.error("Camera did not start: %s", exc)
        if self.settings.machine.backend == "simulator":
            try:
                self.machine.connect()
            except Exception as exc:
                LOGGER.error("Simulator did not connect: %s", exc)

    def stop(self) -> None:
        try:
            self.machine.disconnect()
        finally:
            self.camera.stop()

    def camera_frame(self, undistort: bool = True) -> np.ndarray:
        frame = self.camera.snapshot()
        if undistort and self.lens.model is not None:
            frame = self.lens.model.undistort(frame)
        return frame

    @staticmethod
    def encode_jpeg(image: np.ndarray, quality: int = 92) -> bytes:
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        if not ok:
            raise RuntimeError("Could not encode image")
        return encoded.tobytes()

    def save_capture(self, prefix: str = "capture", undistort: bool = True) -> Path:
        image = self.camera_frame(undistort=undistort)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        safe_prefix = _SAFE_NAME_RE.sub("-", prefix).strip("-._")[:60] or "capture"
        path = self.settings.app.data_dir / "captures" / f"{safe_prefix}-{timestamp}.jpg"
        if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 96]):
            raise RuntimeError(f"Could not save capture to {path}")
        return path

    def capture_bed_reference(self) -> dict[str, Any]:
        image = self.camera_frame(undistort=True)
        if not cv2.imwrite(str(self.bed_reference_path), image, [cv2.IMWRITE_JPEG_QUALITY, 98]):
            raise RuntimeError("Could not save bed reference image")
        return {"width": int(image.shape[1]), "height": int(image.shape[0]), "path": self.bed_reference_path.name}

    def bed_reference(self) -> np.ndarray:
        if self.bed_reference_path.exists():
            image = cv2.imread(str(self.bed_reference_path), cv2.IMREAD_COLOR)
            if image is not None:
                return image
        return self.camera_frame(undistort=True)

    def rectified_frame(self, refresh: bool = True) -> np.ndarray:
        if not refresh and self.workspace_path.exists():
            image = cv2.imread(str(self.workspace_path), cv2.IMREAD_COLOR)
            if image is not None:
                return image
        image = self.camera_frame(undistort=True)
        rectified = self.bed.rectify(image)
        self.workspace_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(self.workspace_path), rectified, [cv2.IMWRITE_JPEG_QUALITY, 96])
        return rectified

    def synthetic_scene(self, scene: str) -> None:
        if not isinstance(self.camera, SyntheticCameraService):
            raise ValueError("Synthetic scenes are available only in simulation mode")
        self.camera.set_scene(scene)

    def add_bed_point(self, payload: dict[str, Any]) -> int:
        point = BedPoint(
            image_x=float(payload["image_x"]),
            image_y=float(payload["image_y"]),
            machine_x=float(payload["machine_x"]),
            machine_y=float(payload["machine_y"]),
            label=str(payload.get("label", ""))[:80],
        )
        return self.bed.add_point(point)

    def solve_bed(self) -> dict[str, Any]:
        image = self.bed_reference()
        calibration = self.bed.solve(image.shape[1], image.shape[0])
        return calibration.to_dict()

    def detect_workpiece(self) -> dict[str, Any]:
        image = self.rectified_frame(refresh=True)
        detection = detect_workpiece(
            image,
            min_area_ratio=self.settings.vision.workpiece_min_area_ratio,
            canny_low=self.settings.vision.workpiece_canny_low,
            canny_high=self.settings.vision.workpiece_canny_high,
        )
        if detection is None:
            return {"detected": False}
        payload = detection.to_dict()
        ppm = self.settings.calibration.bed.pixels_per_mm
        area = self.settings.machine.work_area

        def to_mm(point: list[float]) -> list[float]:
            return [area.x_min + point[0] / ppm, area.y_max - point[1] / ppm]

        payload["polygon_mm"] = [to_mm(point) for point in detection.polygon_px]
        payload["center_mm"] = to_mm(detection.center_px)
        payload["width_mm"] = detection.width_px / ppm
        payload["height_mm"] = detection.height_px / ppm
        payload["detected"] = True
        return payload

    def detect_fiducials(self) -> dict[str, Any]:
        image = self.bed_reference()
        return {"markers": detect_aruco_markers(image)}

    def analyze_svg(self, svg_text: str) -> dict[str, Any]:
        geometry = parse_svg(svg_text)
        return {
            "bounds": list(geometry.bounds),
            "intrinsic_width_mm": geometry.intrinsic_width_mm,
            "intrinsic_height_mm": geometry.intrinsic_height_mm,
            "path_count": len(geometry.polylines),
            "point_count": geometry.point_count,
            "warnings": geometry.warnings,
        }

    def _placement(self, payload: dict[str, Any]) -> DesignPlacement:
        return DesignPlacement(
            center_x_mm=float(payload["center_x_mm"]),
            center_y_mm=float(payload["center_y_mm"]),
            width_mm=float(payload["width_mm"]),
            height_mm=float(payload["height_mm"]),
            rotation_deg=float(payload.get("rotation_deg", 0.0)),
            mirror_x=bool(payload.get("mirror_x", False)),
            mirror_y=bool(payload.get("mirror_y", False)),
        )

    def _toolpath_options(self, payload: dict[str, Any]) -> ToolpathOptions:
        laser = self.settings.laser
        return ToolpathOptions(
            power_mode=str(payload.get("power_mode", laser.power_mode)).upper(),
            power=int(payload.get("power", laser.default_power)),
            power_max=laser.power_max,
            travel_feed_mm_min=float(payload.get("travel_feed_mm_min", laser.travel_feed_mm_min)),
            engrave_feed_mm_min=float(payload.get("engrave_feed_mm_min", laser.engrave_feed_mm_min)),
            boundary_margin_mm=laser.boundary_margin_mm,
            optimize_order=bool(payload.get("optimize_order", True)),
            include_return_move=laser.return_to_photo_position,
            return_x_mm=self.settings.machine.photo_x,
            return_y_mm=self.settings.machine.photo_y,
        )

    def generate_gcode(self, payload: dict[str, Any]) -> dict[str, Any]:
        svg_text = str(payload["svg"])
        geometry = parse_svg(svg_text)
        placement = self._placement(dict(payload["placement"]))
        options = self._toolpath_options(dict(payload.get("toolpath", {})))
        name = str(payload.get("name", "design.svg"))
        program = generate_vector_gcode(
            geometry,
            placement,
            options,
            self.settings.machine.work_area,
            design_name=name,
        )
        safe_base = _SAFE_NAME_RE.sub("-", Path(name).stem).strip("-.") or "design"
        filename = f"{safe_base}-{time.strftime('%Y%m%d-%H%M%S')}.gcode"
        path = self.settings.app.data_dir / "generated" / filename
        path.write_text(program.text, encoding="utf-8")
        return {
            "filename": filename,
            "download_url": f"/api/generated/{filename}",
            "gcode": program.text,
            "metadata": program.metadata(),
        }

    def generate_frame(self, payload: dict[str, Any]) -> dict[str, Any]:
        bounds = tuple(float(value) for value in payload["bounds_mm"])
        if len(bounds) != 4:
            raise ValueError("bounds_mm must contain four numbers")
        requested_laser = bool(payload.get("laser_enabled", False))
        if requested_laser and not self.settings.laser.allow_low_power_frame:
            raise CalibrationError(
                "Low-power laser framing is disabled. Set laser.allow_low_power_frame only after validating the controller power scale."
            )
        options = self._toolpath_options(
            {
                "power": self.settings.laser.frame_power,
                "travel_feed_mm_min": payload.get("feed_mm_min", self.settings.laser.travel_feed_mm_min),
            }
        )
        program = generate_frame_gcode(
            bounds, options, self.settings.machine.work_area, laser_enabled=requested_laser
        )
        filename = f"frame-{time.strftime('%Y%m%d-%H%M%S')}.gcode"
        path = self.settings.app.data_dir / "generated" / filename
        path.write_text(program.text, encoding="utf-8")
        return {
            "filename": filename,
            "download_url": f"/api/generated/{filename}",
            "gcode": program.text,
            "metadata": program.metadata(),
        }

    def status(self) -> dict[str, Any]:
        camera_status = asdict(self.camera.status())
        if self._camera_start_error and not camera_status["connected"]:
            camera_status["last_error"] = self._camera_start_error
        return {
            "version": __version__,
            "settings": self.settings.public_dict(),
            "camera": camera_status,
            "lens": self.lens.status(),
            "bed": self.bed.status(),
            "machine": self.machine.status(),
            "devices": {
                "cameras": list_video_devices(),
                "serial_ports": list_serial_ports(),
            },
        }
