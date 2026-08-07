from __future__ import annotations

import copy
import logging
import re
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from . import __version__
from .calibration.bed import BedMapper, BedPoint
from .calibration.lens import LensCalibrator
from .calibration.registration import (
    AccuracyValidationJob,
    FineRegistrationJob,
    accuracy_validation_targets,
    analyze_accuracy_measurements,
    analyze_dense_mesh_measurements,
    analyze_dense_validation_refinement,
    analyze_homography_refinement,
    analyze_registration_measurements,
    dense_confirmation_targets,
    dense_mesh_targets,
    dense_validation_targets,
    generate_registration_program,
    registration_targets,
    review_registration_measurements,
    suggested_registration_exclusions,
)
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
    generate_frame_gcode,
    generate_vector_gcode,
)
from .geometry.svg import parse_svg
from .machine.service import MachineService, list_serial_ports
from .storage import atomic_write_json, read_json
from .vision.fiducials import (
    detect_aruco_markers,
    detect_crosshairs_burst,
    detect_crosshairs_near,
)
from .vision.workpiece import detect_workpiece

LOGGER = logging.getLogger(__name__)
_SAFE_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


class AppContext:
    def __init__(self, settings: Settings, hardware_enabled: bool = False):
        self.settings = settings
        self.hardware_enabled = bool(hardware_enabled)
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
        self.fine_registration_path = settings.app.data_dir / "fine_registration.json"
        self.accuracy_validation_path = settings.app.data_dir / "accuracy_validation.json"
        self.dense_calibration_path = settings.app.data_dir / "dense_calibration.json"
        self._camera_start_error: str | None = None
        self._simulation_workspace_lock = threading.RLock()
        self._simulation_workspace_image: np.ndarray | None = None
        self._simulation_workspace_info: dict[str, Any] | None = None

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
        if undistort and self.lens.model is not None:
            frame = self.lens.model.undistort(frame)
        return frame

    def precision_camera_burst(self, undistort: bool = True) -> FrameBurst:
        burst = self.camera.capture_burst(self.settings.camera.precision_capture)
        if undistort and self.lens.model is not None:
            burst.frames = tuple(
                self.lens.model.undistort(frame) for frame in burst.frames
            )
            burst.sharpness_scores = tuple(
                self.camera._sharpness_score(frame) for frame in burst.frames
            )
        return burst

    def stable_camera_frame(self, undistort: bool = True) -> tuple[np.ndarray, dict[str, Any]]:
        burst = self.precision_camera_burst(undistort=undistort)
        return burst.sharpest_frame, burst.diagnostics()

    @staticmethod
    def encode_jpeg(image: np.ndarray, quality: int = 92) -> bytes:
        ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, int(quality)])
        if not ok:
            raise RuntimeError("Could not encode image")
        return encoded.tobytes()

    def save_capture(
        self,
        prefix: str = "capture",
        undistort: bool = True,
        *,
        precision: bool = True,
    ) -> Path:
        image = (
            self.stable_camera_frame(undistort=undistort)[0]
            if precision
            else self.camera_frame(undistort=undistort)
        )
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        safe_prefix = _SAFE_NAME_RE.sub("-", prefix).strip("-._")[:60] or "capture"
        path = self.settings.app.data_dir / "captures" / f"{safe_prefix}-{timestamp}.jpg"
        if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 96]):
            raise RuntimeError(f"Could not save capture to {path}")
        return path

    def capture_bed_reference(self, *, precision: bool = True) -> dict[str, Any]:
        if precision:
            image, diagnostics = self.stable_camera_frame(undistort=True)
        else:
            image = self.camera_frame(undistort=True)
            diagnostics = None
        if not cv2.imwrite(str(self.bed_reference_path), image, [cv2.IMWRITE_JPEG_QUALITY, 98]):
            raise RuntimeError("Could not save bed reference image")
        return {
            "width": int(image.shape[1]),
            "height": int(image.shape[0]),
            "path": self.bed_reference_path.name,
            "capture_diagnostics": diagnostics,
        }

    def bed_reference(self) -> np.ndarray:
        if self.bed_reference_path.exists():
            image = cv2.imread(str(self.bed_reference_path), cv2.IMREAD_COLOR)
            if image is not None:
                return image
        return self.camera_frame(undistort=True)

    def rectified_frame(self, refresh: bool = True, *, precision: bool = False) -> np.ndarray:
        with self._simulation_workspace_lock:
            if self._simulation_workspace_image is not None:
                return self._simulation_workspace_image.copy()
        if not refresh and self.workspace_path.exists():
            image = cv2.imread(str(self.workspace_path), cv2.IMREAD_COLOR)
            if image is not None:
                return image
        image = (
            self.stable_camera_frame(undistort=True)[0]
            if precision
            else self.camera_frame(undistort=True)
        )
        rectified = self.bed.rectify(image)
        self.workspace_path.parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(self.workspace_path), rectified, [cv2.IMWRITE_JPEG_QUALITY, 96])
        return rectified

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
        calibration = self.bed.solve(image.shape[1], image.shape[0])
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
        coordinates = (20.0, 65.0, 110.0, 155.0, 200.0)

        if self.bed.calibration is None:
            return {
                "detected": False,
                "reason": (
                    "A rough existing bed mapping is required for boundary-independent "
                    "detection. Keep the current manual mapping, capture the burned grid, "
                    "then run detection."
                ),
                "points": [],
            }

        expected_points: list[dict[str, Any]] = []
        identifier = 1
        for machine_y in coordinates:
            for machine_x in coordinates:
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

    def prepare_fine_registration_job(
        self,
        *,
        powered: bool,
        power_percent: float,
        mark_size_mm: float,
        speed_mm_min: float,
    ) -> FineRegistrationJob:
        if self.bed.calibration is None:
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
        filename = f"fine-registration-{time.strftime('%Y%m%d-%H%M%S')}.gcode"
        generated_path = self.settings.app.data_dir / "generated" / filename
        generated_path.write_text(program.text, encoding="utf-8")
        atomic_write_json(
            self.fine_registration_path,
            {
                "schema_version": 1,
                "created_at": time.time(),
                "filename": filename,
                "powered": powered,
                "power_percent": power_percent if powered else 0.0,
                "mark_size_mm": mark_size_mm,
                "speed_mm_min": speed_mm_min,
                "targets": [target.to_dict() for target in targets],
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
        filename = f"accuracy-validation-{time.strftime('%Y%m%d-%H%M%S')}.gcode"
        generated_path = self.settings.app.data_dir / "generated" / filename
        generated_path.write_text(program.text, encoding="utf-8")
        atomic_write_json(
            self.accuracy_validation_path,
            {
                "schema_version": 1,
                "created_at": time.time(),
                "filename": filename,
                "powered": powered,
                "power_percent": power_percent if powered else 0.0,
                "mark_size_mm": mark_size_mm,
                "speed_mm_min": speed_mm_min,
                "targets": [target.to_dict() for target in targets],
                "image_to_machine": calibration.image_to_machine.tolist(),
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
        filename = f"{design_name}-{time.strftime('%Y%m%d-%H%M%S')}.gcode"
        (self.settings.app.data_dir / "generated" / filename).write_text(program.text, encoding="utf-8")
        atomic_write_json(
            self.dense_calibration_path,
            {
                "schema_version": 1,
                "created_at": time.time(),
                "filename": filename,
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

    def analyze_dense_calibration_image(self, image: np.ndarray) -> dict[str, Any]:
        calibration = self.bed.calibration
        if calibration is None:
            raise CalibrationError("Solve the bed mapping before dense calibration")
        session = read_json(self.dense_calibration_path, {})
        targets = session.get("targets") if isinstance(session, dict) else None
        validation = bool(session.get("validation")) if isinstance(session, dict) else False
        confirmation = bool(session.get("confirmation")) if isinstance(session, dict) else False
        expected_count = 16 if validation or confirmation else 25
        if not isinstance(targets, list) or len(targets) != expected_count:
            raise CalibrationError("Prepare the matching dense-grid job before capture")
        if not bool(session.get("powered")):
            raise CalibrationError("Run the powered dense-grid job before analyzing marks")
        current_mesh_created_at = None if calibration.residual_mesh is None else calibration.residual_mesh.created_at
        if session.get("residual_mesh_created_at") != current_mesh_created_at:
            raise CalibrationError("The local correction changed after this job was prepared; prepare a new job")
        expected = []
        for item in targets:
            machine_x, machine_y = float(item["machine_x"]), float(item["machine_y"])
            image_x, image_y = self.bed.mm_to_image(machine_x, machine_y)
            expected.append({**item, "image_x": image_x, "image_y": image_y})
        detection = detect_crosshairs_near(image, expected, search_radius_px=48)
        if not detection.get("detected"):
            return detection
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
        updated = dict(session)
        reviewed_measurements = list(analysis.get("measurements", measurements))
        updated.update(
            {
                "captured_at": time.time(),
                "measurements": reviewed_measurements,
                "analysis": analysis,
            }
        )
        atomic_write_json(self.dense_calibration_path, updated)
        return {
            "detected": True,
            "points": detection["points"],
            "measurements": reviewed_measurements,
            "analysis": analysis,
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
        refinement = analysis.get("refinement")
        if not isinstance(refinement, dict) or not refinement.get("can_refine"):
            raise CalibrationError("This validation result did not pass the refinement gates")
        return self.bed.refine_residual_mesh(
            np.asarray(refinement["delta_corrections_mm"], dtype=np.float64),
            analyzed_mesh_created_at=float(refinement["base_mesh_created_at"]),
            predicted_rms_mm=float(refinement["predicted_rms_mm"]),
            predicted_max_mm=float(refinement["predicted_max_mm"]),
        ).to_dict()

    def analyze_accuracy_validation_image(
        self, image: np.ndarray
    ) -> dict[str, Any]:
        return self._analyze_accuracy_validation_capture(image, (image,), None)

    def capture_accuracy_validation(
        self,
        *,
        home_first: bool = True,
    ) -> tuple[np.ndarray, dict[str, Any]]:
        if home_first:
            self.machine.prepare_photo_position()
        burst = self.precision_camera_burst(undistort=True)
        return burst.sharpest_frame, self.analyze_accuracy_validation_burst(burst)

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
        if not bool(session.get("powered")):
            raise CalibrationError(
                "The prepared validation job is dry motion only. After reviewing it, "
                "prepare and run the powered holdout job before analyzing marks."
            )
        prepared_map = np.asarray(session.get("image_to_machine", []), dtype=np.float64)
        if prepared_map.shape != (3, 3) or not np.allclose(
            prepared_map,
            calibration.image_to_machine,
            rtol=1e-10,
            atol=1e-10,
        ):
            raise CalibrationError("The bed map changed after this validation job was prepared; prepare a new job")

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
            )
        capture_diagnostics = (
            {
                "camera": camera_diagnostics,
                "aggregation": detection.get("capture_diagnostics", {}),
            }
            if camera_diagnostics is not None
            else None
        )
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        capture_path = self.settings.app.data_dir / "captures" / f"accuracy-validation-{timestamp}.jpg"
        if not cv2.imwrite(str(capture_path), image, [cv2.IMWRITE_JPEG_QUALITY, 98]):
            raise RuntimeError("Could not save the accuracy-validation capture")
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
            observed_x, observed_y = self.bed.image_to_mm(
                float(point["image_x"]), float(point["image_y"])
            )
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
        if home_first:
            self.machine.prepare_photo_position()
        burst = self.precision_camera_burst(undistort=True)
        return burst.sharpest_frame, self.analyze_fine_registration_burst(burst)

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
        if self.bed.calibration is None:
            raise CalibrationError("Solve the bed mapping before fine registration")
        session = read_json(self.fine_registration_path, {})
        raw_targets = session.get("targets") if isinstance(session, dict) else None
        if not isinstance(raw_targets, list) or len(raw_targets) < 4:
            raise CalibrationError("Prepare the fine-registration mark job before capturing its marks")
        if not bool(session.get("powered")):
            raise CalibrationError(
                "The prepared registration job is dry motion only. After reviewing it, "
                "prepare and run the powered mark job before analyzing marks."
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
            )
        capture_diagnostics = (
            {
                "camera": camera_diagnostics,
                "aggregation": detection.get("capture_diagnostics", {}),
            }
            if camera_diagnostics is not None
            else None
        )
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        capture_path = self.settings.app.data_dir / "captures" / f"fine-registration-{timestamp}.jpg"
        if not cv2.imwrite(str(capture_path), image, [cv2.IMWRITE_JPEG_QUALITY, 98]):
            raise RuntimeError("Could not save the fine-registration capture")
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
            observed_x, observed_y = self.bed.image_to_mm(
                float(point["image_x"]), float(point["image_y"])
            )
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
            analysis = review_registration_measurements(
                all_measurements, suggested_exclusions
            )
            analysis["full_map_refinement"] = self._analyze_full_map_refinement(
                all_measurements, suggested_exclusions
            )
        analysis["precision_capture"] = capture_diagnostics
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
        if not analysis.get("can_apply_translation"):
            raise CalibrationError("This result is not a safe global translation; do not apply it")
        calibration = self.bed.apply_registration_translation(
            float(analysis["correction_x_mm"]),
            float(analysis["correction_y_mm"]),
            analysis=analysis,
        )
        return calibration.to_dict()

    def apply_fine_registration_homography(self, analysis: dict[str, Any]) -> dict[str, Any]:
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
            spot_offset_x_mm=laser.spot_offset_x_mm,
            spot_offset_y_mm=laser.spot_offset_y_mm,
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
        program = generate_frame_gcode(bounds, options, self.settings.machine.work_area, laser_enabled=requested_laser)
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
            "simulation_workspace_frame": self.simulation_workspace_frame_status(),
            "machine": self.machine.status(),
            "devices": {
                "cameras": list_video_devices(),
                "serial_ports": list_serial_ports(),
            },
        }
