from __future__ import annotations

import hashlib
import json
import logging
import math
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from ..storage import atomic_write_json, read_json

LOGGER = logging.getLogger(__name__)
HONEYCOMB_SUPPORT_SCHEMA_VERSION = 2
HONEYCOMB_COORDINATE_FRAME_VERSION = 2
_HONEYCOMB_COORDINATE_FRAME_KIND = "honeycomb-rigid-frame"
_LEGACY_MEASUREMENT_METHOD = "legacy-three-point"
_AUTOMATIC_MEASUREMENT_METHOD = "automatic-four-edge-fit"
_SEMANTIC_CORNER_ORDER = ("origin", "+x", "opposite", "+y")


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
class HoneycombCoordinateFrame:
    """Rigid honeycomb-local coordinates expressed in machine millimetres.

    The frame origin is the observed shared ruler zero. Its axes are the
    closest orientation-preserving orthonormal fit to the independently
    observed X and Y ruler directions. Keeping the axes rigid prevents small
    detection disagreement from shearing project geometry.
    """

    origin_machine_mm: tuple[float, float]
    x_axis_machine: tuple[float, float]
    y_axis_machine: tuple[float, float]
    width_mm: float
    height_mm: float
    provenance_digest: str

    def __post_init__(self) -> None:
        origin = _finite_point(self.origin_machine_mm, "Coordinate-frame origin")
        x_axis = _finite_point(self.x_axis_machine, "Coordinate-frame X axis")
        y_axis = _finite_point(self.y_axis_machine, "Coordinate-frame Y axis")
        width = _finite_number(self.width_mm, "Coordinate-frame width")
        height = _finite_number(self.height_mm, "Coordinate-frame height")
        if width <= 0.0 or height <= 0.0:
            raise ValueError("Coordinate-frame dimensions must be positive")
        x_length = math.hypot(*x_axis)
        y_length = math.hypot(*y_axis)
        dot = x_axis[0] * y_axis[0] + x_axis[1] * y_axis[1]
        determinant = x_axis[0] * y_axis[1] - x_axis[1] * y_axis[0]
        if (
            not math.isclose(x_length, 1.0, abs_tol=1e-12)
            or not math.isclose(y_length, 1.0, abs_tol=1e-12)
            or not math.isclose(dot, 0.0, abs_tol=1e-12)
            or not math.isclose(determinant, 1.0, abs_tol=1e-12)
        ):
            raise ValueError(
                "Coordinate-frame axes must form a right-handed orthonormal basis"
            )
        digest = self.provenance_digest
        if (
            type(digest) is not str
            or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise ValueError("Coordinate-frame provenance digest must be SHA-256")
        object.__setattr__(self, "origin_machine_mm", origin)
        object.__setattr__(self, "x_axis_machine", x_axis)
        object.__setattr__(self, "y_axis_machine", y_axis)
        object.__setattr__(self, "width_mm", width)
        object.__setattr__(self, "height_mm", height)

    @property
    def local_bounds_mm(self) -> tuple[float, float, float, float]:
        """Return local ``(x_min, y_min, x_max, y_max)`` bounds."""

        return (0.0, 0.0, self.width_mm, self.height_mm)

    @property
    def provenance_signature(self) -> tuple[str, int, str]:
        """Return an immutable signature suitable for binding prepared work."""

        return (
            _HONEYCOMB_COORDINATE_FRAME_KIND,
            HONEYCOMB_COORDINATE_FRAME_VERSION,
            self.provenance_digest,
        )

    def local_to_machine(
        self,
        x_mm: float,
        y_mm: float,
    ) -> tuple[float, float]:
        """Transform one honeycomb-local point into machine coordinates."""

        local_x = _finite_number(x_mm, "Honeycomb-local X")
        local_y = _finite_number(y_mm, "Honeycomb-local Y")
        origin_x, origin_y = self.origin_machine_mm
        x_axis_x, x_axis_y = self.x_axis_machine
        y_axis_x, y_axis_y = self.y_axis_machine
        return (
            origin_x + local_x * x_axis_x + local_y * y_axis_x,
            origin_y + local_x * x_axis_y + local_y * y_axis_y,
        )

    def machine_to_local(
        self,
        x_mm: float,
        y_mm: float,
    ) -> tuple[float, float]:
        """Transform one machine point into honeycomb-local coordinates."""

        machine_x = _finite_number(x_mm, "Machine X")
        machine_y = _finite_number(y_mm, "Machine Y")
        delta_x = machine_x - self.origin_machine_mm[0]
        delta_y = machine_y - self.origin_machine_mm[1]
        return (
            delta_x * self.x_axis_machine[0]
            + delta_y * self.x_axis_machine[1],
            delta_x * self.y_axis_machine[0]
            + delta_y * self.y_axis_machine[1],
        )

    @property
    def corners_machine_mm(self) -> tuple[tuple[float, float], ...]:
        """Return lower-left, +X, opposite, and +Y rigid-frame corners."""

        return (
            self.local_to_machine(0.0, 0.0),
            self.local_to_machine(self.width_mm, 0.0),
            self.local_to_machine(self.width_mm, self.height_mm),
            self.local_to_machine(0.0, self.height_mm),
        )


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
    schema_version: int = 1
    raw_corners_machine_mm: tuple[tuple[float, float], ...] | None = None
    corner_topology: tuple[int, int, int, int] | None = None
    measurement_method: str = _LEGACY_MEASUREMENT_METHOD
    taught_reference_digest: str | None = None

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
            schema_version=1,
            measurement_method=_LEGACY_MEASUREMENT_METHOD,
        )
        return cls.from_dict(reference.to_dict())

    @classmethod
    def from_four_corner_observations(
        cls,
        *,
        raw_corners_machine_mm: tuple[tuple[float, float], ...],
        corner_topology: tuple[int, int, int, int],
        support_width_mm: float,
        support_height_mm: float,
        bed_calibration_created_at: float,
        measurement_method: str = _AUTOMATIC_MEASUREMENT_METHOD,
        taught_reference_digest: str | None = None,
        created_at: float | None = None,
    ) -> HoneycombSupportReference:
        """Create execution-grade evidence from four independently fitted corners.

        ``raw_corners_machine_mm`` preserves detector order. ``corner_topology``
        records which raw entries mean shared zero, +X, opposite, and +Y. The
        redundant legacy points remain in schema 2 so display-only consumers can
        continue to show the ruler topology without inventing a fourth corner.
        """

        raw_corners = tuple(
            _finite_point(point, f"Raw corner {index}")
            for index, point in enumerate(raw_corners_machine_mm)
        )
        if len(raw_corners) != 4:
            raise ValueError(
                "Four-corner honeycomb evidence must contain exactly four corners"
            )
        topology = cls._validated_topology(corner_topology)
        ordered = tuple(raw_corners[index] for index in topology)
        reference = cls(
            ruler_origin_machine_mm=ordered[0],
            ruler_x_mark_machine_mm=ordered[1],
            ruler_xy_mark_machine_mm=ordered[2],
            ruler_mark_mm=float(support_width_mm),
            support_width_mm=support_width_mm,
            support_height_mm=support_height_mm,
            created_at=time.time() if created_at is None else created_at,
            bed_calibration_created_at=bed_calibration_created_at,
            schema_version=HONEYCOMB_SUPPORT_SCHEMA_VERSION,
            raw_corners_machine_mm=raw_corners,
            corner_topology=topology,
            measurement_method=measurement_method,
            taught_reference_digest=taught_reference_digest,
        )
        return cls.from_dict(reference.to_dict())

    @classmethod
    def from_dict(cls, raw: Any) -> HoneycombSupportReference:
        if not isinstance(raw, dict):
            raise ValueError("Honeycomb support reference must be an object")
        raw_dict = raw
        schema = raw_dict.get("schema_version")
        if type(schema) is not int or schema not in {1, HONEYCOMB_SUPPORT_SCHEMA_VERSION}:
            raise ValueError(
                "Unsupported honeycomb support schema; record the ruler reference again"
            )
        raw_corners: tuple[tuple[float, float], ...] | None = None
        topology: tuple[int, int, int, int] | None = None
        measurement_method = _LEGACY_MEASUREMENT_METHOD
        taught_reference_digest: str | None = None
        if schema == HONEYCOMB_SUPPORT_SCHEMA_VERSION:
            corner_payload = raw_dict.get("raw_corners_machine_mm")
            if not isinstance(corner_payload, (list, tuple)) or len(corner_payload) != 4:
                raise ValueError(
                    "Schema-2 honeycomb evidence must contain four raw machine corners"
                )
            raw_corners = tuple(
                _finite_point(point, f"Raw corner {index}")
                for index, point in enumerate(corner_payload)
            )
            topology = cls._validated_topology(raw_dict.get("corner_topology"))
            if raw_dict.get("corner_topology_semantics") != list(
                _SEMANTIC_CORNER_ORDER
            ):
                raise ValueError(
                    "Schema-2 corner topology semantics must be origin, +X, "
                    "opposite, +Y"
                )
            measurement_method = raw_dict.get("measurement_method")
            if type(measurement_method) is not str or measurement_method != _AUTOMATIC_MEASUREMENT_METHOD:
                raise ValueError(
                    "Schema-2 honeycomb evidence requires automatic four-edge measurement"
                )
            taught_reference_digest = raw_dict.get("taught_reference_digest")
            if taught_reference_digest is not None and (
                type(taught_reference_digest) is not str
                or len(taught_reference_digest) != 64
                or any(
                    character not in "0123456789abcdef"
                    for character in taught_reference_digest
                )
            ):
                raise ValueError("Taught-reference digest must be SHA-256 or null")
        reference = cls(
            ruler_origin_machine_mm=_finite_point(
                raw_dict.get("ruler_origin_machine_mm"), "Ruler origin"
            ),
            ruler_x_mark_machine_mm=_finite_point(
                raw_dict.get("ruler_x_mark_machine_mm"), "Ruler X mark"
            ),
            ruler_xy_mark_machine_mm=_finite_point(
                raw_dict.get("ruler_xy_mark_machine_mm"), "Ruler X/Y mark"
            ),
            ruler_mark_mm=_finite_number(
                raw_dict.get("ruler_mark_mm"), "Ruler mark"
            ),
            support_width_mm=_finite_number(
                raw_dict.get("support_width_mm"), "Support width"
            ),
            support_height_mm=_finite_number(
                raw_dict.get("support_height_mm"), "Support height"
            ),
            created_at=_finite_number(raw_dict.get("created_at"), "Creation time"),
            bed_calibration_created_at=_finite_number(
                raw_dict.get("bed_calibration_created_at"),
                "Bed calibration creation time",
            ),
            schema_version=schema,
            raw_corners_machine_mm=raw_corners,
            corner_topology=topology,
            measurement_method=measurement_method,
            taught_reference_digest=taught_reference_digest,
        )
        reference._validate_geometry()
        return reference

    @staticmethod
    def _validated_topology(value: Any) -> tuple[int, int, int, int]:
        if not isinstance(value, (list, tuple)) or len(value) != 4:
            raise ValueError(
                "Corner topology must identify origin, +X, opposite, and +Y"
            )
        if any(type(index) is not int for index in value):
            raise ValueError("Corner topology indices must be integers")
        topology = tuple(value)
        if set(topology) != {0, 1, 2, 3}:
            raise ValueError("Corner topology must be a permutation of 0, 1, 2, 3")
        return topology  # type: ignore[return-value]

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

        if self.has_four_corner_evidence:
            assert self.raw_corners_machine_mm is not None
            assert self.corner_topology is not None
            raw_corners = np.asarray(self.raw_corners_machine_mm, dtype=np.float64)
            corners = raw_corners[np.asarray(self.corner_topology)]
            redundant = np.asarray(
                (
                    self.ruler_origin_machine_mm,
                    self.ruler_x_mark_machine_mm,
                    self.ruler_xy_mark_machine_mm,
                ),
                dtype=np.float64,
            )
            if not np.allclose(redundant, corners[:3], atol=1e-9, rtol=0.0):
                raise ValueError(
                    "Schema-2 ruler points disagree with the four-corner topology"
                )
            edges = np.roll(corners, -1, axis=0) - corners
            lengths = np.linalg.norm(edges, axis=1)
            expected = np.asarray((width, height, width, height), dtype=np.float64)
            # The four values are intersections of independently fitted ruler
            # baselines after a calibrated perspective map. A 1.5% teaching
            # gate admits sub-three-millimetre line/intersection noise while
            # remaining far tighter than the legacy ±20% plausibility check.
            side_tolerance = max(2.0, max(width, height) * 0.015)
            maximum_side_error = float(np.max(np.abs(lengths - expected)))
            if maximum_side_error > side_tolerance + 1e-9:
                raise ValueError(
                    "Automatic honeycomb side lengths disagree with the nominal "
                    f"rectangle by {maximum_side_error:.2f} mm (limit "
                    f"{side_tolerance:.2f} mm)"
                )
            closure_error = float(np.linalg.norm(corners[0] + corners[2] - corners[1] - corners[3]))
            closure_tolerance = max(2.0, max(width, height) * 0.015)
            if closure_error > closure_tolerance + 1e-9:
                raise ValueError(
                    "Automatic honeycomb corners do not close as one rectangle: "
                    f"{closure_error:.2f} mm residual (limit "
                    f"{closure_tolerance:.2f} mm)"
                )
            following = np.roll(edges, -1, axis=0)
            cross_products = (
                edges[:, 0] * following[:, 1]
                - edges[:, 1] * following[:, 0]
            )
            if not (np.all(cross_products > 0.0) or np.all(cross_products < 0.0)):
                raise ValueError(
                    "Automatic honeycomb corners are not one convex rectangle"
                )
            angle_tolerance_deg = 2.0
            for index in range(4):
                first = edges[index]
                second = edges[(index + 1) % 4]
                cosine = abs(
                    float(
                        np.dot(first, second)
                        / (lengths[index] * lengths[(index + 1) % 4])
                    )
                )
                angle_error = math.degrees(math.asin(min(1.0, cosine)))
                if angle_error > angle_tolerance_deg + 1e-9:
                    raise ValueError(
                        "Automatic honeycomb corners are not square: corner "
                        f"{index + 1} differs from 90 degrees by "
                        f"{angle_error:.2f} degrees (limit "
                        f"{angle_tolerance_deg:.2f} degrees)"
                    )
            return

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
    def coordinate_frame(self) -> HoneycombCoordinateFrame:
        """Derive the closest rigid frame while preserving semantic topology.

        Schema 2 fits all four independent corners to the ideal local rectangle
        using a proper rigid transform. Schema 1 remains display-compatible and
        uses its historical three-point closest-axis fit, but cannot authorize
        execution.
        """

        if self.has_four_corner_evidence:
            corners = np.asarray(self.support_corners_machine_mm, dtype=np.float64)
            ideal = np.asarray(
                (
                    (0.0, 0.0),
                    (self.support_width_mm, 0.0),
                    (self.support_width_mm, self.support_height_mm),
                    (0.0, self.support_height_mm),
                ),
                dtype=np.float64,
            )
            ideal_center = np.mean(ideal, axis=0)
            observed_center = np.mean(corners, axis=0)
            covariance = (ideal - ideal_center).T @ (corners - observed_center)
            left, _singular, right_transpose = np.linalg.svd(covariance)
            rotation = right_transpose.T @ left.T
            if float(np.linalg.det(rotation)) < 0.0:
                right_transpose[-1, :] *= -1.0
                rotation = right_transpose.T @ left.T
            fitted_origin = observed_center - ideal_center @ rotation.T
            return HoneycombCoordinateFrame(
                origin_machine_mm=(
                    float(fitted_origin[0]),
                    float(fitted_origin[1]),
                ),
                x_axis_machine=(float(rotation[0, 0]), float(rotation[1, 0])),
                y_axis_machine=(float(rotation[0, 1]), float(rotation[1, 1])),
                width_mm=self.support_width_mm,
                height_mm=self.support_height_mm,
                provenance_digest=self.coordinate_frame_digest,
            )

        origin = np.asarray(self.ruler_origin_machine_mm, dtype=np.float64)
        x_mark = np.asarray(self.ruler_x_mark_machine_mm, dtype=np.float64)
        xy_mark = np.asarray(self.ruler_xy_mark_machine_mm, dtype=np.float64)
        observed_x = (x_mark - origin) / self.ruler_mark_mm
        observed_y = (xy_mark - x_mark) / self.ruler_mark_mm

        # For R = [[cos, -sin], [sin, cos]], maximizing trace(R.T @ A)
        # gives the closest orientation-preserving orthogonal matrix to the
        # observed per-mm basis A = [observed_x observed_y]. This closed
        # form avoids an SVD sign ambiguity for nearly ideal ruler observations.
        cosine_numerator = float(observed_x[0] + observed_y[1])
        sine_numerator = float(observed_x[1] - observed_y[0])
        normalization = math.hypot(cosine_numerator, sine_numerator)
        if normalization <= 1e-12:
            raise ValueError(
                "The selected ruler directions cannot define a rigid positive X/Y frame"
            )
        cosine = cosine_numerator / normalization
        sine = sine_numerator / normalization
        x_axis = (cosine, sine)
        y_axis = (-sine, cosine)
        return HoneycombCoordinateFrame(
            origin_machine_mm=(float(origin[0]), float(origin[1])),
            x_axis_machine=x_axis,
            y_axis_machine=y_axis,
            width_mm=self.support_width_mm,
            height_mm=self.support_height_mm,
            provenance_digest=self.coordinate_frame_digest,
        )

    @property
    def coordinate_frame_digest(self) -> str:
        """Hash the exact support evidence and rigid-frame algorithm version."""

        canonical = json.dumps(
            {
                "coordinate_frame_version": HONEYCOMB_COORDINATE_FRAME_VERSION,
                "kind": _HONEYCOMB_COORDINATE_FRAME_KIND,
                "support_reference": self.to_dict(),
            },
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    @property
    def coordinate_frame_signature(self) -> tuple[str, int, str]:
        """Return the versioned immutable identity of this derived frame."""

        return self.coordinate_frame.provenance_signature

    @property
    def local_bounds_mm(self) -> tuple[float, float, float, float]:
        """Return honeycomb-local ``0..width`` and ``0..height`` bounds."""

        return self.coordinate_frame.local_bounds_mm

    def local_to_machine(
        self,
        x_mm: float,
        y_mm: float,
    ) -> tuple[float, float]:
        return self.coordinate_frame.local_to_machine(x_mm, y_mm)

    def machine_to_local(
        self,
        x_mm: float,
        y_mm: float,
    ) -> tuple[float, float]:
        return self.coordinate_frame.machine_to_local(x_mm, y_mm)

    @property
    def rigid_support_corners_machine_mm(self) -> tuple[tuple[float, float], ...]:
        """Return support corners from the non-shearing coordinate frame."""

        return self.coordinate_frame.corners_machine_mm

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
        if self.has_four_corner_evidence:
            assert self.raw_corners_machine_mm is not None
            assert self.corner_topology is not None
            return tuple(
                self.raw_corners_machine_mm[index] for index in self.corner_topology
            )
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

    @property
    def has_four_corner_evidence(self) -> bool:
        """Whether the reference contains independent execution-grade evidence."""

        return (
            self.schema_version == HONEYCOMB_SUPPORT_SCHEMA_VERSION
            and self.measurement_method == _AUTOMATIC_MEASUREMENT_METHOD
            and self.raw_corners_machine_mm is not None
            and len(self.raw_corners_machine_mm) == 4
            and self.corner_topology is not None
        )

    @property
    def is_execution_verifiable(self) -> bool:
        """Whether a fresh four-edge fit can verify this exact taught topology."""

        return self.has_four_corner_evidence

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "ruler_origin_machine_mm": list(self.ruler_origin_machine_mm),
            "ruler_x_mark_machine_mm": list(self.ruler_x_mark_machine_mm),
            "ruler_xy_mark_machine_mm": list(self.ruler_xy_mark_machine_mm),
            "ruler_mark_mm": self.ruler_mark_mm,
            "support_width_mm": self.support_width_mm,
            "support_height_mm": self.support_height_mm,
            "created_at": self.created_at,
            "bed_calibration_created_at": self.bed_calibration_created_at,
        }
        if self.schema_version == HONEYCOMB_SUPPORT_SCHEMA_VERSION:
            payload.update(
                {
                    "raw_corners_machine_mm": [
                        list(point) for point in self.raw_corners_machine_mm or ()
                    ],
                    "corner_topology": list(self.corner_topology or ()),
                    "corner_topology_semantics": list(_SEMANTIC_CORNER_ORDER),
                    "measurement_method": self.measurement_method,
                    "taught_reference_digest": self.taught_reference_digest,
                }
            )
        return payload


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
