from __future__ import annotations

import copy
import logging
import math
import threading
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
        image_to_machine = np.asarray(raw["image_to_machine"], dtype=np.float64)
        machine_to_image = np.asarray(raw["machine_to_image"], dtype=np.float64)
        rms_error = float(raw["rms_error_mm"])
        max_error = float(raw["max_error_mm"])
        inlier_count = int(raw["inlier_count"])
        point_count = int(raw["point_count"])
        created_at = float(raw["created_at"])
        if (
            image_to_machine.shape != (3, 3)
            or machine_to_image.shape != (3, 3)
            or not np.isfinite(image_to_machine).all()
            or not np.isfinite(machine_to_image).all()
            or abs(float(np.linalg.det(image_to_machine))) < 1e-12
            or abs(float(np.linalg.det(machine_to_image))) < 1e-12
        ):
            raise ValueError("Bed calibration backup homographies are invalid")
        product = image_to_machine @ machine_to_image
        if abs(float(product[2, 2])) < 1e-12:
            raise ValueError("Bed calibration backup homographies are not mutual inverses")
        product /= product[2, 2]
        if not np.allclose(product, np.eye(3), atol=1e-5, rtol=1e-5):
            raise ValueError("Bed calibration backup homographies are not mutual inverses")
        if (
            not all(math.isfinite(value) for value in (rms_error, max_error, created_at))
            or rms_error < 0
            or max_error < 0
            or point_count < 4
            or not 4 <= inlier_count <= point_count
        ):
            raise ValueError("Bed calibration backup metrics or point counts are invalid")
        return cls(
            image_to_machine=image_to_machine,
            machine_to_image=machine_to_image,
            rms_error_mm=rms_error,
            max_error_mm=max_error,
            inlier_count=inlier_count,
            point_count=point_count,
            created_at=created_at,
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
        created_at = float(raw["created_at"])
        fit_rms = float(raw["fit_rms_mm"])
        fit_max = float(raw["fit_max_mm"])
        refinement_count = int(raw.get("refinement_count", 0))
        if (
            not all(math.isfinite(value) for value in (created_at, fit_rms, fit_max))
            or fit_rms < 0
            or fit_max < 0
            or refinement_count not in {0, 1}
        ):
            raise ValueError("Invalid residual correction mesh metadata")
        if float(np.max(np.linalg.norm(corrections, axis=2))) > 3.0:
            raise ValueError("Residual correction exceeds the 3 mm safety bound")
        dx = np.diff(corrections, axis=1) / np.diff(x_nodes)[None, :, None]
        dy = np.diff(corrections, axis=0) / np.diff(y_nodes)[:, None, None]
        if float(np.max(np.abs(dx))) > 0.08 or float(np.max(np.abs(dy))) > 0.08:
            raise ValueError("Residual correction changes too sharply between neighboring cells")
        return cls(
            x_nodes_mm=x_nodes,
            y_nodes_mm=y_nodes,
            corrections_mm=corrections,
            created_at=created_at,
            fit_rms_mm=fit_rms,
            fit_max_mm=fit_max,
            refinement_count=refinement_count,
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
    provenance: dict[str, Any] | None = None

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
            "provenance": copy.deepcopy(self.provenance),
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BedCalibration:
        registration = raw.get("fine_registration") or {}
        axis_mapping = raw.get("axis_mapping") or {}
        refinement = registration.get("homography_refinement") or {}
        base = refinement.get("base_calibration")
        image_to_machine = np.asarray(raw["image_to_machine"], dtype=np.float64)
        machine_to_image = np.asarray(raw["machine_to_image"], dtype=np.float64)
        if (
            image_to_machine.shape != (3, 3)
            or machine_to_image.shape != (3, 3)
            or not np.isfinite(image_to_machine).all()
            or not np.isfinite(machine_to_image).all()
            or abs(float(np.linalg.det(image_to_machine))) < 1e-12
            or abs(float(np.linalg.det(machine_to_image))) < 1e-12
        ):
            raise ValueError("Bed calibration homographies must be finite invertible 3x3 matrices")
        image_width = int(raw["image_width"])
        image_height = int(raw["image_height"])
        rms_error = float(raw["rms_error_mm"])
        max_error = float(raw["max_error_mm"])
        inlier_count = int(raw["inlier_count"])
        point_count = int(raw["point_count"])
        created_at = float(raw.get("created_at", 0.0))
        if image_width <= 0 or image_height <= 0:
            raise ValueError("Bed calibration image dimensions must be positive")
        if (
            not all(math.isfinite(value) for value in (rms_error, max_error, created_at))
            or rms_error < 0
            or max_error < 0
            or point_count < 4
            or not 4 <= inlier_count <= point_count
        ):
            raise ValueError("Bed calibration metrics or point counts are invalid")

        def optional_axis(name: str) -> bool | None:
            value = axis_mapping.get(name)
            if value is None:
                return None
            if type(value) is not bool:
                raise ValueError(f"axis_mapping.{name} must be a JSON boolean or null")
            return value

        provenance = raw.get("provenance")
        if provenance is not None and not isinstance(provenance, dict):
            raise ValueError("Bed calibration provenance must be an object or null")
        registration_x = float(registration.get("translation_x_mm", 0.0))
        registration_y = float(registration.get("translation_y_mm", 0.0))
        registration_created_at = (
            None if registration.get("created_at") is None else float(registration["created_at"])
        )
        refinement_created_at = (
            None if refinement.get("created_at") is None else float(refinement["created_at"])
        )
        optional_times = tuple(
            value
            for value in (registration_created_at, refinement_created_at)
            if value is not None
        )
        if (
            not all(
                math.isfinite(value)
                for value in (registration_x, registration_y, *optional_times)
            )
            or math.hypot(registration_x, registration_y) > 5.0 + 1e-9
        ):
            raise ValueError("Bed calibration registration state is invalid")
        calibration = cls(
            image_to_machine=image_to_machine,
            machine_to_image=machine_to_image,
            image_width=image_width,
            image_height=image_height,
            rms_error_mm=rms_error,
            max_error_mm=max_error,
            inlier_count=inlier_count,
            point_count=point_count,
            created_at=created_at,
            registration_x_mm=registration_x,
            registration_y_mm=registration_y,
            registration_created_at=registration_created_at,
            refinement_base=(None if not isinstance(base, dict) else BedCalibrationBackup.from_dict(base)),
            refinement_created_at=refinement_created_at,
            axis_reversed_x=optional_axis("reverse_x"),
            axis_reversed_y=optional_axis("reverse_y"),
            residual_mesh=(
                None
                if not isinstance(raw.get("residual_mesh"), dict)
                else BedResidualMesh.from_dict(raw["residual_mesh"])
            ),
            provenance=copy.deepcopy(provenance),
        )
        product = calibration.image_to_machine @ calibration.machine_to_image
        product /= product[2, 2]
        if not np.allclose(product, np.eye(3), atol=1e-5, rtol=1e-5):
            raise ValueError("Bed calibration homographies are not mutual inverses")
        return calibration


class BedMapper:
    def __init__(self, data_dir: Path, settings: BedCalibrationSettings, work_area: WorkArea):
        self.data_dir = data_dir
        self.settings = settings
        self.work_area = work_area
        self.points_path = data_dir / "bed_points.json"
        self.model_path = data_dir / "bed_calibration.json"
        self._points_file_valid = True
        self._calibration_unavailable_reason: str | None = None
        self._pending_axis_mapping: tuple[bool | None, bool | None] | None = None
        self._points = self._load_points()
        self._calibration = self._load_calibration()
        self._rectification_map_lock = threading.RLock()
        self._rectification_map_cache: dict[
            tuple[int, float],
            tuple[BedCalibration, tuple[np.ndarray, np.ndarray]],
        ] = {}

    @property
    def points(self) -> list[BedPoint]:
        return list(self._points)

    @property
    def calibration(self) -> BedCalibration | None:
        return self._calibration

    @property
    def calibration_unavailable_reason(self) -> str | None:
        return self._calibration_unavailable_reason

    @staticmethod
    def _point_from_payload(item: Any) -> BedPoint:
        if not isinstance(item, dict):
            raise ValueError("Bed calibration point must be an object")
        point = BedPoint(**item)
        coordinates = (
            point.image_x,
            point.image_y,
            point.machine_x,
            point.machine_y,
        )
        if any(
            type(value) is bool
            or not isinstance(value, (int, float, np.integer, np.floating))
            for value in coordinates
        ) or not all(math.isfinite(float(value)) for value in coordinates):
            raise ValueError("Bed calibration point coordinates must be finite numbers")
        if not isinstance(point.label, str):
            raise ValueError("Bed calibration point label must be text")
        return BedPoint(*(float(value) for value in coordinates), label=point.label)

    @classmethod
    def _points_from_payload(cls, raw: Any) -> list[BedPoint]:
        if not isinstance(raw, list):
            raise ValueError("Bed calibration points must be an array")
        return [cls._point_from_payload(item) for item in raw]

    @classmethod
    def _canonical_points(cls, points: list[BedPoint]) -> list[BedPoint]:
        try:
            return cls._points_from_payload([asdict(point) for point in points])
        except (TypeError, ValueError) as exc:
            raise CalibrationError(f"Invalid bed calibration point: {exc}") from None

    @staticmethod
    def _points_match(left: list[BedPoint], right: list[BedPoint]) -> bool:
        # json.dump/json.load round-trips finite Python floats exactly. Requiring
        # exact coordinates, labels, and order prevents a tolerance from hiding
        # a separately persisted calibration generation.
        return len(left) == len(right) and all(
            asdict(left_point) == asdict(right_point)
            for left_point, right_point in zip(left, right, strict=True)
        )

    def _load_points(self) -> list[BedPoint]:
        raw = read_json(self.points_path, [])
        points: list[BedPoint] = []
        if not isinstance(raw, list):
            self._points_file_valid = False
            return points
        for item in raw:
            try:
                points.append(self._point_from_payload(item))
            except (TypeError, ValueError):
                self._points_file_valid = False
                LOGGER.warning("Skipping invalid bed calibration point: %r", item)
        return points

    def _save_points(self) -> None:
        atomic_write_json(self.points_path, [asdict(point) for point in self._points])
        self._points_file_valid = True

    def _load_calibration(self) -> BedCalibration | None:
        raw = read_json(self.model_path)
        if raw is None and not self.model_path.exists():
            return None
        try:
            if not isinstance(raw, dict) or not raw:
                raise ValueError("bed calibration file is empty or unreadable")
            calibration = BedCalibration.from_dict(raw)
            embedded_points = self._points_from_payload(raw.get("points"))
            if not self._points_file_valid or not self._points_match(
                self._points,
                embedded_points,
            ):
                raise ValueError(
                    "separate bed points do not match the point generation embedded in the model"
                )
            return calibration
        except (KeyError, OverflowError, TypeError, ValueError) as exc:
            self._calibration_unavailable_reason = (
                "Saved bed calibration is unavailable because its persisted point "
                f"generation is invalid or stale: {exc}"
            )
            LOGGER.warning("Ignoring invalid bed calibration file: %s", exc)
            return None

    def _save_unsolved_points(self, points: list[BedPoint]) -> None:
        replacement = self._canonical_points(points)
        previous_points = self._points
        previous_calibration = self._calibration
        previous_reason = self._calibration_unavailable_reason
        previous_axis_mapping = self._pending_axis_mapping
        had_model = previous_calibration is not None or self.model_path.exists()
        self._points = replacement
        if previous_calibration is not None:
            self._pending_axis_mapping = (
                previous_calibration.axis_reversed_x,
                previous_calibration.axis_reversed_y,
            )
        self._calibration = None
        self._calibration_unavailable_reason = (
            "Saved bed points changed after the bed calibration was solved; "
            "solve the bed mapping again"
            if had_model
            else None
        )
        try:
            self._save_points()
        except Exception:
            self._points = previous_points
            self._calibration = previous_calibration
            self._calibration_unavailable_reason = previous_reason
            self._pending_axis_mapping = previous_axis_mapping
            raise

    def add_point(self, point: BedPoint) -> int:
        self._save_unsolved_points([*self._points, point])
        return len(self._points) - 1

    def replace_points(self, points: list[BedPoint]) -> None:
        self._save_unsolved_points(list(points))

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
                for point in self._points
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
                for point in self._points
            ]
        replacement, errors, inlier_mask = self._fit_points(
            reflected,
            calibration.image_width,
            calibration.image_height,
            axis_reversed_x=(
                desired if normalized == "x" else calibration.axis_reversed_x
            ),
            axis_reversed_y=(
                desired if normalized == "y" else calibration.axis_reversed_y
            ),
        )
        replacement = replace(
            replacement,
            provenance=copy.deepcopy(calibration.provenance),
        )
        return self._install_points_and_calibration(
            reflected,
            replacement,
            errors,
            inlier_mask,
        )

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
        replacement = list(self._points)
        del replacement[index]
        self._save_unsolved_points(replacement)

    def clear(self) -> None:
        self._save_unsolved_points([])
        self.model_path.unlink(missing_ok=True)
        self._calibration = None
        self._calibration_unavailable_reason = None
        self._pending_axis_mapping = None

    def _persist_calibration(self, analysis: dict[str, Any] | None = None) -> None:
        calibration = self._require()
        existing = read_json(self.model_path, {})
        payload = dict(existing) if isinstance(existing, dict) else {}
        payload.update(calibration.to_dict())
        payload["points"] = [asdict(point) for point in self._points]
        if analysis is not None:
            payload["fine_registration"]["analysis"] = analysis
        atomic_write_json(self.model_path, payload)
        self._calibration_unavailable_reason = None

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

    def _fit_points(
        self,
        points: list[BedPoint],
        image_width: int,
        image_height: int,
        *,
        axis_reversed_x: bool | None,
        axis_reversed_y: bool | None,
    ) -> tuple[BedCalibration, np.ndarray, np.ndarray]:
        if len(points) < self.settings.minimum_points:
            raise CalibrationError(
                f"Need at least {self.settings.minimum_points} point pairs; have {len(points)}"
            )
        image_points = np.asarray([[point.image_x, point.image_y] for point in points], dtype=np.float64)
        machine_points = np.asarray([[point.machine_x, point.machine_y] for point in points], dtype=np.float64)
        if not np.isfinite(image_points).all() or not np.isfinite(machine_points).all():
            raise CalibrationError("Bed calibration points must contain finite coordinates")
        if len(points) == 4:
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
        inlier_mask = np.ones(len(points), dtype=bool) if mask is None else mask.reshape(-1).astype(bool)
        inlier_errors = errors[inlier_mask]
        rms = float(np.sqrt(np.mean(np.square(inlier_errors)))) if len(inlier_errors) else float("inf")
        maximum = float(np.max(inlier_errors)) if len(inlier_errors) else float("inf")

        return (
            BedCalibration(
            image_to_machine=homography,
            machine_to_image=inverse,
            image_width=int(image_width),
            image_height=int(image_height),
            rms_error_mm=rms,
            max_error_mm=maximum,
            inlier_count=int(np.sum(inlier_mask)),
            point_count=len(points),
            created_at=time.time(),
            axis_reversed_x=axis_reversed_x,
            axis_reversed_y=axis_reversed_y,
            ),
            errors,
            inlier_mask,
        )

    @staticmethod
    def _calibration_payload(
        calibration: BedCalibration,
        points: list[BedPoint],
        errors: np.ndarray,
        inlier_mask: np.ndarray,
    ) -> dict[str, Any]:
        payload = calibration.to_dict()
        payload["points"] = [asdict(point) for point in points]
        payload["point_errors_mm"] = errors.tolist()
        payload["inliers"] = inlier_mask.tolist()
        return payload

    def solve(
        self,
        image_width: int,
        image_height: int,
        *,
        provenance: dict[str, Any] | None = None,
    ) -> BedCalibration:
        if self._calibration is not None:
            prior_x = self._calibration.axis_reversed_x
            prior_y = self._calibration.axis_reversed_y
        elif self._pending_axis_mapping is not None:
            prior_x, prior_y = self._pending_axis_mapping
        else:
            prior_x = False
            prior_y = False
        calibration, errors, inlier_mask = self._fit_points(
            self._points,
            image_width,
            image_height,
            axis_reversed_x=prior_x,
            axis_reversed_y=prior_y,
        )
        calibration = replace(calibration, provenance=copy.deepcopy(provenance))
        payload = self._calibration_payload(calibration, self._points, errors, inlier_mask)
        atomic_write_json(self.model_path, payload)
        self._calibration = calibration
        self._calibration_unavailable_reason = None
        self._pending_axis_mapping = None
        return calibration

    def analyze_replacement(
        self,
        points: list[BedPoint],
        image_width: int,
        image_height: int,
    ) -> dict[str, Any]:
        replacement = self._canonical_points(list(points))
        calibration, errors, inlier_mask = self._fit_points(
            replacement,
            image_width,
            image_height,
            axis_reversed_x=None,
            axis_reversed_y=None,
        )
        payload = calibration.to_dict()
        payload["point_errors_mm"] = errors.tolist()
        payload["inliers"] = inlier_mask.tolist()
        return payload

    def replace_points_and_solve(
        self,
        points: list[BedPoint],
        image_width: int,
        image_height: int,
        *,
        provenance: dict[str, Any] | None = None,
        axis_reversed_x: bool | None = None,
        axis_reversed_y: bool | None = None,
    ) -> BedCalibration:
        """Install a fresh base map without exposing mixed old/new persisted state."""
        if axis_reversed_x is not None and type(axis_reversed_x) is not bool:
            raise CalibrationError("Fresh bed-map X orientation must be a boolean or null")
        if axis_reversed_y is not None and type(axis_reversed_y) is not bool:
            raise CalibrationError("Fresh bed-map Y orientation must be a boolean or null")
        replacement = self._canonical_points(list(points))
        calibration, errors, inlier_mask = self._fit_points(
            replacement,
            image_width,
            image_height,
            axis_reversed_x=axis_reversed_x,
            axis_reversed_y=axis_reversed_y,
        )
        calibration = replace(calibration, provenance=copy.deepcopy(provenance))

        return self._install_points_and_calibration(
            replacement,
            calibration,
            errors,
            inlier_mask,
        )

    def _install_points_and_calibration(
        self,
        replacement: list[BedPoint],
        calibration: BedCalibration,
        errors: np.ndarray,
        inlier_mask: np.ndarray,
    ) -> BedCalibration:
        points_payload = [asdict(point) for point in replacement]
        model_payload = self._calibration_payload(
            calibration,
            replacement,
            errors,
            inlier_mask,
        )
        old_points_present = self.points_path.exists()
        old_model_present = self.model_path.exists()
        old_points_payload = read_json(self.points_path, [])
        old_model_payload = read_json(self.model_path, {})
        try:
            atomic_write_json(self.points_path, points_payload)
            atomic_write_json(self.model_path, model_payload)
        except Exception:
            if old_points_present:
                atomic_write_json(self.points_path, old_points_payload)
            else:
                self.points_path.unlink(missing_ok=True)
            if old_model_present:
                atomic_write_json(self.model_path, old_model_payload)
            else:
                self.model_path.unlink(missing_ok=True)
            raise
        self._points = replacement
        self._calibration = calibration
        self._calibration_unavailable_reason = None
        self._pending_axis_mapping = None
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
        ppm = float(
            self.settings.pixels_per_mm
            if pixels_per_mm is None
            else pixels_per_mm
        )
        if not np.isfinite(ppm) or ppm <= 0:
            raise CalibrationError("Rectification pixels_per_mm must be finite and positive")
        machine_to_canvas = np.array(
            [
                [ppm, 0.0, -self.work_area.x_min * ppm],
                [0.0, -ppm, self.work_area.y_max * ppm],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )
        return machine_to_canvas @ calibration.image_to_machine

    def rectification_map(
        self,
        pixels_per_mm: float | None = None,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Map corrected-camera pixels into the top-down workspace canvas."""

        calibration = self._require()
        ppm = float(
            self.settings.pixels_per_mm
            if pixels_per_mm is None
            else pixels_per_mm
        )
        if not np.isfinite(ppm) or ppm <= 0:
            raise CalibrationError("Rectification pixels_per_mm must be finite and positive")
        key = (id(calibration), ppm)
        with self._rectification_map_lock:
            cached = self._rectification_map_cache.get(key)
            if cached is not None and cached[0] is calibration:
                return cached[1]

        output_width = max(1, int(round(self.work_area.width * ppm)))
        output_height = max(1, int(round(self.work_area.height * ppm)))
        canvas_x = self.work_area.x_min + np.arange(output_width) / ppm
        canvas_y = self.work_area.y_max - np.arange(output_height) / ppm
        xx, yy = np.meshgrid(canvas_x, canvas_y)
        target = np.column_stack((xx.ravel(), yy.ravel()))
        base = target.copy()
        if calibration.residual_mesh is not None:
            for _ in range(12):
                base = target - self._mesh_correction(calibration.residual_mesh, base)
        source = cv2.perspectiveTransform(
            base.reshape(-1, 1, 2),
            calibration.machine_to_image,
        ).reshape(output_height, output_width, 2)
        if not np.isfinite(source).all():
            raise CalibrationError("Bed rectification produced non-finite camera coordinates")
        map_x = np.ascontiguousarray(source[:, :, 0], dtype=np.float32)
        map_y = np.ascontiguousarray(source[:, :, 1], dtype=np.float32)
        map_x.setflags(write=False)
        map_y.setflags(write=False)
        result = (map_x, map_y)
        with self._rectification_map_lock:
            # Retain the calibration object with its maps. Keeping only id()
            # permits CPython to reuse that id after two unsampled updates.
            self._rectification_map_cache = {key: (calibration, result)}
        return result

    def rectify(self, image: np.ndarray, pixels_per_mm: float | None = None) -> np.ndarray:
        calibration = self._require()
        height, width = image.shape[:2]
        if (width, height) != (calibration.image_width, calibration.image_height):
            raise CalibrationError(
                "Current camera resolution does not match the bed calibration "
                f"({width}x{height} vs {calibration.image_width}x{calibration.image_height})"
        )
        ppm = float(
            self.settings.pixels_per_mm
            if pixels_per_mm is None
            else pixels_per_mm
        )
        map_x, map_y = self.rectification_map(ppm)
        if self._calibration is not calibration:
            raise CalibrationError("Bed calibration changed while rectification was being prepared")
        rectified = cv2.remap(
            image,
            map_x,
            map_y,
            cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(35, 35, 35),
        )
        if self._calibration is not calibration:
            raise CalibrationError("Bed calibration changed while the camera image was rectified")
        return rectified

    def status(self) -> dict[str, Any]:
        status = {
            "calibrated": self._calibration is not None,
            "model_present": self.model_path.exists(),
            "calibration_unavailable_reason": self._calibration_unavailable_reason,
            "calibration": None if self._calibration is None else self._calibration.to_dict(),
            "points": [asdict(point) for point in self._points],
            "minimum_points": self.settings.minimum_points,
            "pixels_per_mm": self.settings.pixels_per_mm,
        }
        if self._calibration is not None:
            status["axis_mapping"] = self.axis_mapping_state()
        return status
