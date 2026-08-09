from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..storage import atomic_write_json, read_json

LOGGER = logging.getLogger(__name__)
HONEYCOMB_SUPPORT_SCHEMA_VERSION = 1


def _finite_point(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must contain exactly two coordinates")
    if any(
        type(item) is bool
        or not isinstance(item, (int, float, np.integer, np.floating))
        for item in value
    ):
        raise ValueError(f"{label} coordinates must be numbers")
    point = (float(value[0]), float(value[1]))
    if not all(math.isfinite(item) for item in point):
        raise ValueError(f"{label} coordinates must be finite")
    return point


def _finite_number(value: Any, label: str) -> float:
    if type(value) is bool or not isinstance(
        value, (int, float, np.integer, np.floating)
    ):
        raise ValueError(f"{label} must be a number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


@dataclass(frozen=True, slots=True)
class HoneycombSupportReference:
    """Physical support geometry measured through an active bed map.

    This is an approximate visual reference. It never changes calibration,
    controller bounds, detection selection, or the guarded laser-output area.
    """

    ruler_origin_machine_mm: tuple[float, float]
    ruler_x_mark_machine_mm: tuple[float, float]
    ruler_xy_mark_machine_mm: tuple[float, float]
    ruler_mark_mm: float
    support_width_mm: float
    support_height_mm: float
    created_at: float
    bed_calibration_created_at: float

    @classmethod
    def from_observations(
        cls,
        *,
        ruler_origin_machine_mm: tuple[float, float],
        ruler_x_mark_machine_mm: tuple[float, float],
        ruler_xy_mark_machine_mm: tuple[float, float],
        ruler_mark_mm: float,
        support_width_mm: float,
        support_height_mm: float,
        bed_calibration_created_at: float,
        created_at: float | None = None,
    ) -> HoneycombSupportReference:
        reference = cls(
            ruler_origin_machine_mm=ruler_origin_machine_mm,
            ruler_x_mark_machine_mm=ruler_x_mark_machine_mm,
            ruler_xy_mark_machine_mm=ruler_xy_mark_machine_mm,
            ruler_mark_mm=ruler_mark_mm,
            support_width_mm=support_width_mm,
            support_height_mm=support_height_mm,
            created_at=time.time() if created_at is None else created_at,
            bed_calibration_created_at=bed_calibration_created_at,
        )
        return cls.from_dict(reference.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> HoneycombSupportReference:
        if not isinstance(raw, dict):
            raise ValueError("Honeycomb support reference must be an object")
        schema = raw.get("schema_version")
        if type(schema) is not int or schema != HONEYCOMB_SUPPORT_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported honeycomb support schema; record the ruler reference again"
            )
        reference = cls(
            ruler_origin_machine_mm=_finite_point(
                raw.get("ruler_origin_machine_mm"), "Ruler origin"
            ),
            ruler_x_mark_machine_mm=_finite_point(
                raw.get("ruler_x_mark_machine_mm"), "Ruler X mark"
            ),
            ruler_xy_mark_machine_mm=_finite_point(
                raw.get("ruler_xy_mark_machine_mm"), "Ruler X/Y mark"
            ),
            ruler_mark_mm=_finite_number(raw.get("ruler_mark_mm"), "Ruler mark"),
            support_width_mm=_finite_number(
                raw.get("support_width_mm"), "Support width"
            ),
            support_height_mm=_finite_number(
                raw.get("support_height_mm"), "Support height"
            ),
            created_at=_finite_number(raw.get("created_at"), "Creation time"),
            bed_calibration_created_at=_finite_number(
                raw.get("bed_calibration_created_at"),
                "Bed calibration creation time",
            ),
        )
        reference._validate_geometry()
        return reference

    def _validate_geometry(self) -> None:
        mark = self.ruler_mark_mm
        width = self.support_width_mm
        height = self.support_height_mm
        if mark <= 0.0 or width <= 0.0 or height <= 0.0:
            raise ValueError("Ruler mark and support dimensions must be positive")
        if mark > width or mark > height:
            raise ValueError("The far ruler mark must lie within the physical support")
        if width > 2_000.0 or height > 2_000.0:
            raise ValueError("Honeycomb support dimensions exceed 2000 mm")

        origin = np.asarray(self.ruler_origin_machine_mm, dtype=np.float64)
        x_mark = np.asarray(self.ruler_x_mark_machine_mm, dtype=np.float64)
        xy_mark = np.asarray(self.ruler_xy_mark_machine_mm, dtype=np.float64)
        x_vector = x_mark - origin
        y_vector = xy_mark - x_mark
        x_length = float(np.linalg.norm(x_vector))
        y_length = float(np.linalg.norm(y_vector))
        if not 0.80 * mark <= x_length <= 1.20 * mark:
            raise ValueError(
                f"The selected X ruler span measures {x_length:.1f} mm; "
                f"expected approximately {mark:g} mm"
            )
        if not 0.80 * mark <= y_length <= 1.20 * mark:
            raise ValueError(
                f"The selected Y ruler span measures {y_length:.1f} mm; "
                f"expected approximately {mark:g} mm"
            )
        cosine = abs(float(np.dot(x_vector, y_vector) / (x_length * y_length)))
        if cosine > math.sin(math.radians(15.0)):
            raise ValueError("The selected ruler axes are not close to perpendicular")

    @property
    def x_basis_machine_per_mm(self) -> np.ndarray:
        return (
            np.asarray(self.ruler_x_mark_machine_mm, dtype=np.float64)
            - np.asarray(self.ruler_origin_machine_mm, dtype=np.float64)
        ) / self.ruler_mark_mm

    @property
    def y_basis_machine_per_mm(self) -> np.ndarray:
        return (
            np.asarray(self.ruler_xy_mark_machine_mm, dtype=np.float64)
            - np.asarray(self.ruler_x_mark_machine_mm, dtype=np.float64)
        ) / self.ruler_mark_mm

    @property
    def support_corners_machine_mm(self) -> tuple[tuple[float, float], ...]:
        origin = np.asarray(self.ruler_origin_machine_mm, dtype=np.float64)
        x_edge = self.x_basis_machine_per_mm * self.support_width_mm
        y_edge = self.y_basis_machine_per_mm * self.support_height_mm
        corners = (origin, origin + x_edge, origin + x_edge + y_edge, origin + y_edge)
        return tuple((float(point[0]), float(point[1])) for point in corners)

    @property
    def measured_ruler_span_mm(self) -> tuple[float, float]:
        return (
            float(
                np.linalg.norm(
                    np.asarray(self.ruler_x_mark_machine_mm, dtype=np.float64)
                    - np.asarray(self.ruler_origin_machine_mm, dtype=np.float64)
                )
            ),
            float(
                np.linalg.norm(
                    np.asarray(self.ruler_xy_mark_machine_mm, dtype=np.float64)
                    - np.asarray(self.ruler_x_mark_machine_mm, dtype=np.float64)
                )
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": HONEYCOMB_SUPPORT_SCHEMA_VERSION,
            "ruler_origin_machine_mm": list(self.ruler_origin_machine_mm),
            "ruler_x_mark_machine_mm": list(self.ruler_x_mark_machine_mm),
            "ruler_xy_mark_machine_mm": list(self.ruler_xy_mark_machine_mm),
            "ruler_mark_mm": self.ruler_mark_mm,
            "support_width_mm": self.support_width_mm,
            "support_height_mm": self.support_height_mm,
            "created_at": self.created_at,
            "bed_calibration_created_at": self.bed_calibration_created_at,
        }


class HoneycombSupportStore:
    def __init__(self, data_dir: Path) -> None:
        self.path = data_dir / "honeycomb_support.json"
        self._load_error: str | None = None
        self._reference = self._load()

    @property
    def reference(self) -> HoneycombSupportReference | None:
        return self._reference

    @property
    def load_error(self) -> str | None:
        return self._load_error

    def _load(self) -> HoneycombSupportReference | None:
        raw = read_json(self.path)
        if raw is None and not self.path.exists():
            return None
        try:
            return HoneycombSupportReference.from_dict(raw)
        except (KeyError, OverflowError, TypeError, ValueError) as exc:
            self._load_error = f"Saved honeycomb support reference is invalid: {exc}"
            LOGGER.warning("Ignoring invalid honeycomb support reference: %s", exc)
            return None

    def save(self, reference: HoneycombSupportReference) -> None:
        canonical = HoneycombSupportReference.from_dict(reference.to_dict())
        atomic_write_json(self.path, canonical.to_dict())
        self._reference = canonical
        self._load_error = None

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
        self._reference = None
        self._load_error = None
