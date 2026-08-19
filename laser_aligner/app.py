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
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import __version__
from .calibration.bed import (
    BedCalibration,
    BedMapper,
    BedPoint,
    _coordinate_frame_transform,
)
from .calibration.lens import LensCalibrator
from .calibration.profiles import CalibrationProfileStore, signature_from_camera_settings
from .calibration.reach import FixtureReachStore
from .calibration.registration import (
    AccuracyValidationJob,
    BaseBedCalibrationJob,
    FineRegistrationJob,
    RegistrationTarget,
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
    points_fit_support,
    registration_targets,
    review_registration_measurements,
    suggested_registration_exclusions,
    targets_fit_support,
)
from .calibration.support import (
    HoneycombCoordinateFrame,
    HoneycombSupportReference,
    HoneycombSupportStore,
)
from .camera.service import (
    CameraService,
    FrameBurst,
    SyntheticCameraService,
    list_video_devices,
)
from .config import Settings, WorkArea
from .errors import CalibrationError
from .gcode.generator import (
    DesignPlacement,
    ToolpathOptions,
    generate_vector_gcode,
)
from .gcode.preview import parse_gcode_segments
from .geometry.polygon import (
    convex_polygon_contains_normalized,
    normalize_convex_polygon,
)
from .geometry.svg import parse_svg
from .imaging import (
    decode_image_payload,
    encode_image,
    image_quality,
    read_encoded_image_payload,
    read_image,
    write_image_atomic,
)
from .machine.service import MachineService, list_serial_ports
from .storage import (
    atomic_write_bytes,
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
from .vision.ruler import (
    HoneycombRulerDetection,
    RulerAxisDetection,
    detect_honeycomb_frame,
    detect_honeycomb_rulers,
    register_honeycomb_reference,
)
from .vision.workpiece import detect_workpiece

LOGGER = logging.getLogger(__name__)
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_CURRENT_LENS_MODEL = object()
_GENERATED_NAME_LOCK = threading.Lock()
_GENERATED_NAME_SEQUENCE = 0


def _ordered_honeycomb_corners(
    frame_image_px: np.ndarray,
    frame_machine_mm: np.ndarray,
) -> tuple[np.ndarray, tuple[int, int, int, int]]:
    """Order a fresh quad as origin, +X, opposite, +Y using machine axes."""

    frame = np.asarray(frame_image_px, dtype=np.float64)
    machine = np.asarray(frame_machine_mm, dtype=np.float64)
    if (
        frame.shape != (4, 2)
        or machine.shape != (4, 2)
        or not np.isfinite(frame).all()
        or not np.isfinite(machine).all()
    ):
        raise CalibrationError(
            "Automatic honeycomb verification requires four finite measured corners"
        )
    origin_index = min(
        range(4),
        key=lambda index: float(machine[index, 0] + machine[index, 1]),
    )
    adjacent = ((origin_index - 1) % 4, (origin_index + 1) % 4)
    x_index = max(adjacent, key=lambda index: float(machine[index, 0]))
    y_index = next(index for index in adjacent if index != x_index)
    opposite_index = next(
        index
        for index in range(4)
        if index not in {origin_index, x_index, y_index}
    )
    indices = (origin_index, x_index, opposite_index, y_index)
    return machine[np.asarray(indices)], indices


def _reference_topology_indices(
    reference: HoneycombSupportReference,
    frame_machine_mm: np.ndarray,
) -> tuple[int, int, int, int]:
    """Match taught origin/+X/opposite/+Y to fresh topology without axis inference."""

    frame = np.asarray(frame_machine_mm, dtype=np.float64)
    if frame.shape != (4, 2) or not np.isfinite(frame).all():
        raise CalibrationError(
            "Automatic honeycomb verification requires four finite measured corners"
        )
    taught = np.asarray(reference.support_corners_machine_mm, dtype=np.float64)
    if taught.shape != (4, 2) or not np.isfinite(taught).all():
        raise CalibrationError("The taught honeycomb topology is invalid")
    # The detector preserves the cyclic ordering emitted by registered taught
    # corners. Select only the cyclic start/direction that best corresponds to
    # the already-taught four-corner topology; never infer zero from machine axes.
    candidates: list[tuple[float, tuple[int, int, int, int]]] = []
    for start in range(4):
        for step in (1, -1):
            indices = tuple((start + step * offset) % 4 for offset in range(4))
            ordered = frame[np.asarray(indices)]
            offset = np.mean(taught - ordered, axis=0)
            residual = float(np.sum((ordered + offset - taught) ** 2))
            candidates.append((residual, indices))
    return min(candidates, key=lambda item: item[0])[1]


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


def _camera_provenance_matches(
    saved: object,
    current: object,
    configured_device: str,
) -> bool:
    if saved == current:
        return True
    if not str(configured_device).lower().startswith("e3camera://"):
        return False
    if not isinstance(saved, Mapping) or not isinstance(current, Mapping):
        return False

    # Older remote-camera bed maps recorded the Pi's live /dev/video* name.
    # Current provenance records the stable desktop e3camera:// endpoint.
    # The calibration profile is already scoped by configured camera settings,
    # so a legacy remote device-name difference alone does not invalidate it.
    saved_device = saved.get("device")
    if not isinstance(saved_device, str):
        return False

    # Only forgive the historical Pi-local camera identifier. A change from
    # one e3camera:// endpoint to another is a real configuration change and
    # must still invalidate the bed map.
    legacy_pi_device = saved_device.startswith("/dev/") or saved_device.isdigit()
    if not legacy_pi_device:
        return False

    saved_camera = dict(saved)
    current_camera = dict(current)
    saved_camera["device"] = current_camera.get("device")
    return saved_camera == current_camera


@dataclass(frozen=True, slots=True)
class RunningMachineIdentity:
    """Detached identity for the machine whose settings own this context."""

    machine_id: str
    machine_name: str
    created_from: str
    machine_profile_id: str
    tool_head_profile_id: str
    expected_camera_profile_id: str | None = None
    expected_calibration_profile_id: str | None = None

    @classmethod
    def standalone(cls) -> RunningMachineIdentity:
        return cls(
            machine_id="standalone",
            machine_name="Standalone / legacy AppContext",
            created_from="standalone",
            machine_profile_id="standalone",
            tool_head_profile_id="standalone",
        )


class AppContext:
    def __init__(
        self,
        settings: Settings,
        hardware_enabled: bool = False,
        laser_lockout: bool = False,
        *,
        machine_identity: RunningMachineIdentity | None = None,
    ):
        if type(hardware_enabled) is not bool:
            raise TypeError("hardware_enabled must be an exact boolean")
        if type(laser_lockout) is not bool:
            raise TypeError("laser_lockout must be an exact boolean")
        self.settings = settings
        self.hardware_enabled = hardware_enabled
        self.laser_lockout = laser_lockout
        identity = machine_identity or RunningMachineIdentity.standalone()
        if not isinstance(identity, RunningMachineIdentity):
            raise TypeError("machine_identity must be a RunningMachineIdentity")
        self.machine_identity = identity
        self.machine_id = identity.machine_id
        self.machine_name = identity.machine_name
        self.machine_created_from = identity.created_from
        self.machine_profile_id = identity.machine_profile_id
        self.tool_head_profile_id = identity.tool_head_profile_id
        self.expected_camera_profile_id = identity.expected_camera_profile_id
        self.expected_calibration_profile_id = (
            identity.expected_calibration_profile_id
        )
        settings.app.data_dir.mkdir(parents=True, exist_ok=True)
        for directory in ("captures", "calibration", "generated", "logs"):
            (settings.app.data_dir / directory).mkdir(parents=True, exist_ok=True)
        if settings.app.simulation:
            self.camera: CameraService = SyntheticCameraService(settings.camera, settings.machine.work_area)
        else:
            if settings.camera.device.lower().startswith("e3camera://"):
                from .camera.remote import RemoteCameraService

                self.camera = RemoteCameraService(settings.camera)
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
        self.fixture_reach = FixtureReachStore(
            settings.app.data_dir,
            machine_id=identity.machine_id,
            migrate_legacy=(
                settings.machine.backend == "serial"
                and identity.created_from == "legacy-config"
            ),
        )
        self.machine = MachineService(
            settings.machine,
            settings.laser,
            hardware_enabled=self.hardware_enabled,
            laser_lockout=self.laser_lockout,
        )
        self.bed_reference_path = calibration_dir / "bed_reference.png"
        self.legacy_bed_reference_path = calibration_dir / "bed_reference.jpg"
        self.base_bed_mapping_path = calibration_dir / "base_bed_mapping.json"
        self.honeycomb_detection_input_path = (
            calibration_dir / "honeycomb_detection_input.png"
        )
        self.honeycomb_detection_diagnostic_path = (
            calibration_dir / "honeycomb_detection_diagnostic.png"
        )
        self.honeycomb_visual_reference_path = (
            calibration_dir / "honeycomb_visual_reference.png"
        )
        self.honeycomb_visual_reference_metadata_path = (
            calibration_dir / "honeycomb_visual_reference.json"
        )
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
        camera_settings = self.settings.camera
        area = self.settings.machine.work_area
        width = int(camera_settings.width)
        height = int(camera_settings.height)
        if lens_model_id is _CURRENT_LENS_MODEL:
            lens = self.lens.model
            current_lens_model_id = None if lens is None else lens.model_id
        else:
            current_lens_model_id = lens_model_id
        return {
            "schema_version": 1,
            "lens_model_id": current_lens_model_id,
            "camera": {
                "device": str(camera_settings.device),
                "synthetic": bool(self.settings.app.simulation),
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
        changed: list[str] = []
        for key, value in current.items():
            if key == "camera" and _camera_provenance_matches(
                saved.get(key),
                value,
                self.settings.camera.device,
            ):
                continue
            if saved.get(key) != value:
                changed.append(key)
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

    def bed_mapping_digest(self) -> str | None:
        """Return a digest of the complete active image↔machine mapping."""

        calibration = self.bed.calibration
        if calibration is None:
            return None
        payload = json.dumps(
            calibration.to_dict(),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def honeycomb_execution_signature(self) -> tuple[str, int, str, str] | None:
        """Bind a support pose to the complete camera-to-machine mapping."""

        support = self._current_honeycomb_support()
        mapping = self.bed_mapping_digest()
        if (
            support is None
            or not support.is_execution_verifiable
            or mapping is None
        ):
            return None
        kind, version, digest = support.coordinate_frame.provenance_signature
        return kind, version, digest, mapping

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

    def _rectify_camera_image(
        self,
        image: np.ndarray,
        *,
        work_area: WorkArea | None = None,
        coordinate_frame: HoneycombCoordinateFrame | None = None,
    ) -> np.ndarray:
        lens = self.lens.model
        if lens is None:
            return self.bed.rectify(
                image,
                work_area=work_area,
                coordinate_frame=coordinate_frame,
            )
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
        area = self.settings.machine.work_area if work_area is None else work_area
        _frame_transform, frame_key = _coordinate_frame_transform(coordinate_frame)
        key = (
            lens.model_id,
            id(calibration),
            width,
            height,
            ppm,
            float(area.x_min),
            float(area.x_max),
            float(area.y_min),
            float(area.y_max),
            *frame_key,
        )
        with self._workspace_lock:
            cached = self._composed_map_cache.get(key)
        maps = None
        if cached is not None and cached[0] is lens and cached[1] is calibration:
            maps = cached[2]
        if maps is None:
            corrected_x, corrected_y = self.bed.rectification_map(
                ppm,
                work_area=area,
                coordinate_frame=coordinate_frame,
            )
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
        work_area: WorkArea | None = None,
        coordinate_frame: HoneycombCoordinateFrame | None = None,
    ) -> np.ndarray:
        with self._simulation_workspace_lock:
            if self._simulation_workspace_image is not None:
                return self._simulation_workspace_image.copy()
        self._require_valid_bed_calibration()
        configured = self.settings.machine.work_area
        uses_configured_area = coordinate_frame is None and (
            work_area is None
            or (
                abs(work_area.x_min - configured.x_min) <= 1e-9
                and abs(work_area.x_max - configured.x_max) <= 1e-9
                and abs(work_area.y_min - configured.y_min) <= 1e-9
                and abs(work_area.y_max - configured.y_max) <= 1e-9
            )
        )
        if not refresh and uses_configured_area:
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
        rectify_options: dict[str, Any] = {}
        if work_area is not None:
            rectify_options["work_area"] = work_area
        if coordinate_frame is not None:
            rectify_options["coordinate_frame"] = coordinate_frame
        rectified = self._rectify_camera_image(image, **rectify_options)
        if uses_configured_area:
            self._cache_workspace(rectified)
            if persist:
                self._persist_workspace(rectified)
        return rectified

    def capture_parked_trace_frame(
        self,
        *,
        work_area: WorkArea | None = None,
        coordinate_frame: HoneycombCoordinateFrame | None = None,
    ) -> np.ndarray:
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
        rectify_options: dict[str, Any] = {}
        if work_area is not None:
            rectify_options["work_area"] = work_area
        if coordinate_frame is not None:
            rectify_options["coordinate_frame"] = coordinate_frame
        rectified = self._rectify_camera_image(image, **rectify_options)
        uses_configured_area = coordinate_frame is None and work_area is None
        if coordinate_frame is None and work_area is not None:
            configured = self.settings.machine.work_area
            uses_configured_area = (
                abs(work_area.x_min - configured.x_min) <= 1e-9
                and abs(work_area.x_max - configured.x_max) <= 1e-9
                and abs(work_area.y_min - configured.y_min) <= 1e-9
                and abs(work_area.y_max - configured.y_max) <= 1e-9
            )
        if uses_configured_area:
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
        image = burst.sharpest_frame.copy()
        write_image_atomic(
            self.honeycomb_detection_input_path,
            image,
            [cv2.IMWRITE_PNG_COMPRESSION, 3],
        )
        return image

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
        return self._honeycomb_reference_from_detection(
            detection,
            ruler_mark_mm=ruler_mark_mm,
            calibration=calibration,
        ), detection

    def _honeycomb_reference_from_detection(
        self,
        detection: HoneycombRulerDetection,
        *,
        ruler_mark_mm: float,
        calibration: Any,
    ) -> HoneycombSupportReference:
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
        return reference

    def detect_honeycomb_support_reference_automatically(
        self,
        image: np.ndarray,
        *,
        ruler_mark_mm: float,
        require_taught_reference: bool = False,
    ) -> tuple[HoneycombSupportReference, HoneycombRulerDetection]:
        """Select one visually and geometrically valid automatic ruler result."""
        self._require_valid_bed_calibration()
        calibration = self.bed.calibration
        visual_metadata = read_json(self.honeycomb_visual_reference_metadata_path, {})
        taught_reference_digest: str | None = None
        if self.honeycomb_visual_reference_path.exists() and isinstance(
            visual_metadata, dict
        ):
            reference_image = read_image(self.honeycomb_visual_reference_path)
            accepted_support = self._current_honeycomb_support()
            legacy_seed_available = bool(
                reference_image is not None
                and visual_metadata.get("schema_version") == 1
                and np.asarray(
                    visual_metadata.get("cutting_surface_corners_px")
                ).shape
                == (4, 2)
            )
            automatic_seed_available = bool(
                reference_image is not None
                and visual_metadata.get("schema_version") == 2
                and visual_metadata.get("kind")
                == "accepted-automatic-honeycomb-teaching-reference"
                and visual_metadata.get("image_sha256")
                == hashlib.sha256(
                    self.honeycomb_visual_reference_path.read_bytes()
                ).hexdigest()
                and np.asarray(
                    visual_metadata.get("cutting_surface_corners_px")
                ).shape
                == (4, 2)
            )
            metadata_valid = bool(
                automatic_seed_available
                and visual_metadata.get("bed_mapping_digest")
                == self.bed_mapping_digest()
                and (
                    not require_taught_reference
                    or (
                        accepted_support is not None
                        and visual_metadata.get(
                            "support_coordinate_frame_digest"
                        )
                        == accepted_support.coordinate_frame_digest
                    )
                )
            )
            if metadata_valid:
                assert reference_image is not None
                try:
                    seed_frame = register_honeycomb_reference(
                        image,
                        reference_image,
                        np.asarray(
                            visual_metadata.get("cutting_surface_corners_px")
                        ),
                    )
                except ValueError:
                    if require_taught_reference:
                        raise CalibrationError(
                            "The fresh camera image could not be registered to the "
                            "accepted honeycomb reference; output remains blocked"
                        ) from None
                    if accepted_support is None:
                        frame = detect_honeycomb_frame(image)
                    else:
                        support_seed = np.asarray(
                            [
                                self.bed.mm_to_image(float(x), float(y))
                                for x, y in accepted_support.support_corners_machine_mm
                            ],
                            dtype=np.float64,
                        )
                        frame = detect_honeycomb_frame(
                            image,
                            seed_corners=support_seed,
                        )
                else:
                    taught_hasher = hashlib.sha256()
                    taught_hasher.update(
                        json.dumps(
                            visual_metadata,
                            allow_nan=False,
                            ensure_ascii=True,
                            separators=(",", ":"),
                            sort_keys=True,
                        ).encode("utf-8")
                    )
                    taught_hasher.update(str(reference_image.shape).encode("ascii"))
                    taught_hasher.update(str(reference_image.dtype).encode("ascii"))
                    taught_hasher.update(reference_image.tobytes())
                    taught_reference_digest = taught_hasher.hexdigest()
                    if require_taught_reference:
                        # Start-time pose evidence comes from a broad,
                        # support-local registration of the exact accepted
                        # teaching image.  The cutting frame can contain ticked
                        # rulers and interrupted edges, so requiring four
                        # uniform intensity transitions here produced false
                        # rejections even when registration was sub-pixel
                        # stable.  Setup still performs the independent
                        # four-edge fit before a reference can be accepted.
                        frame = seed_frame
                    else:
                        frame = detect_honeycomb_frame(
                            image,
                            seed_corners=seed_frame,
                        )
            elif automatic_seed_available and not require_taught_reference:
                # A newly applied camera-to-machine map intentionally makes the
                # accepted support execution-stale, but it does not make the
                # teaching image's pixel outline useless.  Registration is only
                # a search hint here: four fresh edges are still fitted and then
                # mapped and gated against the new calibration before saving.
                assert reference_image is not None
                try:
                    seed_frame = register_honeycomb_reference(
                        image,
                        reference_image,
                        np.asarray(
                            visual_metadata.get("cutting_surface_corners_px")
                        ),
                    )
                    frame = detect_honeycomb_frame(
                        image,
                        seed_corners=seed_frame,
                    )
                except ValueError:
                    if accepted_support is None:
                        frame = detect_honeycomb_frame(image)
                    else:
                        support_seed = np.asarray(
                            [
                                self.bed.mm_to_image(float(x), float(y))
                                for x, y in accepted_support.support_corners_machine_mm
                            ],
                            dtype=np.float64,
                        )
                        frame = detect_honeycomb_frame(
                            image,
                            seed_corners=support_seed,
                        )
            elif legacy_seed_available and not require_taught_reference:
                assert reference_image is not None
                try:
                    seed_frame = register_honeycomb_reference(
                        image,
                        reference_image,
                        np.asarray(
                            visual_metadata.get("cutting_surface_corners_px")
                        ),
                    )
                    frame = detect_honeycomb_frame(
                        image,
                        seed_corners=seed_frame,
                    )
                except ValueError:
                    # A legacy annotated image can guide the one-time upgrade,
                    # but it is never execution evidence. If registration or
                    # fresh edge fitting fails, continue with independent
                    # automatic segmentation for Setup review.
                    if accepted_support is None:
                        frame = detect_honeycomb_frame(image)
                    else:
                        support_seed = np.asarray(
                            [
                                self.bed.mm_to_image(float(x), float(y))
                                for x, y in accepted_support.support_corners_machine_mm
                            ],
                            dtype=np.float64,
                        )
                        frame = detect_honeycomb_frame(
                            image,
                            seed_corners=support_seed,
                        )
            elif require_taught_reference:
                raise CalibrationError(
                    "The accepted honeycomb teaching reference is missing, stale, "
                    "or corrupt; run automatic honeycomb detection again"
                )
            else:
                if accepted_support is None:
                    frame = detect_honeycomb_frame(image)
                else:
                    # A current legacy three-point reference is not execution
                    # evidence, but its mapped outline is a strong automatic
                    # search prior for upgrading the same physical fixture. Four
                    # fresh edge fits still supply every persisted schema-2
                    # corner; the projected legacy corners are never returned.
                    support_seed = np.asarray(
                        [
                            self.bed.mm_to_image(float(x), float(y))
                            for x, y in accepted_support.support_corners_machine_mm
                        ],
                        dtype=np.float64,
                    )
                    frame = detect_honeycomb_frame(
                        image,
                        seed_corners=support_seed,
                    )
        else:
            if require_taught_reference:
                raise CalibrationError(
                    "No accepted automatic honeycomb teaching image is recorded; "
                    "run automatic honeycomb detection again"
                )
            accepted_support = self._current_honeycomb_support()
            if accepted_support is None:
                frame = detect_honeycomb_frame(image)
            else:
                support_seed = np.asarray(
                    [
                        self.bed.mm_to_image(float(x), float(y))
                        for x, y in accepted_support.support_corners_machine_mm
                    ],
                    dtype=np.float64,
                )
                frame = detect_honeycomb_frame(
                    image,
                    seed_corners=support_seed,
                )
        diagnostic = image.copy()
        frame_int = np.rint(frame).astype(np.int32).reshape(-1, 1, 2)
        cv2.polylines(diagnostic, [frame_int], True, (205, 95, 220), 4, cv2.LINE_AA)
        for index, point in enumerate(frame_int[:, 0], start=1):
            cv2.circle(diagnostic, tuple(point), 10, (0, 225, 255), 3, cv2.LINE_AA)
            cv2.putText(
                diagnostic,
                str(index),
                (int(point[0]) + 12, int(point[1]) - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (0, 225, 255),
                2,
                cv2.LINE_AA,
            )
        write_image_atomic(
            self.honeycomb_detection_input_path,
            image,
            [cv2.IMWRITE_PNG_COMPRESSION, 3],
        )
        write_image_atomic(
            self.honeycomb_detection_diagnostic_path,
            diagnostic,
            [cv2.IMWRITE_PNG_COMPRESSION, 3],
        )
        machine = np.asarray(
            [self.bed.image_to_mm(float(point[0]), float(point[1])) for point in frame],
            dtype=np.float64,
        )
        if self.bed.calibration is not calibration or calibration is None:
            raise CalibrationError(
                "Bed calibration changed while the honeycomb frame was measured"
            )
        # For initial teaching, the active homography supplies direction; no
        # printed-number OCR or assumption about camera rotation is needed.
        _ordered_machine, ordered_indices = _ordered_honeycomb_corners(frame, machine)
        origin_index, x_index, opposite_index, y_index = ordered_indices
        origin = frame[origin_index]
        x_corner = frame[x_index]
        y_corner = frame[y_index]
        opposite = frame[opposite_index]
        x_length_px = float(np.linalg.norm(x_corner - origin))
        y_length_px = float(np.linalg.norm(y_corner - origin))
        detection = HoneycombRulerDetection(
            ruler_origin_image_px=(float(origin[0]), float(origin[1])),
            ruler_x_mark_image_px=(float(x_corner[0]), float(x_corner[1])),
            ruler_xy_mark_image_px=(float(opposite[0]), float(opposite[1])),
            axis_x=RulerAxisDetection(
                (float(origin[0]), float(origin[1])),
                (float(x_corner[0]), float(x_corner[1])),
                x_length_px / float(ruler_mark_mm),
                0,
                0.0,
                0.0,
                0.0,
            ),
            axis_y=RulerAxisDetection(
                (float(x_corner[0]), float(x_corner[1])),
                (float(opposite[0]), float(opposite[1])),
                y_length_px / float(ruler_mark_mm),
                0,
                0.0,
                0.0,
                0.0,
            ),
            corner_error_px=0.0,
            axis_angle_deg=float(
                math.degrees(
                    math.acos(
                        min(
                            1.0,
                            abs(
                                float(
                                    np.dot(x_corner - origin, y_corner - origin)
                                    / (x_length_px * y_length_px)
                                )
                            ),
                        )
                    )
                )
            ),
            frame_corners_image_px=tuple(
                (float(point[0]), float(point[1])) for point in frame
            ),
        )
        # The fresh four-edge fit is physical evidence. Preserve its independently
        # observed edge lengths and angle; never fabricate a unit 190 mm square
        # from configured dimensions. The reference model's ruler mark is the
        # nominal physical coordinate associated with each measured far edge.
        span = float(ruler_mark_mm)
        reference = HoneycombSupportReference.from_four_corner_observations(
            raw_corners_machine_mm=tuple(
                (float(point[0]), float(point[1])) for point in machine
            ),
            corner_topology=(origin_index, x_index, opposite_index, y_index),
            support_width_mm=span,
            support_height_mm=span,
            bed_calibration_created_at=calibration.created_at,
            taught_reference_digest=taught_reference_digest,
        )
        return reference, detection

    def save_honeycomb_support_reference(
        self,
        reference: HoneycombSupportReference,
        *,
        teaching_image: np.ndarray | None = None,
        teaching_corners_px: tuple[tuple[float, float], ...] | None = None,
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
        if reference.is_execution_verifiable:
            if teaching_image is None or teaching_corners_px is None:
                raise CalibrationError(
                    "Saving an automatic four-corner honeycomb requires the exact "
                    "reviewed teaching image and corners"
                )
            corners = np.asarray(teaching_corners_px, dtype=np.float64)
            if corners.shape != (4, 2) or not np.isfinite(corners).all():
                raise CalibrationError(
                    "The reviewed honeycomb teaching corners are invalid"
                )
            encoded = encode_image(teaching_image, ".png")
            image_digest = hashlib.sha256(encoded).hexdigest()
            metadata = {
                "schema_version": 2,
                "kind": "accepted-automatic-honeycomb-teaching-reference",
                "cutting_surface_corners_px": corners.tolist(),
                "image_sha256": image_digest,
                "bed_mapping_digest": self.bed_mapping_digest(),
                "support_coordinate_frame_digest": reference.coordinate_frame_digest,
                "created_at": reference.created_at,
            }
            atomic_write_bytes(self.honeycomb_visual_reference_path, encoded)
            atomic_write_json(
                self.honeycomb_visual_reference_metadata_path,
                metadata,
            )
        self.honeycomb_support.save(reference)
        return reference

    def clear_honeycomb_support_reference(self) -> None:
        self.honeycomb_support.clear()

    def _current_honeycomb_support(self) -> HoneycombSupportReference | None:
        """Return only a support reference measured through the active bed map."""

        reference = self.honeycomb_support.reference
        calibration = self.bed.calibration
        if reference is None or calibration is None:
            return None
        if abs(reference.bed_calibration_created_at - calibration.created_at) > 1e-9:
            return None
        return reference

    def verify_honeycomb_pose_for_execution(
        self,
        expected_signature: tuple[Any, ...],
        *,
        corner_tolerance_mm: float = 0.5,
        edge_length_tolerance_mm: float = 0.75,
        orthogonality_tolerance_deg: float = 1.0,
    ) -> dict[str, Any]:
        """Re-measure four support edges from a fresh parked capture before motion."""

        recorded = self._current_honeycomb_support()
        if recorded is None:
            raise CalibrationError(
                "The prepared honeycomb reference is no longer current; detect it again"
            )
        if not recorded.has_four_corner_evidence:
            raise CalibrationError(
                "The prepared honeycomb reference lacks four independent automatic "
                "corner measurements; run automatic honeycomb detection again"
            )
        recorded_calibration = self.bed.calibration
        if recorded_calibration is None:
            raise CalibrationError(
                "The camera-to-machine map is unavailable for honeycomb verification"
            )
        before = self.honeycomb_execution_signature()
        if before is None or tuple(before) != tuple(expected_signature):
            raise CalibrationError(
                "The honeycomb pose or camera-to-machine mapping changed after job preparation"
            )
        image = self.capture_parked_work_area_reference()
        observed, _detection = self.detect_honeycomb_support_reference_automatically(
            image,
            ruler_mark_mm=recorded.support_width_mm,
            require_taught_reference=True,
        )
        if not observed.has_four_corner_evidence:
            raise CalibrationError(
                "Automatic honeycomb verification did not retain four independent corners"
            )
        expected_corners = np.asarray(
            recorded.support_corners_machine_mm,
            dtype=np.float64,
        )
        if expected_corners.shape != (4, 2) or not np.isfinite(expected_corners).all():
            raise CalibrationError(
                "The prepared honeycomb reference does not contain four valid corners"
            )
        measured_machine = np.asarray(
            observed.raw_corners_machine_mm,
            dtype=np.float64,
        )
        if measured_machine.shape != (4, 2) or not np.isfinite(measured_machine).all():
            raise CalibrationError(
                "Automatic honeycomb verification did not retain four freshly measured corners"
            )
        if self.bed.calibration is not recorded_calibration:
            raise CalibrationError(
                "Bed calibration changed while the honeycomb pose was verified"
            )
        topology = _reference_topology_indices(recorded, measured_machine)
        observed_corners = measured_machine[np.asarray(topology)]
        expected_edges = np.linalg.norm(
            np.roll(expected_corners, -1, axis=0) - expected_corners,
            axis=1,
        )
        observed_edges = np.linalg.norm(
            np.roll(observed_corners, -1, axis=0) - observed_corners,
            axis=1,
        )
        edge_errors = np.abs(observed_edges - expected_edges)
        maximum_edge_error = float(np.max(edge_errors))
        if maximum_edge_error > float(edge_length_tolerance_mm) + 1e-9:
            raise CalibrationError(
                "The fresh honeycomb edge measurement changed scale: maximum "
                f"edge-length difference is {maximum_edge_error:.3f} mm (limit "
                f"{float(edge_length_tolerance_mm):.3f} mm)"
            )
        observed_x = observed_corners[1] - observed_corners[0]
        observed_y = observed_corners[3] - observed_corners[0]
        axis_angle = math.degrees(
            math.acos(
                min(
                    1.0,
                    abs(
                        float(
                            np.dot(observed_x, observed_y)
                            / (
                                np.linalg.norm(observed_x)
                                * np.linalg.norm(observed_y)
                            )
                        )
                    ),
                )
            )
        )
        angle_error = abs(90.0 - axis_angle)
        if angle_error > float(orthogonality_tolerance_deg) + 1e-9:
            raise CalibrationError(
                "The fresh honeycomb edges are not square enough: measured "
                f"{axis_angle:.3f} degrees (limit "
                f"±{float(orthogonality_tolerance_deg):.3f} degrees)"
            )
        errors = np.linalg.norm(observed_corners - expected_corners, axis=1)
        maximum = float(np.max(errors))
        if maximum > float(corner_tolerance_mm) + 1e-9:
            raise CalibrationError(
                "The honeycomb moved after job preparation: automatic corner "
                f"check differs by {maximum:.3f} mm (limit "
                f"{float(corner_tolerance_mm):.3f} mm)"
            )
        after = self.honeycomb_execution_signature()
        if after is None or tuple(after) != tuple(expected_signature):
            raise CalibrationError(
                "The honeycomb or camera-to-machine mapping changed during start verification"
            )
        return {
            "verified": True,
            "maximum_corner_error_mm": maximum,
            "corner_tolerance_mm": float(corner_tolerance_mm),
            "maximum_edge_length_error_mm": maximum_edge_error,
            "edge_length_tolerance_mm": float(edge_length_tolerance_mm),
            "axis_angle_deg": axis_angle,
            "orthogonality_tolerance_deg": float(orthogonality_tolerance_deg),
        }

    def validate_honeycomb_execution_binding(
        self,
        expected_signature: tuple[Any, ...],
    ) -> None:
        """Reject a stale prepared honeycomb binding without camera motion."""

        current = self.honeycomb_execution_signature()
        if current is None or tuple(current) != tuple(expected_signature):
            raise CalibrationError(
                "The honeycomb reference or camera-to-machine mapping changed "
                "after job preparation; generate the job again"
            )

    def current_honeycomb_coordinate_frame(
        self,
    ) -> HoneycombCoordinateFrame | None:
        """Return the current rigid honeycomb-local frame, if one is valid."""

        reference = self._current_honeycomb_support()
        return None if reference is None else reference.coordinate_frame

    def honeycomb_trace_background(
        self,
        *,
        work_area: WorkArea,
        coordinate_frame: HoneycombCoordinateFrame,
    ) -> np.ndarray | None:
        """Return the accepted empty honeycomb in the current Trace coordinates.

        This is optional review evidence only. It is returned solely when the
        teaching image, complete bed map, and support pose all match the active
        execution-grade reference; stale or legacy evidence is ignored.
        """

        reference = self._current_honeycomb_support()
        if reference is None or not reference.is_execution_verifiable:
            return None
        if coordinate_frame != reference.coordinate_frame:
            return None
        metadata = read_json(self.honeycomb_visual_reference_metadata_path, {})
        if not isinstance(metadata, dict):
            return None
        try:
            image_payload = read_encoded_image_payload(
                self.honeycomb_visual_reference_path
            )
        except ValueError:
            return None
        image_digest = image_payload.content_sha256
        if (
            metadata.get("schema_version") != 2
            or metadata.get("kind")
            != "accepted-automatic-honeycomb-teaching-reference"
            or metadata.get("image_sha256") != image_digest
            or metadata.get("bed_mapping_digest") != self.bed_mapping_digest()
            or metadata.get("support_coordinate_frame_digest")
            != reference.coordinate_frame_digest
        ):
            return None
        try:
            image = decode_image_payload(image_payload).image
        except ValueError:
            return None
        # Accepted teaching images come from
        # capture_parked_work_area_reference(), which lens-corrects them before
        # persistence.  BedMapper consumes that corrected coordinate domain
        # directly; _rectify_camera_image() is reserved for raw camera frames
        # and would apply the inverse lens map a second time here.
        return self.bed.rectify(
            image,
            work_area=work_area,
            coordinate_frame=coordinate_frame,
        )

    def trace_camera_work_area(self) -> WorkArea:
        """Return a display/detection area containing the work area and honeycomb.

        This area controls camera rectification only. It does not alter machine,
        project, guarded-output, preflight, arming, or execution bounds.
        """

        configured = self.settings.machine.work_area
        with self._simulation_workspace_lock:
            if self._simulation_workspace_image is not None:
                return WorkArea(
                    configured.x_min,
                    configured.x_max,
                    configured.y_min,
                    configured.y_max,
                )
        support = self._current_honeycomb_support()
        if support is None:
            return WorkArea(
                configured.x_min,
                configured.x_max,
                configured.y_min,
                configured.y_max,
            )
        corners = np.asarray(support.support_corners_machine_mm, dtype=np.float64)
        padding_mm = 2.0
        ppm = float(self.settings.calibration.bed.pixels_per_mm)
        x_min = min(float(configured.x_min), float(np.min(corners[:, 0])) - padding_mm)
        x_max = max(float(configured.x_max), float(np.max(corners[:, 0])) + padding_mm)
        y_min = min(float(configured.y_min), float(np.min(corners[:, 1])) - padding_mm)
        y_max = max(float(configured.y_max), float(np.max(corners[:, 1])) + padding_mm)
        # Pixel-aligned limits preserve the exact px/mm scale and keep image
        # dimensions stable across captures with the same support reference.
        return WorkArea(
            math.floor(x_min * ppm) / ppm,
            math.ceil(x_max * ppm) / ppm,
            math.floor(y_min * ppm) / ppm,
            math.ceil(y_max * ppm) / ppm,
        )

    def validate_powered_calibration_support(self, gcode: str, filename: str) -> None:
        """Reject any support-bound powered setup job outside its taught surface.

        The fresh base-map job is intentionally not included here: it is the
        bootstrap measurement that creates the image-to-machine transform needed
        to locate the honeycomb in machine coordinates. Every later powered
        camera-registration/validation job is support-bound.
        """

        matched = self._calibration_support_session(filename)
        if matched is None:
            return
        label, session = matched
        if not self._session_boolean(session, "powered", label):
            return
        expected_digest = session.get("program_digest")
        recorded_polygon = session.get("guarded_output_polygon_mm")
        actual_digest = (
            self.machine.preflight_program(gcode).digest
            if recorded_polygon is None
            else self.machine.preflight_program(
                gcode,
                guarded_output_polygon_mm=tuple(
                    (float(point[0]), float(point[1]))
                    for point in recorded_polygon
                ),
            ).digest
        )
        if not isinstance(expected_digest, str) or actual_digest != expected_digest:
            raise CalibrationError(
                f"The {label} program no longer matches the support-bound prepared session"
            )
        recorded = session.get("honeycomb_execution_signature")
        if not isinstance(recorded, list) or len(recorded) != 4:
            raise CalibrationError(
                f"The powered {label} job predates four-corner honeycomb binding; prepare it again"
            )
        support = self._current_honeycomb_support()
        current = self.honeycomb_execution_signature()
        if (
            support is None
            or not support.is_execution_verifiable
            or current is None
            or tuple(recorded) != tuple(current)
        ):
            raise CalibrationError(
                f"The detected honeycomb or camera map changed after this {label} job "
                "was prepared; prepare it again"
            )
        segments = [segment for segment in parse_gcode_segments(gcode) if segment.laser_on]
        spot_x = float(self.settings.laser.spot_offset_x_mm)
        spot_y = float(self.settings.laser.spot_offset_y_mm)
        points = np.asarray(
            [
                (point[0] + spot_x, point[1] + spot_y)
                for segment in segments
                for point in ((segment.start_x, segment.start_y), (segment.end_x, segment.end_y))
            ],
            dtype=np.float64,
        )
        if not segments or not points_fit_support(points, support):
            raise CalibrationError(
                f"Powered {label} motion leaves the detected honeycomb cutting surface"
            )

    def calibration_job_guarded_output_polygon(
        self,
        filename: str,
    ) -> tuple[tuple[float, float], ...] | None:
        """Return the exact prepared output polygon for a setup job, if any."""

        matched = self._calibration_support_session(filename)
        if matched is None:
            return None
        _label, session = matched
        polygon = session.get("guarded_output_polygon_mm")
        if polygon is None:
            return None
        return normalize_convex_polygon(
            polygon,
            label="prepared calibration output polygon",
        )

    def calibration_job_honeycomb_signature(
        self,
        filename: str,
    ) -> tuple[Any, ...] | None:
        """Return the prepared fixture binding for a powered setup job."""

        matched = self._calibration_support_session(filename)
        if matched is None:
            return None
        _label, session = matched
        if session.get("powered") is not True:
            return None
        signature = session.get("honeycomb_execution_signature")
        if not isinstance(signature, list) or len(signature) != 4:
            return None
        return tuple(signature)

    def _calibration_support_session(
        self,
        filename: str,
    ) -> tuple[str, dict[str, Any]] | None:
        sessions = (
            ("fine registration", self.fine_registration_path),
            ("accuracy validation", self.accuracy_validation_path),
            ("dense local correction", self.dense_calibration_path),
            ("dense validation", self.dense_validation_path),
            ("dense confirmation", self.dense_confirmation_path),
        )
        for label, path in sessions:
            session = read_json(path, {})
            if isinstance(session, dict) and session.get("filename") == filename:
                return label, session
        return None

    def _required_calibration_support(
        self,
        *,
        powered: bool,
        label: str,
    ) -> HoneycombSupportReference | None:
        support = self._current_honeycomb_support()
        if powered and (
            support is None or not support.is_execution_verifiable
        ):
            raise CalibrationError(
                f"Automatically detect and save the four honeycomb corners before "
                f"preparing powered {label} marks"
            )
        return support if support is not None and support.is_execution_verifiable else None

    def _calibration_support_fields(
        self,
        support: HoneycombSupportReference | None,
    ) -> dict[str, Any]:
        if support is None:
            return {"honeycomb_execution_signature": None}
        signature = self.honeycomb_execution_signature()
        if signature is None:
            raise CalibrationError(
                "The honeycomb or complete camera-to-machine mapping is not current"
            )
        return {
            "honeycomb_execution_signature": list(signature),
            "honeycomb_support_corners_machine_mm": [
                list(point) for point in support.support_corners_machine_mm
            ],
        }

    def _support_contained_calibration_targets(
        self,
        target_factory: Any,
        support: HoneycombSupportReference,
        *,
        mark_size_mm: float,
        boundary_margin_mm: float,
    ) -> tuple[tuple[Any, ...], WorkArea]:
        """Find the broadest axis-aligned target grid contained by the support.

        Dense residual meshes must stay regular in machine X/Y, so their nodes
        cannot simply be rotated into the movable support frame. Shrinking the
        configured rectangle toward a point inside the machine/support
        intersection retains a regular machine grid while keeping complete
        cross extents on the cutting surface.
        """

        machine_area = self.settings.machine.work_area
        polygon = [
            np.asarray(point, dtype=np.float64)
            for point in support.support_corners_machine_mm
        ]

        def clip(
            points: list[np.ndarray],
            inside: Any,
            intersection: Any,
        ) -> list[np.ndarray]:
            if not points:
                return []
            output: list[np.ndarray] = []
            previous = points[-1]
            previous_inside = bool(inside(previous))
            for current in points:
                current_inside = bool(inside(current))
                if current_inside != previous_inside:
                    output.append(intersection(previous, current))
                if current_inside:
                    output.append(current)
                previous = current
                previous_inside = current_inside
            return output

        def vertical_intersection(
            left: np.ndarray,
            right: np.ndarray,
            x_value: float,
        ) -> np.ndarray:
            delta = right - left
            if abs(float(delta[0])) <= 1e-12:
                return np.asarray((x_value, float(left[1])), dtype=np.float64)
            ratio = (x_value - float(left[0])) / float(delta[0])
            return left + ratio * delta

        def horizontal_intersection(
            left: np.ndarray,
            right: np.ndarray,
            y_value: float,
        ) -> np.ndarray:
            delta = right - left
            if abs(float(delta[1])) <= 1e-12:
                return np.asarray((float(left[0]), y_value), dtype=np.float64)
            ratio = (y_value - float(left[1])) / float(delta[1])
            return left + ratio * delta

        polygon = clip(
            polygon,
            lambda point: point[0] >= machine_area.x_min,
            lambda left, right: vertical_intersection(left, right, machine_area.x_min),
        )
        polygon = clip(
            polygon,
            lambda point: point[0] <= machine_area.x_max,
            lambda left, right: vertical_intersection(left, right, machine_area.x_max),
        )
        polygon = clip(
            polygon,
            lambda point: point[1] >= machine_area.y_min,
            lambda left, right: horizontal_intersection(left, right, machine_area.y_min),
        )
        polygon = clip(
            polygon,
            lambda point: point[1] <= machine_area.y_max,
            lambda left, right: horizontal_intersection(left, right, machine_area.y_max),
        )
        if len(polygon) < 3:
            raise CalibrationError(
                "The detected honeycomb does not overlap the configured machine work area"
            )
        center = np.mean(np.asarray(polygon), axis=0)

        def targets_at(scale: float) -> tuple[Any, ...] | None:
            area = WorkArea(
                x_min=float(center[0] + (machine_area.x_min - center[0]) * scale),
                x_max=float(center[0] + (machine_area.x_max - center[0]) * scale),
                y_min=float(center[1] + (machine_area.y_min - center[1]) * scale),
                y_max=float(center[1] + (machine_area.y_max - center[1]) * scale),
            )
            try:
                targets = target_factory(
                    area,
                    mark_size_mm=mark_size_mm,
                    boundary_margin_mm=boundary_margin_mm,
                )
            except CalibrationError:
                return None
            clearance = boundary_margin_mm + mark_size_mm * 0.5
            return (
                targets
                if targets_fit_support(targets, support, clearance)
                else None
            )

        failed_scale = 1.0
        successful_scale: float | None = None
        successful_targets: tuple[Any, ...] | None = None
        for index in range(201):
            scale = 1.0 - index / 200.0
            candidate = targets_at(scale)
            if candidate is not None:
                successful_scale = scale
                successful_targets = candidate
                break
            failed_scale = scale
        if successful_scale is None or successful_targets is None:
            raise CalibrationError(
                "No regular calibration grid fits inside both the detected honeycomb "
                "and configured machine work area"
            )
        lower = successful_scale
        upper = min(1.0, failed_scale)
        for _iteration in range(40):
            middle = (lower + upper) * 0.5
            candidate = targets_at(middle)
            if candidate is None:
                upper = middle
            else:
                lower = middle
                successful_targets = candidate
        fitted_area = WorkArea(
            x_min=float(center[0] + (machine_area.x_min - center[0]) * lower),
            x_max=float(center[0] + (machine_area.x_max - center[0]) * lower),
            y_min=float(center[1] + (machine_area.y_min - center[1]) * lower),
            y_max=float(center[1] + (machine_area.y_max - center[1]) * lower),
        )
        return successful_targets, fitted_area

    def _full_surface_dense_targets(
        self,
        support: HoneycombSupportReference,
        *,
        mark_size_mm: float,
        requested_span_mm: float = 180.0,
    ) -> tuple[tuple[RegistrationTarget, ...], WorkArea, tuple[tuple[float, float], ...]]:
        """Place five machine-axis mesh nodes across nearly the full support."""

        configured = self.settings.laser.guarded_output_polygon_mm
        if configured is None:
            raise CalibrationError(
                "A configured honeycomb output polygon is required for the full-surface dense grid"
            )
        output_polygon = normalize_convex_polygon(
            configured,
            label="laser.guarded_output_polygon_mm",
        )
        center = np.mean(
            np.asarray(support.support_corners_machine_mm, dtype=np.float64),
            axis=0,
        )
        half_mark = float(mark_size_mm) * 0.5

        def targets_for_span(span_mm: float) -> tuple[RegistrationTarget, ...] | None:
            offsets = np.linspace(-span_mm * 0.5, span_mm * 0.5, 5)
            targets = tuple(
                RegistrationTarget(
                    id=row * 5 + column + 1,
                    machine_x=float(center[0] + offset_x),
                    machine_y=float(center[1] + offset_y),
                )
                for row, offset_y in enumerate(offsets)
                for column, offset_x in enumerate(offsets)
            )
            if not targets_fit_support(targets, support, half_mark):
                return None
            endpoints = (
                point
                for target in targets
                for point in (
                    (target.machine_x - half_mark, target.machine_y),
                    (target.machine_x + half_mark, target.machine_y),
                    (target.machine_x, target.machine_y - half_mark),
                    (target.machine_x, target.machine_y + half_mark),
                )
            )
            if not all(
                convex_polygon_contains_normalized(point, output_polygon)
                for point in endpoints
            ):
                return None
            return targets

        targets = targets_for_span(requested_span_mm)
        if targets is None:
            raise CalibrationError(
                f"A {requested_span_mm:g} × {requested_span_mm:g} mm dense grid with "
                f"{mark_size_mm:g} mm crosses does not fit the detected support and "
                "configured output polygon"
            )
        area = WorkArea(
            x_min=float(center[0] - requested_span_mm * 0.5),
            x_max=float(center[0] + requested_span_mm * 0.5),
            y_min=float(center[1] - requested_span_mm * 0.5),
            y_max=float(center[1] + requested_span_mm * 0.5),
        )
        return targets, area, output_polygon

    @staticmethod
    def _dense_mesh_target_area(calibration: BedCalibration) -> WorkArea:
        mesh = calibration.residual_mesh
        if mesh is None:
            raise CalibrationError(
                "Apply the dense 5×5 fit before preparing its validation grid"
            )
        x_nodes = np.asarray(mesh.x_nodes_mm, dtype=np.float64)
        y_nodes = np.asarray(mesh.y_nodes_mm, dtype=np.float64)
        if (
            x_nodes.shape != (5,)
            or y_nodes.shape != (5,)
            or not np.isfinite(x_nodes).all()
            or not np.isfinite(y_nodes).all()
        ):
            raise CalibrationError("The active dense mesh nodes are invalid")
        fraction_span = 0.85 - 0.15
        width = float((x_nodes[-1] - x_nodes[0]) / fraction_span)
        height = float((y_nodes[-1] - y_nodes[0]) / fraction_span)
        return WorkArea(
            x_min=float(x_nodes[0] - 0.15 * width),
            x_max=float(x_nodes[-1] + 0.15 * width),
            y_min=float(y_nodes[0] - 0.15 * height),
            y_max=float(y_nodes[-1] + 0.15 * height),
        )

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
        support_reference = self._required_calibration_support(
            powered=powered,
            label="fine-registration",
        )
        targets = registration_targets(
            self.settings.machine.work_area,
            mark_size_mm=mark_size_mm,
            boundary_margin_mm=self.settings.laser.boundary_margin_mm,
            support_reference=support_reference,
        )
        if support_reference is not None and not targets_fit_support(
            targets,
            support_reference,
            self.settings.laser.boundary_margin_mm + mark_size_mm * 0.5,
        ):
            raise CalibrationError(
                "Fine-registration crosses do not fit inside the detected honeycomb cutting surface"
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
                **self._calibration_support_fields(support_reference),
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
        support_reference = self._required_calibration_support(
            powered=powered,
            label="accuracy-validation",
        )
        if support_reference is not None:
            targets, _target_area = self._support_contained_calibration_targets(
                accuracy_validation_targets,
                support_reference,
                mark_size_mm=mark_size_mm,
                boundary_margin_mm=self.settings.laser.boundary_margin_mm,
            )
        else:
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
                **self._calibration_support_fields(support_reference),
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
        support_reference = self._required_calibration_support(
            powered=powered,
            label=(
                "dense confirmation"
                if confirmation
                else "dense validation"
                if validation
                else "dense local correction"
            ),
        )
        if confirmation:
            if calibration.residual_mesh is None or calibration.residual_mesh.refinement_count != 1:
                raise CalibrationError("Apply the reviewed validation refinement before confirmation")
            target_factory = dense_confirmation_targets
        else:
            target_factory = dense_validation_targets if validation else dense_mesh_targets
        guarded_output_polygon: tuple[tuple[float, float], ...] | None = None
        if support_reference is not None and not validation and not confirmation:
            if self.settings.laser.guarded_output_polygon_mm is None:
                targets, target_area = self._support_contained_calibration_targets(
                    target_factory,
                    support_reference,
                    mark_size_mm=mark_size_mm,
                    boundary_margin_mm=self.settings.laser.boundary_margin_mm,
                )
            else:
                targets, target_area, guarded_output_polygon = self._full_surface_dense_targets(
                    support_reference,
                    mark_size_mm=mark_size_mm,
                )
        else:
            target_area = (
                self._dense_mesh_target_area(calibration)
                if validation or confirmation
                else self.settings.machine.work_area
            )
            targets = target_factory(
                target_area,
                mark_size_mm=mark_size_mm,
                boundary_margin_mm=self.settings.laser.boundary_margin_mm,
            )
            if support_reference is not None and not targets_fit_support(
                targets,
                support_reference,
                self.settings.laser.boundary_margin_mm + mark_size_mm * 0.5,
            ):
                raise CalibrationError(
                    "The dense validation crosses do not fit inside the detected "
                    "honeycomb cutting surface"
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
            guarded_output_polygon_mm=guarded_output_polygon,
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
        program_digest = (
            self.machine.preflight_program(program.text).digest
            if guarded_output_polygon is None
            else self.machine.preflight_program(
                program.text,
                guarded_output_polygon_mm=guarded_output_polygon,
            ).digest
        )
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
                "target_area": asdict(target_area),
                "guarded_output_polygon_mm": (
                    None
                    if guarded_output_polygon is None
                    else [list(point) for point in guarded_output_polygon]
                ),
                **self._calibration_support_fields(support_reference),
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
            guarded_output_polygon_mm=guarded_output_polygon,
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
        calibration = self.bed.apply_residual_mesh(
            np.asarray(analysis["x_nodes_mm"]),
            np.asarray(analysis["y_nodes_mm"]),
            np.asarray(analysis["corrections_mm"]),
            fit_rms_mm=float(analysis["fit_rms_mm"]),
            fit_max_mm=float(analysis["fit_max_mm"]),
        )
        self.honeycomb_support.clear()
        return calibration.to_dict()

    def reset_dense_calibration(self) -> dict[str, Any]:
        calibration = self.bed.reset_residual_mesh()
        self.honeycomb_support.clear()
        return calibration.to_dict()

    def apply_dense_validation_refinement(self, analysis: dict[str, Any]) -> dict[str, Any]:
        analysis = self._require_current_analysis(
            self.dense_validation_path,
            analysis,
            "dense-validation",
        )
        refinement = analysis.get("refinement")
        if not isinstance(refinement, dict) or not refinement.get("can_refine"):
            raise CalibrationError("This validation result did not pass the refinement gates")
        calibration = self.bed.refine_residual_mesh(
            np.asarray(refinement["delta_corrections_mm"], dtype=np.float64),
            analyzed_mesh_created_at=float(refinement["base_mesh_created_at"]),
            predicted_rms_mm=float(refinement["predicted_rms_mm"]),
            predicted_max_mm=float(refinement["predicted_max_mm"]),
        )
        self.honeycomb_support.clear()
        return calibration.to_dict()

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
        self.honeycomb_support.clear()
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
        self.honeycomb_support.clear()
        return calibration.to_dict()

    def reset_fine_registration(self) -> dict[str, Any]:
        calibration = self.bed.reset_registration_translation()
        self.honeycomb_support.clear()
        result = calibration.to_dict()
        session = read_json(self.fine_registration_path, {})
        measurements = session.get("measurements") if isinstance(session, dict) else None
        prior_analysis = session.get("analysis") if isinstance(session, dict) else None
        if isinstance(measurements, list) and measurements:
            excluded_ids = (
                [int(value) for value in prior_analysis.get("excluded_ids", [])]
                if isinstance(prior_analysis, dict)
                else []
            )
            analysis = self.review_fine_registration_measurements(
                measurements,
                excluded_ids,
            )
            result["review_measurements"] = measurements
            result["review_analysis"] = analysis
        return result

    def reset_fine_registration_homography(self) -> dict[str, Any]:
        calibration = self.bed.reset_registration_homography()
        self.honeycomb_support.clear()
        return calibration.to_dict()

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
