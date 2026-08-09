from __future__ import annotations

import json
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


def test_honeycomb_support_extrapolates_oriented_physical_edges() -> None:
    reference = _reference()

    assert reference.measured_ruler_span_mm == pytest.approx((190.0, 190.0))
    assert np.asarray(reference.support_corners_machine_mm) == pytest.approx(
        np.asarray(((28.0, 17.0), (228.0, 17.0), (228.0, 217.0), (28.0, 217.0)))
    )


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
