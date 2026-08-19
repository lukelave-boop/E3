from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pytest

from laser_aligner.app import AppContext, RunningMachineIdentity
from laser_aligner.calibration.audit import (
    honeycomb_support_validity,
    inspect_coordinate_point,
    source_to_display_pixel,
)
from laser_aligner.calibration.bed import BedPoint
from laser_aligner.calibration.profiles import signature_from_camera_settings
from laser_aligner.calibration.support import HoneycombSupportReference
from laser_aligner.config import load_settings


def _settings(tmp_path: Path, *, honeycomb_span_mm: float | None = None):
    payload = {
        "app": {
            "data_dir": str(tmp_path / "data"),
            "simulation": True,
            "open_browser": False,
        },
        "camera": {"view_rotation_degrees": 90},
        "machine": {
            "backend": "simulator",
            "honeycomb_span_mm": honeycomb_span_mm,
            "photo_position": {"x": 15.0, "y": 195.0, "z": None},
        },
        "laser": {
            "boundary_margin_mm": 5.0,
            "spot_offset_x_mm": 2.0,
            "spot_offset_y_mm": -3.0,
        },
    }
    path = tmp_path / "audit-config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_settings(path)


def _support(*, span: float, bed_created_at: float = 7.0):
    return HoneycombSupportReference.from_four_corner_observations(
        raw_corners_machine_mm=(
            (20.0, 30.0),
            (20.0 + span, 30.0),
            (20.0 + span, 30.0 + span),
            (20.0, 30.0 + span),
        ),
        corner_topology=(0, 1, 2, 3),
        support_width_mm=span,
        support_height_mm=span,
        bed_calibration_created_at=bed_created_at,
    )


def _identity(settings, expected: str | None) -> RunningMachineIdentity:
    return RunningMachineIdentity(
        machine_id="laser-one",
        machine_name="Workshop E3",
        created_from="profile",
        machine_profile_id="ender-3-s1-pro",
        tool_head_profile_id="diode-10w",
        expected_camera_profile_id="camera-c920",
        expected_calibration_profile_id=expected,
    )


def test_missing_machine_honeycomb_span_is_explicit(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    active = signature_from_camera_settings(settings.camera).key
    context = AppContext(settings, machine_identity=_identity(settings, active))

    status = context.coordinate_audit_status()

    assert status["machine_identity"]["machine_id"] == "laser-one"
    assert status["machine_identity"]["machine_name"] == "Workshop E3"
    assert status["honeycomb"]["state"] == "UNCONFIGURED"
    assert status["honeycomb"]["expected_span_mm"] is None
    assert any("no configured physical honeycomb" in item for item in status["blockers"])


def test_configured_machine_honeycomb_span_is_used() -> None:
    validity = honeycomb_support_validity(
        _support(span=191.0),
        bed_calibration_created_at=7.0,
        expected_span_mm=191.0,
    )

    assert validity["state"] == "CURRENT"
    assert validity["expected_span_mm"] == 191.0
    assert validity["recorded_size_mm"] == [191.0, 191.0]


def test_calibration_binding_mismatch_is_a_blocker(tmp_path: Path) -> None:
    settings = _settings(tmp_path, honeycomb_span_mm=190.0)
    context = AppContext(
        settings,
        machine_identity=_identity(settings, "different-calibration-profile"),
    )

    status = context.coordinate_audit_status()

    assert not status["machine_identity"]["calibration_binding_matches"]
    assert any("Calibration binding mismatch" in item for item in status["blockers"])


def test_context_coordinate_audit_refresh_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, honeycomb_span_mm=190.0)
    active = signature_from_camera_settings(settings.camera).key
    context = AppContext(settings, machine_identity=_identity(settings, active))

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("audit refresh must not command hardware")

    monkeypatch.setattr(context.machine, "ensure_connected", unexpected)
    monkeypatch.setattr(context.machine, "send_command", unexpected)
    monkeypatch.setattr(context.machine, "prepare_photo_position", unexpected)
    monkeypatch.setattr(context.machine, "sample_realtime_position", unexpected)

    status = context.coordinate_audit_status()

    assert status["machine"]["connected"] is False


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [(0, (10.0, 20.0)), (90, (79.0, 10.0)), (180, (189.0, 79.0)), (270, (20.0, 189.0))],
)
def test_source_to_display_pixel_is_presentation_only(
    rotation: int, expected: tuple[float, float]
) -> None:
    assert source_to_display_pixel(
        10.0,
        20.0,
        source_width=200,
        source_height=100,
        rotation_degrees=rotation,
    ) == pytest.approx(expected)


def test_point_inspection_reports_independent_containment(tmp_path: Path) -> None:
    settings = _settings(tmp_path, honeycomb_span_mm=191.0)

    class IdentityBed:
        @staticmethod
        def image_to_mm(image_x: float, image_y: float) -> tuple[float, float]:
            return image_x, image_y

    point = inspect_coordinate_point(
        settings,
        IdentityBed(),
        source_image_point=(25.0, 35.0),
        source_image_size=(200, 100),
        support_reference=_support(span=191.0),
    )

    assert point["display_pixel"] == pytest.approx([64.0, 25.0])
    assert point["machine_mm"] == pytest.approx([25.0, 35.0])
    assert point["desired_beam_mm"] == pytest.approx([25.0, 35.0])
    assert point["honeycomb_local_mm"] == pytest.approx([5.0, 5.0])
    assert point["spot_corrected_carriage_mm"] == pytest.approx([23.0, 38.0])
    assert point["inside_machine_work_area"]
    assert point["inside_guarded_beam_authority"]
    assert point["inside_guarded_carriage_authority"]
    assert point["inside_honeycomb"]


def test_capture_snapshot_survives_current_coordinate_trust_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _settings(tmp_path, honeycomb_span_mm=190.0)
    active = signature_from_camera_settings(settings.camera).key
    context = AppContext(settings, machine_identity=_identity(settings, active))
    context.bed.replace_points_and_solve(
        [
            BedPoint(0.0, 9.0, 0.0, 0.0),
            BedPoint(9.0, 9.0, 220.0, 0.0),
            BedPoint(9.0, 0.0, 220.0, 220.0),
            BedPoint(0.0, 0.0, 0.0, 220.0),
        ],
        10,
        10,
    )
    coordinate_state = {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }
    positions = iter(
        (
            {"available": True, "state": "Idle", "mpos_mm": [15.0, 195.0, 0.0], "wpos_mm": [15.0, 195.0, 0.0], "wco_mm": [0.0, 0.0, 0.0]},
            {"available": True, "state": "Idle", "mpos_mm": [15.001, 195.0, 0.0], "wpos_mm": [15.001, 195.0, 0.0], "wco_mm": [0.0, 0.0, 0.0]},
        )
    )

    class Burst:
        sharpest_frame = np.full((12, 16, 3), 80, dtype=np.uint8)

    park_options: list[dict[str, object]] = []

    def prepare_photo_position(**kwargs: object) -> dict[str, object]:
        park_options.append(kwargs)
        return {
            "homed": True,
            "parked": True,
            "coordinate_state": coordinate_state,
            "home_position_snapshot": None,
        }

    monkeypatch.setattr(context, "_require_camera_calibration_ready", lambda: None)
    monkeypatch.setattr(context, "_require_valid_bed_calibration", lambda: None)
    monkeypatch.setattr(context.machine, "temporary_stepper_hold", nullcontext)
    monkeypatch.setattr(
        context.machine,
        "prepare_photo_position",
        prepare_photo_position,
    )
    monkeypatch.setattr(context, "_sample_coordinate_audit_position", lambda: next(positions))
    monkeypatch.setattr(context, "_stable_camera_burst", Burst)
    monkeypatch.setattr(context, "_prepare_camera_burst", lambda burst, *, undistort: burst)
    monkeypatch.setattr(
        context.machine,
        "status",
        lambda: {
            "connected": True,
            "backend": "simulator",
            "protocol": "grbl",
            "coordinate_reference_ready": False,
        },
    )

    context.capture_parked_work_area_reference()
    capture = context.coordinate_audit_status()["machine"]["capture_pose"]

    assert capture["state"] == "CURRENT"
    assert capture["trusted_at_capture"] is True
    assert capture["current_position_trusted_after_cleanup"] is False
    assert capture["maximum_position_delta_mm"] == pytest.approx(0.001)
    assert park_options == [{"capture_home_position": True}]
