"""Application-owned material mapping and non-destructive calibration workflows.

Optical evidence is separate from the fixed support map and controller authority.
All motion continues through the application's existing MachineService methods.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import threading
import time
import uuid
from typing import Any

import numpy as np

from .calibration.bed import BedPoint
from .calibration.registration import (
    BaseBedCalibrationJob,
    base_bed_grid_mark_sizes,
    base_bed_grid_targets,
    generate_registration_program,
)
from .errors import CalibrationError, MachineError
from .storage import atomic_write_json, read_json
from .vision.fiducials import detect_keyed_crosshair_grid


def identity(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    allow_nan=False).encode()).hexdigest()


class MaterialWorkspaceMixin:
    """Shared desktop/browser implementation; no Qt objects or second owner."""

    @property
    def surface_placement_path(self):
        return self.surface_calibration.path.with_name("surface-placement.json")

    @property
    def surface_capture_path(self):
        return self.surface_calibration.path.with_name("surface-capture.json")

    def _surface_placement_state(self) -> dict[str, Any]:
        if not hasattr(self, "surface_calibration"):
            return {}
        raw = read_json(self.surface_placement_path, None)
        if raw is None:
            return {}
        if (type(raw) is not dict or set(raw) != {"schema_version", "reference", "mount_revision", "enabled"}
                or type(raw.get("schema_version")) is not int or raw.get("schema_version") != 1
                or type(raw.get("enabled")) is not bool
                or type(raw.get("reference")) is not str or not 1 <= len(raw["reference"].strip()) <= 160
                or type(raw.get("mount_revision")) is not str or len(raw["mount_revision"]) != 36):
            raise CalibrationError("Invalid material-placement setup; begin a new height calibration")
        return raw

    def begin_surface_height_calibration(self, reference: str) -> dict[str, Any]:
        self._require_valid_bed_calibration()
        if type(reference) is not str or not 1 <= len(reference.strip()) <= 160:
            raise CalibrationError("Name the fixed height reference")
        state = dict(schema_version=1, reference=reference.strip(),
                     mount_revision=str(uuid.uuid4()), enabled=False)
        atomic_write_json(self.surface_placement_path, state)
        self._clear_material_mapping_cache()
        return state

    def production_surface_binding(self, reference: str | None = None) -> dict[str, Any]:
        state = self._surface_placement_state()
        if not state:
            raise CalibrationError("Begin a new height calibration before collecting production evidence")
        if reference is not None and reference != state["reference"]:
            raise CalibrationError("The named height datum changed; begin a new calibration")
        return {
            **self.surface_calibration_binding(),
            "datum_id": state["reference"],
            "capture_pose": [self.settings.machine.photo_x, self.settings.machine.photo_y,
                             self.settings.machine.photo_z],
            "mount_revision": state["mount_revision"],
            "honeycomb_height_mm": self._saved_surface_honeycomb_height(),
        }

    def _saved_surface_honeycomb_height(self) -> float | None:
        status = getattr(self.machine, "status", lambda: {})()
        value = status.get("honeycomb_height_mm")
        if value is None:
            return None
        if type(value) not in (int, float) or not math.isfinite(value) or not -10 <= value <= 10:
            raise CalibrationError("Saved honeycomb height is invalid")
        return float(value)

    def _clear_material_mapping_cache(self) -> None:
        with self._workspace_lock:
            self._workspace_image = self._workspace_revision = None
            self._composed_map_cache.clear()
            self._material_mapping_cache = None
            self._material_selection_cache = None

    def enable_material_height_correction(self, enabled: bool = True) -> dict[str, Any]:
        if type(enabled) is not bool:
            raise CalibrationError("Height correction selection must be a boolean")
        state = self._surface_placement_state()
        if not state:
            raise CalibrationError("Complete the height calibration first")
        if enabled:
            from .calibration.material_plane import require_precision_model
            model = self.solve_surface_height_model()
            require_precision_model(model)
        atomic_write_json(self.surface_placement_path, {**state, "enabled": enabled})
        self._clear_material_mapping_cache()
        return {**state, "enabled": enabled, "physically_qualified": False}

    def selected_material_surface(self):
        state = self._surface_placement_state()
        if not state.get("enabled"):
            return None
        from .calibration.material_plane import MaterialPlaneSelection
        snapshot = self.machine.material_surface_snapshot()
        if snapshot is None:
            raise CalibrationError("Measure the surface before using height-corrected placement")
        binding = self.production_surface_binding()
        if self.surface_calibration.path.stat().st_size > 2_000_000:
            raise CalibrationError("Surface calibration exceeds its size limit")
        evidence_bytes = self.surface_calibration.path.read_bytes()
        if len(evidence_bytes) > 2_000_000:
            raise CalibrationError("Surface calibration exceeds its size limit")
        key = (identity(snapshot), identity(binding), hashlib.sha256(evidence_bytes).hexdigest())
        cached = getattr(self, "_material_selection_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        model = self.solve_surface_height_model()
        selection = MaterialPlaneSelection.from_measurement(
            model, snapshot, datum_id=state["reference"], binding_id=identity(binding),
            capture_pose=tuple(binding["capture_pose"]),
        )
        self._material_selection_cache = (key, selection)
        return selection

    def material_surface_signature(self) -> tuple | None:
        selection = self.selected_material_surface()
        return None if selection is None else selection.signature

    def material_surface_metadata(self) -> dict[str, Any] | None:
        selection = self.selected_material_surface()
        return None if selection is None else selection.to_dict()

    def _material_preview_state(self) -> tuple[bool, tuple | None]:
        """Allow an explicit support-plane probe preview before the first measurement.

        Invalid measured heights and stale optical evidence still reject. This
        state is observational and never supplies placement or job authority.
        """
        state = self._surface_placement_state()
        if not state.get("enabled"):
            return False, None
        if self.machine.material_surface_snapshot() is not None:
            return False, self.material_surface_signature()
        from .calibration.material_plane import require_precision_model
        binding = self.production_surface_binding()
        evidence = self.surface_calibration.path.read_bytes()
        if len(evidence) > 2_000_000:
            raise CalibrationError("Surface calibration exceeds its size limit")
        key = (identity(binding), hashlib.sha256(evidence).hexdigest())
        cached = getattr(self, "_material_preview_model_cache", None)
        if cached is None or cached[0] != key:
            model = self.solve_surface_height_model()
            require_precision_model(model)
            cached = (key, model.model_id)
            self._material_preview_model_cache = cached
        return True, ("approximate-support", cached[1], key[0])

    def material_preview_is_approximate(self) -> bool:
        return self._material_preview_state()[0]

    def material_preview_signature(self) -> tuple | None:
        return self._material_preview_state()[1]

    def material_preview_mapper(self):
        return self.bed if self.material_preview_is_approximate() else self.material_mapper()

    def material_mapper(self):
        selection = self.selected_material_surface()
        if selection is None:
            return self.bed
        key = (selection.signature, self.bed_mapping_digest())
        cached = getattr(self, "_material_mapping_cache", None)
        if cached is not None and cached[0] == key:
            return cached[1]
        mapper = copy.copy(self.bed)
        mapper._calibration = selection.as_bed_calibration(self.bed.calibration)
        mapper._rectification_map_lock = threading.RLock()
        mapper._rectification_map_cache = {}
        self._material_mapping_cache = (key, mapper)
        return mapper

    def precision_pixels_per_mm(self) -> float:
        """Retain native local sampling; this does not invent camera detail."""
        mapper, lens = self.material_mapper(), self.lens.model
        area = self.settings.machine.work_area
        if mapper.calibration is None or lens is None:
            raise CalibrationError("Current camera and bed calibration are required for precision placement")
        ratios = []
        step = .01
        for y in np.linspace(area.y_min, area.y_max, 5):
            for x in np.linspace(area.x_min, area.x_max, 5):
                source = np.asarray([mapper.mm_to_image(x, y), mapper.mm_to_image(x + step, y),
                                     mapper.mm_to_image(x, y + step)])
                raw = lens.distort_points(source)
                jacobian = np.column_stack(((raw[1] - raw[0]) / step, (raw[2] - raw[0]) / step))
                ratios.append(float(np.linalg.svd(jacobian, compute_uv=False)[0]))
        ppm = max(float(self.settings.calibration.bed.pixels_per_mm), max(ratios))
        limit = math.sqrt(16_000_000 / (area.width * area.height))
        if not math.isfinite(ppm) or ppm <= 0 or ppm > limit:
            raise CalibrationError("Native-detail precision capture exceeds the bounded analysis image size")
        return ppm

    def precision_setup_binding(self) -> dict[str, Any]:
        state = self._surface_placement_state()
        model = self.solve_surface_height_model() if state.get("enabled") else None
        setup_snapshot = getattr(self.machine, "setup_evidence_snapshot", lambda: None)()
        focus_setup = None
        if (setup_snapshot is not None and setup_snapshot.get("calibration_compatible") is True
                and setup_snapshot.get("calibration") is not None):
            calibration = setup_snapshot["calibration"]
            focus_setup = copy.deepcopy({
                "calibration": {key: calibration.get(key) for key in
                                ("id", "focus_offset_mm", "gauge_mm", "firmware", "geometry")},
                "probe_xy_offset_mm": setup_snapshot.get("probe_xy_offset_mm"),
                "firmware_geometry": setup_snapshot.get("firmware_geometry"),
            })
        return {"schema_version": 1, "camera": self.surface_calibration_binding(),
                "mount_revision": state.get("mount_revision"), "datum_id": state.get("reference"),
                "focus_setup": focus_setup,
                "bed_mapping_digest": self.bed_mapping_digest(),
                "capture_pose": [self.settings.machine.photo_x, self.settings.machine.photo_y,
                                 self.settings.machine.photo_z],
                "surface_model": None if model is None else model.model_id,
                "height_binding": None if model is None else self.production_surface_binding(),
                "honeycomb_height_mm": self._saved_surface_honeycomb_height(),
                "target_xy_mm": .1}

    def bind_material_surface_program(self, text: str) -> str:
        selection = self.selected_material_surface()
        if selection is None:
            return text
        from .machine.material_surface import attach
        return attach(text, selection.to_dict())

    def validate_material_surface_program(self, text: str) -> None:
        from .gcode.preview import strip_comment
        from .machine.material_surface import program_binding
        recorded = program_binding(tuple(strip_comment(line) for line in text.splitlines() if strip_comment(line)))
        current = self.material_surface_metadata()
        if recorded != current:
            raise CalibrationError("Material surface or camera model changed after preview; capture and prepare again")

    def precision_assessment_binding(self) -> dict[str, Any]:
        snapshot = getattr(self.machine, "material_surface_snapshot", lambda: None)()
        return {**self.precision_setup_binding(), "measured_surface": self.material_surface_metadata(),
                "probe_measurement": None if snapshot is None else {
                    key: snapshot.get(key) for key in ("id", "measurement_id", "surface_elevation_mm",
                                                     "honeycomb_height_mm", "reference", "session")}}

    def precision_sampling_diagnostics(self) -> dict[str, Any]:
        from .calibration.precision_budget import precision_error_budget
        selection = self.selected_material_surface()
        if selection is not None:
            from .calibration.material_plane import camera_sampling_diagnostics
            diagnostics = camera_sampling_diagnostics(selection.model, self.lens.model,
                                                      height_mm=selection.elevation_mm)
            diagnostics["source_image_size"] = list(self.lens.model.image_size)
            diagnostics["calibration_height_uncertainties_mm"] = {
                slot: evidence.height_uncertainty_mm for slot, evidence in self.surface_calibration.load().items()
            }
            budget = precision_error_budget(optical_fit_max_mm=selection.model.fit_max_mm,
                                            optical_holdout_max_mm=selection.model.check_max_mm)
            diagnostics["numerical_error_budget"] = budget
            remaining = budget["remaining_target_mm"]
            sensitivity = diagnostics["maximum_xy_mm_per_height_mm"]
            diagnostics["remaining_xy_budget_mm"] = remaining
            diagnostics["height_error_from_remaining_xy_budget_mm"] = (None if sensitivity == 0 else remaining / sensitivity)
            return diagnostics
        mapper, lens, area = self.bed, self.lens.model, self.settings.machine.work_area
        if lens is None or mapper.calibration is None:
            raise CalibrationError("Calibrate the camera before measuring native sampling")
        points, values = [], []
        step = .01
        for y in (area.y_min, (area.y_min + area.y_max) / 2, area.y_max):
            for x in (area.x_min, (area.x_min + area.x_max) / 2, area.x_max):
                raw = lens.distort_points(np.asarray([
                    mapper.mm_to_image(x, y), mapper.mm_to_image(x + step, y), mapper.mm_to_image(x, y + step)]))
                jacobian = np.column_stack(((raw[1] - raw[0]) / step, (raw[2] - raw[0]) / step))
                singular = np.linalg.svd(jacobian, compute_uv=False)
                if np.min(singular) <= 1e-9:
                    raise CalibrationError("Camera mapping is singular at a sampling location")
                values.append(float(1 / min(singular)))
                points.append(dict(machine_xy_mm=[x, y], worst_mm_per_source_pixel=values[-1],
                                   xy_mm_per_height_mm=None))
        return dict(samples=points, worst_mm_per_source_pixel=max(values),
                    source_image_size=list(lens.image_size), height_sensitivity=None,
                    numerical_error_budget=precision_error_budget(),
                    note="Source sampling only; physical accuracy and height sensitivity require independent evidence")

    def prepare_surface_height_capture(
        self, slot: str, height_mm: float, reference: str, uncertainty_mm: float = 0., *,
        powered: bool, power_percent: float, mark_size_mm: float, speed_mm_min: float,
    ) -> BaseBedCalibrationJob:
        from .app import _publish_unique_artifact
        self._require_valid_bed_calibration()
        if slot not in {"lower", "upper", "check"}:
            raise CalibrationError("Choose lower, upper, or check evidence")
        if type(powered) is not bool:
            raise CalibrationError("Choose an explicit powered or laser-off calibration job")
        if (type(height_mm) not in (int, float) or not math.isfinite(height_mm)
                or type(uncertainty_mm) not in (int, float) or not math.isfinite(uncertainty_mm)
                or not 0 <= uncertainty_mm <= 1):
            raise CalibrationError("Supply a finite measured height and uncertainty between 0 and 1 mm")
        binding = self.production_surface_binding(reference)
        measurement = self._surface_acquisition_measurement(height_mm, uncertainty_mm) if powered else None
        support = self._required_calibration_support(powered=powered, label="surface-height calibration")
        sizes = base_bed_grid_mark_sizes(mark_size_mm)
        if support is not None:
            targets, _area = self._support_contained_calibration_targets(
                base_bed_grid_targets, support, mark_size_mm=max(sizes.values()),
                boundary_margin_mm=self.settings.laser.boundary_margin_mm,
            )
        else:
            targets = base_bed_grid_targets(self.settings.machine.work_area,
                                           mark_size_mm=max(sizes.values()),
                                           boundary_margin_mm=self.settings.laser.boundary_margin_mm)
        program = generate_registration_program(
            targets, self.settings.laser, self.settings.machine.work_area,
            mark_size_mm=mark_size_mm, power_percent=power_percent, powered=powered,
            speed_mm_min=speed_mm_min, design_name="surface-height-keyed-crosses", mark_sizes_mm=sizes,
        )
        filename, _path = _publish_unique_artifact(
            self.settings.app.data_dir / "generated", stem="surface-height", suffix=".gcode",
            data=program.text.encode("utf-8"),
        )
        session = dict(schema_version=1, kind="surface-height", slot=slot,
                       height_mm=float(height_mm), reference=reference, uncertainty_mm=float(uncertainty_mm),
                       binding=binding, filename=filename, powered=powered,
                       measurement=measurement,
                       program_digest=self.machine.preflight_program(program.text).digest,
                       created_at=time.time(), targets=[target.to_dict() for target in targets],
                       keyed_mark_sizes_mm={str(k): v for k, v in sizes.items()},
                       **self._calibration_support_fields(support),
                       **self._bed_mapping_session_fields(self.bed.calibration))
        atomic_write_json(self.surface_capture_path, session)
        return BaseBedCalibrationJob(program=program, filename=filename, targets=targets,
                                     powered=powered, power_percent=power_percent if powered else 0.,
                                     mark_size_mm=mark_size_mm, display_name="Surface height calibration")

    def _surface_capture_session(self, *, require_executed: bool = True) -> dict[str, Any]:
        session = read_json(self.surface_capture_path, {})
        if (type(session) is not dict or session.get("schema_version") != 1
                or session.get("binding") != self.production_surface_binding()
                or not isinstance(session.get("targets"), list) or len(session["targets"]) != 25):
            raise CalibrationError("Prepare a current surface-height calibration job first")
        self._require_session_bed_mapping(session, self.bed.calibration, "surface-height")
        if session.get("powered") is True:
            try:
                current_measurement = self._surface_acquisition_measurement(session["height_mm"], session["uncertainty_mm"])
            except (KeyError, TypeError, ValueError) as exc:
                raise CalibrationError("Prepared height measurement is invalid; prepare a new target") from exc
            if session.get("measurement") != current_measurement:
                raise CalibrationError("Measured calibration surface changed; prepare and mark a new height target")
            support = self._required_calibration_support(powered=True, label="surface-height calibration")
            if any(session.get(key) != value for key, value in self._calibration_support_fields(support).items()):
                raise CalibrationError("Calibration support changed after marking; prepare a new height target")
        if require_executed:
            if session.get("powered") is not True:
                raise CalibrationError("Run the reviewed powered height marks before capture")
            self._require_session_execution(session, "surface-height")
        return session

    def _surface_acquisition_measurement(self, height_mm: float, uncertainty_mm: float) -> dict[str, Any]:
        """Bind a powered calibration target to its probed top and fixed datum."""
        if (type(height_mm) not in (int, float) or not math.isfinite(height_mm)
                or type(uncertainty_mm) not in (int, float) or not math.isfinite(uncertainty_mm)
                or not 0 <= uncertainty_mm <= 1):
            raise CalibrationError("Supply a finite measured height and uncertainty between 0 and 1 mm")
        snapshot = self.machine.material_surface_snapshot()
        if snapshot is None:
            raise CalibrationError("Probe the calibration target and select measured focus before preparing powered height marks")
        from .machine.material_surface import snapshot_from_plan
        try:
            snapshot = snapshot_from_plan(snapshot)
        except MachineError as exc:
            raise CalibrationError(f"Invalid probed calibration surface: {exc}") from exc
        measured = snapshot["surface_elevation_mm"]
        if abs(height_mm - measured) > uncertainty_mm + .0005 + 1e-9:
            raise CalibrationError(
                f"Entered target height {height_mm:.3f} mm disagrees with probed elevation {measured:.3f} mm; "
                "resolve the height datum or measurement uncertainty before calibration"
            )
        return json.loads(json.dumps({
            key: snapshot[key] for key in ("id", "measurement_id", "surface_elevation_mm",
                                          "honeycomb_height_mm", "reference", "session")
        }, allow_nan=False))

    def capture_surface_height_evidence(self) -> tuple[np.ndarray, dict[str, Any]]:
        self._require_camera_calibration_ready()
        session = self._surface_capture_session()
        signature = identity(session)
        self.machine.prepare_photo_position()
        with self.machine.temporary_stepper_hold():
            burst = self.precision_camera_burst(undistort=False)
        burst = self._prepare_camera_burst(burst, undistort=True)
        image = burst.sharpest_frame.copy()
        detection = detect_keyed_crosshair_grid(image, session["targets"])
        if signature != identity(self._surface_capture_session()):
            raise CalibrationError("Height calibration changed during capture")
        detection = {**detection, "session_id": signature,
                     "acquisition": {key: session[key] for key in
                                     ("slot", "height_mm", "reference", "uncertainty_mm")},
                     "source_digest": hashlib.sha256(image.tobytes()).hexdigest(),
                     "binding": session["binding"], "precision_capture": burst.diagnostics()}
        self._surface_detection = copy.deepcopy(detection)
        return image, detection

    def save_surface_height_evidence(
        self, slot: str, height_mm: float, reference: str, uncertainty_mm: float,
        detection: dict[str, Any],
    ) -> dict[str, Any]:
        session = self._surface_capture_session()
        if (detection != getattr(self, "_surface_detection", None)
                or detection.get("session_id") != identity(session)
                or not detection.get("detected")
                or (slot, height_mm, reference, uncertainty_mm) !=
                   (session["slot"], session["height_mm"], session["reference"], session["uncertainty_mm"])):
            raise CalibrationError("Review a fresh complete detection for this measured height")
        raw = detection.get("points", [])
        expected = {p["id"]: (p["machine_x"], p["machine_y"]) for p in session["targets"]}
        if len(raw) != 25 or {p["id"]: (p["machine_x"], p["machine_y"]) for p in raw} != expected:
            raise CalibrationError("Height observations do not match the commanded calibration targets")
        points = [BedPoint(p["image_x"], p["image_y"], p["machine_x"], p["machine_y"]) for p in raw]
        result = self.surface_calibration.save_observations(
            slot, height_mm=height_mm, reference=reference, points=points,
            source_digest=detection["source_digest"], lens=self.lens.model,
            work_area=self.settings.machine.work_area, binding=session["binding"],
            height_uncertainty_mm=uncertainty_mm,
        )
        self._clear_material_mapping_cache()
        return result.to_dict()
