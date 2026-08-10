from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import re
import secrets
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import __version__
from .calibration.bed import BedCalibration, BedMapper, BedPoint
from .calibration.lens import LensCalibrator
from .calibration.profiles import CalibrationProfileStore, signature_from_camera_settings
from .calibration.registration import (
    AccuracyValidationJob,
    BaseBedCalibrationJob,
    FineRegistrationJob,
    accuracy_validation_targets,
    analyze_accuracy_measurements,
    analyze_dense_mesh_measurements,
    analyze_dense_validation_refinement,
    analyze_homography_refinement,
    analyze_registration_measurements,
    base_bed_grid_mark_sizes,
    base_bed_grid_targets,
    dense_confirmation_targets,
    dense_mesh_targets,
    dense_validation_targets,
    generate_registration_program,
    registration_targets,
    review_registration_measurements,
    suggested_registration_exclusions,
)
from .calibration.support import HoneycombSupportReference, HoneycombSupportStore
from .camera.service import (
    CameraService,
    FrameBurst,
    SyntheticCameraService,
    list_video_devices,
)
from .config import Settings
from .errors import CalibrationError
from .gcode.generator import (
    DesignPlacement,
    ToolpathOptions,
    generate_vector_gcode,
)
from .geometry.svg import parse_svg
from .imaging import encode_image, image_quality, read_image, write_image_atomic
from .machine.service import MachineService, list_serial_ports
from .storage import (
    atomic_write_bytes_if_absent,
    atomic_write_json,
    read_json,
)
from .vision.fiducials import (
    detect_aruco_markers,
    detect_crosshairs_burst,
    detect_crosshairs_near,
    detect_keyed_crosshair_grid,
)
from .vision.ruler import HoneycombRulerDetection, detect_honeycomb_rulers
from .vision.workpiece import detect_workpiece

LOGGER = logging.getLogger(__name__)
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_CURRENT_LENS_MODEL = object()
_GENERATED_NAME_LOCK = threading.Lock()
_GENERATED_NAME_SEQUENCE = 0


def _unique_artifact_filename(stem: str, suffix: str) -> str:
    """Return a bounded, distinct artifact name for concurrent same-second writes."""
    global _GENERATED_NAME_SEQUENCE
    with _GENERATED_NAME_LOCK:
        _GENERATED_NAME_SEQUENCE += 1
        sequence = _GENERATED_NAME_SEQUENCE
        entropy = secrets.token_hex(6)
    extension = suffix if suffix.startswith(".") else f".{suffix}"
    return f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}-{sequence:08x}-{entropy}{extension}"


def _publish_unique_artifact(
    directory: Path,
    *,
    stem: str,
    suffix: str,
    data: bytes,
) -> tuple[str, Path]:
    for _attempt in range(100):
        filename = _unique_artifact_filename(stem, suffix)
        path = directory / filename
        if atomic_write_bytes_if_absent(path, data):
            return filename, path
    raise RuntimeError("Could not reserve a unique artifact filename")


def _payload_boolean(payload: Mapping[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"{key} must be a JSON boolean")
    return value


def _payload_finite_number(
    payload: Mapping[str, Any],
    key: str,
    default: int | float,
) -> float:
    value = payload.get(key, default)
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise ValueError(f"{key} must be a finite JSON number")
    return float(value)


def _payload_integer(
    payload: Mapping[str, Any],
    key: str,
    default: int,
) -> int:
    value = payload.get(key, default)
    if type(value) is not int:
        raise ValueError(f"{key} must be a JSON integer")
    return value


def _payload_string(payload: Mapping[str, Any], key: str, default: str) -> str:
    value = payload.get(key, default)
    if type(value) is not str:
        raise ValueError(f"{key} must be a JSON string")
    return value


class AppContext:
    def __init__(
        self,
        settings: Settings,
        hardware_enabled: bool = False,
        laser_lockout: bool = False,
    ):
        if type(hardware_enabled) is not bool:
            raise TypeError("hardware_enabled must be an exact boolean")
        if type(laser_lockout) is not bool:
            raise TypeError("laser_lockout must be an exact boolean")
        self.settings = settings
        self.hardware_enabled = hardware_enabled
        self.laser_lockout = laser_lockout
        settings.app.data_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("captures", "calibration", "generated", "logs"):
            (settings.app.data_dir / directory).mkdir(parents=True, exist_ok=True)
        if settings.app.simulation:
            self.camera: CameraService = SyntheticCameraService(settings.camera, settings.machine.work_area)
        else:
            self.camera = CameraService(settings.camera)
        self.calibration_profiles = CalibrationProfileStore(
            settings.app.data_dir,
            signature_from_camera_settings(settings.camera),
        )
        calibration_dir = self.calibration_profiles.active_dir
        self.lens = LensCalibrator(calibration_dir, settings.calibration.lens)
        self.bed = BedMapper(calibration_dir, settings.calibration.bed, settings.machine.work_area)
        self.honeycomb_support = HoneycombSupportStore(calibration_dir)
        self.machine = MachineService(
            settings.machine,
            settings.laser,
            hardware_enabled=self.hardware_enabled,
            laser_lockout=self.laser_lockout,
        )
        self.bed_reference_path = calibration_dir / "bed_reference.png"
        self.legacy_bed_reference_path = calibration_dir / "bed_reference.jpg"
        self.base_bed_mapping_path = calibration_dir / "base_bed_mapping.json"
        self.workspace_path = settings.app.data_dir / "captures" / "workspace.png"
        self.legacy_workspace_path = settings.app.data_dir / "captures" / "workspace.jpg"
        self.fine_registration_path = calibration_dir / "fine_registration.json"
        self.accuracy_validation_path = calibration_dir / "accuracy_validation.json"
        self.dense_calibration_path = calibration_dir / "dense_calibration.json"
        self.dense_validation_path = calibration_dir / "dense_validation.json"
        self.dense_confirmation_path = calibration_dir / "dense_confirmation.json"
        self._migrate_legacy_dense_session()
        self._camera_start_error: str | None = None
        self._simulation_workspace_lock = threading.RLock()
        self._simulation_workspace_image: np.ndarray | None = None
        self._simulation_workspace_info: dict[str, Any] | None = None
        self._lens_workflow_lock = threading.RLock()
        self._workspace_lock = threading.RLock()
        self._workspace_image: np.ndarray | None = None
        self._workspace_revision: tuple[Any, ...] | None = None
        self._composed_map_cache: dict[
            tuple[Any, ...],
            tuple[object, object, tuple[np.ndarray, np.ndarray]],
        ] = {}

    def _migrate_legacy_dense_session(self) -> None:
        """Preserve validation metadata formerly stored in the shared fit file."""

        legacy = read_json(self.dense_calibration_path, {})
        if not isinstance(legacy, dict) or not legacy:
            return
        destination = None
        if legacy.get("confirmation") is True:
            destination = self.dense_confirmation_path
        elif legacy.get("validation") is True:
            destination = self.dense_validation_path
        if destination is not None and not destination.exists():
            atomic_write_json(destination, legacy)

    def _bed_provenance(
        self,
        *,
        lens_model_id: str | None | object = _CURRENT_LENS_MODEL,
    ) -> dict[str, Any]:
        camera_status = self.camera.status()
        area = self.settings.machine.work_area
        width = int(camera_status.width or self.settings.camera.width)
        height = int(camera_status.height or self.settings.camera.height)
        if lens_model_id is _CURRENT_LENS_MODEL:
            lens = self.lens.model
            current_lens_model_id = None if lens is None else lens.model_id
        else:
            current_lens_model_id = lens_model_id
        return {
            "schema_version": 1,
            "lens_model_id": current_lens_model_id,
            "camera": {
                "device": str(camera_status.device),
                "synthetic": bool(camera_status.synthetic),
                "width": width,
                "height": height,
                "fourcc": str(self.settings.camera.fourcc),
                "controls": copy.deepcopy(self.settings.camera.controls),
            },
            "work_area": {
                "x_min": float(area.x_min),
                "x_max": float(area.x_max),
                "y_min": float(area.y_min),
                "y_max": float(area.y_max),
            },
            "pixels_per_mm": float(self.settings.calibration.bed.pixels_per_mm),
        }

    def bed_calibration_validity(
        self,
        *,
        lens_model_id: str | None | object = _CURRENT_LENS_MODEL,
    ) -> dict[str, Any]:
        calibration = self.bed.calibration
        if calibration is None:
            unavailable_reason = self.bed.calibration_unavailable_reason
            if unavailable_reason is not None:
                return {"state": "STALE", "reasons": [unavailable_reason]}
            return {"state": "MISSING", "reasons": ["No bed map is installed"]}
        saved = calibration.provenance
        if not isinstance(saved, dict):
            return {
                "state": "UNKNOWN",
                "reasons": [
                    "This legacy bed map has no camera/lens provenance and must be remapped"
                ],
            }
        current = self._bed_provenance(lens_model_id=lens_model_id)
        changed = [key for key in current if saved.get(key) != current[key]]
        if changed:
            return {
                "state": "STALE",
                "reasons": [
                    "Bed-map dependency changed: " + ", ".join(changed)
                ],
            }
        return {"state": "VALID", "reasons": []}

    def camera_calibration_readiness(self) -> dict[str, Any]:
        """Report whether the live camera is stable enough for calibration."""

        status = self.camera.status()
        reasons: list[str] = []
        if not status.connected:
            reasons.append("Camera is not connected")
        if status.synthetic:
            return {
                "state": "READY" if status.connected else "BLOCKED",
                "reasons": reasons,
                "synthetic": True,
            }
        expected = (int(self.settings.camera.width), int(self.settings.camera.height))
        actual = (int(status.width), int(status.height))
        if actual != expected:
            reasons.append(
                "Camera resolution is not the configured calibration mode: "
                f"expected {expected[0]}x{expected[1]}, got {actual[0]}x{actual[1]}"
            )
        if status.frame_age_seconds is None:
            reasons.append("Camera has not delivered a live frame")
        elif status.frame_age_seconds > 2.0:
            reasons.append(
                f"Latest camera frame is stale ({status.frame_age_seconds:.2f} s old)"
            )
        for requirement, detail in sorted(status.controls_critical_unverified.items()):
            reasons.append(f"{requirement.replace('_', ' ')} is unverified: {detail}")
        return {
            "state": "READY" if not reasons else "BLOCKED",
            "reasons": reasons,
            "synthetic": False,
            "expected_resolution": list(expected),
            "actual_resolution": list(actual),
            "controls_verified": dict(status.controls_verified),
            "controls_satisfied": dict(status.controls_satisfied),
        }

    def _require_camera_calibration_ready(self) -> None:
        readiness = self.camera_calibration_readiness()
        if readiness["state"] != "READY":
            raise CalibrationError(
                "Camera is not ready for calibration: "
                + "; ".join(readiness["reasons"])
            )

    def _require_accepted_lens_calibration(self) -> Any:
        model = self.lens.model
        if model is None:
            raise CalibrationError(
                "Solve lens calibration before capturing the fresh base-map grid"
            )
        gate = str(model.quality.get("gate", "")).strip().lower()
        if gate not in {"pass", "warning"}:
            raise CalibrationError(
                "The active lens model has no accepted pose-diversity and coverage "
                "diagnostics. Clear or replace the legacy model, capture genuinely "
                "tilted checkerboard views across the frame, and solve again before "
                "fresh base mapping."
            )
        return model

    def _require_session_execution(
        self,
        session: Any,
        label: str,
    ) -> dict[str, Any]:
        if not isinstance(session, dict):
            raise CalibrationError(f"Prepare the {label} job before capture")
        if not self._session_boolean(session, "powered", label):
            raise CalibrationError(
                f"The prepared {label} job has laser power 0%. Prepare and run its "
                "positive-power version before capture."
            )
        digest = session.get("program_digest")
        created_at = session.get("created_at")
        if not isinstance(digest, str) or not digest or not isinstance(
            created_at, (int, float)
        ):
            raise CalibrationError(
                f"The saved {label} session predates execution receipts. Prepare and "
                "run the exact powered job again."
            )
        receipt = self.machine.successful_job_receipt(
            digest,
            not_before=float(created_at),
        )
        if receipt is None:
            receipt = self._persisted_session_execution_receipt(session, digest)
        if receipt is None or receipt.get("powered") is not True:
            raise CalibrationError(
                f"The exact powered {label} program has not completed successfully in "
                "this app session, and no matching verified capture receipt was saved. "
                "Run it to completion before capture."
            )
        return receipt

    def _persisted_session_execution_receipt(
        self,
        session: dict[str, Any],
        digest: str,
    ) -> dict[str, Any] | None:
        """Recover only a receipt that was verified while saving a prior capture."""
        receipt = session.get("execution_receipt")
        detection = session.get("detection")
        captured_at = session.get("captured_at")
        created_at = session.get("created_at")
        if (
            not isinstance(receipt, dict)
            or not isinstance(detection, dict)
            or type(captured_at) not in (int, float)
            or type(created_at) not in (int, float)
            or not math.isfinite(float(captured_at))
            or not math.isfinite(float(created_at))
        ):
            return None
        finished_at = receipt.get("finished_at")
        completed_lines = receipt.get("completed_lines")
        total_lines = receipt.get("total_lines")
        if (
            receipt.get("program_digest") != digest
            or receipt.get("name") != session.get("filename")
            or receipt.get("powered") is not True
            or receipt.get("backend") != self.settings.machine.backend
            or receipt.get("hardware_enabled") is not self.hardware_enabled
            or not isinstance(receipt.get("protocol"), str)
            or not receipt["protocol"]
            or type(finished_at) not in (int, float)
            or not math.isfinite(float(finished_at))
            or float(finished_at) < float(created_at)
            or float(captured_at) < float(finished_at)
            or type(completed_lines) is not int
            or type(total_lines) is not int
            or completed_lines <= 0
            or completed_lines != total_lines
        ):
            return None
        return copy.deepcopy(receipt)

    @staticmethod
    def _session_boolean(session: dict[str, Any], key: str, label: str) -> bool:
        value = session.get(key)
        if type(value) is not bool:
            raise CalibrationError(
                f"The saved {label} session has an invalid {key!r} flag. Prepare "
                "the job again instead of trusting altered session metadata."
            )
        return value

    @staticmethod
    def _bed_mapping_session_fields(calibration: BedCalibration) -> dict[str, Any]:
        return {
            "image_to_machine": calibration.image_to_machine.tolist(),
            "residual_mesh_created_at": (
                None
                if calibration.residual_mesh is None
                else calibration.residual_mesh.created_at
            ),
        }

    @staticmethod
    def _require_session_bed_mapping(
        session: dict[str, Any],
        calibration: BedCalibration,
        label: str,
    ) -> None:
        try:
            prepared_map = np.asarray(
                session.get("image_to_machine", []),
                dtype=np.float64,
            )
        except (TypeError, ValueError):
            prepared_map = np.empty((0, 0), dtype=np.float64)
        current_mesh_created_at = (
            None
            if calibration.residual_mesh is None
            else calibration.residual_mesh.created_at
        )
        if (
            prepared_map.shape != (3, 3)
            or not np.isfinite(prepared_map).all()
            or not np.allclose(
                prepared_map,
                calibration.image_to_machine,
                rtol=1e-10,
                atol=1e-10,
            )
            or "residual_mesh_created_at" not in session
            or session.get("residual_mesh_created_at") != current_mesh_created_at
        ):
            raise CalibrationError(
                f"The bed map changed after this {label} job was prepared; "
                "prepare a new job"
            )

    @staticmethod
    def _seal_analysis(analysis: dict[str, Any]) -> dict[str, Any]:
        sealed = copy.deepcopy(analysis)
        sealed["analysis_id"] = secrets.token_urlsafe(24)
        canonical = json.dumps(
            sealed,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        sealed["analysis_digest"] = hashlib.sha256(canonical).hexdigest()
        return sealed

    def _require_current_analysis(
        self,
        session_path: Path,
        analysis: Any,
        label: str,
    ) -> dict[str, Any]:
        if not isinstance(analysis, dict):
            raise CalibrationError(f"No reviewed {label} analysis is available")
        digest = analysis.get("analysis_digest")
        identifier = analysis.get("analysis_id")
        if not isinstance(digest, str) or not isinstance(identifier, str):
            raise CalibrationError(
                f"This {label} result is not bound to a saved capture; recapture it"
            )
        candidate = copy.deepcopy(analysis)
        candidate.pop("analysis_digest", None)
        canonical = json.dumps(
            candidate,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if hashlib.sha256(canonical).hexdigest() != digest:
            raise CalibrationError(f"This {label} result was modified after review")
        session = read_json(session_path, {})
        saved = session.get("analysis") if isinstance(session, dict) else None
        if (
            not isinstance(saved, dict)
            or saved.get("analysis_id") != identifier
            or saved.get("analysis_digest") != digest
        ):
            raise CalibrationError(
                f"This {label} result is stale; use the latest successful capture"
            )
        return copy.deepcopy(saved)

    def _require_valid_bed_calibration(self) -> None:
        validity = self.bed_calibration_validity()
        if validity["state"] != "VALID":
            raise CalibrationError(
                f"Bed calibration is {validity['state']}: "
                + "; ".join(validity["reasons"])
            )

    def bed_status(
        self,
        *,
        lens_model_id: str | None | object = _CURRENT_LENS_MODEL,
    ) -> dict[str, Any]:
        status = self.bed.status()
        validity = self.bed_calibration_validity(lens_model_id=lens_model_id)
        status["calibrated"] = validity["state"] == "VALID"
        status["validity"] = validity
        return status

    def start(self) -> None:
        if self.settings.camera.autostart:
            try:
                self.camera.start()
                if isinstance(self.camera, SyntheticCameraService) and self.bed.calibration is None:
                    self.capture_bed_reference(precision=False)
                    points = [
                        BedPoint(image_x=u, image_y=v, machine_x=x, machine_y=y, label=label)
                        for u, v, x, y, label in self.camera.calibration_correspondences()
                    ]
                    self.bed.replace_points(points)
                    image = self.bed_reference()
                    self.bed.solve(
                        image.shape[1],
                        image.shape[0],
                        provenance=self._bed_provenance(),
                    )
            except Exception as exc:
                self._camera_start_error = str(exc)
                LOGGER.error("Camera did not start: %s", exc)
        if self.settings.machine.backend == "simulator":
            try:
                self.machine.connect()
            except Exception as exc:
                LOGGER.error("Simulator did not connect: %s", exc)

    def stop(self) -> None:
        self.clear_simulation_workspace_frame()
        try:
            self.machine.disconnect()
        finally:
            self.camera.stop()

    def restart_camera(self) -> dict[str, Any]:
        """Release and reopen the configured camera after an explicit retry."""
        try:
            self.camera.restart()
        except Exception as exc:
            self._camera_start_error = str(exc)
            raise
        self._camera_start_error = None
        return asdict(self.camera.status())

    def camera_frame(
        self,
        undistort: bool = True,
        *,
        after_sequence: int | None = None,
        timeout: float = 6.0,
    ) -> np.ndarray:
        frame = (
            self.camera.snapshot()
            if after_sequence is None
            else self.camera.snapshot_after(after_sequence, timeout=timeout)
        )
        lens = self.lens.model if undistort else None
        if lens is not None:
            frame = lens.undistort(frame)
        return frame

    def precision_camera_burst(self, undistort: bool = True) -> FrameBurst:
        # Raw parked captures are completed under the machine's temporary
        # position hold. Defer every CPU-side score until the caller leaves
        # that hold and explicitly prepares the burst.
        burst = self.camera.capture_burst(
            self.settings.camera.precision_capture,
            score_frames=undistort,
        )
        if not undistort:
            return burst
        return self._prepare_camera_burst(burst, undistort=undistort)

    def _prepare_camera_burst(self, burst: FrameBurst, *, undistort: bool) -> FrameBurst:
        validate = getattr(self.camera, "ensure_burst_current", None)
        if callable(validate):
            validate(burst)
        lens = self.lens.model if undistort else None
        if lens is not None:
            # Drop the tuple's references before replacing frames one by one so
            # a 1080p burst never retains complete raw and corrected copies.
            frames = list(burst.frames)
            burst.frames = ()
            for index, frame in enumerate(frames):
                frames[index] = lens.undistort(frame)
                if callable(validate):
                    validate(burst)
            burst.frames = tuple(frames)
        if lens is not None or not burst.sharpness_scores:
            sharpness: list[float] = []
            for frame in burst.frames:
                sharpness.append(self.camera._sharpness_score(frame))
                if callable(validate):
                    validate(burst)
            burst.sharpness_scores = tuple(sharpness)
        if callable(validate):
            validate(burst)
        return burst

    def _stable_camera_burst(self) -> FrameBurst:
        sample_frames = 5
        profile = replace(
            self.settings.camera.precision_capture,
            settle_seconds=0.1,
            discard_frames=2,
            sample_frames=sample_frames,
            timeout_seconds=2.0,
            minimum_valid_frames=1,
            consensus_frames=min(
                self.settings.camera.precision_capture.consensus_frames,
                sample_frames,
            ),
        )
        return self.camera.capture_burst(profile, score_frames=False)

    def stable_camera_frame(self, undistort: bool = True) -> tuple[np.ndarray, dict[str, Any]]:
        burst = self._stable_camera_burst()
        burst = self._prepare_camera_burst(burst, undistort=undistort)
        diagnostics = burst.diagnostics()
        diagnostics["capture_class"] = "interactive_stable"
        return burst.sharpest_frame, diagnostics

    @staticmethod
    def encode_jpeg(image: np.ndarray, quality: int = 92) -> bytes:
        return encode_image(image, ".jpg", [cv2.IMWRITE_JPEG_QUALITY, int(quality)])

    def save_capture(
        self,
        prefix: str = "capture",
        undistort: bool = True,
        *,
        precision: bool = True,
    ) -> Path:
        image = (
            self.stable_camera_frame(undistort=undistort)[0] if precision else self.camera_frame(undistort=undistort)
        )
        safe_prefix = _SAFE_NAME_RE.sub("-", prefix).strip("-._")[:60] or "capture"
        encoded = encode_image(
            image,
            ".jpg",
            [cv2.IMWRITE_JPEG_QUALITY, 96],
        )
        _filename, path = _publish_unique_artifact(
            self.settings.app.data_dir / "captures",
            stem=safe_prefix,
            suffix=".jpg",
            data=encoded,
        )
        return path

    def capture_bed_reference(self, *, precision: bool = True) -> dict[str, Any]:
        self._require_camera_calibration_ready()
        if precision:
            image, diagnostics = self.stable_camera_frame(undistort=True)
        else:
            image = self.camera_frame(undistort=True)
            diagnostics = None
        write_image_atomic(
            self.bed_reference_path,
            image,
            [cv2.IMWRITE_PNG_COMPRESSION, 3],
        )
        return {
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
            "path": self.bed_reference_path.name,
            "capture_diagnostics": diagnostics,
            "image_quality": image_quality(image).to_dict(),
        }

    def capture_lens_calibration(self) -> dict[str, Any]:
        with self._lens_workflow_lock:
            self._require_camera_calibration_ready()
            image, diagnostics = self.stable_camera_frame(undistort=False)
            result = self.lens.capture(image)
            return {**result, "capture_diagnostics": diagnostics}

    def solve_lens_calibration(self) -> Any:
        with self._lens_workflow_lock:
            self._require_camera_calibration_ready()
            status = self.camera.status()
            model = self.lens.solve(image_size=(int(status.width), int(status.height)))
            with self._workspace_lock:
                self._workspace_image = None
                self._workspace_revision = None
                self._composed_map_cache.clear()
            return model

    def clear_lens_calibration(self, *, delete_images: bool = False) -> None:
        with self._lens_workflow_lock:
            self.lens.clear(delete_images=delete_images)
            with self._workspace_lock:
                self._workspace_image = None
                self._workspace_revision = None
                self._composed_map_cache.clear()

    def bed_reference(self) -> np.ndarray:
        for path in (self.bed_reference_path, self.legacy_bed_reference_path):
            image = read_image(path)
            if image is not None:
                return image
        return self.camera_frame(undistort=True)

    def _current_workspace_revision(self) -> tuple[Any, ...]:
        lens = self.lens.model
        bed = self.bed.calibration
        mesh = None if bed is None else bed.residual_mesh
        return (
            None if lens is None else lens.created_at,
            None if bed is None else bed.created_at,
            None if bed is None else bed.registration_created_at,
            None if bed is None else bed.refinement_created_at,
            None if mesh is None else mesh.created_at,
            None if mesh is None else mesh.refinement_count,
        )

    def _cache_workspace(self, image: np.ndarray) -> None:
        with self._workspace_lock:
            self._workspace_image = np.ascontiguousarray(image).copy()
            self._workspace_revision = self._current_workspace_revision()

    def _cached_workspace(self) -> np.ndarray | None:
        revision = self._current_workspace_revision()
        with self._workspace_lock:
            if self._workspace_image is None or self._workspace_revision != revision:
                self._workspace_image = None
                self._workspace_revision = None
                return None
            return self._workspace_image.copy()

    def _persist_workspace(self, image: np.ndarray) -> None:
        write_image_atomic(
            self.workspace_path,
            image,
            [cv2.IMWRITE_PNG_COMPRESSION, 3],
        )

    def _rectify_camera_image(self, image: np.ndarray) -> np.ndarray:
        lens = self.lens.model
        if lens is None:
            return self.bed.rectify(image)
        height, width = image.shape[:2]
        if (width, height) != lens.image_size:
            raise CalibrationError(
                "Current camera resolution does not match the lens calibration "
                f"({width}x{height} vs {lens.image_width}x{lens.image_height})"
            )
        calibration = self.bed.calibration
        if calibration is None:
            raise CalibrationError("Bed calibration has not been solved")
        if (width, height) != (calibration.image_width, calibration.image_height):
            raise CalibrationError(
                "Current camera resolution does not match the bed calibration "
                f"({width}x{height} vs "
                f"{calibration.image_width}x{calibration.image_height})"
            )
        ppm = float(self.settings.calibration.bed.pixels_per_mm)
        key = (
            lens.model_id,
            id(calibration),
            width,
            height,
            ppm,
        )
        with self._workspace_lock:
            cached = self._composed_map_cache.get(key)
        maps = None
        if cached is not None and cached[0] is lens and cached[1] is calibration:
            maps = cached[2]
        if maps is None:
            corrected_x, corrected_y = self.bed.rectification_map(ppm)
            corrected_points = np.stack((corrected_x, corrected_y), axis=-1)
            raw_points = lens.distort_points(corrected_points)
            raw_x = np.ascontiguousarray(raw_points[:, :, 0], dtype=np.float32)
            raw_y = np.ascontiguousarray(raw_points[:, :, 1], dtype=np.float32)
            raw_x.setflags(write=False)
            raw_y.setflags(write=False)
            maps = (raw_x, raw_y)
            if self.lens.model is not lens or self.bed.calibration is not calibration:
                raise CalibrationError("Camera calibration changed while rectification maps were built")
            with self._workspace_lock:
                # Keep both source models alive with the maps so an id() cannot
                # be reused for different calibration state.
                self._composed_map_cache = {key: (lens, calibration, maps)}
        rectified = cv2.remap(
            image,
            maps[0],
            maps[1],
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(35, 35, 35),
        )
        if self.lens.model is not lens or self.bed.calibration is not calibration:
            raise CalibrationError("Camera calibration changed while the image was rectified")
        return rectified

    def rectified_frame(
        self,
        refresh: bool = True,
        *,
        precision: bool = False,
        persist: bool = False,
    ) -> np.ndarray:
        with self._simulation_workspace_lock:
            if self._simulation_workspace_image is not None:
                return self._simulation_workspace_image.copy()
        self._require_valid_bed_calibration()
        if not refresh:
            cached = self._cached_workspace()
            if cached is not None:
                return cached
            for path in (self.workspace_path, self.legacy_workspace_path):
                image = read_image(path)
                if image is not None:
                    self._cache_workspace(image)
                    return image
        image = (
            self.stable_camera_frame(undistort=False)[0]
            if precision
            else self.camera_frame(undistort=False)
        )
        rectified = self._rectify_camera_image(image)
        self._cache_workspace(rectified)
        if persist:
            self._persist_workspace(rectified)
        return rectified

    def capture_parked_trace_frame(self) -> np.ndarray:
        """Home, park, hold, and capture the frame used for object tracing."""

        with self._simulation_workspace_lock:
            if self._simulation_workspace_image is not None:
                return self._simulation_workspace_image.copy()
        self._require_valid_bed_calibration()
        with self.machine.temporary_stepper_hold():
            self.machine.prepare_photo_position()
            burst = self._stable_camera_burst()
        burst = self._prepare_camera_burst(burst, undistort=False)
        image = burst.sharpest_frame
        rectified = self._rectify_camera_image(image)
        self._cache_workspace(rectified)
        self._persist_workspace(rectified)
        return rectified

    def capture_parked_work_area_reference(self) -> np.ndarray:
        """Capture a lens-corrected raw view for a machine-coordinate overlay."""

        self._require_camera_calibration_ready()
        self._require_valid_bed_calibration()
        calibration = self.bed.calibration
        with self.machine.temporary_stepper_hold():
            self.machine.prepare_photo_position()
            burst = self._stable_camera_burst()
        burst = self._prepare_camera_burst(burst, undistort=True)
        if self.bed.calibration is not calibration:
            raise CalibrationError(
                "Bed calibration changed while the work-area reference was captured"
            )
        return burst.sharpest_frame.copy()

    def detect_honeycomb_support_reference(
        self,
        image: np.ndarray,
        image_points: tuple[
            tuple[float, float],
            tuple[float, float],
            tuple[float, float],
        ],
        *,
        ruler_mark_mm: float,
    ) -> tuple[HoneycombSupportReference, HoneycombRulerDetection]:
        """Fit physical ruler features near rough hints without changing calibration."""

        self._require_valid_bed_calibration()
        calibration = self.bed.calibration
        detection = detect_honeycomb_rulers(
            image,
            image_points,
            ruler_span_mm=ruler_mark_mm,
        )
        machine_points = tuple(
            self.bed.image_to_mm(float(image_x), float(image_y))
            for image_x, image_y in detection.image_points
        )
        if self.bed.calibration is not calibration or calibration is None:
            raise CalibrationError(
                "Bed calibration changed while the honeycomb support was measured"
            )
        reference = HoneycombSupportReference.from_observations(
            ruler_origin_machine_mm=machine_points[0],
            ruler_x_mark_machine_mm=machine_points[1],
            ruler_xy_mark_machine_mm=machine_points[2],
            ruler_mark_mm=ruler_mark_mm,
            support_width_mm=ruler_mark_mm,
            support_height_mm=ruler_mark_mm,
            bed_calibration_created_at=calibration.created_at,
        )
        measured_x, measured_y = reference.measured_ruler_span_mm
        maximum_span_error = max(2.0, float(ruler_mark_mm) * 0.01)
        failed_axes = [
            f"{axis} measured {measured:.1f} mm"
            for axis, measured in (("X", measured_x), ("Y", measured_y))
            if abs(measured - float(ruler_mark_mm)) > maximum_span_error
        ]
        if failed_axes:
            details = "; ".join(failed_axes)
            raise CalibrationError(
                f"Detected ruler fit disagrees with the {ruler_mark_mm:g} mm "
                f"reference: {details}. Adjust the hints and try again; the "
                "saved visual reference was not changed."
            )
        return reference, detection

    def save_honeycomb_support_reference(
        self,
        reference: HoneycombSupportReference,
    ) -> HoneycombSupportReference:
        """Persist a reviewed visual reference if its precision map is still active."""

        self._require_valid_bed_calibration()
        calibration = self.bed.calibration
        if (
            calibration is None
            or abs(
                calibration.created_at - reference.bed_calibration_created_at
            )
            > 1e-9
        ):
            raise CalibrationError(
                "Bed calibration changed after ruler detection; detect it again"
            )
        self.honeycomb_support.save(reference)
        return reference

    def clear_honeycomb_support_reference(self) -> None:
        self.honeycomb_support.clear()

    @property
    def simulation_workspace_frame_supported(self) -> bool:
        """Whether a memory-only corrected test frame is safe in this process."""

        return bool(
            self.settings.app.simulation and self.settings.machine.backend == "simulator" and not self.hardware_enabled
        )

    @property
    def has_simulation_workspace_frame(self) -> bool:
        with self._simulation_workspace_lock:
            return self._simulation_workspace_image is not None

    def simulation_workspace_frame_info(self) -> dict[str, Any] | None:
        with self._simulation_workspace_lock:
            return copy.deepcopy(self._simulation_workspace_info)

    def simulation_workspace_frame_status(self) -> dict[str, Any] | None:
        """Return lightweight source state for frequently-polled status payloads."""

        with self._simulation_workspace_lock:
            if self._simulation_workspace_info is None:
                return None
            return {
                key: self._simulation_workspace_info[key]
                for key in (
                    "active",
                    "source_name",
                    "width",
                    "height",
                    "pixels_per_mm",
                )
            }

    def set_simulation_workspace_frame(
        self,
        image: np.ndarray,
        *,
        source_name: str,
        metadata: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Freeze one corrected full-bed frame for safe simulation workflows."""

        if not self.simulation_workspace_frame_supported:
            raise RuntimeError(
                "Test images require simulation mode, the simulator machine backend, "
                "and a process without hardware access"
            )
        if not isinstance(image, np.ndarray) or image.size == 0:
            raise ValueError("The corrected test image is empty")
        if image.ndim != 3 or image.shape[2] != 3 or image.dtype != np.uint8:
            raise ValueError("The corrected test image must be an 8-bit BGR image")
        work_area = self.settings.machine.work_area
        pixels_per_mm = float(self.settings.calibration.bed.pixels_per_mm)
        expected_width = max(1, int(round(work_area.width * pixels_per_mm)))
        expected_height = max(1, int(round(work_area.height * pixels_per_mm)))
        actual_height, actual_width = image.shape[:2]
        if (actual_width, actual_height) != (expected_width, expected_height):
            raise ValueError(
                "The corrected test image must cover the complete work area at "
                f"{pixels_per_mm:g} px/mm: expected {expected_width}x{expected_height}, "
                f"got {actual_width}x{actual_height}"
            )
        label = str(source_name).strip()
        if not label:
            raise ValueError("The test-image source name must not be empty")
        info = {
            "active": True,
            "source_name": label,
            "width": expected_width,
            "height": expected_height,
            "pixels_per_mm": pixels_per_mm,
            "metadata": copy.deepcopy(dict(metadata or {})),
        }
        replacement = np.ascontiguousarray(image).copy()
        with self._simulation_workspace_lock:
            self._simulation_workspace_image = replacement
            self._simulation_workspace_info = info
        return copy.deepcopy(info)

    def clear_simulation_workspace_frame(self) -> None:
        with self._simulation_workspace_lock:
            self._simulation_workspace_image = None
            self._simulation_workspace_info = None

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
        calibration = self.bed.solve(
            image.shape[1],
            image.shape[0],
            provenance=self._bed_provenance(),
        )
        return calibration.to_dict()

    def detect_workpiece(self) -> dict[str, Any]:
        image = self.rectified_frame(refresh=True, precision=True)
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

    def detect_bed_cross_grid(self) -> dict[str, Any]:
        image = self.bed_reference()
        area = self.settings.machine.work_area
        margin = float(self.settings.laser.boundary_margin_mm)

        def grid_axis(minimum: float, maximum: float) -> tuple[float, ...]:
            safe_minimum = float(minimum) + margin
            safe_maximum = float(maximum) - margin
            span = safe_maximum - safe_minimum
            edge_inset = span * 0.025
            return tuple(
                float(value)
                for value in np.linspace(
                    safe_minimum + edge_inset,
                    safe_maximum - edge_inset,
                    5,
                )
            )

        x_coordinates = grid_axis(area.x_min, area.x_max)
        y_coordinates = grid_axis(area.y_min, area.y_max)

        validity = self.bed_calibration_validity()
        if validity["state"] != "VALID":
            return {
                "detected": False,
                "reason": (
                    "A current, provenance-verified rough bed mapping is required for "
                    "this legacy detector. Use the fresh keyed base-map workflow after "
                    "moving the camera."
                ),
                "points": [],
            }

        expected_points: list[dict[str, Any]] = []
        identifier = 1
        for machine_y in y_coordinates:
            for machine_x in x_coordinates:
                image_x, image_y = self.bed.mm_to_image(machine_x, machine_y)
                expected_points.append(
                    {
                        "id": identifier,
                        "image_x": image_x,
                        "image_y": image_y,
                        "machine_x": machine_x,
                        "machine_y": machine_y,
                    }
                )
                identifier += 1

        return detect_crosshairs_near(image, expected_points, search_radius_px=65)

    def _base_bed_mapping_geometry(self) -> dict[str, Any]:
        area = self.settings.machine.work_area
        laser = self.settings.laser
        return {
            "work_area": asdict(area),
            "boundary_margin_mm": laser.boundary_margin_mm,
            "spot_offset_x_mm": laser.spot_offset_x_mm,
            "spot_offset_y_mm": laser.spot_offset_y_mm,
        }

    def prepare_base_bed_mapping_job(
        self,
        *,
        powered: bool,
        power_percent: float,
        mark_size_mm: float,
        speed_mm_min: float,
    ) -> BaseBedCalibrationJob:
        targets = base_bed_grid_targets(
            self.settings.machine.work_area,
            mark_size_mm=mark_size_mm,
            boundary_margin_mm=self.settings.laser.boundary_margin_mm,
        )
        keyed_sizes = base_bed_grid_mark_sizes(mark_size_mm)
        program = generate_registration_program(
            targets,
            self.settings.laser,
            self.settings.machine.work_area,
            mark_size_mm=mark_size_mm,
            power_percent=power_percent,
            powered=powered,
            speed_mm_min=speed_mm_min,
            design_name="base-bed-mapping-keyed-crosses",
            mark_sizes_mm=keyed_sizes,
        )
        filename, _generated_path = _publish_unique_artifact(
            self.settings.app.data_dir / "generated",
            stem="base-bed-mapping",
            suffix=".gcode",
            data=program.text.encode("utf-8"),
        )
        created_at = time.time()
        program_digest = self.machine.preflight_program(program.text).digest
        atomic_write_json(
            self.base_bed_mapping_path,
            {
                "schema_version": 2,
                "kind": "base_bed_mapping",
                "created_at": created_at,
                "filename": filename,
                "program_digest": program_digest,
                "powered": powered,
                "power_percent": power_percent if powered else 0.0,
                "mark_size_mm": mark_size_mm,
                "speed_mm_min": speed_mm_min,
                "targets": [target.to_dict() for target in targets],
                "keyed_mark_sizes_mm": {str(key): value for key, value in keyed_sizes.items()},
                "generation_geometry": self._base_bed_mapping_geometry(),
            },
        )
        return BaseBedCalibrationJob(
            program=program,
            filename=filename,
            targets=targets,
            powered=powered,
            power_percent=power_percent if powered else 0.0,
            mark_size_mm=mark_size_mm,
        )

    def _base_bed_mapping_session(self, *, require_powered: bool) -> dict[str, Any]:
        session = read_json(self.base_bed_mapping_path, {})
        if (
            not isinstance(session, dict)
            or session.get("schema_version") != 2
            or session.get("kind") != "base_bed_mapping"
        ):
            raise CalibrationError("Prepare the fresh base-map 5x5 job before capture")
        targets = session.get("targets")
        if not isinstance(targets, list) or len(targets) != 25:
            raise CalibrationError("The saved base-map session does not contain all 25 targets")
        if session.get("generation_geometry") != self._base_bed_mapping_geometry():
            raise CalibrationError(
                "The work area, boundary margin, or laser offset changed after this base-map job was prepared"
            )
        try:
            mark_size_mm = float(session["mark_size_mm"])
            expected_targets = [
                target.to_dict()
                for target in base_bed_grid_targets(
                    self.settings.machine.work_area,
                    mark_size_mm=mark_size_mm,
                    boundary_margin_mm=self.settings.laser.boundary_margin_mm,
                )
            ]
            expected_key_sizes = {
                str(key): value for key, value in base_bed_grid_mark_sizes(mark_size_mm).items()
            }
        except (KeyError, TypeError, ValueError):
            raise CalibrationError("The saved base-map session geometry is invalid") from None
        if targets != expected_targets or session.get("keyed_mark_sizes_mm") != expected_key_sizes:
            raise CalibrationError("The saved base-map targets do not match the generated keyed grid")
        if require_powered and not self._session_boolean(
            session,
            "powered",
            "fresh base-map",
        ):
            raise CalibrationError(
                "The prepared base-map job has laser power 0%. Prepare and run the "
                "positive-power job before capture."
            )
        if require_powered:
            session = {
                **session,
                "execution_receipt": self._require_session_execution(
                    session,
                    "fresh base-map",
                ),
            }
        return session

    def _evaluate_base_bed_mapping_candidate(
        self,
        points: list[BedPoint],
        image_width: int,
        image_height: int,
    ) -> dict[str, Any]:
        candidate = self.bed.analyze_replacement(points, image_width, image_height)
        point_errors = np.asarray(candidate.get("point_errors_mm", []), dtype=np.float64)
        all_errors_finite = len(point_errors) == 25 and bool(np.isfinite(point_errors).all())
        all_inliers = int(candidate["inlier_count"]) == 25
        rms_ok = float(candidate["rms_error_mm"]) <= 0.50
        maximum_error = float(np.max(point_errors)) if all_errors_finite else float("inf")
        maximum_ok = maximum_error <= 0.80
        can_apply = bool(all_errors_finite and all_inliers and rms_ok and maximum_ok)
        candidate["candidate_max_error_mm"] = maximum_error
        candidate["can_apply"] = can_apply
        candidate["reason"] = (
            "All 25 keyed crosses passed the base-map fit gates"
            if can_apply
            else "The keyed grid was found, but its geometric fit did not pass all base-map gates"
        )
        return candidate

    def analyze_base_bed_mapping_image(self, image: np.ndarray) -> dict[str, Any]:
        session = self._base_bed_mapping_session(require_powered=True)
        detection = detect_keyed_crosshair_grid(image, list(session["targets"]))
        if not detection.get("detected"):
            return detection
        points = [
            BedPoint(
                image_x=float(item["image_x"]),
                image_y=float(item["image_y"]),
                machine_x=float(item["machine_x"]),
                machine_y=float(item["machine_y"]),
                label=str(item.get("label", ""))[:80],
            )
            for item in detection["points"]
        ]
        candidate = self._evaluate_base_bed_mapping_candidate(
            points,
            image.shape[1],
            image.shape[0],
        )
        return {**detection, "candidate": candidate}

    def capture_base_bed_mapping(self) -> tuple[np.ndarray, dict[str, Any]]:
        self._require_camera_calibration_ready()
        self._base_bed_mapping_session(require_powered=True)
        self._require_accepted_lens_calibration()
        with self.machine.temporary_stepper_hold():
            self.machine.prepare_photo_position()
            burst = self.precision_camera_burst(undistort=False)
        burst = self._prepare_camera_burst(burst, undistort=True)
        image = burst.sharpest_frame.copy()
        write_image_atomic(
            self.bed_reference_path,
            image,
            [cv2.IMWRITE_PNG_COMPRESSION, 3],
        )
        result = self.analyze_base_bed_mapping_image(image)
        result["precision_capture"] = {"camera": burst.diagnostics()}
        session = self._base_bed_mapping_session(require_powered=True)
        atomic_write_json(
            self.base_bed_mapping_path,
            {**session, "captured_at": time.time(), "detection": result},
        )
        return image, result

    def apply_base_bed_mapping(self, detection: dict[str, Any]) -> dict[str, Any]:
        session = self._base_bed_mapping_session(require_powered=True)
        candidate = detection.get("candidate") if isinstance(detection, dict) else None
        raw_points = detection.get("points") if isinstance(detection, dict) else None
        if not isinstance(candidate, dict) or not candidate.get("can_apply"):
            raise CalibrationError("This base-grid detection did not pass the application gates")
        if not isinstance(raw_points, list) or len(raw_points) != 25:
            raise CalibrationError("A complete 25-point base-grid detection is required")
        expected = {
            int(item["id"]): (float(item["machine_x"]), float(item["machine_y"]))
            for item in session["targets"]
        }
        observed = {
            int(item["id"]): (float(item["machine_x"]), float(item["machine_y"]))
            for item in raw_points
        }
        if observed != expected:
            raise CalibrationError("Detected base-grid coordinates do not match the prepared powered job")
        image = self.bed_reference()
        points = [
            BedPoint(
                image_x=float(item["image_x"]),
                image_y=float(item["image_y"]),
                machine_x=float(item["machine_x"]),
                machine_y=float(item["machine_y"]),
                label=str(item.get("label", ""))[:80],
            )
            for item in raw_points
        ]
        reviewed = self._evaluate_base_bed_mapping_candidate(points, image.shape[1], image.shape[0])
        if not reviewed.get("can_apply"):
            raise CalibrationError("The base-grid fit no longer passes the application gates")
        calibration = self.bed.replace_points_and_solve(
            points,
            image.shape[1],
            image.shape[0],
            provenance=self._bed_provenance(),
            axis_reversed_x=False,
            axis_reversed_y=False,
        )
        atomic_write_json(
            self.base_bed_mapping_path,
            {**session, "applied_at": time.time(), "applied_calibration": calibration.to_dict()},
        )
        return calibration.to_dict()

    def prepare_fine_registration_job(
        self,
        *,
        powered: bool,
        power_percent: float,
        mark_size_mm: float,
        speed_mm_min: float,
    ) -> FineRegistrationJob:
        calibration = self.bed.calibration
        if calibration is None:
            raise CalibrationError("Solve the bed mapping before fine registration")
        targets = registration_targets(
            self.settings.machine.work_area,
            mark_size_mm=mark_size_mm,
            boundary_margin_mm=self.settings.laser.boundary_margin_mm,
        )
        program = generate_registration_program(
            targets,
            self.settings.laser,
            self.settings.machine.work_area,
            mark_size_mm=mark_size_mm,
            power_percent=power_percent,
            powered=powered,
            speed_mm_min=speed_mm_min,
        )
        filename, _generated_path = _publish_unique_artifact(
            self.settings.app.data_dir / "generated",
            stem="fine-registration",
            suffix=".gcode",
            data=program.text.encode("utf-8"),
        )
        created_at = time.time()
        program_digest = self.machine.preflight_program(program.text).digest
        atomic_write_json(
            self.fine_registration_path,
            {
                "schema_version": 2,
                "created_at": created_at,
                "filename": filename,
                "program_digest": program_digest,
                "powered": powered,
                "power_percent": power_percent if powered else 0.0,
                "mark_size_mm": mark_size_mm,
                "speed_mm_min": speed_mm_min,
                "targets": [target.to_dict() for target in targets],
                **self._bed_mapping_session_fields(calibration),
            },
        )
        return FineRegistrationJob(
            program=program,
            filename=filename,
            targets=targets,
            powered=powered,
            power_percent=power_percent if powered else 0.0,
            mark_size_mm=mark_size_mm,
        )

    def prepare_accuracy_validation_job(
        self,
        *,
        powered: bool,
        power_percent: float,
        mark_size_mm: float,
        speed_mm_min: float,
    ) -> AccuracyValidationJob:
        calibration = self.bed.calibration
        if calibration is None:
            raise CalibrationError("Solve the bed mapping before accuracy validation")
        targets = accuracy_validation_targets(
            self.settings.machine.work_area,
            mark_size_mm=mark_size_mm,
            boundary_margin_mm=self.settings.laser.boundary_margin_mm,
        )
        program = generate_registration_program(
            targets,
            self.settings.laser,
            self.settings.machine.work_area,
            mark_size_mm=mark_size_mm,
            power_percent=power_percent,
            powered=powered,
            speed_mm_min=speed_mm_min,
            design_name="accuracy-validation-holdout-crosses",
        )
        filename, _generated_path = _publish_unique_artifact(
            self.settings.app.data_dir / "generated",
            stem="accuracy-validation",
            suffix=".gcode",
            data=program.text.encode("utf-8"),
        )
        created_at = time.time()
        program_digest = self.machine.preflight_program(program.text).digest
        atomic_write_json(
            self.accuracy_validation_path,
            {
                "schema_version": 2,
                "created_at": created_at,
                "filename": filename,
                "program_digest": program_digest,
                "powered": powered,
                "power_percent": power_percent if powered else 0.0,
                "mark_size_mm": mark_size_mm,
                "speed_mm_min": speed_mm_min,
                "targets": [target.to_dict() for target in targets],
                **self._bed_mapping_session_fields(calibration),
            },
        )
        return AccuracyValidationJob(
            program=program,
            filename=filename,
            targets=targets,
            powered=powered,
            power_percent=power_percent if powered else 0.0,
            mark_size_mm=mark_size_mm,
        )

    def prepare_dense_calibration_job(
        self,
        *,
        powered: bool,
        power_percent: float,
        mark_size_mm: float,
        speed_mm_min: float,
        validation: bool = False,
        confirmation: bool = False,
    ) -> AccuracyValidationJob:
        calibration = self.bed.calibration
        if calibration is None:
            raise CalibrationError("Solve the bed mapping before dense calibration")
        if (validation or confirmation) and calibration.residual_mesh is None:
            raise CalibrationError("Apply a dense local correction before its 4×4 validation")
        if not validation and not confirmation and calibration.residual_mesh is not None:
            raise CalibrationError("Reset the existing local correction mesh before fitting another")
        if confirmation:
            if calibration.residual_mesh is None or calibration.residual_mesh.refinement_count != 1:
                raise CalibrationError("Apply the reviewed validation refinement before confirmation")
            target_factory = dense_confirmation_targets
        else:
            target_factory = dense_validation_targets if validation else dense_mesh_targets
        targets = target_factory(
            self.settings.machine.work_area,
            mark_size_mm=mark_size_mm,
            boundary_margin_mm=self.settings.laser.boundary_margin_mm,
        )
        design_name = (
            "dense-mesh-confirmation-crosses"
            if confirmation
            else "dense-mesh-validation-crosses"
            if validation
            else "dense-local-correction-crosses"
        )
        program = generate_registration_program(
            targets,
            self.settings.laser,
            self.settings.machine.work_area,
            mark_size_mm=mark_size_mm,
            power_percent=power_percent,
            powered=powered,
            speed_mm_min=speed_mm_min,
            design_name=design_name,
        )
        filename, _generated_path = _publish_unique_artifact(
            self.settings.app.data_dir / "generated",
            stem=design_name,
            suffix=".gcode",
            data=program.text.encode("utf-8"),
        )
        session_path = (
            self.dense_confirmation_path
            if confirmation
            else self.dense_validation_path
            if validation
            else self.dense_calibration_path
        )
        created_at = time.time()
        program_digest = self.machine.preflight_program(program.text).digest
        atomic_write_json(
            session_path,
            {
                "schema_version": 2,
                "created_at": created_at,
                "filename": filename,
                "program_digest": program_digest,
                "powered": powered,
                "validation": validation,
                "confirmation": confirmation,
                "mark_size_mm": mark_size_mm,
                "targets": [target.to_dict() for target in targets],
                "image_to_machine": calibration.image_to_machine.tolist(),
                "residual_mesh_created_at": (
                    None if calibration.residual_mesh is None else calibration.residual_mesh.created_at
                ),
            },
        )
        return AccuracyValidationJob(
            program=program,
            filename=filename,
            targets=targets,
            powered=powered,
            power_percent=power_percent if powered else 0.0,
            mark_size_mm=mark_size_mm,
            display_name=(
                "Dense mesh confirmation"
                if confirmation
                else "Dense mesh validation"
                if validation
                else "Dense local correction"
            ),
        )

    def analyze_dense_calibration_image(
        self,
        image: np.ndarray,
        *,
        validation: bool = False,
        confirmation: bool = False,
    ) -> dict[str, Any]:
        return self._analyze_dense_calibration_capture(
            image,
            (image,),
            None,
            validation=validation,
            confirmation=confirmation,
        )

    def _fiducial_search_radius_px(
        self,
        expected_points: list[dict[str, Any]],
        *,
        radius_mm: float,
    ) -> int:
        """Project a machine-space association gate into the camera image."""
        if not math.isfinite(radius_mm) or radius_mm <= 0:
            raise CalibrationError("Fiducial search radius must be finite and positive")
        radii: list[float] = []
        for point in expected_points:
            machine_x = float(point["machine_x"])
            machine_y = float(point["machine_y"])
            image_x = float(point["image_x"])
            image_y = float(point["image_y"])
            for dx, dy in (
                (radius_mm, 0.0),
                (-radius_mm, 0.0),
                (0.0, radius_mm),
                (0.0, -radius_mm),
            ):
                projected_x, projected_y = self.bed.mm_to_image(
                    machine_x + dx,
                    machine_y + dy,
                )
                radii.append(
                    math.hypot(projected_x - image_x, projected_y - image_y)
                )
        if not radii or not all(math.isfinite(value) for value in radii):
            raise CalibrationError("Could not project the fiducial search radius")
        return max(1, int(math.ceil(max(radii))))

    def capture_dense_calibration(
        self,
        *,
        validation: bool = False,
        confirmation: bool = False,
        home_first: bool = True,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        self._require_camera_calibration_ready()
        session_path = (
            self.dense_confirmation_path
            if confirmation
            else self.dense_validation_path
            if validation
            else self.dense_calibration_path
        )
        self._require_session_execution(
            read_json(session_path, {}),
            "dense confirmation"
            if confirmation
            else "dense validation"
            if validation
            else "dense calibration",
        )
        with self.machine.temporary_stepper_hold():
            if home_first:
                self.machine.prepare_photo_position()
            burst = self.precision_camera_burst(undistort=False)
        burst = self._prepare_camera_burst(burst, undistort=True)
        analysis = self._analyze_dense_calibration_capture(
            burst.sharpest_frame,
            burst.frames,
            burst.diagnostics(),
            validation=validation,
            confirmation=confirmation,
        )
        selected = analysis.get("precision_capture", {}).get("aggregation", {}).get("selected_frame_index")
        image = burst.frames[int(selected)].copy() if selected is not None else burst.sharpest_frame
        return image, analysis

    def _analyze_dense_calibration_capture(
        self,
        image: np.ndarray,
        images: tuple[np.ndarray, ...],
        camera_diagnostics: dict[str, Any] | None,
        *,
        validation: bool,
        confirmation: bool,
    ) -> dict[str, Any]:
        calibration = self.bed.calibration
        if calibration is None:
            raise CalibrationError("Solve the bed mapping before dense calibration")
        if validation and confirmation:
            raise CalibrationError("Dense capture cannot be both validation and confirmation")
        session_path = (
            self.dense_confirmation_path
            if confirmation
            else self.dense_validation_path
            if validation
            else self.dense_calibration_path
        )
        session = read_json(session_path, {})
        targets = session.get("targets") if isinstance(session, dict) else None
        expected_count = 16 if validation or confirmation else 25
        if not isinstance(targets, list) or len(targets) != expected_count:
            grid_name = "confirmation" if confirmation else "validation" if validation else "5×5 fit"
            raise CalibrationError(f"Prepare the matching dense-grid {grid_name} job before capture")
        if (
            confirmation
            and session.get("confirmation") is True
            and session.get("validation") is True
            and str(session.get("filename", "")).startswith("dense-mesh-confirmation-crosses-")
        ):
            # Repair confirmation sessions produced by the former desktop path,
            # which incorrectly set both mutually exclusive flags. The filename,
            # dedicated file, target count, and confirmation flag bind this repair
            # narrowly enough to preserve the already-burned confirmation marks.
            session = {**session, "validation": False}
            atomic_write_json(session_path, session)
        session_validation = self._session_boolean(
            session,
            "validation",
            "dense-grid",
        )
        session_confirmation = self._session_boolean(
            session,
            "confirmation",
            "dense-grid",
        )
        if session_validation != validation or session_confirmation != confirmation:
            raise CalibrationError("The saved dense-grid session does not match this capture button")
        if not self._session_boolean(session, "powered", "dense-grid"):
            raise CalibrationError("Run the powered dense-grid job before analyzing marks")
        session = {
            **session,
            "execution_receipt": self._require_session_execution(
                session,
                "dense confirmation"
                if confirmation
                else "dense validation"
                if validation
                else "dense calibration",
            ),
        }
        current_mesh_created_at = None if calibration.residual_mesh is None else calibration.residual_mesh.created_at
        if session.get("residual_mesh_created_at") != current_mesh_created_at:
            if validation and not confirmation:
                raise CalibrationError(
                    "These 16 interstitial marks belong to the mesh state from before "
                    "the validation refinement was applied. Because those same marks "
                    "were used to calculate the refinement, they cannot independently "
                    "confirm the refined map. Use 'Home / park, capture and score shifted "
                    "confirmation' to check the fresh shifted marks instead."
                )
            raise CalibrationError(
                "The local correction changed after this mark job was prepared, so its "
                "coordinates no longer match the active map. Prepare and run the matching "
                "powered mark job again before capturing it."
            )
        expected = []
        for item in targets:
            machine_x, machine_y = float(item["machine_x"]), float(item["machine_y"])
            image_x, image_y = self.bed.mm_to_image(machine_x, machine_y)
            expected.append({**item, "image_x": image_x, "image_y": image_y})
        search_radius_px = self._fiducial_search_radius_px(
            expected,
            radius_mm=2.0,
        )
        if camera_diagnostics is None:
            detection = detect_crosshairs_near(image, expected, search_radius_px=search_radius_px)
        else:
            profile = self.settings.camera.precision_capture
            detection = detect_crosshairs_burst(
                images,
                expected,
                search_radius_px=search_radius_px,
                minimum_valid_frames=profile.minimum_valid_frames,
                mad_multiplier=profile.mad_multiplier,
                outlier_floor_px=profile.outlier_floor_px,
                max_jitter_rms_px=profile.max_jitter_rms_px,
                coordinate_strategy=profile.coordinate_strategy,
                consensus_frames=profile.consensus_frames,
                frame_quality_scores=tuple(camera_diagnostics["sharpness_scores"]),
            )
            selected = detection.get("capture_diagnostics", {}).get("selected_frame_index")
            if selected is not None:
                image = images[int(selected)].copy()
        precision_capture = (
            {
                "camera": camera_diagnostics,
                "aggregation": detection.get("capture_diagnostics", {}),
            }
            if camera_diagnostics is not None
            else None
        )
        if not detection.get("detected"):
            failed_session = dict(session)
            failed_session.update(
                {
                    "captured_at": time.time(),
                    "detection_failure": detection.get("reason"),
                    "precision_capture": precision_capture,
                }
            )
            failed_session.pop("analysis", None)
            failed_session.pop("measurements", None)
            atomic_write_json(session_path, failed_session)
            return {**detection, "precision_capture": precision_capture}
        if confirmation:
            excessive = [
                int(point["id"])
                for point in detection.get("points", [])
                if float(point.get("shift_px", float("inf"))) > 10.0
            ]
            if excessive:
                return {
                    "detected": False,
                    "reason": (
                        "Shifted confirmation rejected detections too far from their "
                        f"predicted positions: {excessive}. Old neighboring marks may be visible."
                    ),
                    "points": detection.get("points", []),
                    "precision_capture": precision_capture,
                }
        measurements = []
        for point in detection["points"]:
            observed_x, observed_y = self.bed.image_to_mm(float(point["image_x"]), float(point["image_y"]))
            measurements.append(
                {
                    "id": int(point["id"]),
                    "machine_x": float(point["machine_x"]),
                    "machine_y": float(point["machine_y"]),
                    "observed_x": observed_x,
                    "observed_y": observed_y,
                    "image_x": float(point["image_x"]),
                    "image_y": float(point["image_y"]),
                    "score": float(point["score"]),
                    "seed_shift_px": float(point["shift_px"]),
                }
            )
        analysis = (
            self._analyze_dense_validation(measurements)
            if validation or confirmation
            else analyze_dense_mesh_measurements(measurements)
        )
        if validation and not confirmation and calibration.residual_mesh is not None:
            refinement = analyze_dense_validation_refinement(
                measurements,
                calibration.residual_mesh.x_nodes_mm,
                calibration.residual_mesh.y_nodes_mm,
            )
            refinement["base_mesh_created_at"] = calibration.residual_mesh.created_at
            if calibration.residual_mesh.refinement_count != 0:
                refinement.update(
                    {
                        "can_refine": False,
                        "reason": "This mesh has already been refined; use shifted confirmation",
                    }
                )
            analysis["refinement"] = refinement
        analysis = self._seal_analysis(analysis)
        updated = dict(session)
        reviewed_measurements = list(analysis.get("measurements", measurements))
        updated.update(
            {
                "captured_at": time.time(),
                "measurements": reviewed_measurements,
                "analysis": analysis,
                "precision_capture": precision_capture,
            }
        )
        atomic_write_json(session_path, updated)
        return {
            "detected": True,
            "points": detection["points"],
            "measurements": reviewed_measurements,
            "analysis": analysis,
            "precision_capture": precision_capture,
        }

    @staticmethod
    def _analyze_dense_validation(measurements: list[dict[str, Any]]) -> dict[str, Any]:
        if len(measurements) != 16:
            raise CalibrationError("Dense validation requires all 16 interstitial marks")
        residuals = np.asarray(
            [[item["observed_x"] - item["machine_x"], item["observed_y"] - item["machine_y"]] for item in measurements],
            dtype=np.float64,
        )
        errors = np.linalg.norm(residuals, axis=1)
        rms, maximum = float(np.sqrt(np.mean(errors**2))), float(np.max(errors))
        output = []
        for item, residual, error in zip(measurements, residuals, errors, strict=True):
            output.append(
                {**item, "error_x_mm": float(residual[0]), "error_y_mm": float(residual[1]), "error_mm": float(error)}
            )
        return {
            "classification": "pass" if rms <= 0.30 and maximum <= 0.60 else "fail",
            "passed": rms <= 0.30 and maximum <= 0.60,
            "rms_error_mm": rms,
            "max_error_mm": maximum,
            "rms_limit_mm": 0.30,
            "max_limit_mm": 0.60,
            "measurements": output,
        }

    def apply_dense_calibration(self, analysis: dict[str, Any]) -> dict[str, Any]:
        analysis = self._require_current_analysis(
            self.dense_calibration_path,
            analysis,
            "dense-calibration",
        )
        if not analysis.get("can_apply"):
            raise CalibrationError("Dense-grid analysis did not pass its application gates")
        return self.bed.apply_residual_mesh(
            np.asarray(analysis["x_nodes_mm"]),
            np.asarray(analysis["y_nodes_mm"]),
            np.asarray(analysis["corrections_mm"]),
            fit_rms_mm=float(analysis["fit_rms_mm"]),
            fit_max_mm=float(analysis["fit_max_mm"]),
        ).to_dict()

    def reset_dense_calibration(self) -> dict[str, Any]:
        return self.bed.reset_residual_mesh().to_dict()

    def apply_dense_validation_refinement(self, analysis: dict[str, Any]) -> dict[str, Any]:
        analysis = self._require_current_analysis(
            self.dense_validation_path,
            analysis,
            "dense-validation",
        )
        refinement = analysis.get("refinement")
        if not isinstance(refinement, dict) or not refinement.get("can_refine"):
            raise CalibrationError("This validation result did not pass the refinement gates")
        return self.bed.refine_residual_mesh(
            np.asarray(refinement["delta_corrections_mm"], dtype=np.float64),
            analyzed_mesh_created_at=float(refinement["base_mesh_created_at"]),
            predicted_rms_mm=float(refinement["predicted_rms_mm"]),
            predicted_max_mm=float(refinement["predicted_max_mm"]),
        ).to_dict()

    def analyze_accuracy_validation_image(self, image: np.ndarray) -> dict[str, Any]:
        return self._analyze_accuracy_validation_capture(image, (image,), None)

    def capture_accuracy_validation(
        self,
        *,
        home_first: bool = True,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        self._require_camera_calibration_ready()
        calibration = self.bed.calibration
        if calibration is None:
            raise CalibrationError("Solve the bed mapping before accuracy validation")
        session = read_json(self.accuracy_validation_path, {})
        self._require_session_execution(session, "accuracy-validation")
        self._require_session_bed_mapping(
            session,
            calibration,
            "accuracy-validation",
        )
        with self.machine.temporary_stepper_hold():
            if home_first:
                self.machine.prepare_photo_position()
            burst = self.precision_camera_burst(undistort=False)
        burst = self._prepare_camera_burst(burst, undistort=True)
        analysis = self.analyze_accuracy_validation_burst(burst)
        selected = analysis.get("precision_capture", {}).get("aggregation", {}).get("selected_frame_index")
        image = burst.frames[int(selected)].copy() if selected is not None else burst.sharpest_frame
        return image, analysis

    def analyze_accuracy_validation_burst(
        self,
        burst: FrameBurst,
    ) -> dict[str, Any]:
        return self._analyze_accuracy_validation_capture(
            burst.sharpest_frame,
            burst.frames,
            burst.diagnostics(),
        )

    def _analyze_accuracy_validation_capture(
        self,
        image: np.ndarray,
        images: tuple[np.ndarray, ...],
        camera_diagnostics: dict[str, Any] | None,
    ) -> dict[str, Any]:
        calibration = self.bed.calibration
        if calibration is None:
            raise CalibrationError("Solve the bed mapping before accuracy validation")
        session = read_json(self.accuracy_validation_path, {})
        raw_targets = session.get("targets") if isinstance(session, dict) else None
        if not isinstance(raw_targets, list) or len(raw_targets) != 5:
            raise CalibrationError("Prepare the accuracy-validation mark job before capturing its marks")
        if not self._session_boolean(session, "powered", "accuracy-validation"):
            raise CalibrationError(
                "The prepared validation job has laser power 0%. After reviewing it, "
                "prepare and run the positive-power holdout job before analyzing marks."
            )
        session = {
            **session,
            "execution_receipt": self._require_session_execution(
                session,
                "accuracy-validation",
            ),
        }
        self._require_session_bed_mapping(
            session,
            calibration,
            "accuracy-validation",
        )

        expected_points = []
        for item in raw_targets:
            machine_x = float(item["machine_x"])
            machine_y = float(item["machine_y"])
            image_x, image_y = self.bed.mm_to_image(machine_x, machine_y)
            expected_points.append(
                {
                    "id": int(item["id"]),
                    "image_x": image_x,
                    "image_y": image_y,
                    "machine_x": machine_x,
                    "machine_y": machine_y,
                }
            )
        search_radius_px = self._fiducial_search_radius_px(
            expected_points,
            radius_mm=2.0,
        )
        if camera_diagnostics is None:
            detection = detect_crosshairs_near(
                image,
                expected_points,
                search_radius_px=search_radius_px,
            )
        else:
            profile = self.settings.camera.precision_capture
            detection = detect_crosshairs_burst(
                images,
                expected_points,
                search_radius_px=search_radius_px,
                minimum_valid_frames=profile.minimum_valid_frames,
                mad_multiplier=profile.mad_multiplier,
                outlier_floor_px=profile.outlier_floor_px,
                max_jitter_rms_px=profile.max_jitter_rms_px,
                coordinate_strategy=profile.coordinate_strategy,
                consensus_frames=profile.consensus_frames,
                frame_quality_scores=tuple(camera_diagnostics["sharpness_scores"]),
            )
            selected = detection.get("capture_diagnostics", {}).get("selected_frame_index")
            if selected is not None:
                image = images[int(selected)].copy()
        capture_diagnostics = (
            {
                "camera": camera_diagnostics,
                "aggregation": detection.get("capture_diagnostics", {}),
            }
            if camera_diagnostics is not None
            else None
        )
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        capture_path = self.settings.app.data_dir / "captures" / f"accuracy-validation-{timestamp}.png"
        write_image_atomic(
            capture_path,
            image,
            [cv2.IMWRITE_PNG_COMPRESSION, 3],
        )
        if not detection.get("detected"):
            updated_session = dict(session)
            updated_session.update(
                {
                    "capture_path": str(capture_path),
                    "captured_at": time.time(),
                    "detection_confidence": detection.get("confidence"),
                    "detection_failure": detection.get("reason"),
                    "precision_capture": capture_diagnostics,
                }
            )
            updated_session.pop("analysis", None)
            updated_session.pop("measurements", None)
            atomic_write_json(self.accuracy_validation_path, updated_session)
            return {
                **detection,
                "capture_path": str(capture_path),
                "precision_capture": capture_diagnostics,
            }

        measurements = []
        for point in detection["points"]:
            observed_x, observed_y = self.bed.image_to_mm(float(point["image_x"]), float(point["image_y"]))
            measurement = {
                "id": int(point["id"]),
                "machine_x": float(point["machine_x"]),
                "machine_y": float(point["machine_y"]),
                "observed_x": observed_x,
                "observed_y": observed_y,
                "image_x": float(point["image_x"]),
                "image_y": float(point["image_y"]),
                "score": float(point["score"]),
                "seed_shift_px": float(point["shift_px"]),
            }
            for key in (
                "sample_count",
                "inlier_count",
                "outlier_count",
                "jitter_rms_px",
                "jitter_max_px",
                "mad_px",
            ):
                if key in point:
                    measurement[key] = point[key]
            measurements.append(measurement)
        analysis = analyze_accuracy_measurements(measurements)
        analysis["precision_capture"] = capture_diagnostics
        updated_session = dict(session)
        updated_session.update(
            {
                "capture_path": str(capture_path),
                "captured_at": time.time(),
                "detection_confidence": detection.get("confidence"),
                "analysis": analysis,
                "measurements": analysis["measurements"],
                "precision_capture": capture_diagnostics,
            }
        )
        atomic_write_json(self.accuracy_validation_path, updated_session)
        return {
            "detected": True,
            "confidence": detection.get("confidence"),
            "points": detection["points"],
            "measurements": analysis["measurements"],
            "analysis": analysis,
            "capture_path": str(capture_path),
            "precision_capture": capture_diagnostics,
        }

    def analyze_fine_registration_image(self, image: np.ndarray) -> dict[str, Any]:
        return self._analyze_fine_registration_capture(image, (image,), None)

    def capture_fine_registration(
        self,
        *,
        home_first: bool = True,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        self._require_camera_calibration_ready()
        calibration = self.bed.calibration
        if calibration is None:
            raise CalibrationError("Solve the bed mapping before fine registration")
        session = read_json(self.fine_registration_path, {})
        self._require_session_execution(session, "fine-registration")
        self._require_session_bed_mapping(
            session,
            calibration,
            "fine-registration",
        )
        with self.machine.temporary_stepper_hold():
            if home_first:
                self.machine.prepare_photo_position()
            burst = self.precision_camera_burst(undistort=False)
        burst = self._prepare_camera_burst(burst, undistort=True)
        analysis = self.analyze_fine_registration_burst(burst)
        selected = analysis.get("precision_capture", {}).get("aggregation", {}).get("selected_frame_index")
        image = burst.frames[int(selected)].copy() if selected is not None else burst.sharpest_frame
        return image, analysis

    def analyze_fine_registration_burst(
        self,
        burst: FrameBurst,
    ) -> dict[str, Any]:
        return self._analyze_fine_registration_capture(
            burst.sharpest_frame,
            burst.frames,
            burst.diagnostics(),
        )

    def _analyze_fine_registration_capture(
        self,
        image: np.ndarray,
        images: tuple[np.ndarray, ...],
        camera_diagnostics: dict[str, Any] | None,
    ) -> dict[str, Any]:
        calibration = self.bed.calibration
        if calibration is None:
            raise CalibrationError("Solve the bed mapping before fine registration")
        session = read_json(self.fine_registration_path, {})
        raw_targets = session.get("targets") if isinstance(session, dict) else None
        if not isinstance(raw_targets, list) or len(raw_targets) < 4:
            raise CalibrationError("Prepare the fine-registration mark job before capturing its marks")
        if not self._session_boolean(session, "powered", "fine-registration"):
            raise CalibrationError(
                "The prepared registration job has laser power 0%. After reviewing it, "
                "prepare and run the positive-power mark job before analyzing marks."
            )
        session = {
            **session,
            "execution_receipt": self._require_session_execution(
                session,
                "fine-registration",
            ),
        }
        self._require_session_bed_mapping(
            session,
            calibration,
            "fine-registration",
        )
        expected_points = []
        for item in raw_targets:
            machine_x = float(item["machine_x"])
            machine_y = float(item["machine_y"])
            image_x, image_y = self.bed.mm_to_image(machine_x, machine_y)
            expected_points.append(
                {
                    "id": int(item["id"]),
                    "image_x": image_x,
                    "image_y": image_y,
                    "machine_x": machine_x,
                    "machine_y": machine_y,
                }
            )
        if camera_diagnostics is None:
            detection = detect_crosshairs_near(
                image,
                expected_points,
                search_radius_px=55,
            )
        else:
            profile = self.settings.camera.precision_capture
            detection = detect_crosshairs_burst(
                images,
                expected_points,
                search_radius_px=55,
                minimum_valid_frames=profile.minimum_valid_frames,
                mad_multiplier=profile.mad_multiplier,
                outlier_floor_px=profile.outlier_floor_px,
                max_jitter_rms_px=profile.max_jitter_rms_px,
                coordinate_strategy=profile.coordinate_strategy,
                consensus_frames=profile.consensus_frames,
                frame_quality_scores=tuple(camera_diagnostics["sharpness_scores"]),
            )
            selected = detection.get("capture_diagnostics", {}).get("selected_frame_index")
            if selected is not None:
                image = images[int(selected)].copy()
        capture_diagnostics = (
            {
                "camera": camera_diagnostics,
                "aggregation": detection.get("capture_diagnostics", {}),
            }
            if camera_diagnostics is not None
            else None
        )
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        capture_path = self.settings.app.data_dir / "captures" / f"fine-registration-{timestamp}.png"
        write_image_atomic(
            capture_path,
            image,
            [cv2.IMWRITE_PNG_COMPRESSION, 3],
        )
        if not detection.get("detected"):
            updated_session = dict(session)
            updated_session.update(
                {
                    "capture_path": str(capture_path),
                    "captured_at": time.time(),
                    "detection_confidence": detection.get("confidence"),
                    "detection_failure": detection.get("reason"),
                    "precision_capture": capture_diagnostics,
                }
            )
            updated_session.pop("analysis", None)
            updated_session.pop("measurements", None)
            atomic_write_json(self.fine_registration_path, updated_session)
            return {
                **detection,
                "capture_path": str(capture_path),
                "precision_capture": capture_diagnostics,
            }

        measurements = []
        for point in detection["points"]:
            observed_x, observed_y = self.bed.image_to_mm(float(point["image_x"]), float(point["image_y"]))
            measurement = {
                "id": int(point["id"]),
                "machine_x": float(point["machine_x"]),
                "machine_y": float(point["machine_y"]),
                "observed_x": observed_x,
                "observed_y": observed_y,
                "image_x": float(point["image_x"]),
                "image_y": float(point["image_y"]),
                "score": float(point["score"]),
                "seed_shift_px": float(point["shift_px"]),
            }
            for key in (
                "sample_count",
                "inlier_count",
                "outlier_count",
                "jitter_rms_px",
                "jitter_max_px",
                "mad_px",
            ):
                if key in point:
                    measurement[key] = point[key]
            measurements.append(measurement)
        all_measurements = analyze_registration_measurements(measurements)["measurements"]
        suggested_exclusions = suggested_registration_exclusions(all_measurements)
        if len(suggested_exclusions) > 2:
            analysis = analyze_registration_measurements(all_measurements)
            analysis.update(
                {
                    "classification": "invalid",
                    "can_apply_translation": False,
                    "reason": (
                        "More than two cross detections are invalid; recapture the marks instead of excluding them"
                    ),
                    "available_point_count": len(all_measurements),
                    "excluded_ids": [],
                }
            )
        else:
            analysis = review_registration_measurements(all_measurements, suggested_exclusions)
            analysis["full_map_refinement"] = self._analyze_full_map_refinement(all_measurements, suggested_exclusions)
        analysis["precision_capture"] = capture_diagnostics
        analysis = self._seal_analysis(analysis)
        updated_session = dict(session)
        updated_session.update(
            {
                "capture_path": str(capture_path),
                "captured_at": time.time(),
                "detection_confidence": detection.get("confidence"),
                "analysis": analysis,
                "measurements": all_measurements,
                "precision_capture": capture_diagnostics,
            }
        )
        atomic_write_json(self.fine_registration_path, updated_session)
        return {
            "detected": True,
            "confidence": detection.get("confidence"),
            "points": detection["points"],
            "measurements": all_measurements,
            "analysis": analysis,
            "capture_path": str(capture_path),
            "precision_capture": capture_diagnostics,
        }

    def review_fine_registration_measurements(
        self,
        measurements: list[dict[str, Any]],
        excluded_ids: list[int],
    ) -> dict[str, Any]:
        analysis = review_registration_measurements(measurements, excluded_ids)
        analysis["full_map_refinement"] = self._analyze_full_map_refinement(measurements, excluded_ids)
        analysis = self._seal_analysis(analysis)
        session = read_json(self.fine_registration_path, {})
        if isinstance(session, dict):
            updated_session = dict(session)
            updated_session["analysis"] = analysis
            updated_session["measurements"] = measurements
            atomic_write_json(self.fine_registration_path, updated_session)
        return analysis

    def _analyze_full_map_refinement(
        self,
        measurements: list[dict[str, Any]],
        excluded_ids: list[int],
    ) -> dict[str, Any]:
        calibration = self.bed.calibration
        if calibration is None:
            raise CalibrationError("Bed calibration has not been solved")
        result = analyze_homography_refinement(
            measurements,
            excluded_ids,
            calibration.image_to_machine,
            self.settings.machine.work_area,
        )
        if calibration.refinement_base is not None:
            result.update(
                {
                    "classification": "invalid",
                    "can_apply_full_map": False,
                    "reason": ("A full-bed refinement is already applied; reset it before reviewing another"),
                }
            )
        elif abs(calibration.registration_x_mm) > 1e-12 or abs(calibration.registration_y_mm) > 1e-12:
            result.update(
                {
                    "classification": "invalid",
                    "can_apply_full_map": False,
                    "reason": ("Reset the fine-registration translation before applying a full-bed refinement"),
                }
            )
        return result

    def apply_fine_registration(self, analysis: dict[str, Any]) -> dict[str, Any]:
        analysis = self._require_current_analysis(
            self.fine_registration_path,
            analysis,
            "fine-registration",
        )
        if not analysis.get("can_apply_translation"):
            raise CalibrationError("This result is not a safe global translation; do not apply it")
        calibration = self.bed.apply_registration_translation(
            float(analysis["correction_x_mm"]),
            float(analysis["correction_y_mm"]),
            analysis=analysis,
        )
        return calibration.to_dict()

    def apply_fine_registration_homography(self, analysis: dict[str, Any]) -> dict[str, Any]:
        analysis = self._require_current_analysis(
            self.fine_registration_path,
            analysis,
            "fine-registration",
        )
        refinement = analysis.get("full_map_refinement")
        if not isinstance(refinement, dict) or not refinement.get("can_apply_full_map"):
            raise CalibrationError("This capture did not pass the reviewed full-bed refinement gates")
        calibration = self.bed.apply_registration_homography(
            np.asarray(refinement["image_to_machine"], dtype=np.float64),
            analysis=refinement,
        )
        return calibration.to_dict()

    def reset_fine_registration(self) -> dict[str, Any]:
        return self.bed.reset_registration_translation().to_dict()

    def reset_fine_registration_homography(self) -> dict[str, Any]:
        return self.bed.reset_registration_homography().to_dict()

    def replace_bed_points(self, payload: dict[str, Any]) -> dict[str, Any]:
        raw_points = payload.get("points")
        if not isinstance(raw_points, list) or len(raw_points) < self.settings.calibration.bed.minimum_points:
            raise CalibrationError("Not enough detected bed points to accept")
        points = [
            BedPoint(
                image_x=float(item["image_x"]),
                image_y=float(item["image_y"]),
                machine_x=float(item["machine_x"]),
                machine_y=float(item["machine_y"]),
                label=str(item.get("label", ""))[:80],
            )
            for item in raw_points
        ]
        self.bed.replace_points(points)
        return self.bed.status()

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
        missing = [
            key
            for key in ("center_x_mm", "center_y_mm", "width_mm", "height_mm")
            if key not in payload
        ]
        if missing:
            raise ValueError(f"placement is missing required field {missing[0]}")
        return DesignPlacement(
            center_x_mm=_payload_finite_number(payload, "center_x_mm", 0.0),
            center_y_mm=_payload_finite_number(payload, "center_y_mm", 0.0),
            width_mm=_payload_finite_number(payload, "width_mm", 0.0),
            height_mm=_payload_finite_number(payload, "height_mm", 0.0),
            rotation_deg=_payload_finite_number(payload, "rotation_deg", 0.0),
            mirror_x=_payload_boolean(payload, "mirror_x", False),
            mirror_y=_payload_boolean(payload, "mirror_y", False),
        )

    def _toolpath_options(self, payload: dict[str, Any]) -> ToolpathOptions:
        laser = self.settings.laser
        return ToolpathOptions(
            power_mode=_payload_string(
                payload,
                "power_mode",
                laser.power_mode,
            ).upper(),
            power=_payload_integer(payload, "power", laser.default_power),
            power_max=laser.power_max,
            travel_feed_mm_min=_payload_finite_number(
                payload,
                "travel_feed_mm_min",
                laser.travel_feed_mm_min,
            ),
            engrave_feed_mm_min=_payload_finite_number(
                payload,
                "engrave_feed_mm_min",
                laser.engrave_feed_mm_min,
            ),
            boundary_margin_mm=laser.boundary_margin_mm,
            spot_offset_x_mm=laser.spot_offset_x_mm,
            spot_offset_y_mm=laser.spot_offset_y_mm,
            optimize_order=_payload_boolean(payload, "optimize_order", True),
            include_return_move=laser.return_to_photo_position,
            return_x_mm=self.settings.machine.photo_x,
            return_y_mm=self.settings.machine.photo_y,
            start_x_mm=self.settings.machine.photo_x,
            start_y_mm=self.settings.machine.photo_y,
        )

    def generate_gcode(self, payload: dict[str, Any]) -> dict[str, Any]:
        svg_text = payload.get("svg")
        if type(svg_text) is not str:
            raise ValueError("svg must be a JSON string")
        raw_placement = payload.get("placement")
        if type(raw_placement) is not dict:
            raise ValueError("placement must be a JSON object")
        raw_toolpath = payload.get("toolpath", {})
        if type(raw_toolpath) is not dict:
            raise ValueError("toolpath must be a JSON object")
        name = payload.get("name", "design.svg")
        if type(name) is not str:
            raise ValueError("name must be a JSON string")
        geometry = parse_svg(svg_text)
        placement = self._placement(raw_placement)
        options = self._toolpath_options(raw_toolpath)
        program = generate_vector_gcode(
            geometry,
            placement,
            options,
            self.settings.machine.work_area,
            design_name=name,
        )
        safe_base = (
            _SAFE_NAME_RE.sub("-", Path(name).stem).strip("-.")[:80] or "design"
        )
        filename, _path = _publish_unique_artifact(
            self.settings.app.data_dir / "generated",
            stem=safe_base,
            suffix=".gcode",
            data=program.text.encode("utf-8"),
        )
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
        lens_status = self.lens.status()
        lens_model = lens_status.get("model")
        lens_model_id = (
            str(lens_model["model_id"])
            if isinstance(lens_model, dict) and lens_model.get("model_id")
            else None
        )
        return {
            "version": __version__,
            "settings": self.settings.public_dict(),
            "camera": camera_status,
            "camera_calibration": self.camera_calibration_readiness(),
            "calibration_profile": self.calibration_profiles.status(),
            "lens": lens_status,
            "bed": self.bed_status(lens_model_id=lens_model_id),
            "simulation_workspace_frame": self.simulation_workspace_frame_status(),
            "machine": self.machine.status(),
            "devices": {
                "cameras": list_video_devices(),
                "serial_ports": list_serial_ports(),
            },
        }
