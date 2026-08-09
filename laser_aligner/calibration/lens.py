from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import os
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from ..config import LensCalibrationSettings
from ..errors import CalibrationError
from ..imaging import (
    EncodedImagePayload,
    ImageEvidenceChangedError,
    assert_image_payload_current,
    decode_image_payload,
    encode_image,
    image_quality,
    probe_image_dimensions,
    read_encoded_image_payload,
)
from ..storage import atomic_write_bytes, atomic_write_json, read_json

LOGGER = logging.getLogger(__name__)

_MODEL_SCHEMA_VERSION = 2
_SUPPORTED_DISTORTION_LENGTHS = {4, 5, 8, 12, 14}
_POSE_MAJOR_REJECT_DEG = 8.0
_POSE_MAJOR_WARN_DEG = 15.0
_POSE_MINOR_REJECT_DEG = 3.0
_POSE_MINOR_WARN_DEG = 7.0
_COVERAGE_REJECT_RATIO = 0.30
_COVERAGE_WARN_RATIO = 0.50
_EDGE_MARGIN_REJECT_RATIO = 0.25
_EDGE_MARGIN_WARN_RATIO = 0.15
_BOARD_SCALE_WARN_RATIO = 1.20
_SOLVE_OPERATION = "solve lens calibration"
_INDEX_OPERATION = "index lens evidence"
_IMAGE_INDEX_SCHEMA_VERSION = 4
_INDEX_MAX_WIDTH = 640
_INDEX_MAX_HEIGHT = 360
_INDEX_DETECTOR_VERSION = 2
_EVIDENCE_MAX_ENCODED_BYTES = 64 * 1024 * 1024
_EVIDENCE_MAX_TOTAL_ENCODED_BYTES = 256 * 1024 * 1024
_SHARPNESS_METHOD = "variance-of-laplacian-gray-central-80pct"
_QUALITY_STRING_FIELDS = {
    "measurement_source": {"exact-encoded-byte-bounded-preview"},
    "sharpness_method": {_SHARPNESS_METHOD},
}
_QUALITY_NUMERIC_FIELDS = {
    "width",
    "height",
    "sharpness",
    "luminance_mean",
    "luminance_p01",
    "luminance_p99",
    "contrast_span",
    "shadow_clip_percent",
    "highlight_clip_percent",
}
_READY_QUALITY_FIELDS = _QUALITY_NUMERIC_FIELDS | set(_QUALITY_STRING_FIELDS)


def _canonical_model_id(
    camera_matrix: np.ndarray,
    distortion: np.ndarray,
    image_size: tuple[int, int],
) -> str:
    payload = {
        "camera_matrix": np.asarray(camera_matrix, dtype=np.float64).tolist(),
        "distortion": np.asarray(distortion, dtype=np.float64).reshape(-1).tolist(),
        "image_size": [int(image_size[0]), int(image_size[1])],
        "model": "opencv-brown-conrady",
    }
    encoded = json.dumps(
        payload,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    model_id: str = ""
    quality: dict[str, Any] = field(default_factory=dict)
    views: tuple[dict[str, Any], ...] = ()
    _map_cache: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )
    _map_lock: threading.RLock = field(
        default_factory=threading.RLock,
        init=False,
        repr=False,
    )

    def __post_init__(self) -> None:
        self.camera_matrix = np.array(self.camera_matrix, dtype=np.float64, copy=True)
        self.distortion = np.array(self.distortion, dtype=np.float64, copy=True).reshape(1, -1)
        if self.camera_matrix.shape != (3, 3) or not np.isfinite(self.camera_matrix).all():
            raise ValueError("Lens camera_matrix must be a finite 3x3 matrix")
        if (
            self.distortion.size not in _SUPPORTED_DISTORTION_LENGTHS
            or not np.isfinite(self.distortion).all()
        ):
            raise ValueError("Lens distortion coefficients are invalid")
        if type(self.image_width) is not int or type(self.image_height) is not int:
            raise ValueError("Lens calibration image dimensions must be integers")
        if self.image_width <= 0 or self.image_height <= 0:
            raise ValueError("Lens calibration image dimensions must be positive")
        if (
            self.camera_matrix[0, 0] <= 0
            or self.camera_matrix[1, 1] <= 0
            or not np.allclose(
                self.camera_matrix[2],
                np.asarray([0.0, 0.0, 1.0]),
                rtol=0.0,
                atol=1e-9,
            )
            or abs(float(np.linalg.det(self.camera_matrix))) <= 1e-12
        ):
            raise ValueError("Lens camera_matrix is not a valid intrinsic matrix")
        numeric_metadata = (
            self.rms_error,
            self.mean_reprojection_error,
            self.created_at,
        )
        if not all(math.isfinite(float(value)) for value in numeric_metadata):
            raise ValueError("Lens calibration metadata must be finite")
        if self.rms_error < 0 or self.mean_reprojection_error < 0 or self.created_at < 0:
            raise ValueError("Lens calibration errors and timestamp cannot be negative")
        if type(self.images_used) is not int:
            raise ValueError("Lens calibration images_used must be an integer")
        if self.images_used < 1:
            raise ValueError("Lens calibration must record at least one image")
        if not isinstance(self.quality, dict):
            raise ValueError("Lens calibration quality diagnostics must be an object")
        if not isinstance(self.views, tuple) or not all(isinstance(item, dict) for item in self.views):
            raise ValueError("Lens per-view diagnostics must be a tuple of objects")
        try:
            json.dumps(
                {"quality": self.quality, "views": self.views},
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("Lens diagnostics must contain finite JSON values") from exc
        expected_id = _canonical_model_id(
            self.camera_matrix,
            self.distortion,
            self.image_size,
        )
        if self.model_id and self.model_id != expected_id:
            raise ValueError("Lens calibration model_id does not match its parameters")
        self.model_id = expected_id
        self.camera_matrix.setflags(write=False)
        self.distortion.setflags(write=False)
        self.quality = dict(self.quality)
        self.views = tuple(dict(item) for item in self.views)

    @property
    def image_size(self) -> tuple[int, int]:
        return self.image_width, self.image_height

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": _MODEL_SCHEMA_VERSION,
            "model_id": self.model_id,
            "camera_matrix": self.camera_matrix.tolist(),
            "distortion": self.distortion.reshape(-1).tolist(),
            "image_width": self.image_width,
            "image_height": self.image_height,
            "rms_error": self.rms_error,
            "mean_reprojection_error": self.mean_reprojection_error,
            "images_used": self.images_used,
            "created_at": self.created_at,
            "quality": copy.deepcopy(self.quality),
            "views": copy.deepcopy(list(self.views)),
        }
        payload["image_files"] = [
            str(item["name"])
            for item in self.views
            if item.get("accepted") and item.get("name")
        ]
        return payload

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LensModel:
        if not isinstance(raw, dict):
            raise ValueError("Lens calibration must be a JSON object")
        schema_version = raw.get("schema_version", 1)
        if type(schema_version) is not int:
            raise ValueError("Lens calibration schema_version must be an integer")
        if schema_version < 1 or schema_version > _MODEL_SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported lens calibration schema version: {schema_version}"
            )
        raw_views = raw.get("views")
        if raw_views is None:
            raw_views = [
                {"name": str(name), "accepted": True}
                for name in raw.get("image_files", [])
            ]
        if not isinstance(raw_views, list) or not all(isinstance(item, dict) for item in raw_views):
            raise ValueError("Lens per-view diagnostics are invalid")
        for field_name in ("image_width", "image_height", "images_used"):
            if type(raw.get(field_name)) is not int:
                raise ValueError(f"Lens calibration {field_name} must be an integer")
        model = cls(
            camera_matrix=np.asarray(raw["camera_matrix"], dtype=np.float64),
            distortion=np.asarray(raw["distortion"], dtype=np.float64).reshape(1, -1),
            image_width=raw["image_width"],
            image_height=raw["image_height"],
            rms_error=float(raw["rms_error"]),
            mean_reprojection_error=float(raw["mean_reprojection_error"]),
            images_used=raw["images_used"],
            created_at=float(raw.get("created_at", 0.0)),
            model_id=str(raw.get("model_id", "")),
            quality=dict(raw.get("quality") or {}),
            views=tuple(dict(item) for item in raw_views),
        )
        if schema_version >= 2 and not raw.get("model_id"):
            raise ValueError("Lens calibration schema 2 requires model_id")
        return model

    def _camera_matrix_for_size(self, width: int, height: int) -> np.ndarray:
        if width <= 0 or height <= 0 or self.image_width <= 0 or self.image_height <= 0:
            raise CalibrationError("Lens calibration and image dimensions must be positive")
        if (width, height) == self.image_size:
            return self.camera_matrix
        scale_x = width / self.image_width
        scale_y = height / self.image_height
        scale_difference = abs(scale_x - scale_y) / max(scale_x, scale_y)
        if scale_difference > 0.002:
            raise CalibrationError(
                "Current camera aspect ratio does not match the lens calibration "
                f"({width}x{height} vs {self.image_width}x{self.image_height})"
            )
        scale = (scale_x + scale_y) * 0.5
        camera_matrix = self.camera_matrix.copy()
        camera_matrix[0, 0] *= scale
        camera_matrix[0, 2] *= scale_x
        camera_matrix[1, 1] *= scale
        camera_matrix[1, 2] *= scale_y
        return camera_matrix

    def _undistort_maps(self, width: int, height: int) -> tuple[np.ndarray, np.ndarray]:
        key = (int(width), int(height))
        with self._map_lock:
            cached = self._map_cache.get(key)
            if cached is not None:
                return cached
            camera_matrix = self._camera_matrix_for_size(*key)
            maps = cv2.initUndistortRectifyMap(
                camera_matrix,
                self.distortion,
                None,
                camera_matrix,
                key,
                cv2.CV_16SC2,
            )
            if len(self._map_cache) >= 3:
                self._map_cache.pop(next(iter(self._map_cache)))
            self._map_cache[key] = maps
            return maps

    def undistort(self, image: np.ndarray) -> np.ndarray:
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise CalibrationError("Cannot undistort an empty camera image")
        height, width = image.shape[:2]
        map_x, map_y = self._undistort_maps(width, height)
        return cv2.remap(
            image,
            map_x,
            map_y,
            interpolation=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
        )

    def distort_points(self, undistorted_pixels: np.ndarray) -> np.ndarray:
        """Project corrected pixel coordinates back into the raw camera frame."""

        points = np.asarray(undistorted_pixels, dtype=np.float64)
        if (
            points.ndim < 2
            or points.shape[-1] != 2
            or points.size == 0
            or not np.isfinite(points).all()
        ):
            raise CalibrationError("Undistorted pixel coordinates must be finite (..., 2) points")
        original_shape = points.shape
        flat = points.reshape(-1, 2)
        homogeneous = np.column_stack((flat, np.ones(len(flat), dtype=np.float64)))
        normalized = homogeneous @ np.linalg.inv(self.camera_matrix).T
        normalized_xy = normalized[:, :2] / normalized[:, 2:3]
        object_points = np.column_stack(
            (normalized_xy, np.ones(len(normalized_xy), dtype=np.float64))
        )
        projected, _ = cv2.projectPoints(
            object_points,
            np.zeros(3, dtype=np.float64),
            np.zeros(3, dtype=np.float64),
            self.camera_matrix,
            self.distortion,
        )
        result = projected.reshape(original_shape)
        if not np.isfinite(result).all():
            raise CalibrationError("Distorted pixel projection produced non-finite coordinates")
        return result


@dataclass(frozen=True, slots=True)
class _LensObservation:
    name: str
    image_size: tuple[int, int]
    image_points: np.ndarray


@dataclass(slots=True)
class _LensFit:
    rms: float
    opencv_rms: float
    camera_matrix: np.ndarray
    distortion: np.ndarray
    rotations: tuple[np.ndarray, ...]
    translations: tuple[np.ndarray, ...]
    residuals: tuple[np.ndarray, ...]


class LensCalibrator:
    """Own lens model and checkerboard evidence as one synchronized state domain.

    Filesystem/index commits hold ``_state_lock``. Expensive corner extraction
    and camera fitting run without that lock while ``_active_operation`` reserves
    the evidence set, allowing status reads but rejecting competing mutations.
    """

    def __init__(self, data_dir: Path, settings: LensCalibrationSettings):
        self.data_dir = data_dir
        self.settings = settings
        self.image_dir = data_dir / "lens_images"
        self.model_path = data_dir / "lens_calibration.json"
        self.image_index_path = data_dir / "lens_image_index.json"
        self._state_lock = threading.RLock()
        self._active_operation: str | None = None
        self._operation_owner: int | None = None
        self._pending_solve_quality: dict[str, Any] | None = None
        self._evidence_revision = 0
        self._index_total = 0
        self._index_completed = 0
        self._index_failed = 0
        self._index_current: str | None = None
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self._last_solve_quality: dict[str, Any] | None = None
        self._model = self.load_model()

    @property
    def model(self) -> LensModel | None:
        with self._state_lock:
            return self._model

    def _begin_operation_locked(self, operation: str) -> None:
        if self._active_operation is not None:
            raise CalibrationError(
                f"Cannot {operation} while lens-calibration "
                f"{self._active_operation} is in progress"
            )
        self._active_operation = operation
        self._operation_owner = threading.get_ident()
        if operation == _SOLVE_OPERATION:
            self._pending_solve_quality = None
        if operation == _INDEX_OPERATION:
            self._index_total = 0
            self._index_completed = 0
            self._index_failed = 0
            self._index_current = None

    def _finish_operation_locked(self, operation: str) -> None:
        if (
            self._active_operation != operation
            or self._operation_owner != threading.get_ident()
        ):
            return
        self._active_operation = None
        self._operation_owner = None
        if operation == _SOLVE_OPERATION:
            self._pending_solve_quality = None
        if operation == _INDEX_OPERATION:
            self._index_current = None

    def _record_solve_quality(self, quality: dict[str, Any]) -> None:
        snapshot = copy.deepcopy(quality)
        with self._state_lock:
            if (
                self._active_operation == _SOLVE_OPERATION
                and self._operation_owner == threading.get_ident()
            ):
                self._pending_solve_quality = snapshot
            else:
                self._last_solve_quality = snapshot

    def load_model(self) -> LensModel | None:
        with self._state_lock:
            raw = read_json(self.model_path)
            if not raw:
                return None
            try:
                return LensModel.from_dict(raw)
            except (KeyError, TypeError, ValueError) as exc:
                LOGGER.warning("Ignoring invalid lens calibration file: %s", exc)
                return None

    def save_model(self, model: LensModel) -> None:
        with self._state_lock:
            self._begin_operation_locked("replace the lens model")
            try:
                atomic_write_json(self.model_path, model.to_dict())
                self._model = model
            finally:
                self._finish_operation_locked("replace the lens model")

    def clear(self, delete_images: bool = False) -> None:
        with self._state_lock:
            self._begin_operation_locked("clear the lens model")
            try:
                self.model_path.unlink(missing_ok=True)
                self._model = None
                if delete_images:
                    self._clear_captures_locked()
            finally:
                self._finish_operation_locked("clear the lens model")

    def clear_captures(self) -> int:
        with self._state_lock:
            self._begin_operation_locked("clear lens captures")
            try:
                return self._clear_captures_locked()
            finally:
                self._finish_operation_locked("clear lens captures")

    def _clear_captures_locked(self) -> int:
        paths = [
            path
            for path in self.image_dir.iterdir()
            if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg"}
        ]
        for path in paths:
            path.unlink(missing_ok=True)
        index_existed = self.image_index_path.exists()
        self.image_index_path.unlink(missing_ok=True)
        if paths or index_existed:
            self._evidence_revision += 1
        return len(paths)

    def delete_capture(self, name: str) -> bool:
        safe_name = str(name).strip()
        if Path(safe_name).name != safe_name or Path(safe_name).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise CalibrationError("Invalid lens-capture filename")
        with self._state_lock:
            self._begin_operation_locked("delete a lens capture")
            try:
                path = self.image_dir / safe_name
                if not path.exists() or not path.is_file():
                    return False
                path.unlink()
                index = self._read_image_index()
                index["images"].pop(safe_name, None)
                self._write_image_index(index)
                self._evidence_revision += 1
                return True
            finally:
                self._finish_operation_locked("delete a lens capture")

    def _image_paths(self) -> list[Path]:
        """Prefer a lossless capture when a legacy JPEG shares its stem."""

        with self._state_lock:
            return self._image_paths_locked()

    def _image_paths_locked(self) -> list[Path]:
        selected: dict[str, Path] = {}
        format_rank = {".jpg": 0, ".jpeg": 1, ".png": 2}
        candidates = sorted(
            (
                path
                for path in self.image_dir.iterdir()
                if path.is_file() and path.suffix.lower() in format_rank
            ),
            key=lambda path: (path.name.casefold(), path.name),
        )
        for path in candidates:
            stem_key = path.stem.casefold()
            current = selected.get(stem_key)
            if current is None or format_rank[path.suffix.lower()] > format_rank[
                current.suffix.lower()
            ]:
                selected[stem_key] = path
        return sorted(
            selected.values(),
            key=lambda path: (path.name.casefold(), path.name),
        )

    def _evidence_signature_locked(self) -> tuple[tuple[str, int, int], ...]:
        signature: list[tuple[str, int, int]] = []
        for path in self._image_paths_locked():
            try:
                size, mtime_ns = self._stat_signature(path)
            except FileNotFoundError:
                return ()
            signature.append((path.name, size, mtime_ns))
        return tuple(signature)

    def _load_evidence_payloads(
        self,
    ) -> tuple[
        dict[str, EncodedImagePayload],
        tuple[tuple[str, int, int, int, int, int, str], ...],
    ]:
        """Read one immutable, bounded byte payload per selected evidence file."""

        paths = self._image_paths()
        payloads: dict[str, EncodedImagePayload] = {}
        signature: list[tuple[str, int, int, int, int, int, str]] = []
        total_bytes = 0
        for path in paths:
            try:
                payload = read_encoded_image_payload(
                    path,
                    max_encoded_bytes=_EVIDENCE_MAX_ENCODED_BYTES,
                    allow_invalid=True,
                )
            except ImageEvidenceChangedError as exc:
                raise CalibrationError(str(exc)) from exc
            except ValueError as exc:
                raise CalibrationError(
                    f"Could not snapshot lens evidence {path.name}: {exc}"
                ) from exc
            total_bytes += len(payload.encoded)
            if total_bytes > _EVIDENCE_MAX_TOTAL_ENCODED_BYTES:
                raise CalibrationError(
                    "Lens evidence exceeds the bounded 256 MiB encoded-byte budget"
                )
            identity = payload.file_identity
            if identity is None:  # pragma: no cover - file reads always carry identity
                raise CalibrationError(f"Lens evidence has no file identity: {path.name}")
            payloads[path.name] = payload
            signature.append(
                (
                    path.name,
                    identity.size,
                    identity.mtime_ns,
                    identity.ctime_ns,
                    identity.device,
                    identity.inode,
                    payload.content_sha256,
                )
            )
        if [path.name for path in self._image_paths()] != list(payloads):
            raise CalibrationError(
                "Checkerboard evidence changed while its encoded bytes were being read"
            )
        return payloads, tuple(signature)

    def _pattern_signature(self) -> dict[str, int]:
        return {"columns": self.settings.columns, "rows": self.settings.rows}

    @staticmethod
    def _index_detector_signature() -> dict[str, int | str]:
        return {
            "method": "bounded-checkerboard-preview",
            "version": _INDEX_DETECTOR_VERSION,
            "max_width": _INDEX_MAX_WIDTH,
            "max_height": _INDEX_MAX_HEIGHT,
        }

    def _empty_image_index(self) -> dict[str, Any]:
        return {
            "schema_version": _IMAGE_INDEX_SCHEMA_VERSION,
            "pattern": self._pattern_signature(),
            "detector": self._index_detector_signature(),
            "images": {},
        }

    def _read_image_index(self) -> dict[str, Any]:
        with self._state_lock:
            raw = read_json(self.image_index_path)
            if not isinstance(raw, dict) or raw.get("pattern") != self._pattern_signature():
                return self._empty_image_index()
            schema_version = raw.get("schema_version")
            if schema_version == _IMAGE_INDEX_SCHEMA_VERSION:
                if raw.get("detector") != self._index_detector_signature():
                    return self._empty_image_index()
            elif schema_version == 3:
                if raw.get("detector") != {
                    **self._index_detector_signature(),
                    "version": 1,
                }:
                    return self._empty_image_index()
            elif schema_version != 2:
                return self._empty_image_index()
            images = raw.get("images")
            if not isinstance(images, dict):
                images = {}
            result = self._empty_image_index()
            result["images"] = images
            result["_source_schema"] = int(schema_version)
            return result

    def _write_image_index(self, index: dict[str, Any]) -> None:
        with self._state_lock:
            payload = self._empty_image_index()
            payload["images"] = copy.deepcopy(index.get("images") or {})
            atomic_write_json(self.image_index_path, payload)

    @staticmethod
    def _stat_signature(path: Path) -> tuple[int, int]:
        stat = path.stat()
        return stat.st_size, stat.st_mtime_ns

    @staticmethod
    def _payload_file_identity(payload: EncodedImagePayload) -> tuple[int, int]:
        identity = payload.file_identity
        if identity is None:
            raise CalibrationError(f"Lens evidence has no file identity: {payload.source}")
        return identity.size, identity.mtime_ns

    def _analyze_bounded_payload(self, payload: EncodedImagePayload) -> dict[str, Any]:
        try:
            decoded = decode_image_payload(
                payload,
                max_width=_INDEX_MAX_WIDTH,
                max_height=_INDEX_MAX_HEIGHT,
            )
        except ImageEvidenceChangedError as exc:
            raise CalibrationError(str(exc)) from exc
        if decoded.content_sha256 != payload.content_sha256:
            raise CalibrationError(
                f"Lens evidence digest changed during decode: {payload.source.name}"
            )
        found, corners = self.detect_corners(decoded.image)
        found = bool(
            found
            and corners is not None
            and len(corners) == self.settings.rows * self.settings.columns
        )
        corners = corners if found else None
        metrics = self._capture_metrics(decoded.image, corners)
        try:
            assert_image_payload_current(payload)
        except ImageEvidenceChangedError as exc:
            raise CalibrationError(str(exc)) from exc
        return {
            "found": found,
            "corner_count": 0 if corners is None else int(len(corners)),
            **metrics,
            "detector": {
                **self._index_detector_signature(),
                "working_width": int(decoded.image.shape[1]),
                "working_height": int(decoded.image.shape[0]),
            },
        }

    def _ready_index_entry(
        self,
        payload: EncodedImagePayload,
        analysis: dict[str, Any],
    ) -> dict[str, Any]:
        if payload.source_size is None:
            raise CalibrationError(f"Lens evidence dimensions are invalid: {payload.source.name}")
        size, mtime_ns = self._payload_file_identity(payload)
        return {
            "name": payload.source.name,
            "found": bool(analysis["found"]),
            "corner_count": int(analysis["corner_count"]),
            "size": size,
            "mtime_ns": mtime_ns,
            "content_sha256": payload.content_sha256,
            "width": int(payload.source_size[0]),
            "height": int(payload.source_size[1]),
            "quality": copy.deepcopy(analysis["quality"]),
            "board_coverage_percent": float(analysis["board_coverage_percent"]),
            "board_center": copy.deepcopy(analysis["board_center"]),
            "index_state": "ready",
            "index_error": None,
            "detector": copy.deepcopy(analysis["detector"]),
        }

    def list_images(self) -> list[dict[str, Any]]:
        """Return a header-only catalog plus already committed detection evidence."""
        with self._state_lock:
            return self._catalog_images_locked()

    @staticmethod
    def _strict_int(value: Any, *, minimum: int = 0) -> int | None:
        if type(value) is not int or value < minimum:
            return None
        return value

    @staticmethod
    def _strict_finite(value: Any, *, minimum: float, maximum: float) -> float | None:
        if type(value) not in {int, float}:
            return None
        result = float(value)
        if not math.isfinite(result) or result < minimum or result > maximum:
            return None
        return result

    @classmethod
    def _canonical_quality(cls, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        result: dict[str, Any] = {}
        for key, value in raw.items():
            if not isinstance(key, str):
                return None
            allowed_strings = _QUALITY_STRING_FIELDS.get(key)
            if allowed_strings is not None:
                if not isinstance(value, str) or value not in allowed_strings:
                    return None
                result[key] = value
                continue
            if key in {"width", "height", "measurement_width", "measurement_height"}:
                integer = cls._strict_int(value, minimum=1)
                if integer is None:
                    return None
                result[key] = integer
                continue
            numeric = cls._strict_finite(value, minimum=0.0, maximum=float("inf"))
            if numeric is None:
                return None
            if key in {"luminance_mean", "luminance_p01", "luminance_p99", "contrast_span"}:
                if numeric > 255.0:
                    return None
            elif key in {"shadow_clip_percent", "highlight_clip_percent"} and numeric > 100.0:
                return None
            result[key] = numeric
        return result

    @classmethod
    def _canonical_detector(cls, raw: Any, *, index_state: str) -> dict[str, Any] | None:
        if not isinstance(raw, dict) or not isinstance(raw.get("method"), str):
            return None
        method = raw["method"]
        if method == "full-resolution-capture":
            return {"method": method} if set(raw) == {"method"} else None
        if method != "bounded-checkerboard-preview":
            return None
        required = {"method", "version", "max_width", "max_height"}
        if index_state == "ready":
            required |= {"working_width", "working_height"}
        if set(raw) != required:
            return None
        values = {
            key: cls._strict_int(raw.get(key), minimum=1)
            for key in required - {"method"}
        }
        if any(value is None for value in values.values()):
            return None
        if (
            values["version"] != _INDEX_DETECTOR_VERSION
            or values["max_width"] != _INDEX_MAX_WIDTH
            or values["max_height"] != _INDEX_MAX_HEIGHT
        ):
            return None
        if index_state == "ready" and (
            values["working_width"] > _INDEX_MAX_WIDTH
            or values["working_height"] > _INDEX_MAX_HEIGHT
        ):
            return None
        return {"method": method, **values}

    def _canonical_cached_entry(
        self,
        cached: Any,
        *,
        path: Path,
        size: int,
        mtime_ns: int,
        source_schema: int,
    ) -> dict[str, Any] | None:
        if not isinstance(cached, dict) or source_schema != _IMAGE_INDEX_SCHEMA_VERSION:
            return None
        if cached.get("name") != path.name:
            return None
        state = cached.get("index_state", "ready")
        dimension_minimum = 0 if state == "error" else 1
        cached_size = self._strict_int(cached.get("size"), minimum=0)
        cached_mtime = self._strict_int(cached.get("mtime_ns"), minimum=0)
        width = self._strict_int(cached.get("width"), minimum=dimension_minimum)
        height = self._strict_int(cached.get("height"), minimum=dimension_minimum)
        corner_count = self._strict_int(cached.get("corner_count"), minimum=0)
        found = cached.get("found")
        digest = cached.get("content_sha256")
        quality = self._canonical_quality(cached.get("quality", {}))
        coverage = self._strict_finite(
            cached.get("board_coverage_percent", 0.0),
            minimum=0.0,
            maximum=100.0,
        )
        center = cached.get("board_center")
        if center is not None:
            if not isinstance(center, list) or len(center) != 2:
                return None
            normalized_center = [
                self._strict_finite(value, minimum=0.0, maximum=1.0) for value in center
            ]
            if any(value is None for value in normalized_center):
                return None
            center = [float(value) for value in normalized_center]
        index_error = cached.get("index_error")
        if (
            cached_size != size
            or cached_mtime != mtime_ns
            or width is None
            or height is None
            or corner_count is None
            or type(found) is not bool
            or state not in {"ready", "error"}
            or quality is None
            or coverage is None
            or not isinstance(digest, str)
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
            or (index_error is not None and not isinstance(index_error, str))
        ):
            return None
        detector = self._canonical_detector(cached.get("detector"), index_state=state)
        if detector is None:
            return None
        expected_corners = self.settings.rows * self.settings.columns
        quality_complete = set(quality) == _READY_QUALITY_FIELDS
        quality_consistent = quality_complete and (
            float(quality["luminance_p01"]) <= float(quality["luminance_p99"])
            and math.isclose(
                float(quality["contrast_span"]),
                float(quality["luminance_p99"]) - float(quality["luminance_p01"]),
                rel_tol=0.0,
                abs_tol=1e-9,
            )
            and float(quality["shadow_clip_percent"])
            + float(quality["highlight_clip_percent"])
            <= 100.0 + 1e-9
        )
        if state == "ready" and (
            not quality_complete
            or not quality_consistent
            or int(quality["width"]) > _INDEX_MAX_WIDTH
            or int(quality["height"]) > _INDEX_MAX_HEIGHT
            or (
                detector["method"] == "bounded-checkerboard-preview"
                and (
                    int(quality["width"]) != int(detector["working_width"])
                    or int(quality["height"]) != int(detector["working_height"])
                )
            )
            or (found and (corner_count != expected_corners or center is None or coverage <= 0.0))
            or (not found and (corner_count != 0 or center is not None or coverage != 0.0))
            or index_error is not None
        ):
            return None
        if state == "error" and (
            found
            or corner_count
            or quality
            or coverage
            or center is not None
            or not index_error
        ):
            return None
        return {
            "name": path.name,
            "found": found,
            "corner_count": corner_count,
            "size": size,
            "mtime_ns": mtime_ns,
            "content_sha256": digest,
            "width": width,
            "height": height,
            "quality": quality,
            "board_coverage_percent": coverage,
            "board_center": center,
            "index_state": state,
            "index_error": index_error,
            "detector": detector,
        }

    def _catalog_images_locked(self) -> list[dict[str, Any]]:
        index = self._read_image_index()
        cached_images = index["images"]
        source_schema = int(index.get("_source_schema", _IMAGE_INDEX_SCHEMA_VERSION))
        entries: list[dict[str, Any]] = []
        for path in self._image_paths_locked():
            try:
                size, mtime_ns = self._stat_signature(path)
            except FileNotFoundError:
                continue
            cached = cached_images.get(path.name)
            entry = self._canonical_cached_entry(
                cached,
                path=path,
                size=size,
                mtime_ns=mtime_ns,
                source_schema=source_schema,
            )
            if entry is None:
                try:
                    width, height = probe_image_dimensions(path)
                    metadata_error = None
                except ValueError as exc:
                    width, height = 0, 0
                    metadata_error = str(exc)
                entry = {
                    "name": path.name,
                    "found": None,
                    "corner_count": 0,
                    "size": size,
                    "mtime_ns": mtime_ns,
                    "content_sha256": None,
                    "width": width,
                    "height": height,
                    "quality": {},
                    "board_coverage_percent": 0.0,
                    "board_center": None,
                    "index_state": "pending",
                    "index_error": metadata_error,
                    "detector": None,
                }
            entries.append(copy.deepcopy(entry))
        return entries

    def capture(self, image: np.ndarray) -> dict[str, Any]:
        operation_started = False
        staging_path: Path | None = None
        try:
            with self._state_lock:
                self._begin_operation_locked("capture lens evidence")
                operation_started = True
            try:
                encoded = encode_image(
                    image,
                    ".png",
                    [cv2.IMWRITE_PNG_COMPRESSION, 3],
                )
            except (RuntimeError, ValueError, cv2.error) as exc:
                raise CalibrationError(
                    f"Could not encode lens calibration image: {exc}"
                ) from exc
            staging_path = self.data_dir / f".lens-capture-{uuid.uuid4().hex}.pending"
            try:
                atomic_write_bytes(staging_path, encoded)
                encoded_payload = read_encoded_image_payload(
                    staging_path,
                    max_encoded_bytes=_EVIDENCE_MAX_ENCODED_BYTES,
                )
                analysis = self._analyze_bounded_payload(encoded_payload)
            except (OSError, RuntimeError, ValueError, cv2.error) as exc:
                raise CalibrationError(
                    f"Could not persist and analyze encoded lens calibration image: {exc}"
                ) from exc
            with self._state_lock:
                timestamp = time.strftime("%Y%m%d-%H%M%S")
                suffix = int((time.time() % 1) * 1000)
                stem = f"lens-{timestamp}-{suffix:03d}"
                path = self.image_dir / f"{stem}.png"
                collision = 1
                while path.exists():
                    path = self.image_dir / f"{stem}-{collision:02d}.png"
                    collision += 1
                try:
                    os.replace(staging_path, path)
                    staging_path = None
                    persisted_payload = read_encoded_image_payload(
                        path,
                        max_encoded_bytes=_EVIDENCE_MAX_ENCODED_BYTES,
                    )
                    if (
                        persisted_payload.content_sha256
                        != encoded_payload.content_sha256
                    ):
                        raise CalibrationError(
                            "Persisted lens capture does not match its analyzed PNG bytes"
                        )
                    entry = self._ready_index_entry(persisted_payload, analysis)
                    index = self._read_image_index()
                    index["images"][path.name] = entry
                    self._write_image_index(index)
                except (OSError, RuntimeError, ValueError) as exc:
                    path.unlink(missing_ok=True)
                    raise CalibrationError(
                        f"Could not save calibration image to {path}: {exc}"
                    ) from exc
                self._evidence_revision += 1
                return {
                    key: copy.deepcopy(entry[key])
                    for key in (
                        "name",
                        "found",
                        "corner_count",
                        "width",
                        "height",
                        "quality",
                        "board_coverage_percent",
                        "board_center",
                        "detector",
                    )
                }
        finally:
            if staging_path is not None:
                staging_path.unlink(missing_ok=True)
            if operation_started:
                with self._state_lock:
                    self._finish_operation_locked("capture lens evidence")

    @staticmethod
    def _index_entry_from_catalog(item: dict[str, Any]) -> dict[str, Any]:
        return {
            key: copy.deepcopy(item.get(key))
            for key in (
                "name",
                "found",
                "corner_count",
                "size",
                "mtime_ns",
                "content_sha256",
                "width",
                "height",
                "quality",
                "board_coverage_percent",
                "board_center",
                "index_state",
                "index_error",
                "detector",
            )
        }

    def _bounded_index_entry(
        self,
        item: dict[str, Any],
        payload: EncodedImagePayload,
    ) -> dict[str, Any]:
        expected_signature = (int(item["size"]), int(item["mtime_ns"]))
        expected_digest = str(item["content_sha256"])
        if (
            payload.source.name != str(item["name"])
            or self._payload_file_identity(payload) != expected_signature
            or payload.content_sha256 != expected_digest
        ):
            raise CalibrationError(
                f"Lens capture changed before indexing began: {item['name']}"
            )
        try:
            return self._ready_index_entry(
                payload,
                self._analyze_bounded_payload(payload),
            )
        except CalibrationError:
            raise
        except (OSError, RuntimeError, ValueError, cv2.error) as exc:
            try:
                assert_image_payload_current(payload)
            except ImageEvidenceChangedError as changed:
                raise CalibrationError(str(changed)) from exc
            source_size = payload.source_size or (0, 0)
            return {
                **self._index_entry_from_catalog(item),
                "size": expected_signature[0],
                "mtime_ns": expected_signature[1],
                "content_sha256": expected_digest,
                "width": int(source_size[0]),
                "height": int(source_size[1]),
                "found": False,
                "corner_count": 0,
                "quality": {},
                "board_coverage_percent": 0.0,
                "board_center": None,
                "index_state": "error",
                "index_error": str(exc),
                "detector": self._index_detector_signature(),
            }

    def index_pending_captures(
        self,
        *,
        retry_errors: bool = False,
        force_all: bool = False,
    ) -> dict[str, Any]:
        """Build the bounded evidence catalog without holding the state lock."""

        operation_started = False
        try:
            with self._state_lock:
                self._begin_operation_locked(_INDEX_OPERATION)
                operation_started = True
                catalog = self._catalog_images_locked()
                evidence_revision = self._evidence_revision
                evidence_signature = self._evidence_signature_locked()

            payloads, content_signature = self._load_evidence_payloads()
            if tuple(item[:3] for item in content_signature) != evidence_signature:
                raise CalibrationError(
                    "Checkerboard evidence changed before its bounded index began; "
                    "index the current capture set again"
                )
            content_by_name = {item[0]: item[-1] for item in content_signature}
            for item in catalog:
                current_digest = content_by_name.get(str(item["name"]))
                if current_digest is None:
                    raise CalibrationError(
                        "Checkerboard evidence changed before its bounded index began; "
                        "index the current capture set again"
                    )
                if force_all or item.get("content_sha256") != current_digest:
                    item.update(
                        {
                            "found": None,
                            "corner_count": 0,
                            "quality": {},
                            "board_coverage_percent": 0.0,
                            "board_center": None,
                            "index_state": "pending",
                            "index_error": None,
                            "detector": None,
                        }
                    )
                item["content_sha256"] = current_digest

            with self._state_lock:
                pending = [
                    item
                    for item in catalog
                    if item.get("index_state") == "pending"
                    or (retry_errors and item.get("index_state") == "error")
                ]
                self._index_total = len(pending)
                self._index_completed = 0
                self._index_failed = 0

            indexed = {
                str(item["name"]): self._index_entry_from_catalog(item)
                for item in catalog
            }
            for item in pending:
                name = str(item["name"])
                with self._state_lock:
                    self._index_current = name
                entry = self._bounded_index_entry(item, payloads[name])
                indexed[name] = entry
                with self._state_lock:
                    self._index_completed += 1
                    if entry["index_state"] == "error":
                        self._index_failed += 1

            for payload in payloads.values():
                try:
                    assert_image_payload_current(payload)
                except ImageEvidenceChangedError as exc:
                    raise CalibrationError(str(exc)) from exc
            del payloads
            _final_payloads, final_content_signature = self._load_evidence_payloads()
            del _final_payloads
            with self._state_lock:
                if (
                    self._evidence_revision != evidence_revision
                    or final_content_signature != content_signature
                    or self._evidence_signature_locked() != evidence_signature
                ):
                    raise CalibrationError(
                        "Checkerboard evidence changed while its bounded index was being "
                        "built; index the current capture set again"
                    )
                self._write_image_index({"images": indexed})
            return {
                "indexed_count": len(pending),
                "ready_count": sum(
                    1 for item in indexed.values() if item.get("index_state") == "ready"
                ),
                "usable_count": sum(1 for item in indexed.values() if item.get("found")),
                "error_count": sum(
                    1 for item in indexed.values() if item.get("index_state") == "error"
                ),
            }
        finally:
            if operation_started:
                with self._state_lock:
                    self._finish_operation_locked(_INDEX_OPERATION)

    def reindex_all_captures(self) -> dict[str, Any]:
        """Rebuild every advisory entry from the current immutable file bytes."""

        return self.index_pending_captures(retry_errors=True, force_all=True)

    @staticmethod
    def _capture_metrics(
        image: np.ndarray,
        corners: np.ndarray | None,
        *,
        quality_image: np.ndarray | None = None,
    ) -> dict[str, Any]:
        quality = image_quality(image if quality_image is None else quality_image).to_dict()
        quality["measurement_source"] = "exact-encoded-byte-bounded-preview"
        quality["sharpness_method"] = _SHARPNESS_METHOD
        if corners is None or not len(corners):
            return {
                "width": int(image.shape[1]),
                "height": int(image.shape[0]),
                "quality": quality,
                "board_coverage_percent": 0.0,
                "board_center": None,
            }
        points = np.asarray(corners, dtype=np.float32).reshape(-1, 2)
        hull = cv2.convexHull(points)
        area = max(1, int(image.shape[0]) * int(image.shape[1]))
        center = np.mean(points, axis=0)
        return {
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
            "quality": quality,
            "board_coverage_percent": float(cv2.contourArea(hull) / area * 100.0),
            "board_center": [
                float(center[0] / image.shape[1]),
                float(center[1] / image.shape[0]),
            ],
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

    def _object_template(self) -> np.ndarray:
        object_template = np.zeros((self.settings.rows * self.settings.columns, 3), np.float32)
        object_template[:, :2] = np.mgrid[
            0 : self.settings.columns, 0 : self.settings.rows
        ].T.reshape(-1, 2)
        object_template *= self.settings.square_size_mm
        return object_template

    @staticmethod
    def _normalize_image_size(image_size: tuple[int, int]) -> tuple[int, int]:
        try:
            width, height = image_size
            width, height = int(width), int(height)
        except (TypeError, ValueError, OverflowError) as exc:
            raise CalibrationError("Lens solve image size must contain width and height") from exc
        if width <= 0 or height <= 0:
            raise CalibrationError("Lens solve image dimensions must be positive")
        return width, height

    @staticmethod
    def _resolution_groups(images: list[dict[str, Any]]) -> list[dict[str, Any]]:
        grouped: dict[tuple[int, int], dict[str, Any]] = {}
        for item in images:
            width = int(item.get("width", 0))
            height = int(item.get("height", 0))
            if width <= 0 or height <= 0:
                continue
            key = (width, height)
            group = grouped.setdefault(
                key,
                {
                    "width": width,
                    "height": height,
                    "image_count": 0,
                    "usable_image_count": 0,
                    "pending_image_count": 0,
                    "error_image_count": 0,
                },
            )
            group["image_count"] += 1
            if item.get("found"):
                group["usable_image_count"] += 1
            state = str(item.get("index_state") or "pending")
            if state == "pending":
                group["pending_image_count"] += 1
            elif state == "error":
                group["error_image_count"] += 1
        return sorted(grouped.values(), key=lambda item: (item["width"], item["height"]))

    def _raise_solve_rejection(
        self,
        code: str,
        message: str,
        *,
        resolution_groups: list[dict[str, Any]] | None = None,
    ) -> None:
        self._record_solve_quality({
            "gate": "reject",
            "reject_reasons": [{"code": code, "message": message}],
            "warning_reasons": [],
            "resolution_groups": list(resolution_groups or []),
        })
        raise CalibrationError(f"Lens calibration rejected: {message}")

    def _select_solve_size(
        self,
        groups: list[dict[str, Any]],
        image_size: tuple[int, int] | None,
    ) -> tuple[int, int]:
        populated = [item for item in groups if int(item["image_count"]) > 0]
        if image_size is not None:
            selected = self._normalize_image_size(image_size)
            if not any(
                (int(item["width"]), int(item["height"])) == selected
                and int(item["image_count"]) > 0
                for item in groups
            ):
                self._raise_solve_rejection(
                    "resolution_has_no_views",
                    f"No checkerboard captures match {selected[0]}x{selected[1]}",
                    resolution_groups=groups,
                )
            return selected
        if len(populated) == 1:
            return int(populated[0]["width"]), int(populated[0]["height"])
        if not populated:
            self._raise_solve_rejection(
                "no_views",
                "No checkerboard captures are available",
                resolution_groups=groups,
            )
        summary = ", ".join(
            f"{item['width']}x{item['height']} ({item['image_count']} captures)"
            for item in populated
        )
        self._raise_solve_rejection(
            "ambiguous_resolution",
            "Captures contain multiple resolutions; select the current camera size explicitly: "
            + summary,
            resolution_groups=groups,
        )

    def _collect_observations(
        self,
        image_size: tuple[int, int],
        *,
        payloads: dict[str, EncodedImagePayload] | None = None,
    ) -> list[_LensObservation]:
        expected_corners = self.settings.rows * self.settings.columns
        observations: list[_LensObservation] = []
        owned_signature = None
        if payloads is None:
            payloads, owned_signature = self._load_evidence_payloads()
        for name, payload in payloads.items():
            if payload.source_size != image_size:
                continue
            try:
                decoded = decode_image_payload(payload)
            except ImageEvidenceChangedError as exc:
                raise CalibrationError(str(exc)) from exc
            except (RuntimeError, ValueError, cv2.error) as exc:
                LOGGER.warning("Skipping %s because its pixels are invalid: %s", name, exc)
                continue
            if (
                decoded.content_sha256 != payload.content_sha256
                or (decoded.image.shape[1], decoded.image.shape[0]) != image_size
            ):
                raise CalibrationError(
                    f"Lens evidence changed or decoded at the wrong size: {name}"
                )
            found, corners = self.detect_corners(decoded.image)
            try:
                assert_image_payload_current(payload)
            except ImageEvidenceChangedError as exc:
                raise CalibrationError(str(exc)) from exc
            if not found or corners is None:
                continue
            points = np.asarray(corners, dtype=np.float32).reshape(-1, 2)
            if len(points) != expected_corners or not np.isfinite(points).all():
                LOGGER.warning("Skipping %s because its checkerboard corners are invalid", name)
                continue
            observations.append(
                _LensObservation(
                    name=name,
                    image_size=image_size,
                    image_points=points,
                )
            )
        if owned_signature is not None:
            _final_payloads, final_signature = self._load_evidence_payloads()
            del _final_payloads
            if final_signature != owned_signature:
                raise CalibrationError(
                    "Checkerboard evidence changed during full-resolution extraction"
                )
        return observations

    def _fit_observations(
        self,
        observations: list[_LensObservation],
        image_size: tuple[int, int],
    ) -> _LensFit:
        object_template = self._object_template()
        object_points = [object_template.copy() for _ in observations]
        image_points = [item.image_points.reshape(-1, 1, 2).astype(np.float32) for item in observations]
        try:
            opencv_rms, camera_matrix, distortion, rotations, translations = cv2.calibrateCamera(
                object_points,
                image_points,
                image_size,
                None,
                None,
            )
        except cv2.error as exc:
            raise CalibrationError(f"OpenCV could not solve lens calibration: {exc}") from exc
        arrays = [camera_matrix, distortion, *rotations, *translations]
        if not math.isfinite(float(opencv_rms)) or not all(np.isfinite(value).all() for value in arrays):
            raise CalibrationError("OpenCV returned non-finite lens calibration parameters")
        residuals: list[np.ndarray] = []
        for observation, object_set, rotation, translation in zip(
            observations,
            object_points,
            rotations,
            translations,
            strict=True,
        ):
            projected, _ = cv2.projectPoints(
                object_set,
                rotation,
                translation,
                camera_matrix,
                distortion,
            )
            delta = observation.image_points - projected.reshape(-1, 2)
            residuals.append(np.linalg.norm(delta, axis=1).astype(np.float64))
        combined_residuals = np.concatenate(residuals)
        return _LensFit(
            rms=float(np.sqrt(np.mean(np.square(combined_residuals)))),
            opencv_rms=float(opencv_rms),
            camera_matrix=np.asarray(camera_matrix, dtype=np.float64),
            distortion=np.asarray(distortion, dtype=np.float64),
            rotations=tuple(np.asarray(value, dtype=np.float64) for value in rotations),
            translations=tuple(np.asarray(value, dtype=np.float64) for value in translations),
            residuals=tuple(residuals),
        )

    @staticmethod
    def _pose_normal(rotation: np.ndarray) -> np.ndarray:
        matrix, _ = cv2.Rodrigues(rotation)
        normal = np.asarray(matrix[:, 2], dtype=np.float64)
        normal /= max(float(np.linalg.norm(normal)), 1e-12)
        if normal[2] < 0:
            normal *= -1.0
        return normal

    def _view_diagnostic(
        self,
        observation: _LensObservation,
        rotation: np.ndarray,
        residuals: np.ndarray,
        *,
        accepted: bool,
        exclusion_reason: str | None = None,
    ) -> dict[str, Any]:
        width, height = observation.image_size
        points = observation.image_points.astype(np.float64)
        center = np.mean(points, axis=0)
        board_area = float(
            cv2.contourArea(cv2.convexHull(points.astype(np.float32)))
            / max(1, width * height)
        )
        normal = self._pose_normal(rotation)
        tilt = math.degrees(math.acos(float(np.clip(normal[2], -1.0, 1.0))))
        azimuth = math.degrees(math.atan2(float(normal[1]), float(normal[0])))
        return {
            "name": observation.name,
            "width": width,
            "height": height,
            "accepted": bool(accepted),
            "exclusion_reason": exclusion_reason,
            "corner_count": int(len(points)),
            "centroid_norm": [float(center[0] / width), float(center[1] / height)],
            "board_area_ratio": board_area,
            "pose_normal": [float(value) for value in normal],
            "pose_xy_deg": [
                math.degrees(math.atan2(float(normal[0]), float(normal[2]))),
                math.degrees(math.atan2(float(normal[1]), float(normal[2]))),
            ],
            "tilt_deg": float(tilt),
            "tilt_azimuth_deg": float(azimuth),
            "reprojection_mean_px": float(np.mean(residuals)),
            "reprojection_rms_px": float(np.sqrt(np.mean(np.square(residuals)))),
            "reprojection_p95_px": float(np.percentile(residuals, 95.0)),
            "reprojection_max_px": float(np.max(residuals)),
        }

    @staticmethod
    def _reason(
        code: str,
        message: str,
        value: Any,
        limit: Any,
    ) -> dict[str, Any]:
        return {
            "code": code,
            "message": message,
            "value": value,
            "limit": limit,
        }

    def _quality_diagnostics(
        self,
        observations: list[_LensObservation],
        fit: _LensFit,
        views: list[dict[str, Any]],
        *,
        input_count: int,
        outlier_threshold_px: float,
        retained_outlier_count: int,
    ) -> dict[str, Any]:
        width, height = observations[0].image_size
        combined = np.vstack([item.image_points for item in observations]).astype(np.float32)
        hull_ratio = float(cv2.contourArea(cv2.convexHull(combined)) / max(1, width * height))
        edge_margins = {
            "left": float(np.min(combined[:, 0]) / width),
            "right": float(1.0 - np.max(combined[:, 0]) / width),
            "top": float(np.min(combined[:, 1]) / height),
            "bottom": float(1.0 - np.max(combined[:, 1]) / height),
        }
        board_areas = [
            float(cv2.contourArea(cv2.convexHull(item.image_points)) / max(1, width * height))
            for item in observations
        ]
        minimum_area = max(min(board_areas), 1e-12)
        board_scale_ratio = float(math.sqrt(max(board_areas) / minimum_area))
        pose_vectors = np.asarray(
            [item["pose_xy_deg"] for item in views if item.get("accepted")],
            dtype=np.float64,
        )
        centered_pose = pose_vectors - np.mean(pose_vectors, axis=0)
        _, _, axes = np.linalg.svd(centered_pose, full_matrices=False)
        pose_spans = np.ptp(centered_pose @ axes.T, axis=0)
        pose_major = float(max(pose_spans))
        pose_minor = float(min(pose_spans))
        accepted_rms = np.asarray(
            [item["reprojection_rms_px"] for item in views if item.get("accepted")],
            dtype=np.float64,
        )
        reject_reasons: list[dict[str, Any]] = []
        warning_reasons: list[dict[str, Any]] = []
        if hull_ratio < _COVERAGE_REJECT_RATIO:
            reject_reasons.append(
                self._reason(
                    "insufficient_corner_coverage",
                    "Checkerboard corners cover too little of the camera image",
                    hull_ratio,
                    _COVERAGE_REJECT_RATIO,
                )
            )
        elif hull_ratio < _COVERAGE_WARN_RATIO:
            warning_reasons.append(
                self._reason(
                    "limited_corner_coverage",
                    "Checkerboard corner coverage is usable but limited",
                    hull_ratio,
                    _COVERAGE_WARN_RATIO,
                )
            )
        maximum_margin = max(edge_margins.values())
        if maximum_margin > _EDGE_MARGIN_REJECT_RATIO:
            reject_reasons.append(
                self._reason(
                    "missing_image_edge_coverage",
                    "Checkerboard corners do not reach every image edge",
                    maximum_margin,
                    _EDGE_MARGIN_REJECT_RATIO,
                )
            )
        elif maximum_margin > _EDGE_MARGIN_WARN_RATIO:
            warning_reasons.append(
                self._reason(
                    "limited_image_edge_coverage",
                    "One or more image edges have limited checkerboard coverage",
                    maximum_margin,
                    _EDGE_MARGIN_WARN_RATIO,
                )
            )
        if pose_major < _POSE_MAJOR_REJECT_DEG:
            reject_reasons.append(
                self._reason(
                    "insufficient_pose_span_major",
                    "Checkerboard views need substantially different out-of-plane tilts",
                    pose_major,
                    _POSE_MAJOR_REJECT_DEG,
                )
            )
        elif pose_major < _POSE_MAJOR_WARN_DEG:
            warning_reasons.append(
                self._reason(
                    "limited_pose_span_major",
                    "Checkerboard tilt diversity is limited along its strongest direction",
                    pose_major,
                    _POSE_MAJOR_WARN_DEG,
                )
            )
        if pose_minor < _POSE_MINOR_REJECT_DEG:
            reject_reasons.append(
                self._reason(
                    "insufficient_pose_span_minor",
                    "Checkerboard views need tilts in more than one direction",
                    pose_minor,
                    _POSE_MINOR_REJECT_DEG,
                )
            )
        elif pose_minor < _POSE_MINOR_WARN_DEG:
            warning_reasons.append(
                self._reason(
                    "limited_pose_span_minor",
                    "Checkerboard tilt diversity is limited in its second direction",
                    pose_minor,
                    _POSE_MINOR_WARN_DEG,
                )
            )
        if board_scale_ratio < _BOARD_SCALE_WARN_RATIO:
            warning_reasons.append(
                self._reason(
                    "limited_board_scale_diversity",
                    "Checkerboard views are all nearly the same apparent size",
                    board_scale_ratio,
                    _BOARD_SCALE_WARN_RATIO,
                )
            )
        median_view_rms = float(np.median(accepted_rms))
        maximum_view_rms = float(np.max(accepted_rms))
        if median_view_rms > 0.75 or maximum_view_rms > 1.5:
            warning_reasons.append(
                self._reason(
                    "high_reprojection_error",
                    "One or more accepted checkerboard views have high reprojection error",
                    {"median_px": median_view_rms, "maximum_px": maximum_view_rms},
                    {"median_px": 0.75, "maximum_px": 1.5},
                )
            )
        excluded_count = input_count - len(observations)
        if excluded_count:
            warning_reasons.append(
                self._reason(
                    "reprojection_outliers_excluded",
                    "High-error checkerboard views were excluded from the final fit",
                    excluded_count,
                    max(1, int(math.floor(input_count * 0.20))),
                )
            )
        if retained_outlier_count:
            warning_reasons.append(
                self._reason(
                    "outlier_refit_was_unstable",
                    "Suspect views were retained because removing them made the returned model less stable",
                    retained_outlier_count,
                    0,
                )
            )
        return {
            "gate": "reject" if reject_reasons else "warning" if warning_reasons else "pass",
            "reject_reasons": reject_reasons,
            "warning_reasons": warning_reasons,
            "input_count": int(input_count),
            "accepted_count": len(observations),
            "excluded_count": int(excluded_count),
            "retained_outlier_count": int(retained_outlier_count),
            "outlier_threshold_px": float(outlier_threshold_px),
            "metrics": {
                "corner_hull_ratio": hull_ratio,
                "edge_margins": edge_margins,
                "board_scale_ratio": board_scale_ratio,
                "pose_span_major_deg": pose_major,
                "pose_span_minor_deg": pose_minor,
                "median_view_rms_px": median_view_rms,
                "maximum_view_rms_px": maximum_view_rms,
                "overall_rms_px": float(fit.rms),
                "opencv_reported_rms_px": float(fit.opencv_rms),
            },
        }

    def _solve_observations(
        self,
        observations: list[_LensObservation],
        image_size: tuple[int, int],
    ) -> LensModel:
        image_size = self._normalize_image_size(image_size)
        expected_corners = self.settings.rows * self.settings.columns
        names = [item.name for item in observations]
        invalid_observations = [
            item.name
            for item in observations
            if item.image_size != image_size
            or np.asarray(item.image_points).shape != (expected_corners, 2)
            or not np.isfinite(item.image_points).all()
        ]
        if invalid_observations or len(set(names)) != len(names):
            self._raise_solve_rejection(
                "invalid_observations",
                "Lens observations must have unique names, the selected resolution, and "
                f"exactly {expected_corners} finite corners",
            )
        if len(observations) < self.settings.minimum_images:
            self._raise_solve_rejection(
                "insufficient_usable_views",
                f"Only {len(observations)} usable {image_size[0]}x{image_size[1]} checkerboard images; "
                f"at least {self.settings.minimum_images} are configured",
            )
        try:
            initial_fit = self._fit_observations(observations, image_size)
        except CalibrationError as exc:
            self._raise_solve_rejection("opencv_solve_failed", str(exc))
        initial_rms = np.asarray(
            [float(np.sqrt(np.mean(np.square(value)))) for value in initial_fit.residuals],
            dtype=np.float64,
        )
        median_rms = float(np.median(initial_rms))
        mad_rms = float(np.median(np.abs(initial_rms - median_rms)))
        outlier_threshold = max(
            1.0,
            2.5 * median_rms,
            median_rms + 3.5 * 1.4826 * mad_rms,
        )
        outlier_indices = {
            index
            for index, value in enumerate(initial_rms)
            if float(value) > outlier_threshold
        }
        accepted = [
            item
            for index, item in enumerate(observations)
            if index not in outlier_indices
        ]
        maximum_exclusions = int(math.floor(len(observations) * 0.20))
        if outlier_indices and (
            len(outlier_indices) > maximum_exclusions
            or len(accepted) < self.settings.minimum_images
        ):
            self._raise_solve_rejection(
                "excessive_reprojection_outliers",
                f"{len(outlier_indices)} checkerboard views have excessive reprojection error; "
                "recapture them instead of fitting an unstable model",
            )
        retained_outlier_count = 0
        final_fit = initial_fit
        if outlier_indices:
            candidate_fit = self._fit_observations(accepted, image_size)
            if candidate_fit.rms <= initial_fit.rms * 1.05:
                final_fit = candidate_fit
            else:
                retained_outlier_count = len(outlier_indices)
                outlier_indices = set()
                accepted = list(observations)
        accepted_diagnostics = {
            observation.name: self._view_diagnostic(
                observation,
                rotation,
                residuals,
                accepted=True,
            )
            for observation, rotation, residuals in zip(
                accepted,
                final_fit.rotations,
                final_fit.residuals,
                strict=True,
            )
        }
        excluded_diagnostics = {
            observations[index].name: self._view_diagnostic(
                observations[index],
                initial_fit.rotations[index],
                initial_fit.residuals[index],
                accepted=False,
                exclusion_reason="reprojection_outlier",
            )
            for index in outlier_indices
        }
        views = [
            accepted_diagnostics.get(item.name) or excluded_diagnostics[item.name]
            for item in observations
        ]
        quality = self._quality_diagnostics(
            accepted,
            final_fit,
            views,
            input_count=len(observations),
            outlier_threshold_px=outlier_threshold,
            retained_outlier_count=retained_outlier_count,
        )
        quality["selected_resolution"] = {
            "width": image_size[0],
            "height": image_size[1],
        }
        if quality["gate"] == "reject":
            self._record_solve_quality({**quality, "views": views})
            messages = "; ".join(
                str(item["message"])
                for item in quality["reject_reasons"]
            )
            raise CalibrationError(f"Lens calibration rejected: {messages}")
        self._record_solve_quality(quality)
        all_residuals = np.concatenate(final_fit.residuals)
        return LensModel(
            camera_matrix=final_fit.camera_matrix,
            distortion=final_fit.distortion,
            image_width=image_size[0],
            image_height=image_size[1],
            rms_error=final_fit.rms,
            mean_reprojection_error=float(np.mean(all_residuals)),
            images_used=len(accepted),
            created_at=time.time(),
            quality=quality,
            views=tuple(views),
        )

    def solve(self, image_size: tuple[int, int] | None = None) -> LensModel:
        groups: list[dict[str, Any]] = []
        operation_started = False
        try:
            with self._state_lock:
                self._begin_operation_locked(_SOLVE_OPERATION)
                operation_started = True
                images = self._catalog_images_locked()
                groups = self._resolution_groups(images)
                selected_size = self._select_solve_size(groups, image_size)
                evidence_revision = self._evidence_revision
                evidence_signature = self._evidence_signature_locked()

            payloads, content_signature = self._load_evidence_payloads()
            if tuple(item[:3] for item in content_signature) != evidence_signature:
                self._raise_solve_rejection(
                    "evidence_changed_before_solve",
                    "Checkerboard evidence changed before lens calibration began; "
                    "index the current capture set again",
                    resolution_groups=groups,
                )
            content_by_name = {item[0]: item[-1] for item in content_signature}
            if any(
                item.get("content_sha256") != content_by_name.get(str(item["name"]))
                for item in images
            ):
                self._raise_solve_rejection(
                    "evidence_digest_not_indexed",
                    "Checkerboard evidence is new or changed since its bounded index; "
                    "finish indexing the current capture set before solving",
                    resolution_groups=groups,
                )

            # Corner extraction and camera fitting are deliberately outside the
            # state lock. The operation marker keeps the evidence immutable while
            # status continues to report the last committed model.
            observations = self._collect_observations(
                selected_size,
                payloads=payloads,
            )
            for payload in payloads.values():
                try:
                    assert_image_payload_current(payload)
                except ImageEvidenceChangedError as exc:
                    self._raise_solve_rejection(
                        "evidence_changed_during_solve",
                        str(exc),
                        resolution_groups=groups,
                    )
            model = self._solve_observations(observations, selected_size)
            solve_groups = copy.deepcopy(groups)
            for group in solve_groups:
                group["preview_usable_image_count"] = int(
                    group.get("usable_image_count", 0)
                )
                if (
                    int(group["width"]),
                    int(group["height"]),
                ) == selected_size:
                    group["full_resolution_usable_image_count"] = len(observations)
            model.quality["resolution_groups"] = solve_groups
            model.quality["selected_resolution"] = {
                "width": selected_size[0],
                "height": selected_size[1],
            }
            model.quality["ignored_resolution_image_count"] = sum(
                int(item["image_count"])
                for item in groups
                if (int(item["width"]), int(item["height"])) != selected_size
            )
            del payloads
            _final_payloads, final_content_signature = self._load_evidence_payloads()
            del _final_payloads
            with self._state_lock:
                if (
                    self._evidence_revision != evidence_revision
                    or final_content_signature != content_signature
                    or self._evidence_signature_locked() != evidence_signature
                ):
                    self._raise_solve_rejection(
                        "evidence_changed_during_solve",
                        "Checkerboard evidence changed while lens calibration was being solved; "
                        "solve the current capture set again",
                        resolution_groups=groups,
                    )
                atomic_write_json(self.model_path, model.to_dict())
                self._model = model
                self._last_solve_quality = copy.deepcopy(model.quality)
            return model
        except CalibrationError:
            if operation_started:
                with self._state_lock:
                    if (
                        self._operation_owner == threading.get_ident()
                        and self._pending_solve_quality is not None
                    ):
                        quality = copy.deepcopy(self._pending_solve_quality)
                        quality["resolution_groups"] = copy.deepcopy(groups)
                        self._last_solve_quality = quality
            raise
        finally:
            if operation_started:
                with self._state_lock:
                    self._finish_operation_locked(_SOLVE_OPERATION)

    def status(self, image_size: tuple[int, int] | None = None) -> dict[str, Any]:
        with self._state_lock:
            images = self._catalog_images_locked()
            groups = self._resolution_groups(images)
            model = self._model
            active_operation = self._active_operation
            last_solve_quality = copy.deepcopy(self._last_solve_quality)
            selected_size: tuple[int, int] | None = None
            selection = "none"
            if image_size is not None:
                selected_size = self._normalize_image_size(image_size)
                selection = "requested"
            elif model is not None:
                selected_size = model.image_size
                selection = "model"
            else:
                populated_groups = [
                    item for item in groups if int(item["image_count"]) > 0
                ]
                if len(populated_groups) == 1:
                    selected_size = (
                        int(populated_groups[0]["width"]),
                        int(populated_groups[0]["height"]),
                    )
                    selection = "single_group"
                elif len(populated_groups) > 1:
                    selection = "ambiguous"
            selected_images = []
            for item in images:
                selected = selected_size == (
                    int(item.get("width", 0)),
                    int(item.get("height", 0)),
                )
                selected_images.append(
                    {**item, "selected_for_active_resolution": selected}
                )
            selected_groups = [
                {
                    **item,
                    "selected": selected_size
                    == (int(item["width"]), int(item["height"])),
                }
                for item in groups
            ]
            active_images = [
                item for item in selected_images if item["selected_for_active_resolution"]
            ]
            occupied_cells: set[tuple[int, int]] = set()
            for item in active_images:
                center = item.get("board_center")
                if item.get("found") and isinstance(center, list) and len(center) == 2:
                    column = min(2, max(0, int(float(center[0]) * 3.0)))
                    row = min(2, max(0, int(float(center[1]) * 3.0)))
                    occupied_cells.add((column, row))
            pending_count = sum(
                1 for item in selected_images if item.get("index_state") == "pending"
            )
            error_count = sum(
                1 for item in selected_images if item.get("index_state") == "error"
            )
            ready_count = len(selected_images) - pending_count - error_count
            indexing = active_operation == _INDEX_OPERATION
            index_state = (
                "indexing"
                if indexing
                else "pending"
                if pending_count
                else "error"
                if error_count
                else "ready"
            )
            return {
                "calibrated": model is not None,
                "model": None if model is None else model.to_dict(),
                "busy": active_operation is not None,
                "active_operation": active_operation,
                "image_count": len(selected_images),
                "usable_image_count": sum(1 for item in active_images if item["found"]),
                "total_usable_image_count": sum(
                    1 for item in selected_images if item["found"]
                ),
                "images": selected_images,
                "active_resolution": (
                    None
                    if selected_size is None
                    else {"width": selected_size[0], "height": selected_size[1]}
                ),
                "resolution_selection": selection,
                "resolution_groups": selected_groups,
                "last_solve_quality": last_solve_quality,
                "index": {
                    "state": index_state,
                    "indexing": indexing,
                    "ready_count": ready_count,
                    "pending_count": pending_count,
                    "error_count": error_count,
                    "total_count": len(selected_images),
                    "completed_count": self._index_completed if indexing else 0,
                    "run_total_count": self._index_total if indexing else 0,
                    "run_error_count": self._index_failed if indexing else 0,
                    "current_name": self._index_current if indexing else None,
                    "working_width": _INDEX_MAX_WIDTH,
                    "working_height": _INDEX_MAX_HEIGHT,
                    "detector_version": _INDEX_DETECTOR_VERSION,
                },
                "view_coverage": {
                    "occupied_cells": len(occupied_cells),
                    "total_cells": 9,
                    "percent": len(occupied_cells) / 9.0 * 100.0,
                },
                "pattern": {
                    "columns": self.settings.columns,
                    "rows": self.settings.rows,
                    "square_size_mm": self.settings.square_size_mm,
                    "minimum_images": self.settings.minimum_images,
                },
            }
