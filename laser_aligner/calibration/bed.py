from __future__ import annotations

import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import BedCalibrationSettings, WorkArea
from ..errors import CalibrationError
from ..storage import atomic_write_json, read_json

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class BedPoint:
    image_x: float
    image_y: float
    machine_x: float
    machine_y: float
    label: str = ""


@dataclass(slots=True)
class BedCalibration:
    image_to_machine: np.ndarray
    machine_to_image: np.ndarray
    image_width: int
    image_height: int
    rms_error_mm: float
    max_error_mm: float
    inlier_count: int
    point_count: int
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_to_machine": self.image_to_machine.tolist(),
            "machine_to_image": self.machine_to_image.tolist(),
            "image_width": self.image_width,
            "image_height": self.image_height,
            "rms_error_mm": self.rms_error_mm,
            "max_error_mm": self.max_error_mm,
            "inlier_count": self.inlier_count,
            "point_count": self.point_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "BedCalibration":
        return cls(
            image_to_machine=np.asarray(raw["image_to_machine"], dtype=np.float64),
            machine_to_image=np.asarray(raw["machine_to_image"], dtype=np.float64),
            image_width=int(raw["image_width"]),
            image_height=int(raw["image_height"]),
            rms_error_mm=float(raw["rms_error_mm"]),
            max_error_mm=float(raw["max_error_mm"]),
            inlier_count=int(raw["inlier_count"]),
            point_count=int(raw["point_count"]),
            created_at=float(raw.get("created_at", 0.0)),
        )


class BedMapper:
    def __init__(self, data_dir: Path, settings: BedCalibrationSettings, work_area: WorkArea):
        self.data_dir = data_dir
        self.settings = settings
        self.work_area = work_area
        self.points_path = data_dir / "bed_points.json"
        self.model_path = data_dir / "bed_calibration.json"
        self._points = self._load_points()
        self._calibration = self._load_calibration()

    @property
    def points(self) -> list[BedPoint]:
        return list(self._points)

    @property
    def calibration(self) -> BedCalibration | None:
        return self._calibration

    def _load_points(self) -> list[BedPoint]:
        raw = read_json(self.points_path, [])
        points: list[BedPoint] = []
        if not isinstance(raw, list):
            return points
        for item in raw:
            try:
                points.append(BedPoint(**item))
            except (TypeError, ValueError):
                LOGGER.warning("Skipping invalid bed calibration point: %r", item)
        return points

    def _save_points(self) -> None:
        atomic_write_json(self.points_path, [asdict(point) for point in self._points])

    def _load_calibration(self) -> BedCalibration | None:
        raw = read_json(self.model_path)
        if not raw:
            return None
        try:
            return BedCalibration.from_dict(raw)
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("Ignoring invalid bed calibration file: %s", exc)
            return None

    def add_point(self, point: BedPoint) -> int:
        values = (point.image_x, point.image_y, point.machine_x, point.machine_y)
        if not all(np.isfinite(value) for value in values):
            raise CalibrationError("Calibration coordinates must be finite numbers")
        self._points.append(point)
        self._save_points()
        return len(self._points) - 1

    def replace_points(self, points: list[BedPoint]) -> None:
        self._points = list(points)
        self._save_points()

    def delete_point(self, index: int) -> None:
        if index < 0 or index >= len(self._points):
            raise CalibrationError("Calibration point index is out of range")
        del self._points[index]
        self._save_points()

    def clear(self) -> None:
        self._points.clear()
        self._save_points()
        self.model_path.unlink(missing_ok=True)
        self._calibration = None

    def solve(self, image_width: int, image_height: int) -> BedCalibration:
        if len(self._points) < self.settings.minimum_points:
            raise CalibrationError(
                f"Need at least {self.settings.minimum_points} point pairs; have {len(self._points)}"
            )
        image_points = np.asarray(
            [[point.image_x, point.image_y] for point in self._points], dtype=np.float64
        )
        machine_points = np.asarray(
            [[point.machine_x, point.machine_y] for point in self._points], dtype=np.float64
        )
        if len(self._points) == 4:
            homography, mask = cv2.findHomography(image_points, machine_points, method=0)
        else:
            homography, mask = cv2.findHomography(
                image_points,
                machine_points,
                method=cv2.RANSAC,
                ransacReprojThreshold=self.settings.ransac_threshold_mm,
                maxIters=5000,
                confidence=0.999,
            )
        if homography is None:
            raise CalibrationError("OpenCV could not solve the bed homography")
        determinant = float(np.linalg.det(homography))
        if abs(determinant) < 1e-12:
            raise CalibrationError("Solved homography is singular")
        inverse = np.linalg.inv(homography)
        predicted = cv2.perspectiveTransform(
            image_points.astype(np.float32).reshape(-1, 1, 2), homography
        ).reshape(-1, 2)
        errors = np.linalg.norm(predicted - machine_points, axis=1)
        inlier_mask = np.ones(len(self._points), dtype=bool) if mask is None else mask.reshape(-1).astype(bool)
        inlier_errors = errors[inlier_mask]
        rms = float(np.sqrt(np.mean(np.square(inlier_errors)))) if len(inlier_errors) else float("inf")
        maximum = float(np.max(inlier_errors)) if len(inlier_errors) else float("inf")

        calibration = BedCalibration(
            image_to_machine=homography,
            machine_to_image=inverse,
            image_width=int(image_width),
            image_height=int(image_height),
            rms_error_mm=rms,
            max_error_mm=maximum,
            inlier_count=int(np.sum(inlier_mask)),
            point_count=len(self._points),
            created_at=time.time(),
        )
        payload = calibration.to_dict()
        payload["points"] = [asdict(point) for point in self._points]
        payload["point_errors_mm"] = errors.tolist()
        payload["inliers"] = inlier_mask.tolist()
        atomic_write_json(self.model_path, payload)
        self._calibration = calibration
        return calibration

    def _require(self) -> BedCalibration:
        if self._calibration is None:
            raise CalibrationError("Bed calibration has not been solved")
        return self._calibration

    def image_to_mm(self, image_x: float, image_y: float) -> tuple[float, float]:
        calibration = self._require()
        point = np.asarray([[[image_x, image_y]]], dtype=np.float64)
        result = cv2.perspectiveTransform(point, calibration.image_to_machine)[0, 0]
        return float(result[0]), float(result[1])

    def mm_to_image(self, machine_x: float, machine_y: float) -> tuple[float, float]:
        calibration = self._require()
        point = np.asarray([[[machine_x, machine_y]]], dtype=np.float64)
        result = cv2.perspectiveTransform(point, calibration.machine_to_image)[0, 0]
        return float(result[0]), float(result[1])

    def image_to_canvas_matrix(self, pixels_per_mm: float | None = None) -> np.ndarray:
        calibration = self._require()
        ppm = float(pixels_per_mm or self.settings.pixels_per_mm)
        machine_to_canvas = np.array(
            [
                [ppm, 0.0, -self.work_area.x_min * ppm],
                [0.0, -ppm, self.work_area.y_max * ppm],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
        return machine_to_canvas @ calibration.image_to_machine

    def rectify(self, image: np.ndarray, pixels_per_mm: float | None = None) -> np.ndarray:
        calibration = self._require()
        height, width = image.shape[:2]
        if (width, height) != (calibration.image_width, calibration.image_height):
            raise CalibrationError(
                "Current camera resolution does not match the bed calibration "
                f"({width}x{height} vs {calibration.image_width}x{calibration.image_height})"
            )
        ppm = float(pixels_per_mm or self.settings.pixels_per_mm)
        output_width = max(1, int(round(self.work_area.width * ppm)))
        output_height = max(1, int(round(self.work_area.height * ppm)))
        matrix = self.image_to_canvas_matrix(ppm)
        return cv2.warpPerspective(
            image,
            matrix,
            (output_width, output_height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(35, 35, 35),
        )

    def status(self) -> dict[str, Any]:
        return {
            "calibrated": self._calibration is not None,
            "calibration": None if self._calibration is None else self._calibration.to_dict(),
            "points": [asdict(point) for point in self._points],
            "minimum_points": self.settings.minimum_points,
            "pixels_per_mm": self.settings.pixels_per_mm,
        }
