from __future__ import annotations

import json
from contextlib import nullcontext
from pathlib import Path

import numpy as np
import pytest

from laser_aligner.app import AppContext
from laser_aligner.calibration.audit import (
    build_coordinate_audit_status,
    honeycomb_support_validity,
    inspect_coordinate_point,
    source_to_display_pixel,
)
from laser_aligner.calibration.bed import BedPoint
from laser_aligner.calibration.support import HoneycombSupportReference
from laser_aligner.config import load_settings


def _settings(tmp_path: Path, **overrides: object):
    payload: dict[str, object] = {
        "app": {
            "data_dir": "data",
            "simulation": True,
            "open_browser": False,
        },
        "camera": {"view_rotation_degrees": 90},
        "machine": {"backend": "simulator"},
        "laser": {
            "boundary_margin_mm": 5.0,
            "spot_offset_x_mm": 2.0,
            "spot_offset_y_mm": -3.0,
        },
    }
    for key, value in overrides.items():
        payload[key] = value
    path = tmp_path / "audit-config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return load_settings(path)


def _support(*, span: float, bed_created_at: float = 7.0) -> HoneycombSupportReference:
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


def test_honeycomb_support_validity_rejects_silent_190_to_191_stretch() -> None:
    validity = honeycomb_support_validity(
        _support(span=190.0),
        bed_calibration_created_at=7.0,
        expected_span_mm=191.0,
    )

    assert validity["state"] == "STALE"
    assert validity["recorded_size_mm"] == [190.0, 190.0]
    assert "requires 191 x 191 mm" in validity["reasons"][0]
    assert not validity["execution_verifiable"]


def test_honeycomb_support_validity_accepts_matching_map_and_span() -> None:
    validity = honeycomb_support_validity(
        _support(span=191.0),
        bed_calibration_created_at=7.0,
        expected_span_mm=191.0,
    )

    assert validity["state"] == "CURRENT"
    assert validity["reasons"] == []
    assert validity["execution_verifiable"]


@pytest.mark.parametrize(
    ("rotation", "expected"),
    [
        (0, (10.0, 20.0)),
        (90, (79.0, 10.0)),
        (180, (189.0, 79.0)),
        (270, (20.0, 189.0)),
    ],
)
def test_source_to_display_pixel_keeps_rotation_presentation_only(
    rotation: int,
    expected: tuple[float, float],
) -> None:
    assert source_to_display_pixel(
        10.0,
        20.0,
        source_width=200,
        source_height=100,
        rotation_degrees=rotation,
    ) == pytest.approx(expected)


def test_point_inspector_reports_machine_local_and_beam_command_frames(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)

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
    assert point["honeycomb_local_mm"] == pytest.approx([5.0, 5.0])
    assert point["spot_corrected_carriage_mm"] == pytest.approx([23.0, 38.0])
    assert point["inside_machine_work_area"]
    assert point["carriage_inside_machine_work_area"]
    assert point["inside_guarded_beam_authority"]
    assert point["inside_guarded_carriage_authority"]
    assert point["inside_guarded_laser_output"]
    assert point["inside_honeycomb"]


def test_point_inspector_requires_beam_and_carriage_inside_explicit_authority(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        laser={
            "guarded_output_polygon_mm": [
                [10.0, 10.0],
                [30.0, 10.0],
                [30.0, 30.0],
                [10.0, 30.0],
            ],
            "spot_offset_x_mm": 5.0,
            "spot_offset_y_mm": 0.0,
        },
    )

    class IdentityBed:
        @staticmethod
        def image_to_mm(image_x: float, image_y: float) -> tuple[float, float]:
            return image_x, image_y

    point = inspect_coordinate_point(
        settings,
        IdentityBed(),
        source_image_point=(12.0, 20.0),
        source_image_size=(200, 100),
        support_reference=None,
    )

    assert point["inside_guarded_beam_authority"]
    assert not point["inside_guarded_carriage_authority"]
    assert not point["inside_guarded_laser_output"]
    assert point["machine_mm"] == pytest.approx([12.0, 20.0])
    assert point["spot_corrected_carriage_mm"] == pytest.approx([7.0, 20.0])


def test_context_coordinate_audit_refresh_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))

    def unexpected_hardware_call(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("coordinate audit refresh must not command hardware")

    monkeypatch.setattr(context.machine, "ensure_connected", unexpected_hardware_call)
    monkeypatch.setattr(context.machine, "send_command", unexpected_hardware_call)
    monkeypatch.setattr(context.machine, "prepare_photo_position", unexpected_hardware_call)

    status = context.coordinate_audit_status()

    assert status["overall_state"] == "BLOCKED"
    assert status["machine"]["connected"] is False


def test_work_area_reference_records_trusted_capture_pose_before_motor_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        tmp_path,
        machine={
            "backend": "simulator",
            "photo_position": {"x": 15.0, "y": 195.0, "z": None},
        },
    )
    context = AppContext(settings)
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
    calibration = context.bed.calibration
    assert calibration is not None

    coordinate_state = {
        "active_workspace": "G54",
        "active_offset_mm": [0.0, 0.0, 0.0],
        "g92_offset_mm": [0.0, 0.0, 0.0],
    }
    positions = iter(
        (
            {
                "available": True,
                "state": "Idle",
                "mpos_mm": [15.0, 195.0, 0.0],
                "wpos_mm": [15.0, 195.0, 0.0],
                "wco_mm": [0.0, 0.0, 0.0],
                "wco_source": "reported",
            },
            {
                "available": True,
                "state": "Idle",
                "mpos_mm": [15.001, 195.0, 0.0],
                "wpos_mm": [15.001, 195.0, 0.0],
                "wco_mm": [0.0, 0.0, 0.0],
                "wco_source": "reported",
            },
        )
    )

    class Burst:
        sharpest_frame = np.full((12, 16, 3), 80, dtype=np.uint8)

    monkeypatch.setattr(context, "_require_camera_calibration_ready", lambda: None)
    monkeypatch.setattr(context, "_require_valid_bed_calibration", lambda: None)
    monkeypatch.setattr(
        context.machine,
        "temporary_stepper_hold",
        lambda: nullcontext(),
    )
    monkeypatch.setattr(
        context.machine,
        "prepare_photo_position",
        lambda **_kwargs: {
            "homed": True,
            "parked": True,
            "coordinate_state": coordinate_state,
        },
    )
    monkeypatch.setattr(
        context,
        "_sample_coordinate_audit_position",
        lambda: next(positions),
    )
    monkeypatch.setattr(context, "_stable_camera_burst", Burst)
    monkeypatch.setattr(
        context,
        "_prepare_camera_burst",
        lambda burst, *, undistort: burst,
    )

    image = context.capture_parked_work_area_reference()
    audit = context.coordinate_audit_status()
    capture = audit["machine"]["capture_pose"]

    assert image.shape == (12, 16, 3)
    assert context.honeycomb_detection_input_path.exists()
    assert capture["state"] == "CURRENT"
    assert capture["trusted_at_capture"] is True
    assert capture["home_completed"] is True
    assert capture["commanded_photo_position_mm"] == [15.0, 195.0, None]
    assert capture["position_after_capture"]["mpos_mm"] == pytest.approx(
        [15.001, 195.0, 0.0]
    )
    assert capture["maximum_position_delta_mm"] == pytest.approx(0.001)
    assert capture["commanded_position_error_xy_mm"] == pytest.approx(
        [0.001, 0.0]
    )
    assert capture["coordinate_state"] == coordinate_state


def test_capture_pose_is_recorded_only_after_audit_image_is_published(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = AppContext(_settings(tmp_path))
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

    class Burst:
        sharpest_frame = np.zeros((12, 16, 3), dtype=np.uint8)

    monkeypatch.setattr(context, "_require_camera_calibration_ready", lambda: None)
    monkeypatch.setattr(context, "_require_valid_bed_calibration", lambda: None)
    monkeypatch.setattr(
        context.machine,
        "temporary_stepper_hold",
        lambda: nullcontext(),
    )
    monkeypatch.setattr(
        context.machine,
        "prepare_photo_position",
        lambda **_kwargs: {
            "homed": True,
            "parked": True,
            "coordinate_state": None,
        },
    )
    monkeypatch.setattr(
        context,
        "_sample_coordinate_audit_position",
        lambda: {"available": False, "error": "status unavailable"},
    )
    monkeypatch.setattr(context, "_stable_camera_burst", Burst)
    monkeypatch.setattr(
        context,
        "_prepare_camera_burst",
        lambda burst, *, undistort: burst,
    )
    monkeypatch.setattr(
        "laser_aligner.app.write_image_atomic",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )

    with pytest.raises(OSError, match="disk full"):
        context.capture_parked_work_area_reference()

    assert context._coordinate_audit_capture_snapshot is None



def test_coordinate_audit_status_exposes_independent_frames_and_stale_reason(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    support = _support(span=190.0)
    status = build_coordinate_audit_status(
        settings,
        machine_status={
            "connected": True,
            "backend": "simulator",
            "protocol": "grbl",
            "coordinate_reference_ready": True,
            "coordinate_state_reference": {
                "active_workspace": "G54",
                "active_offset_mm": [0.0, 0.0, 0.0],
                "g92_offset_mm": [0.0, 0.0, 0.0],
            },
            "jog_position_mm": {"x": 110.0, "y": 110.0},
        },
        camera_status={"connected": True, "width": 1920, "height": 1080},
        camera_readiness={"state": "READY", "reasons": []},
        lens_status={"model": {"model_id": "lens-1", "image_size": [1920, 1080]}},
        bed_status={
            "calibration": {
                "created_at": 7.0,
                "rms_error_mm": 0.1,
                "max_error_mm": 0.3,
                "point_count": 25,
                "inlier_count": 25,
            },
            "validity": {"state": "VALID", "reasons": []},
            "axis_mapping": {"x_reversed": False, "y_reversed": False},
        },
        support_reference=support,
        honeycomb_execution_signature=None,
    )

    assert status["overall_state"] == "BLOCKED"
    assert status["machine"]["work_area_mm"] == [0.0, 220.0, 0.0, 220.0]
    assert status["camera"]["display_rotation_degrees"] == 90
    assert status["honeycomb"]["state"] == "STALE"
    assert status["honeycomb"]["expected_span_mm"] == 191.0
    assert any("requires 191 x 191 mm" in reason for reason in status["blockers"])
    assert "configured 191 mm span" in status["required_next_action"]
    assert status["honeycomb"]["origin_machine_mm"] == pytest.approx([20.0, 30.0])


def test_coordinate_audit_status_ready_has_no_blockers_and_no_reteach_action(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    support = _support(span=191.0)
    status = build_coordinate_audit_status(
        settings,
        machine_status={
            "connected": True,
            "backend": "simulator",
            "protocol": "grbl",
            "coordinate_reference_ready": True,
            "coordinate_state_reference": {
                "active_workspace": "G54",
                "active_offset_mm": [0.0, 0.0, 0.0],
                "g92_offset_mm": [0.0, 0.0, 0.0],
            },
            "jog_position_mm": {"x": 110.0, "y": 110.0},
        },
        camera_status={"connected": True, "width": 1920, "height": 1080},
        camera_readiness={"state": "READY", "reasons": []},
        lens_status={"model": {"model_id": "lens-1", "image_size": [1920, 1080]}},
        bed_status={
            "calibration": {
                "created_at": 7.0,
                "rms_error_mm": 0.1,
                "max_error_mm": 0.3,
                "point_count": 25,
                "inlier_count": 25,
            },
            "validity": {"state": "VALID", "reasons": []},
            "axis_mapping": {"x_reversed": False, "y_reversed": False},
        },
        support_reference=support,
        honeycomb_execution_signature=("honeycomb", 2, "frame", "map"),
    )

    assert status["overall_state"] == "READY"
    assert status["blockers"] == []
    assert status["required_next_action"].startswith("No coordinate dependency")


def _ready_coordinate_audit_inputs() -> dict[str, object]:
    return {
        "camera_status": {"connected": True, "width": 1920, "height": 1080},
        "camera_readiness": {"state": "READY", "reasons": []},
        "lens_status": {
            "model": {"model_id": "lens-1", "image_size": [1920, 1080]}
        },
        "bed_status": {
            "calibration": {
                "created_at": 7.0,
                "rms_error_mm": 0.1,
                "max_error_mm": 0.3,
                "point_count": 25,
                "inlier_count": 25,
            },
            "validity": {"state": "VALID", "reasons": []},
            "axis_mapping": {"x_reversed": False, "y_reversed": False},
        },
    }


def test_serial_audit_prioritizes_capture_pose_before_honeycomb_reteach(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        machine={
            "backend": "serial",
            "protocol": "grbl",
            "port": "/dev/ttyUSB0",
            "allow_motion": True,
        },
    )
    status = build_coordinate_audit_status(
        settings,
        machine_status={
            "connected": True,
            "backend": "serial",
            "protocol": "grbl",
            "coordinate_reference_ready": False,
            "coordinate_state_reference": None,
            "jog_position_mm": None,
        },
        support_reference=_support(span=190.0),
        honeycomb_execution_signature=None,
        **_ready_coordinate_audit_inputs(),
    )

    assert "Run Home / park and capture audit view" in status["required_next_action"]
    assert "automatic four-edge" not in status["required_next_action"]
    assert status["machine"]["capture_pose"]["state"] == "MISSING"


def test_serial_audit_advances_to_honeycomb_reteach_after_trusted_capture(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        machine={
            "backend": "serial",
            "protocol": "grbl",
            "port": "/dev/ttyUSB0",
            "allow_motion": True,
            "photo_position": {"x": 15.0, "y": 195.0, "z": None},
        },
    )
    capture = {
        "trusted_at_capture": True,
        "bed_calibration_created_at": 7.0,
        "home_completed": True,
        "parked": True,
        "commanded_photo_position_mm": [15.0, 195.0, None],
        "coordinate_state": {
            "active_workspace": "G54",
            "active_offset_mm": [0.0, 0.0, 0.0],
            "g92_offset_mm": [0.0, 0.0, 0.0],
        },
        "position_before_capture": {
            "available": True,
            "state": "Idle",
            "mpos_mm": [15.0, 195.0, 0.0],
            "wpos_mm": [15.0, 195.0, 0.0],
            "wco_mm": [0.0, 0.0, 0.0],
        },
        "position_after_capture": {
            "available": True,
            "state": "Idle",
            "mpos_mm": [15.0, 195.0, 0.0],
            "wpos_mm": [15.0, 195.0, 0.0],
            "wco_mm": [0.0, 0.0, 0.0],
        },
    }
    status = build_coordinate_audit_status(
        settings,
        machine_status={
            "connected": True,
            "backend": "serial",
            "protocol": "grbl",
            "coordinate_reference_ready": False,
            "coordinate_state_reference": None,
            "jog_position_mm": None,
        },
        support_reference=_support(span=190.0),
        honeycomb_execution_signature=None,
        capture_snapshot=capture,
        **_ready_coordinate_audit_inputs(),
    )

    assert "automatic four-edge honeycomb detection" in status["required_next_action"]
    assert status["machine"]["capture_pose"]["state"] == "CURRENT"
    assert status["machine"]["capture_pose"]["trusted_at_capture"] is True


def test_capture_pose_becomes_stale_when_bed_map_changes(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path,
        machine={
            "backend": "serial",
            "protocol": "grbl",
            "port": "/dev/ttyUSB0",
            "allow_motion": True,
        },
    )
    status = build_coordinate_audit_status(
        settings,
        machine_status={
            "connected": True,
            "backend": "serial",
            "protocol": "grbl",
            "coordinate_reference_ready": False,
            "coordinate_state_reference": None,
            "jog_position_mm": None,
        },
        support_reference=_support(span=191.0),
        honeycomb_execution_signature=("honeycomb", 2, "frame", "map"),
        capture_snapshot={
            "trusted_at_capture": True,
            "bed_calibration_created_at": 6.0,
        },
        **_ready_coordinate_audit_inputs(),
    )

    capture = status["machine"]["capture_pose"]
    assert capture["state"] == "STALE"
    assert capture["trusted_at_capture"] is False
    assert capture["bed_map_current"] is False
    assert "Run Home / park and capture audit view" in status["required_next_action"]


def _ready_audit_dependencies() -> dict[str, object]:
    return {
        "camera_status": {"connected": True, "width": 1920, "height": 1080},
        "camera_readiness": {"state": "READY", "reasons": []},
        "lens_status": {
            "model": {"model_id": "lens-1", "image_size": [1920, 1080]}
        },
        "bed_status": {
            "calibration": {
                "created_at": 7.0,
                "rms_error_mm": 0.1,
                "max_error_mm": 0.3,
                "point_count": 25,
                "inlier_count": 25,
            },
            "validity": {"state": "VALID", "reasons": []},
            "axis_mapping": {"x_reversed": False, "y_reversed": False},
        },
    }


def test_serial_audit_prioritizes_capture_pose_before_stale_support_reteach(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        machine={
            "backend": "serial",
            "protocol": "grbl",
            "allow_motion": True,
        },
    )
    status = build_coordinate_audit_status(
        settings,
        machine_status={
            "connected": True,
            "backend": "serial",
            "protocol": "grbl",
            "coordinate_reference_ready": False,
            "coordinate_state_reference": None,
            "jog_position_mm": None,
        },
        support_reference=_support(span=190.0),
        honeycomb_execution_signature=None,
        capture_snapshot=None,
        **_ready_audit_dependencies(),
    )

    assert status["machine"]["capture_pose"]["state"] == "MISSING"
    assert status["required_next_action"].startswith(
        "Run Home / park and capture audit view"
    )
    assert "requires 191 x 191 mm" in " ".join(status["blockers"])


def test_serial_audit_preserves_capture_trust_after_motor_release(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        machine={
            "backend": "serial",
            "protocol": "grbl",
            "allow_motion": True,
            "photo_position": {"x": 15.0, "y": 195.0, "z": None},
        },
    )
    capture = {
        "trusted_at_capture": True,
        "home_completed": True,
        "parked": True,
        "bed_calibration_created_at": 7.0,
        "position_immediately_after_home": {
            "available": True,
            "state": "Idle",
            "mpos_mm": [0.0, 0.0, 0.0],
            "wpos_mm": [0.0, 0.0, None],
            "wco_mm": [0.0, 0.0, None],
        },
        "position_before_capture": {
            "available": True,
            "state": "Idle",
            "mpos_mm": [15.0, 195.0, 0.0],
            "wpos_mm": [15.0, 195.0, None],
            "wco_mm": [0.0, 0.0, None],
        },
        "position_after_capture": {
            "available": True,
            "state": "Idle",
            "mpos_mm": [15.0, 195.0, 0.0],
            "wpos_mm": [15.0, 195.0, None],
            "wco_mm": [0.0, 0.0, None],
        },
        "position_stable_during_capture": True,
        "maximum_position_delta_mm": 0.0,
        "commanded_position_error_xy_mm": [0.0, 0.0],
        "commanded_position_error_mm": 0.0,
        "coordinate_state": {
            "active_workspace": "G54",
            "active_offset_mm": [0.0, 0.0, 0.0],
            "g92_offset_mm": [0.0, 0.0, 0.0],
        },
        "motors_released_after_capture": True,
        "current_position_trusted_after_cleanup": False,
        "sampling_errors": [],
    }
    status = build_coordinate_audit_status(
        settings,
        machine_status={
            "connected": True,
            "backend": "serial",
            "protocol": "grbl",
            "coordinate_reference_ready": False,
            "coordinate_state_reference": None,
            "jog_position_mm": None,
        },
        support_reference=_support(span=190.0),
        honeycomb_execution_signature=None,
        capture_snapshot=capture,
        **_ready_audit_dependencies(),
    )

    pose = status["machine"]["capture_pose"]
    assert pose["state"] == "CURRENT"
    assert pose["trusted_at_capture"] is True
    assert pose["position_immediately_after_home"]["mpos_mm"] == pytest.approx(
        [0.0, 0.0, 0.0]
    )
    assert status["machine"]["coordinate_reference_ready"] is False
    assert "automatic four-edge honeycomb detection" in status["required_next_action"]


def test_serial_audit_requires_recapture_when_bed_map_changed(
    tmp_path: Path,
) -> None:
    settings = _settings(
        tmp_path,
        machine={
            "backend": "serial",
            "protocol": "grbl",
            "allow_motion": True,
        },
    )
    status = build_coordinate_audit_status(
        settings,
        machine_status={
            "connected": True,
            "backend": "serial",
            "protocol": "grbl",
            "coordinate_reference_ready": False,
            "coordinate_state_reference": None,
            "jog_position_mm": None,
        },
        support_reference=_support(span=190.0),
        honeycomb_execution_signature=None,
        capture_snapshot={
            "trusted_at_capture": True,
            "bed_calibration_created_at": 6.0,
        },
        **_ready_audit_dependencies(),
    )

    assert status["machine"]["capture_pose"]["state"] == "STALE"
    assert status["required_next_action"].startswith(
        "Run Home / park and capture audit view"
    )
