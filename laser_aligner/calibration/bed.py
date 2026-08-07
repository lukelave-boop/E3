from __future__ import annotations

import logging
import math
import time
from dataclasses import asdict, dataclass, replace
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
class BedCalibrationBackup:
    image_to_machine: np.ndarray
    machine_to_image: np.ndarray
    rms_error_mm: float
    max_error_mm: float
    inlier_count: int
    point_count: int
    created_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_to_machine": self.image_to_machine.tolist(),
            "machine_to_image": self.machine_to_image.tolist(),
            "rms_error_mm": self.rms_error_mm,
            "max_error_mm": self.max_error_mm,
            "inlier_count": self.inlier_count,
            "point_count": self.point_count,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BedCalibrationBackup:
        return cls(
            image_to_machine=np.asarray(raw["image_to_machine"], dtype=np.float64),
            machine_to_image=np.asarray(raw["machine_to_image"], dtype=np.float64),
            rms_error_mm=float(raw["rms_error_mm"]),
            max_error_mm=float(raw["max_error_mm"]),
            inlier_count=int(raw["inlier_count"]),
            point_count=int(raw["point_count"]),
            created_at=float(raw["created_at"]),
        )


@dataclass(slots=True)
class BedResidualMesh:
    """Small, locally interpolated correction layered over the bed homography."""

    x_nodes_mm: np.ndarray
    y_nodes_mm: np.ndarray
    corrections_mm: np.ndarray
    created_at: float
    fit_rms_mm: float
    fit_max_mm: float
    refinement_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "x_nodes_mm": self.x_nodes_mm.tolist(),
            "y_nodes_mm": self.y_nodes_mm.tolist(),
            "corrections_mm": self.corrections_mm.tolist(),
            "created_at": self.created_at,
            "fit_rms_mm": self.fit_rms_mm,
            "fit_max_mm": self.fit_max_mm,
            "refinement_count": self.refinement_count,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BedResidualMesh:
        x_nodes = np.asarray(raw["x_nodes_mm"], dtype=np.float64)
        y_nodes = np.asarray(raw["y_nodes_mm"], dtype=np.float64)
        corrections = np.asarray(raw["corrections_mm"], dtype=np.float64)
        if (
            x_nodes.ndim != 1
            or y_nodes.ndim != 1
            or len(x_nodes) < 2
            or len(y_nodes) < 2
            or corrections.shape != (len(y_nodes), len(x_nodes), 2)
            or not np.isfinite(x_nodes).all()
            or not np.isfinite(y_nodes).all()
            or not np.isfinite(corrections).all()
            or np.any(np.diff(x_nodes) <= 0)
            or np.any(np.diff(y_nodes) <= 0)
        ):
            raise ValueError("Invalid residual correction mesh")
        return cls(
            x_nodes_mm=x_nodes,
            y_nodes_mm=y_nodes,
            corrections_mm=corrections,
            created_at=float(raw["created_at"]),
            fit_rms_mm=float(raw["fit_rms_mm"]),
            fit_max_mm=float(raw["fit_max_mm"]),
            refinement_count=int(raw.get("refinement_count", 0)),
        )


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
    registration_x_mm: float = 0.0
    registration_y_mm: float = 0.0
    registration_created_at: float | None = None
    refinement_base: BedCalibrationBackup | None = None
    refinement_created_at: float | None = None
    axis_reversed_x: bool | None = None
    axis_reversed_y: bool | None = None
    residual_mesh: BedResidualMesh | None = None

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
            "axis_mapping": {
                "reverse_x": self.axis_reversed_x,
                "reverse_y": self.axis_reversed_y,
            },
            "fine_registration": {
                "translation_x_mm": self.registration_x_mm,
                "translation_y_mm": self.registration_y_mm,
                "created_at": self.registration_created_at,
                "homography_refinement": (
                    None
                    if self.refinement_base is None
                    else {
                        "created_at": self.refinement_created_at,
                        "base_calibration": self.refinement_base.to_dict(),
                    }
                ),
            },
            "residual_mesh": (None if self.residual_mesh is None else self.residual_mesh.to_dict()),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BedCalibration:
        registration = raw.get("fine_registration") or {}
        axis_mapping = raw.get("axis_mapping") or {}
        refinement = registration.get("homography_refinement") or {}
        base = refinement.get("base_calibration")
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
            registration_x_mm=float(registration.get("translation_x_mm", 0.0)),
            registration_y_mm=float(registration.get("translation_y_mm", 0.0)),
            registration_created_at=(
                None if registration.get("created_at") is None else float(registration["created_at"])
            ),
            refinement_base=(None if not isinstance(base, dict) else BedCalibrationBackup.from_dict(base)),
            refinement_created_at=(None if refinement.get("created_at") is None else float(refinement["created_at"])),
            axis_reversed_x=(None if axis_mapping.get("reverse_x") is None else bool(axis_mapping["reverse_x"])),
            axis_reversed_y=(None if axis_mapping.get("reverse_y") is None else bool(axis_mapping["reverse_y"])),
            residual_mesh=(
                None
                if not isinstance(raw.get("residual_mesh"), dict)
                else BedResidualMesh.from_dict(raw["residual_mesh"])
            ),
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

    def axis_mapping_state(self) -> dict[str, dict[str, bool]]:
        """Return effective and explicitly recorded bed-map axis orientation."""
        calibration = self._require()

        def inferred(axis: str) -> bool:
            center_x = calibration.image_width * 0.5
            center_y = calibration.image_height * 0.5
            center = np.asarray([[[center_x, center_y]]], dtype=np.float64)
            offset = center.copy()
            offset[0, 0, 0 if axis == "x" else 1] += 1.0
            mapped = cv2.perspectiveTransform(
                np.concatenate((center, offset), axis=0),
                calibration.image_to_machine,
            ).reshape(-1, 2)
            component = 0 if axis == "x" else 1
            return bool(mapped[1, component] - mapped[0, component] < 0.0)

        return {
            "x": {
                "reversed": (inferred("x") if calibration.axis_reversed_x is None else calibration.axis_reversed_x),
                "recorded": calibration.axis_reversed_x is not None,
            },
            "y": {
                "reversed": (inferred("y") if calibration.axis_reversed_y is None else calibration.axis_reversed_y),
                "recorded": calibration.axis_reversed_y is not None,
            },
        }

    def set_machine_axis_reversed(self, axis: str, enabled: bool) -> BedCalibration:
        """Set and persist one explicit bed-map axis orientation."""
        normalized = axis.strip().lower()
        if normalized not in {"x", "y"}:
            raise CalibrationError("Bed mapping axis must be X or Y")
        calibration = self._require()
        if not self._points:
            raise CalibrationError("Bed mapping has no saved point pairs to reverse")

        state = self.axis_mapping_state()[normalized]
        desired = bool(enabled)
        if state["reversed"] == desired:
            self._calibration = replace(
                calibration,
                axis_reversed_x=(desired if normalized == "x" else calibration.axis_reversed_x),
                axis_reversed_y=(desired if normalized == "y" else calibration.axis_reversed_y),
            )
            self._persist_calibration()
            return self._calibration

        original_points = list(self._points)
        if normalized == "x":
            axis_sum = self.work_area.x_min + self.work_area.x_max
            reflected = [
                BedPoint(
                    point.image_x,
                    point.image_y,
                    axis_sum - point.machine_x,
                    point.machine_y,
                    point.label,
                )
                for point in original_points
            ]
        else:
            axis_sum = self.work_area.y_min + self.work_area.y_max
            reflected = [
                BedPoint(
                    point.image_x,
                    point.image_y,
                    point.machine_x,
                    axis_sum - point.machine_y,
                    point.label,
                )
                for point in original_points
            ]

        self._points = reflected
        try:
            self._save_points()
            solved = self.solve(calibration.image_width, calibration.image_height)
            self._calibration = replace(
                solved,
                axis_reversed_x=(desired if normalized == "x" else calibration.axis_reversed_x),
                axis_reversed_y=(desired if normalized == "y" else calibration.axis_reversed_y),
            )
            self._persist_calibration()
            return self._calibration
        except Exception:
            self._points = original_points
            self._save_points()
            self.solve(calibration.image_width, calibration.image_height)
            raise

    def reverse_machine_axis(self, axis: str) -> BedCalibration:
        """Compatibility toggle for callers that do not supply an explicit state."""
        normalized = axis.strip().lower()
        if normalized not in {"x", "y"}:
            raise CalibrationError("Bed mapping axis must be X or Y")
        state = self.axis_mapping_state()[normalized]
        return self.set_machine_axis_reversed(normalized, not state["reversed"])

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

    def _persist_calibration(self, analysis: dict[str, Any] | None = None) -> None:
        calibration = self._require()
        existing = read_json(self.model_path, {})
        payload = dict(existing) if isinstance(existing, dict) else {}
        payload.update(calibration.to_dict())
        payload["points"] = [asdict(point) for point in self._points]
        if analysis is not None:
            payload["fine_registration"]["analysis"] = analysis
        atomic_write_json(self.model_path, payload)

    def apply_registration_translation(
        self,
        correction_x_mm: float,
        correction_y_mm: float,
        *,
        analysis: dict[str, Any] | None = None,
    ) -> BedCalibration:
        calibration = self._require()
        correction = np.asarray([float(correction_x_mm), float(correction_y_mm)], dtype=np.float64)
        if not np.isfinite(correction).all():
            raise CalibrationError("Fine-registration correction must be finite")
        total = (
            np.asarray(
                [calibration.registration_x_mm, calibration.registration_y_mm],
                dtype=np.float64,
            )
            + correction
        )
        if float(np.linalg.norm(total)) > 5.0 + 1e-9:
            raise CalibrationError("Fine-registration translation exceeds the 5 mm limit; redo the full bed mapping")
        transform = np.asarray(
            [
                [1.0, 0.0, correction[0]],
                [0.0, 1.0, correction[1]],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
        image_to_machine = transform @ calibration.image_to_machine
        if not np.isfinite(image_to_machine).all() or abs(float(np.linalg.det(image_to_machine))) < 1e-12:
            raise CalibrationError("Fine-registration correction produced an invalid transform")
        self._calibration = replace(
            calibration,
            image_to_machine=image_to_machine,
            machine_to_image=np.linalg.inv(image_to_machine),
            registration_x_mm=float(total[0]),
            registration_y_mm=float(total[1]),
            registration_created_at=time.time(),
        )
        self._persist_calibration(analysis)
        return self._calibration

    def reset_registration_translation(self) -> BedCalibration:
        calibration = self._require()
        correction_x = calibration.registration_x_mm
        correction_y = calibration.registration_y_mm
        if math.hypot(correction_x, correction_y) <= 1e-12:
            return calibration
        inverse = np.asarray(
            [
                [1.0, 0.0, -correction_x],
                [0.0, 1.0, -correction_y],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
        image_to_machine = inverse @ calibration.image_to_machine
        self._calibration = replace(
            calibration,
            image_to_machine=image_to_machine,
            machine_to_image=np.linalg.inv(image_to_machine),
            registration_x_mm=0.0,
            registration_y_mm=0.0,
            registration_created_at=None,
        )
        self._persist_calibration()
        return self._calibration

    def apply_registration_homography(
        self,
        image_to_machine: np.ndarray,
        *,
        analysis: dict[str, Any],
    ) -> BedCalibration:
        calibration = self._require()
        if not analysis.get("can_apply_full_map"):
            raise CalibrationError("The reviewed full-bed refinement did not pass its gates")
        analyzed_base = np.asarray(analysis.get("base_image_to_machine", []), dtype=np.float64)
        if analyzed_base.shape != (3, 3) or not np.allclose(
            analyzed_base, calibration.image_to_machine, rtol=1e-10, atol=1e-10
        ):
            raise CalibrationError("The bed map changed after this refinement was analyzed; recapture the marks")
        if calibration.refinement_base is not None:
            raise CalibrationError("A full-bed refinement is already applied; reset it before applying another")
        if math.hypot(calibration.registration_x_mm, calibration.registration_y_mm) > 1e-12:
            raise CalibrationError("Reset the fine-registration translation before applying a full-bed refinement")
        proposed = np.asarray(image_to_machine, dtype=np.float64)
        if proposed.shape != (3, 3) or not np.isfinite(proposed).all() or abs(float(np.linalg.det(proposed))) < 1e-12:
            raise CalibrationError("The proposed full-bed refinement is invalid")
        inverse = np.linalg.inv(proposed)
        if not np.isfinite(inverse).all():
            raise CalibrationError("The proposed full-bed refinement is not invertible")

        relative = proposed @ calibration.machine_to_image
        corners = np.asarray(
            [
                [self.work_area.x_min, self.work_area.y_min],
                [self.work_area.x_max, self.work_area.y_min],
                [self.work_area.x_max, self.work_area.y_max],
                [self.work_area.x_min, self.work_area.y_max],
                [
                    (self.work_area.x_min + self.work_area.x_max) * 0.5,
                    (self.work_area.y_min + self.work_area.y_max) * 0.5,
                ],
            ],
            dtype=np.float64,
        )
        mapped = cv2.perspectiveTransform(corners.reshape(-1, 1, 2), relative).reshape(-1, 2)
        if not np.isfinite(mapped).all() or float(np.max(np.linalg.norm(mapped - corners, axis=1))) > 8.0:
            raise CalibrationError("The proposed full-bed refinement moves part of the bed by more than 8 mm")

        backup = BedCalibrationBackup(
            image_to_machine=calibration.image_to_machine.copy(),
            machine_to_image=calibration.machine_to_image.copy(),
            rms_error_mm=calibration.rms_error_mm,
            max_error_mm=calibration.max_error_mm,
            inlier_count=calibration.inlier_count,
            point_count=calibration.point_count,
            created_at=calibration.created_at,
        )
        self._calibration = replace(
            calibration,
            image_to_machine=proposed,
            machine_to_image=inverse,
            rms_error_mm=float(analysis["rms_error_mm"]),
            max_error_mm=float(analysis["max_error_mm"]),
            inlier_count=int(analysis["inlier_count"]),
            point_count=int(analysis["selected_count"]),
            created_at=time.time(),
            refinement_base=backup,
            refinement_created_at=time.time(),
        )
        self._persist_calibration(analysis)
        return self._calibration

    def reset_registration_homography(self) -> BedCalibration:
        calibration = self._require()
        backup = calibration.refinement_base
        if backup is None:
            return calibration
        if math.hypot(calibration.registration_x_mm, calibration.registration_y_mm) > 1e-12:
            raise CalibrationError("Reset the fine-registration translation before resetting the full-bed refinement")
        self._calibration = replace(
            calibration,
            image_to_machine=backup.image_to_machine,
            machine_to_image=backup.machine_to_image,
            rms_error_mm=backup.rms_error_mm,
            max_error_mm=backup.max_error_mm,
            inlier_count=backup.inlier_count,
            point_count=backup.point_count,
            created_at=backup.created_at,
            refinement_base=None,
            refinement_created_at=None,
        )
        self._persist_calibration()
        return self._calibration

    @staticmethod
    def _mesh_correction(mesh: BedResidualMesh, points_mm: np.ndarray) -> np.ndarray:
        """Vectorized bilinear interpolation, clamped to the calibrated mesh edge."""
        points = np.asarray(points_mm, dtype=np.float64).reshape(-1, 2)
        x = np.clip(points[:, 0], mesh.x_nodes_mm[0], mesh.x_nodes_mm[-1])
        y = np.clip(points[:, 1], mesh.y_nodes_mm[0], mesh.y_nodes_mm[-1])
        ix = np.clip(np.searchsorted(mesh.x_nodes_mm, x) - 1, 0, len(mesh.x_nodes_mm) - 2)
        iy = np.clip(np.searchsorted(mesh.y_nodes_mm, y) - 1, 0, len(mesh.y_nodes_mm) - 2)
        x0, x1 = mesh.x_nodes_mm[ix], mesh.x_nodes_mm[ix + 1]
        y0, y1 = mesh.y_nodes_mm[iy], mesh.y_nodes_mm[iy + 1]
        tx = ((x - x0) / (x1 - x0))[:, None]
        ty = ((y - y0) / (y1 - y0))[:, None]
        c00 = mesh.corrections_mm[iy, ix]
        c10 = mesh.corrections_mm[iy, ix + 1]
        c01 = mesh.corrections_mm[iy + 1, ix]
        c11 = mesh.corrections_mm[iy + 1, ix + 1]
        return c00 * (1 - tx) * (1 - ty) + c10 * tx * (1 - ty) + c01 * (1 - tx) * ty + c11 * tx * ty

    def apply_residual_mesh(
        self,
        x_nodes_mm: np.ndarray,
        y_nodes_mm: np.ndarray,
        corrections_mm: np.ndarray,
        *,
        fit_rms_mm: float,
        fit_max_mm: float,
    ) -> BedCalibration:
        calibration = self._require()
        if calibration.residual_mesh is not None:
            raise CalibrationError("A local correction mesh is already applied; reset it first")
        mesh = BedResidualMesh(
            np.asarray(x_nodes_mm, dtype=np.float64),
            np.asarray(y_nodes_mm, dtype=np.float64),
            np.asarray(corrections_mm, dtype=np.float64),
            time.time(),
            float(fit_rms_mm),
            float(fit_max_mm),
            0,
        )
        # Reuse strict persistence validation and bound both displacement and
        # local distortion before allowing this nonlinear map into service.
        mesh = BedResidualMesh.from_dict(mesh.to_dict())
        magnitudes = np.linalg.norm(mesh.corrections_mm, axis=2)
        if float(np.max(magnitudes)) > 3.0:
            raise CalibrationError("Local correction exceeds the 3 mm safety bound")
        dx = np.diff(mesh.corrections_mm, axis=1) / np.diff(mesh.x_nodes_mm)[None, :, None]
        dy = np.diff(mesh.corrections_mm, axis=0) / np.diff(mesh.y_nodes_mm)[:, None, None]
        if float(np.max(np.abs(dx))) > 0.08 or float(np.max(np.abs(dy))) > 0.08:
            raise CalibrationError("Local correction changes too sharply between neighboring cells")
        self._calibration = replace(calibration, residual_mesh=mesh)
        self._persist_calibration()
        return self._calibration

    def refine_residual_mesh(
        self,
        delta_corrections_mm: np.ndarray,
        *,
        analyzed_mesh_created_at: float,
        predicted_rms_mm: float,
        predicted_max_mm: float,
    ) -> BedCalibration:
        calibration = self._require()
        current = calibration.residual_mesh
        if current is None:
            raise CalibrationError("Apply the dense local correction before refining it")
        if current.refinement_count != 0:
            raise CalibrationError("The local mesh has already been refined; run shifted confirmation instead")
        if not math.isclose(current.created_at, float(analyzed_mesh_created_at), rel_tol=0.0, abs_tol=1e-6):
            raise CalibrationError("The local mesh changed after validation; capture a new validation grid")
        delta = np.asarray(delta_corrections_mm, dtype=np.float64)
        if delta.shape != current.corrections_mm.shape or not np.isfinite(delta).all():
            raise CalibrationError("The proposed validation refinement is invalid")
        if float(np.max(np.linalg.norm(delta, axis=2))) > 1.5:
            raise CalibrationError("Validation refinement exceeds the 1.5 mm update bound")
        updated = current.corrections_mm + delta
        # Validate the total field through the same displacement and gradient
        # gates used by the original mesh before replacing it atomically.
        replacement = BedResidualMesh(
            x_nodes_mm=current.x_nodes_mm.copy(),
            y_nodes_mm=current.y_nodes_mm.copy(),
            corrections_mm=updated,
            created_at=time.time(),
            fit_rms_mm=float(predicted_rms_mm),
            fit_max_mm=float(predicted_max_mm),
            refinement_count=1,
        )
        replacement = BedResidualMesh.from_dict(replacement.to_dict())
        if float(np.max(np.linalg.norm(updated, axis=2))) > 3.0:
            raise CalibrationError("Refined local correction exceeds the 3 mm total bound")
        dx = np.diff(updated, axis=1) / np.diff(current.x_nodes_mm)[None, :, None]
        dy = np.diff(updated, axis=0) / np.diff(current.y_nodes_mm)[:, None, None]
        if float(np.max(np.abs(dx))) > 0.08 or float(np.max(np.abs(dy))) > 0.08:
            raise CalibrationError("Refined correction changes too sharply between cells")
        self._calibration = replace(calibration, residual_mesh=replacement)
        self._persist_calibration()
        return self._calibration

    def reset_residual_mesh(self) -> BedCalibration:
        calibration = self._require()
        if calibration.residual_mesh is None:
            return calibration
        self._calibration = replace(calibration, residual_mesh=None)
        self._persist_calibration()
        return self._calibration

    def solve(self, image_width: int, image_height: int) -> BedCalibration:
        if len(self._points) < self.settings.minimum_points:
            raise CalibrationError(
                f"Need at least {self.settings.minimum_points} point pairs; have {len(self._points)}"
            )
        image_points = np.asarray([[point.image_x, point.image_y] for point in self._points], dtype=np.float64)
        machine_points = np.asarray([[point.machine_x, point.machine_y] for point in self._points], dtype=np.float64)
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
        predicted = cv2.perspectiveTransform(image_points.astype(np.float32).reshape(-1, 1, 2), homography).reshape(
            -1, 2
        )
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
            axis_reversed_x=(False if self._calibration is None else self._calibration.axis_reversed_x),
            axis_reversed_y=(False if self._calibration is None else self._calibration.axis_reversed_y),
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
        if calibration.residual_mesh is not None:
            result = result + self._mesh_correction(calibration.residual_mesh, result.reshape(1, 2))[0]
        return float(result[0]), float(result[1])

    def mm_to_image(self, machine_x: float, machine_y: float) -> tuple[float, float]:
        calibration = self._require()
        base_point = np.asarray([[machine_x, machine_y]], dtype=np.float64)
        if calibration.residual_mesh is not None:
            target = base_point.copy()
            for _ in range(12):
                base_point = target - self._mesh_correction(calibration.residual_mesh, base_point)
        point = base_point.reshape(1, 1, 2)
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
        if calibration.residual_mesh is not None:
            canvas_x = self.work_area.x_min + np.arange(output_width) / ppm
            canvas_y = self.work_area.y_max - np.arange(output_height) / ppm
            xx, yy = np.meshgrid(canvas_x, canvas_y)
            target = np.column_stack((xx.ravel(), yy.ravel()))
            base = target.copy()
            for _ in range(12):
                base = target - self._mesh_correction(calibration.residual_mesh, base)
            source = (
                cv2.perspectiveTransform(base.reshape(-1, 1, 2), calibration.machine_to_image)
                .reshape(output_height, output_width, 2)
                .astype(np.float32)
            )
            return cv2.remap(
                image,
                source[:, :, 0],
                source[:, :, 1],
                cv2.INTER_LINEAR,
                borderMode=cv2.BORDER_CONSTANT,
                borderValue=(35, 35, 35),
            )
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
        status = {
            "calibrated": self._calibration is not None,
            "calibration": None if self._calibration is None else self._calibration.to_dict(),
            "points": [asdict(point) for point in self._points],
            "minimum_points": self.settings.minimum_points,
            "pixels_per_mm": self.settings.pixels_per_mm,
        }
        if self._calibration is not None:
            status["axis_mapping"] = self.axis_mapping_state()
        return status
