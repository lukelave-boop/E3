from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import LensCalibrationSettings
from ..errors import CalibrationError
from ..storage import atomic_write_json, read_json

LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class LensModel:
    camera_matrix: np.ndarray
    distortion: np.ndarray
    image_width: int
    image_height: int
    rms_error: float
    mean_reprojection_error: float
    images_used: int
    created_at: float

    @property
    def image_size(self) -> tuple[int, int]:
        return self.image_width, self.image_height

    def to_dict(self) -> dict[str, Any]:
        return {
            "camera_matrix": self.camera_matrix.tolist(),
            "distortion": self.distortion.reshape(-1).tolist(),
            "image_width": self.image_width,
            "image_height": self.image_height,
            "rms_error": self.rms_error,
            "mean_reprojection_error": self.mean_reprojection_error,
            "images_used": self.images_used,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "LensModel":
        return cls(
            camera_matrix=np.asarray(raw["camera_matrix"], dtype=np.float64),
            distortion=np.asarray(raw["distortion"], dtype=np.float64).reshape(1, -1),
            image_width=int(raw["image_width"]),
            image_height=int(raw["image_height"]),
            rms_error=float(raw["rms_error"]),
            mean_reprojection_error=float(raw["mean_reprojection_error"]),
            images_used=int(raw["images_used"]),
            created_at=float(raw.get("created_at", 0.0)),
        )

    def undistort(self, image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        if (width, height) != self.image_size:
            scale_x = width / self.image_width
            scale_y = height / self.image_height
            camera_matrix = self.camera_matrix.copy()
            camera_matrix[0, 0] *= scale_x
            camera_matrix[0, 2] *= scale_x
            camera_matrix[1, 1] *= scale_y
            camera_matrix[1, 2] *= scale_y
        else:
            camera_matrix = self.camera_matrix
        return cv2.undistort(image, camera_matrix, self.distortion)


class LensCalibrator:
    def __init__(self, data_dir: Path, settings: LensCalibrationSettings):
        self.data_dir = data_dir
        self.settings = settings
        self.image_dir = data_dir / "lens_images"
        self.model_path = data_dir / "lens_calibration.json"
        self.image_index_path = data_dir / "lens_image_index.json"
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self._model = self.load_model()

    @property
    def model(self) -> LensModel | None:
        return self._model

    def load_model(self) -> LensModel | None:
        raw = read_json(self.model_path)
        if not raw:
            return None
        try:
            return LensModel.from_dict(raw)
        except (KeyError, TypeError, ValueError) as exc:
            LOGGER.warning("Ignoring invalid lens calibration file: %s", exc)
            return None

    def save_model(self, model: LensModel) -> None:
        atomic_write_json(self.model_path, model.to_dict())
        self._model = model

    def clear(self, delete_images: bool = False) -> None:
        self.model_path.unlink(missing_ok=True)
        self._model = None
        if delete_images:
            for path in self.image_dir.glob("*.jpg"):
                path.unlink(missing_ok=True)
            self.image_index_path.unlink(missing_ok=True)

    def _pattern_signature(self) -> dict[str, int]:
        return {"columns": self.settings.columns, "rows": self.settings.rows}

    def _read_image_index(self) -> dict[str, Any]:
        raw = read_json(self.image_index_path)
        if not isinstance(raw, dict) or raw.get("pattern") != self._pattern_signature():
            return {"pattern": self._pattern_signature(), "images": {}}
        images = raw.get("images")
        if not isinstance(images, dict):
            images = {}
        return {"pattern": self._pattern_signature(), "images": images}

    def _write_image_index(self, index: dict[str, Any]) -> None:
        atomic_write_json(self.image_index_path, index)

    @staticmethod
    def _stat_signature(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns

    def _cache_image_result(
        self,
        index: dict[str, Any],
        path: Path,
        *,
        found: bool,
        corner_count: int,
    ) -> dict[str, Any]:
        size, mtime_ns = self._stat_signature(path)
        entry = {
            "name": path.name,
            "found": bool(found),
            "corner_count": int(corner_count),
            "size": size,
            "mtime_ns": mtime_ns,
        }
        index["images"][path.name] = entry
        return entry

    def list_images(self) -> list[dict[str, Any]]:
        """Return cached checkerboard results without rescanning every status request.

        Existing or externally modified files are detected once, then recorded in a
        small JSON index. New captures are indexed immediately by ``capture``.
        """
        index = self._read_image_index()
        cached_images = index["images"]
        entries: list[dict[str, Any]] = []
        changed = False
        active_names: set[str] = set()

        for path in sorted(self.image_dir.glob("*.jpg")):
            active_names.add(path.name)
            size, mtime_ns = self._stat_signature(path)
            cached = cached_images.get(path.name)
            if (
                isinstance(cached, dict)
                and cached.get("size") == size
                and cached.get("mtime_ns") == mtime_ns
                and "found" in cached
            ):
                entry = dict(cached)
            else:
                image = cv2.imread(str(path), cv2.IMREAD_COLOR)
                found = False
                corners = None
                if image is not None:
                    found, corners = self.detect_corners(image)
                entry = self._cache_image_result(
                    index,
                    path,
                    found=bool(found),
                    corner_count=0 if corners is None else len(corners),
                )
                changed = True
            entries.append(
                {
                    "name": entry["name"],
                    "found": bool(entry["found"]),
                    "corner_count": int(entry.get("corner_count", 0)),
                    "size": int(entry["size"]),
                }
            )

        stale_names = set(cached_images) - active_names
        if stale_names:
            for name in stale_names:
                cached_images.pop(name, None)
            changed = True
        if changed:
            self._write_image_index(index)
        return entries

    def capture(self, image: np.ndarray) -> dict[str, Any]:
        found, corners = self.detect_corners(image)
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        suffix = int((time.time() % 1) * 1000)
        path = self.image_dir / f"lens-{timestamp}-{suffix:03d}.jpg"
        if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 96]):
            raise CalibrationError(f"Could not save calibration image to {path}")
        corner_count = 0 if corners is None else int(len(corners))
        index = self._read_image_index()
        self._cache_image_result(
            index, path, found=bool(found), corner_count=corner_count
        )
        self._write_image_index(index)
        return {
            "name": path.name,
            "found": bool(found),
            "corner_count": corner_count,
        }

    def detect_corners(self, image: np.ndarray) -> tuple[bool, np.ndarray | None]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        pattern = (self.settings.columns, self.settings.rows)
        corners: np.ndarray | None = None
        found = False
        if hasattr(cv2, "findChessboardCornersSB"):
            found, corners = cv2.findChessboardCornersSB(
                gray,
                pattern,
                flags=cv2.CALIB_CB_EXHAUSTIVE | cv2.CALIB_CB_ACCURACY | cv2.CALIB_CB_NORMALIZE_IMAGE,
            )
        if not found:
            found, corners = cv2.findChessboardCorners(
                gray,
                pattern,
                flags=cv2.CALIB_CB_ADAPTIVE_THRESH | cv2.CALIB_CB_NORMALIZE_IMAGE,
            )
            if found and corners is not None:
                criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 40, 0.001)
                corners = cv2.cornerSubPix(gray, corners, (11, 11), (-1, -1), criteria)
        return bool(found), corners

    def solve(self) -> LensModel:
        object_template = np.zeros((self.settings.rows * self.settings.columns, 3), np.float32)
        object_template[:, :2] = np.mgrid[
            0 : self.settings.columns, 0 : self.settings.rows
        ].T.reshape(-1, 2)
        object_template *= self.settings.square_size_mm

        object_points: list[np.ndarray] = []
        image_points: list[np.ndarray] = []
        image_size: tuple[int, int] | None = None
        used_names: list[str] = []

        for path in sorted(self.image_dir.glob("*.jpg")):
            image = cv2.imread(str(path), cv2.IMREAD_COLOR)
            if image is None:
                continue
            size = (image.shape[1], image.shape[0])
            if image_size is not None and size != image_size:
                LOGGER.warning("Skipping %s because its resolution differs", path.name)
                continue
            found, corners = self.detect_corners(image)
            if found and corners is not None:
                image_size = size
                object_points.append(object_template.copy())
                image_points.append(corners.astype(np.float32))
                used_names.append(path.name)

        if len(object_points) < self.settings.minimum_images:
            raise CalibrationError(
                f"Only {len(object_points)} usable checkerboard images; "
                f"at least {self.settings.minimum_images} are configured"
            )
        assert image_size is not None

        rms, camera_matrix, distortion, rotations, translations = cv2.calibrateCamera(
            object_points,
            image_points,
            image_size,
            None,
            None,
        )
        total_error = 0.0
        total_points = 0
        for index, object_set in enumerate(object_points):
            projected, _ = cv2.projectPoints(
                object_set,
                rotations[index],
                translations[index],
                camera_matrix,
                distortion,
            )
            error = cv2.norm(image_points[index], projected, cv2.NORM_L2)
            total_error += error * error
            total_points += len(object_set)
        mean_error = float(np.sqrt(total_error / max(total_points, 1)))

        model = LensModel(
            camera_matrix=camera_matrix,
            distortion=distortion,
            image_width=image_size[0],
            image_height=image_size[1],
            rms_error=float(rms),
            mean_reprojection_error=mean_error,
            images_used=len(object_points),
            created_at=time.time(),
        )
        payload = model.to_dict()
        payload["image_files"] = used_names
        atomic_write_json(self.model_path, payload)
        self._model = model
        return model

    def status(self) -> dict[str, Any]:
        images = self.list_images()
        return {
            "calibrated": self._model is not None,
            "model": None if self._model is None else self._model.to_dict(),
            "image_count": len(images),
            "usable_image_count": sum(1 for item in images if item["found"]),
            "images": images,
            "pattern": {
                "columns": self.settings.columns,
                "rows": self.settings.rows,
                "square_size_mm": self.settings.square_size_mm,
                "minimum_images": self.settings.minimum_images,
            },
        }
