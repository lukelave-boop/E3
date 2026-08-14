from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pytest

from laser_aligner.calibration.support import (
    HoneycombSupportReference,
    HoneycombSupportStore,
)


def _reference() -> HoneycombSupportReference:
    return HoneycombSupportReference.from_observations(
        ruler_origin_machine_mm=(28.0, 17.0),
        ruler_x_mark_machine_mm=(218.0, 17.0),
        ruler_xy_mark_machine_mm=(218.0, 207.0),
        ruler_mark_mm=190.0,
        support_width_mm=200.0,
        support_height_mm=200.0,
        bed_calibration_created_at=12.5,
        created_at=20.0,
    )


def _four_corner_reference() -> HoneycombSupportReference:
    return HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=(
            (218.1, 207.0),
            (28.0, 17.0),
            (28.2, 207.1),
            (218.0, 17.1),
        ),
        corner_topology=(1, 3, 0, 2),
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=12.5,
        taught_reference_digest="a" * 64,
        created_at=20.0,
    )


def test_honeycomb_support_extrapolates_oriented_physical_edges() -> None:
    reference = _reference()

    assert reference.measured_ruler_span_mm == pytest.approx((190.0, 190.0))
    assert np.asarray(reference.support_corners_machine_mm) == pytest.approx(
        np.asarray(((28.0, 17.0), (228.0, 17.0), (228.0, 217.0), (28.0, 217.0)))
    )


def test_honeycomb_coordinate_frame_is_closest_rigid_axis_fit() -> None:
    reference = HoneycombSupportReference.from_observations(
        ruler_origin_machine_mm=(29.233277663144637, 37.3174240233171),
        ruler_x_mark_machine_mm=(219.20124700220413, 40.80606633718939),
        ruler_xy_mark_machine_mm=(217.55443339406006, 230.79892937404145),
        ruler_mark_mm=190.0,
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=12.5,
        created_at=20.0,
    )

    frame = reference.coordinate_frame
    x_axis = np.asarray(frame.x_axis_machine)
    y_axis = np.asarray(frame.y_axis_machine)

    assert np.linalg.norm(x_axis) == pytest.approx(1.0, abs=1e-12)
    assert np.linalg.norm(y_axis) == pytest.approx(1.0, abs=1e-12)
    assert np.dot(x_axis, y_axis) == pytest.approx(0.0, abs=1e-12)
    assert np.linalg.det(np.column_stack((x_axis, y_axis))) == pytest.approx(
        1.0, abs=1e-12
    )
    # The closest proper rotation bisects the independently observed ruler
    # headings (1.0521 and 90.4966 degrees) rather than retaining their shear.
    assert math.degrees(math.atan2(x_axis[1], x_axis[0])) == pytest.approx(
        0.7743483283, abs=1e-9
    )
    assert frame.origin_machine_mm == reference.ruler_origin_machine_mm
    assert frame.local_bounds_mm == (0.0, 0.0, 190.0, 190.0)
    assert np.asarray(frame.corners_machine_mm) == pytest.approx(
        np.asarray(
            (
                (29.2332776631, 37.3174240233),
                (219.2159257948, 39.8851821505),
                (216.6481676676, 229.8678302822),
                (26.6655195359, 227.3000721550),
            )
        ),
        abs=1e-7,
    )


def test_schema_two_coordinate_frame_is_closest_rigid_fit_to_all_four_corners() -> None:
    reference = _four_corner_reference()
    frame = reference.coordinate_frame
    measured = np.asarray(reference.support_corners_machine_mm)
    fitted = np.asarray(frame.corners_machine_mm)

    assert frame.origin_machine_mm != reference.ruler_origin_machine_mm
    assert np.mean(fitted, axis=0) == pytest.approx(np.mean(measured, axis=0))
    assert np.linalg.norm(frame.x_axis_machine) == pytest.approx(1.0)
    assert np.linalg.norm(frame.y_axis_machine) == pytest.approx(1.0)
    assert np.dot(frame.x_axis_machine, frame.y_axis_machine) == pytest.approx(0.0)
    assert np.linalg.det(
        np.column_stack((frame.x_axis_machine, frame.y_axis_machine))
    ) == pytest.approx(1.0)

def test_honeycomb_coordinate_transforms_round_trip_points() -> None:
    reference = HoneycombSupportReference.from_observations(
        ruler_origin_machine_mm=(12.0, -7.0),
        ruler_x_mark_machine_mm=(12.0, 183.0),
        ruler_xy_mark_machine_mm=(-178.0, 183.0),
        ruler_mark_mm=190.0,
        support_width_mm=200.0,
        support_height_mm=200.0,
        bed_calibration_created_at=12.5,
        created_at=20.0,
    )

    assert reference.local_bounds_mm == (0.0, 0.0, 200.0, 200.0)
    assert reference.local_to_machine(25.0, 40.0) == pytest.approx((-28.0, 18.0))
    assert reference.machine_to_local(-28.0, 18.0) == pytest.approx((25.0, 40.0))
    for point in ((0.0, 0.0), (190.0, 180.0), (81.25, 92.75), (-3.0, 201.0)):
        assert reference.machine_to_local(*reference.local_to_machine(*point)) == (
            pytest.approx(point)
        )


def test_honeycomb_rigid_fit_accounts_for_both_measured_axis_lengths() -> None:
    y_angle = math.radians(95.0)
    reference = HoneycombSupportReference.from_observations(
        ruler_origin_machine_mm=(0.0, 0.0),
        ruler_x_mark_machine_mm=(180.0, 0.0),
        ruler_xy_mark_machine_mm=(
            180.0 + 200.0 * math.cos(y_angle),
            200.0 * math.sin(y_angle),
        ),
        ruler_mark_mm=190.0,
        support_width_mm=200.0,
        support_height_mm=200.0,
        bed_calibration_created_at=12.5,
        created_at=20.0,
    )
    measured_x = np.asarray(reference.x_basis_machine_per_mm)
    measured_y = np.asarray(reference.y_basis_machine_per_mm)
    expected_angle = math.atan2(
        measured_x[1] - measured_y[0],
        measured_x[0] + measured_y[1],
    )

    fitted_x = reference.coordinate_frame.x_axis_machine
    assert math.atan2(fitted_x[1], fitted_x[0]) == pytest.approx(expected_angle)


def test_honeycomb_coordinate_frame_signature_is_stable_and_binds_provenance() -> None:
    reference = _reference()
    serialized = reference.to_dict()
    round_tripped = HoneycombSupportReference.from_dict(
        json.loads(json.dumps(serialized))
    )

    assert round_tripped.to_dict() == serialized
    kind, version, digest = round_tripped.coordinate_frame_signature
    assert kind == "honeycomb-rigid-frame"
    assert version == 2
    assert len(digest) == 64
    assert round_tripped.coordinate_frame_signature == reference.coordinate_frame_signature
    assert len(reference.coordinate_frame_digest) == 64

    moved = reference.to_dict()
    moved["ruler_origin_machine_mm"] = [28.001, 17.0]
    moved["ruler_x_mark_machine_mm"] = [218.001, 17.0]
    moved["ruler_xy_mark_machine_mm"] = [218.001, 207.0]
    moved_reference = HoneycombSupportReference.from_dict(moved)
    assert moved_reference.coordinate_frame_digest != reference.coordinate_frame_digest

    re_recorded = reference.to_dict()
    re_recorded["created_at"] = 21.0
    re_recorded_reference = HoneycombSupportReference.from_dict(re_recorded)
    assert re_recorded_reference.coordinate_frame_digest != reference.coordinate_frame_digest


def test_honeycomb_support_store_round_trips_and_clears(tmp_path: Path) -> None:
    store = HoneycombSupportStore(tmp_path)
    assert store.reference is None

    store.save(_reference())
    loaded = HoneycombSupportStore(tmp_path)
    assert loaded.reference == _reference()
    assert loaded.load_error is None

    loaded.clear()
    assert loaded.reference is None
    assert not loaded.path.exists()


def test_schema_two_round_trip_preserves_raw_corner_order_and_provenance() -> None:
    reference = _four_corner_reference()
    payload = reference.to_dict()
    restored = HoneycombSupportReference.from_dict(json.loads(json.dumps(payload)))

    assert payload["schema_version"] == 2
    assert restored == reference
    assert restored.has_four_corner_evidence is True
    assert restored.is_execution_verifiable is True
    assert restored.raw_corners_machine_mm == reference.raw_corners_machine_mm
    assert restored.corner_topology == (1, 3, 0, 2)
    assert restored.support_corners_machine_mm == (
        (28.0, 17.0),
        (218.0, 17.1),
        (218.1, 207.0),
        (28.2, 207.1),
    )
    assert restored.measurement_method == "automatic-four-edge-fit"
    assert restored.taught_reference_digest == "a" * 64


def test_schema_one_loads_for_display_but_is_not_execution_verifiable() -> None:
    reference = HoneycombSupportReference.from_dict(_reference().to_dict())

    assert reference.schema_version == 1
    assert reference.has_four_corner_evidence is False
    assert reference.is_execution_verifiable is False
    assert reference.raw_corners_machine_mm is None


def test_coordinate_frame_digest_binds_every_schema_two_evidence_field() -> None:
    reference = _four_corner_reference()
    baseline = reference.coordinate_frame_digest
    mutations = (
        ("raw_corners_machine_mm", [[218.2, 207.0], [28.0, 17.0], [28.2, 207.1], [218.0, 17.1]]),
        ("corner_topology", [3, 0, 2, 1]),
        ("taught_reference_digest", "b" * 64),
        ("created_at", 21.0),
    )
    for key, value in mutations:
        payload = reference.to_dict()
        payload[key] = value
        if key in {"corner_topology", "raw_corners_machine_mm"}:
            raw = payload["raw_corners_machine_mm"]
            topology = payload["corner_topology"]
            payload["ruler_origin_machine_mm"] = raw[topology[0]]
            payload["ruler_x_mark_machine_mm"] = raw[topology[1]]
            payload["ruler_xy_mark_machine_mm"] = raw[topology[2]]
        mutated = HoneycombSupportReference.from_dict(payload)
        assert mutated.coordinate_frame_digest != baseline


@pytest.mark.parametrize(
    "change,match",
    (
        ({"raw_corners_machine_mm": [[0.0, 0.0]] * 3}, "four raw"),
        ({"corner_topology": [0, 1, 1, 3]}, "permutation"),
        ({"corner_topology_semantics": ["origin", "+y", "opposite", "+x"]}, "semantics"),
        ({"measurement_method": "manual-three-click"}, "automatic four-edge"),
        ({"taught_reference_digest": "not-a-digest"}, "SHA-256"),
        (
            {
                "raw_corners_machine_mm": [
                    [218.1, 207.0],
                    [28.0, 17.0],
                    [28.2, 207.1],
                    [214.0, 17.1],
                ]
            },
            "side lengths",
        ),
        (
            {
                "raw_corners_machine_mm": [
                    [218.1, 204.25],
                    [28.0, 17.0],
                    [28.2, 207.1],
                    [218.0, 17.1],
                ]
            },
            "close as one rectangle",
        ),
    ),
)
def test_schema_two_rejects_incomplete_or_inconsistent_evidence(
    change: dict[str, object],
    match: str,
) -> None:
    payload = _four_corner_reference().to_dict()
    payload.update(change)
    if "raw_corners_machine_mm" in change and len(payload["raw_corners_machine_mm"]) == 4:
        raw = payload["raw_corners_machine_mm"]
        topology = payload["corner_topology"]
        payload["ruler_origin_machine_mm"] = raw[topology[0]]
        payload["ruler_x_mark_machine_mm"] = raw[topology[1]]
        payload["ruler_xy_mark_machine_mm"] = raw[topology[2]]
    with pytest.raises(ValueError, match=match):
        HoneycombSupportReference.from_dict(payload)


@pytest.mark.parametrize(
    "change,match",
    (
        ({"schema_version": True}, "schema"),
        ({"ruler_origin_machine_mm": [float("nan"), 0.0]}, "finite"),
        ({"ruler_x_mark_machine_mm": [40.0, 0.0]}, "X ruler span"),
        ({"ruler_xy_mark_machine_mm": [380.0, 0.0]}, "perpendicular"),
        ({"support_width_mm": 180.0}, "far ruler mark"),
    ),
)
def test_honeycomb_support_rejects_corrupt_or_implausible_geometry(
    change: dict[str, object],
    match: str,
) -> None:
    payload = _reference().to_dict()
    payload.update(change)
    with pytest.raises(ValueError, match=match):
        HoneycombSupportReference.from_dict(payload)


def test_honeycomb_support_store_ignores_invalid_persisted_payload(
    tmp_path: Path,
) -> None:
    path = tmp_path / "honeycomb_support.json"
    path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")

    store = HoneycombSupportStore(tmp_path)

    assert store.reference is None
    assert "invalid" in str(store.load_error).lower()
