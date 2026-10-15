from __future__ import annotations

import json
import time
import uuid
from dataclasses import replace

import pytest

from laser_aligner.config import ConfigError, LaserSettings, MachineSettings, WorkArea, load_settings
from laser_aligner.errors import SafetyError
from laser_aligner.machine.pi_job_service import (
    PiJobService,
    canonical_program_bytes,
    execution_policy_diagnostic_profile,
    execution_policy_digest,
)
from laser_aligner.machine.pi_job_store import PiJobStore
from laser_aligner.machine.profiles import MachineRegistry, _validated_pair
from laser_aligner.machine.remote_service import RemoteMachineService
from laser_aligner.machine.service import MachineService
from laser_aligner.raster_safety import (
    CAPABILITY,
    COORDINATE_REFERENCE,
    RasterTravelEnvelope,
    parse_raster_boundary,
    program_raster_boundary,
    raster_boundary_directive,
)
from tests.fakes.simulator_transport import SimulatedTransport

SURFACE = ((0.0, 0.0), (191.0, 0.0), (191.0, 191.0), (0.0, 191.0))


def envelope(**changes):
    return replace(RasterTravelEnvelope(
        -10, 245, -10, 245, True, "test controller", "test firmware",
        "test configuration digest", COORDINATE_REFERENCE, "2026-10-15",
    ), **changes)


def service(*, travel="default", offset=(0.0, 0.0), area=None, park=(10.0, 10.0)):
    settings = MachineSettings(
        protocol="grbl", port="test-only", allow_motion=True,
        controller_startup_delay=0.0, home_and_release_after_powered_job=False,
        work_area=area or WorkArea(0, 191, 0, 191),
        photo_x=park[0], photo_y=park[1],
        raster_travel_envelope=envelope() if travel == "default" else travel,
    )
    return MachineService(settings, LaserSettings(
        spot_offset_x_mm=offset[0], spot_offset_y_mm=offset[1],
    ), hardware_enabled=True)


def program(body, *, travel=None, surface=SURFACE):
    return "\n".join((raster_boundary_directive(surface, travel or envelope()),
                      "G21", "G90", "M5", *body, "M5"))


def test_surface_and_travel_are_distinct_and_reproduction_is_accepted():
    machine = service()
    text = program([
        "G0 X-2.106 Y3 F1000", "G1 X2.413 Y3 F1000", "M4 S200",
        "G1 X183.165 Y3 F1000", "M5", "G1 X187.684 Y3 F1000",
        "G0 X187.684 Y184.6 F1000", "G1 X183.165 Y184.6 F1000", "M4 S200",
        "G1 X2.413 Y184.6 F1000", "M5", "G1 X-2.106 Y184.6 F1000",
    ])
    result = machine.preflight_program(text)
    assert result.requires_motion and result.requires_laser_authorization
    assert "M4 S200" in result.lines
    assert result.lines[0] == text.splitlines()[0]
    assert result.lines[-1] == "M5"


@pytest.mark.parametrize("body", [
    ["G0 X-1 Y10 F1000", "M4 S10", "G1 X10 Y10 F1000"],
    ["G0 X-1 Y10 F1000", "M4 S0", "G1 X10 Y10 F1000 S10"],
    ["G0 X10 Y10 F1000", "M4 S10", "G1 X192 Y10 F1000"],
    ["G0 X10 Y10 F1000", "M4 S10", "G1 X10 Y192 F1000"],
])
def test_powered_current_start_and_end_points_must_stay_inside_surface(body):
    with pytest.raises(SafetyError, match="calibrated engraving surface"):
        service().preflight_program(program(body))


@pytest.mark.parametrize("body", [
    ["G0 X-10.1 Y10 F1000"],
    ["G0 X10 Y10 F1000", "G1 X246 Y10 F1000"],
    ["G0 X10 Y10 F1000", "G0 X10 Y246 F1000", "G0 X20 Y20 F1000"],
])
def test_all_off_movements_including_row_transitions_remain_travel_bounded(body):
    with pytest.raises(SafetyError, match="verified travel.*extra clearance required"):
        service().preflight_program(program(body))


@pytest.mark.parametrize("travel", [None, envelope(verified=False)])
def test_missing_or_unverified_travel_fails_even_for_inside_surface_job(travel):
    with pytest.raises(SafetyError, match="separately configured verified"):
        service(travel=travel).preflight_program(program(["G0 X10 Y10 F1000"]))


def test_initial_park_transfer_is_checked():
    with pytest.raises(SafetyError, match="Initial Home / park transfer"):
        service(park=(-11, 10)).preflight_program(program(["G0 X10 Y10 F1000"]))


@pytest.mark.parametrize("point", [(float("nan"), 10), (10, float("nan")),
                                  (float("inf"), 10), (10, -float("inf"))])
def test_nonfinite_actual_initial_position_cannot_pass_clearance_check(point):
    boundary = parse_raster_boundary(raster_boundary_directive(SURFACE, envelope()))
    with pytest.raises(SafetyError, match="must be finite"):
        boundary.require_travel_point(point, label="Actual initial carriage transfer")


def test_nonzero_spot_offset_is_subtracted_for_head_and_added_for_powered_beam():
    machine = service(offset=(2, -3), area=WorkArea(-5, 200, -5, 200))
    machine.preflight_program(program([
        "G0 X-5 Y10 F1000", "G1 X-2 Y10 F1000", "M4 S10",
        "G1 X189 Y10 F1000", "M5", "G1 X194 Y10 F1000",
    ]))
    with pytest.raises(SafetyError, match="calibrated engraving surface"):
        machine.preflight_program(program([
            "G0 X-2 Y2 F1000", "M4 S10", "G1 X10 Y2 F1000",
        ]))


def test_nonzero_offset_cannot_remove_existing_controller_output_guard():
    machine = service(offset=(2, 0))
    with pytest.raises(SafetyError, match="powered controller.*guarded output"):
        machine.preflight_program(program([
            "G0 X-1 Y10 F1000", "M4 S10", "G1 X10 Y10 F1000",
        ]))


def test_rotated_engraving_surface_checks_polygon_not_bounding_box():
    diamond = ((100.0, 0.0), (190.0, 100.0), (100.0, 190.0), (0.0, 100.0))
    service().preflight_program(program([
        "G0 X-2 Y100 F1000", "G1 X5 Y100 F1000", "M4 S10",
        "G1 X185 Y100 F1000", "M5", "G1 X195 Y100 F1000",
    ], surface=diamond))
    with pytest.raises(SafetyError, match="calibrated engraving surface"):
        service().preflight_program(program(["G0 X1 Y1 F1000", "M4 S10"], surface=diamond))


def test_boundary_cannot_widen_existing_output_authority():
    with pytest.raises(SafetyError, match="surface extends outside"):
        service().preflight_program(program(["G0 X10 Y10 F1000"], surface=(
            (0, 0), (192, 0), (192, 191), (0, 191),
        )))


def test_legacy_programs_keep_existing_off_motion_output_bounds():
    with pytest.raises(SafetyError, match="guarded output authority"):
        service().preflight_program("G21\nG90\nM5\nG0 X-1 Y10 F1000\nM5")


def test_rapid_while_enabled_and_modal_requirements_remain_rejected():
    with pytest.raises(SafetyError, match="rapid G0"):
        service().preflight_program(program(["G0 X10 Y10 F1000", "M4 S10", "G0 X20 Y10 F1000"]))
    text = program(["G0 X10 Y10 F1000"])
    for removed in ("G21\n", "G90\n", "M5\n"):
        with pytest.raises(SafetyError):
            service().preflight_program(text.replace(removed, "", 1))


@pytest.mark.parametrize("field,value", [
    ("x_min", True), ("x_max", float("inf")), ("y_min", "0"),
    ("verified", 1), ("controller", ""), ("firmware", []),
    ("configuration", " "), ("reference", "honeycomb_local"),
    ("tested_at", "unknown"),
])
def test_envelope_config_strict_validation(tmp_path, field, value):
    payload = envelope().to_dict()
    payload[field] = value
    config = tmp_path / "invalid.json"
    config.write_text(json.dumps({"machine": {"raster_travel_envelope": payload}}))
    with pytest.raises(ConfigError):
        load_settings(config)


def test_envelope_config_roundtrip_and_registry_retains_evidence(tmp_path):
    config = tmp_path / "valid.json"
    config.write_text(json.dumps({"machine": {"raster_travel_envelope": envelope().to_dict()}}))
    settings = load_settings(config)
    assert settings.machine.raster_travel_envelope == envelope()
    assert settings.public_dict()["machine"]["raster_travel_envelope"] == envelope().to_dict()
    machine, _ = _validated_pair(settings.public_dict()["machine"], {})
    assert machine.raster_travel_envelope == envelope()
    registry = MachineRegistry.load_or_migrate(settings)
    duplicated = registry.duplicate_machine(registry.active_machine_id)
    assert duplicated.machine.raster_travel_envelope is None
    assert registry.active_machine.machine.raster_travel_envelope == envelope()


def test_directive_is_canonical_unique_and_strict():
    line = raster_boundary_directive(SURFACE, envelope())
    assert parse_raster_boundary(line).directive == line
    with pytest.raises(SafetyError, match="only one"):
        program_raster_boundary([line, line])
    with pytest.raises(SafetyError):
        parse_raster_boundary(line.replace('"schema":1', '"schema":true'))
    with pytest.raises(SafetyError):
        parse_raster_boundary(line.replace('"schema":1', '"schema":1,"schema":1'))
    with pytest.raises(SafetyError):
        parse_raster_boundary("E3RASTER garbage")


@pytest.mark.parametrize("template", [
    "G21\nG90\nM5\nG0 X10 Y10 F1000\n{boundary}\nM5",
    "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S0\n{boundary}\nM5",
    "G21\nG90\nM5\nG0 X10 Y10 F1000\nM5\n{boundary}",
    "G21\nG90\nM5\n{boundary}",
])
def test_directive_requires_prologue_position_and_final_standalone_m5(template):
    text = template.format(boundary=raster_boundary_directive(SURFACE, envelope()))
    with pytest.raises(SafetyError, match="boundary must occur|end with a standalone M5"):
        service().preflight_program(text)


def test_config_verification_change_invalidates_cached_preflight():
    machine = service()
    validated = machine.preflight_program(program(["G0 X-2 Y10 F1000"]))
    machine._require_validated_program_integrity(validated)
    before = execution_policy_digest(validated)
    machine.settings.raster_travel_envelope = envelope(firmware="changed firmware")
    with pytest.raises(SafetyError, match="changed"):
        machine._require_validated_program_integrity(validated)
    fresh = machine.preflight_program(program(["G0 X-2 Y10 F1000"], travel=machine.settings.raster_travel_envelope))
    assert execution_policy_digest(fresh) != before


def test_unconfigured_policy_keeps_legacy_wire_identity():
    machine = service(travel=None)
    prepared = machine.preflight_program("G21\nG90\nM5\nG0 X10 Y10 F1000\nM5")
    assert len(prepared.safety_profile) == 25
    assert len(execution_policy_diagnostic_profile(prepared)["profile"]) == 25


def test_marlin_cannot_claim_zero_grbl_work_offset_reference():
    machine = service()
    machine.settings.protocol = "marlin"
    with pytest.raises(SafetyError, match="Marlin is not supported"):
        machine.preflight_program(program(["G0 X10 Y10 F1000"]))


def test_remote_requires_new_companion_before_sending_raster_job(monkeypatch):
    monkeypatch.setenv("E3_BRIDGE_TOKEN", "test-only-token-with-enough-characters")
    settings = service().settings
    settings.port = "e3bridge://test-only:8765"
    remote = RemoteMachineService(settings, LaserSettings(), hardware_enabled=True)
    with pytest.raises(SafetyError, match="Update the Pi companion"):
        remote.preflight_program(program(["G0 X-2 Y10 F1000"]))
    remote._node_capabilities = (CAPABILITY,)
    accepted = remote.preflight_program(program(["G0 X-2 Y10 F1000"]))
    assert accepted.requires_motion


def test_pi_independently_validates_and_rejects_changed_travel_evidence(tmp_path):
    machine = service()
    pi = PiJobService(machine, PiJobStore(tmp_path / "jobs"))
    prepared = machine.preflight_program(program(["G0 X-2 Y10 F1000"]))
    raw = canonical_program_bytes(prepared)
    identifier = str(uuid.uuid4())
    pi.begin_upload(identifier, "raster.gcode", len(raw), prepared.digest)
    pi.append_upload_chunk(identifier, 0, raw)
    args = dict(program_digest=prepared.digest, requires_laser_authorization=False,
                requires_motion=True, guarded_output_polygon_mm=None,
                policy_digest=execution_policy_digest(prepared),
                policy_diagnostic=execution_policy_diagnostic_profile(prepared))
    pi.finalize_upload(identifier, **args)
    machine.settings.raster_travel_envelope = envelope(configuration="changed config")
    with pytest.raises(SafetyError, match="verification changed"):
        pi.finalize_upload(identifier, **args)


@pytest.mark.parametrize("home_only", [False, True])
def test_raster_directive_never_reaches_controller_and_actual_start_is_checked(monkeypatch, home_only):
    class RecordingTransport(SimulatedTransport):
        def __init__(self):
            super().__init__()
            self.commands = []

        def write_line(self, line):
            self.commands.append(line)
            super().write_line(line)

    transport = RecordingTransport()
    monkeypatch.setattr("laser_aligner.machine.service.create_machine_transport", lambda *_a: transport)
    machine = service()
    try:
        machine.connect()
        if home_only:
            machine.prepare_job_start()
        else:
            machine.prepare_photo_position()
        prepared = machine.preflight_program(program(["G0 X-2 Y10 F1000", "G0 X20 Y10 F1000"]))
        machine._jog_position_mm = (-11.0, 10.0)
        with pytest.raises(SafetyError, match="Actual initial carriage transfer"):
            machine.start_validated_program(prepared)
        machine._jog_position_mm = None if home_only else (10.0, 10.0)
        machine.start_validated_program(prepared)
        deadline = time.monotonic() + 5
        while machine._job.running and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not machine._job.running
        assert machine._job.error is None
        assert "G0 X-2 Y10 F1000" in transport.commands
        assert not any("E3RASTER" in line for line in transport.commands)
        assert transport.commands[-1] == "?" or "M5" in transport.commands
    finally:
        machine.disconnect()
