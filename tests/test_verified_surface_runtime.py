"""Execution and wire guards for explicitly verified surface travel."""
from __future__ import annotations

import hashlib
import time
import uuid
from dataclasses import replace

import pytest

from laser_aligner.config import LaserSettings, WorkArea
from laser_aligner.errors import SafetyError
from laser_aligner.gcode.job_plan import build_job_plan, restart_program_from_move
from laser_aligner.machine import job_focus
from laser_aligner.machine.pi_job_service import (
    PiJobService,
    canonical_program_bytes,
    execution_policy_diagnostic_profile,
    execution_policy_digest,
)
from laser_aligner.machine.pi_job_store import PiJobStore
from laser_aligner.machine.pi_machine_server import SERVER_CAPABILITIES
from laser_aligner.machine.remote_service import RemoteMachineService
from laser_aligner.raster_safety import (
    CAPABILITY,
    SURFACE_CAPABILITY,
    VerifiedSurfaceTravel,
    parse_raster_boundary,
)
from tests.fakes.simulator_transport import SimulatedTransport
from tests.test_raster_safety import envelope, program, service

SURFACE = ((5.0, 0.0), (15.0, 0.0), (15.0, 15.0), (5.0, 15.0))


def travel_record(**changes):
    surface = VerifiedSurfaceTravel(
        SURFACE, (0.0, 0.0), "a" * 64, True, "2026-10-16", "Test-only confirmed whole surface",
    )
    return envelope(x_min=0, x_max=10, y_min=0, y_max=10, surface=replace(surface, **changes))


def machine_for(travel=None, *, park=(5, 5), offset=(0, 0)):
    return service(travel=travel or travel_record(), area=WorkArea(-5, 20, -5, 20), park=park, offset=offset)


def surface_program(body, *, travel=None):
    return program(body, travel=travel or travel_record(), surface=SURFACE)


def test_surface_powered_coverage_and_outside_rectangle_travel_are_accepted():
    machine = machine_for()
    prepared = machine.preflight_program(surface_program([
        "G0 X5 Y15 F1000", "M4 S10", "G1 X15 Y15 F1000", "M5", "G0 X10 Y10 F1000",
    ]))
    assert prepared.requires_laser_authorization
    assert '"schema":2' in prepared.lines[0]


@pytest.mark.parametrize("body,park", [
    (["G0 X5 Y15 F1000"], (0, 10)),
    (["G0 X0 Y10 F1000", "G0 X5 Y15 F1000"], (5, 5)),
    (["G0 X5 Y15 F1000", "G1 X0 Y10 F1000"], (5, 5)),
])
def test_initial_and_subsequent_transfers_reject_nonconvex_union_gap(body, park):
    with pytest.raises(SafetyError, match="segment crosses a gap"):
        machine_for(park=park).preflight_program(surface_program(body))


def test_overlap_waypoint_keeps_full_surface_reachable():
    prepared = machine_for(park=(0, 10)).preflight_program(surface_program([
        "G0 X5 Y10 F1000", "G0 X5 Y15 F1000", "M4 S10", "G1 X15 Y15 F1000",
    ]))
    assert prepared.requires_laser_authorization


@pytest.mark.parametrize("body", [
    ["G0 X4 Y15 F1000"],
    ["G0 X15.001 Y15 F1000"],
    ["G0 X15 Y15.001 F1000"],
])
def test_surface_authority_does_not_grant_bounding_rectangle_corners_or_runout(body):
    with pytest.raises(SafetyError, match="outside.*verified"):
        machine_for().preflight_program(surface_program(body))


@pytest.mark.parametrize("body", [
    ["G0 X0 Y5 F1000", "M4 S10"],
    ["G0 X0 Y5 F1000", "M4 S0", "G1 X5 Y5 F1000 S10"],
    ["G0 X5 Y5 F1000", "M4 S10", "G1 X0 Y5 F1000"],
])
def test_rectangle_travel_does_not_expand_powered_surface(body):
    with pytest.raises(SafetyError, match="calibrated engraving surface"):
        machine_for().preflight_program(surface_program(body))


@pytest.mark.parametrize("change", ["polygon", "surface_digest", "engraving", "bounds"])
def test_job_declared_surface_is_never_authority_without_exact_local_match(change):
    text = surface_program(["G0 X5 Y5 F1000"])
    boundary = parse_raster_boundary(text.splitlines()[0])
    changes = {
        "polygon": {"travel_surface_polygon_mm": ((0, 0), (20, 0), (20, 20), (0, 20))},
        "surface_digest": {"surface_travel_digest": "b" * 64},
        "engraving": {"engraving_polygon_mm": ((0, 0), (15, 0), (15, 15), (0, 15))},
        "bounds": {"travel_bounds_mm": (0, 20, 0, 20)},
    }
    forged = text.replace(boundary.directive, replace(boundary, **changes[change]).directive)
    with pytest.raises(SafetyError):
        machine_for().preflight_program(forged)


@pytest.mark.parametrize("travel", [envelope(x_min=0, x_max=10, y_min=0, y_max=10),
                                  travel_record(verified=False),
                                  travel_record(coordinate_frame_digest="b" * 64)])
def test_missing_unverified_or_stale_local_surface_rejects_declared_authority(travel):
    with pytest.raises(SafetyError):
        machine_for(travel).preflight_program(surface_program(["G0 X5 Y15 F1000"]))


def test_current_offset_must_match_the_explicit_surface_record():
    with pytest.raises(SafetyError, match="offset"):
        machine_for(offset=(1, 0)).preflight_program(surface_program(["G0 X5 Y15 F1000"]))


def test_nonzero_verified_offset_preserves_powered_surface_and_carriage_checks():
    travel = travel_record(spot_offset_mm=(2, -3))
    machine = machine_for(travel, offset=(2, -3))
    prepared = machine.preflight_program(surface_program([
        "G0 X3 Y18 F1000", "M4 S10", "G1 X13 Y18 F1000",
    ], travel=travel))
    assert prepared.requires_laser_authorization
    with pytest.raises(SafetyError, match="outside.*verified"):
        machine.preflight_program(surface_program(["G0 X15 Y18 F1000"], travel=travel))


def test_surface_evidence_change_invalidates_cached_preflight_and_policy():
    machine = machine_for()
    prepared = machine.preflight_program(surface_program(["G0 X5 Y15 F1000"]))
    original_digest = execution_policy_digest(prepared)
    machine.settings.raster_travel_envelope = travel_record(evidence="Changed test evidence")
    with pytest.raises(SafetyError, match="changed"):
        machine._require_validated_program_integrity(prepared)
    changed = machine.preflight_program(surface_program(
        ["G0 X5 Y15 F1000"], travel=machine.settings.raster_travel_envelope,
    ))
    assert execution_policy_digest(changed) != original_digest


def test_legacy_program_keeps_output_bounds_even_with_verified_surface():
    machine = machine_for()
    machine.settings.work_area = WorkArea(0, 10, 0, 10)
    with pytest.raises(SafetyError, match="guarded output authority"):
        machine.preflight_program("G21\nG90\nM5\nG0 X5 Y15 F1000\nM5")


def test_start_here_retains_surface_contract_and_routes_new_approach():
    text = surface_program(["G0 X5 Y5 F1000", "G0 X5 Y15 F1000", "G1 X15 Y15 F1000"])
    plan = build_job_plan(text, power_max=1000, start_position=(5, 5))
    restarted, _ = restart_program_from_move(plan, 1, start_position=(0, 10))
    assert '"schema":2' in restarted
    prepared = machine_for(park=(0, 10)).preflight_program(restarted)
    assert prepared.requires_motion


@pytest.mark.parametrize("home_only", [False, True])
def test_actual_initial_transfer_is_checked_before_any_job_motion(monkeypatch, home_only):
    class RecordingTransport(SimulatedTransport):
        def __init__(self):
            super().__init__()
            self.commands = []

        def write_line(self, line):
            self.commands.append(line)
            super().write_line(line)

    transport = RecordingTransport()
    monkeypatch.setattr("laser_aligner.machine.service.create_machine_transport", lambda *_a: transport)
    machine = machine_for()
    try:
        machine.connect()
        machine.prepare_job_start() if home_only else machine.prepare_photo_position()
        prepared = machine.preflight_program(surface_program(["G0 X5 Y15 F1000", "G0 X10 Y10 F1000"]))
        if home_only:
            machine._jog_position_mm = None
            monkeypatch.setattr(machine, "sample_realtime_position", lambda **_kw: {
                "state": "Idle", "mpos_mm": (0, 10, 0),
            })
        else:
            machine._jog_position_mm = (0, 10)
        before = len(transport.commands)
        with pytest.raises(SafetyError, match="Actual initial carriage transfer.*segment crosses a gap"):
            machine.start_validated_program(prepared)
        assert not any(line.startswith(("G0", "G1")) for line in transport.commands[before:])
        machine._jog_position_mm = (5, 5)
        machine.start_validated_program(prepared)
        deadline = time.monotonic() + 5
        while machine._job.running and time.monotonic() < deadline:
            time.sleep(0.01)
        assert not machine._job.running and machine._job.error is None
        assert "G0 X5 Y15 F1000" in transport.commands
        assert not any("E3RASTER" in line for line in transport.commands)
    finally:
        machine.disconnect()


def test_measured_focus_transfer_rejects_gap_with_both_endpoints_inside(monkeypatch):
    machine = machine_for()
    prepared = machine.preflight_program(surface_program(["G0 X5 Y15 F1000"]))
    token = str(uuid.uuid4())
    prepared = replace(prepared, lines=(f"E3FOCUS {token}", *prepared.lines))
    plan = {"xy": (0, 10)}
    monkeypatch.setattr(job_focus, "selection_snapshot", lambda *_a: plan)
    monkeypatch.setattr(job_focus, "validate_targets", lambda *_a: None)
    with pytest.raises(SafetyError, match="Measured-focus transfer.*segment crosses a gap"):
        job_focus.selected_plan(machine, prepared)
    plan["xy"] = (5, 10)
    assert job_focus.selected_plan(machine, prepared) == plan


@pytest.mark.parametrize("legacy_program", [False, True])
def test_remote_requires_v2_for_surface_contract_or_configuration(monkeypatch, legacy_program):
    monkeypatch.setenv("E3_BRIDGE_TOKEN", "test-only-token-with-enough-characters")
    settings = machine_for().settings
    settings.port = "e3bridge://test-only:8765"
    remote = RemoteMachineService(settings, LaserSettings(), hardware_enabled=True)
    text = ("G21\nG90\nM5\nG0 X5 Y5 F1000\nM5" if legacy_program else
            surface_program(["G0 X5 Y15 F1000"]))
    remote._node_capabilities = (CAPABILITY,)
    with pytest.raises(SafetyError, match="Update the Pi companion.*verified surface"):
        remote.preflight_program(text)
    remote._node_capabilities = (CAPABILITY, SURFACE_CAPABILITY)
    assert remote.preflight_program(text).requires_motion
    assert SURFACE_CAPABILITY in SERVER_CAPABILITIES


def test_v1_companion_and_unconfigured_legacy_policy_remain_compatible(monkeypatch):
    monkeypatch.setenv("E3_BRIDGE_TOKEN", "test-only-token-with-enough-characters")
    settings = service().settings
    settings.port = "e3bridge://test-only:8765"
    remote = RemoteMachineService(settings, LaserSettings(), hardware_enabled=True)
    remote._node_capabilities = (CAPABILITY,)
    assert remote.preflight_program(program(["G0 X-2 Y10 F1000"])).requires_motion
    settings.raster_travel_envelope = None
    remote._node_capabilities = ()
    prepared = remote.preflight_program("G21\nG90\nM5\nG0 X10 Y10 F1000\nM5")
    assert len(prepared.safety_profile) == 25


@pytest.mark.parametrize("phase", ["finalize", "start"])
def test_pi_finalization_and_start_independently_check_surface_provenance(tmp_path, monkeypatch, phase):
    machine = machine_for()
    pi = PiJobService(machine, PiJobStore(tmp_path / "jobs"))
    prepared = machine.preflight_program(surface_program(["G0 X5 Y15 F1000"]))
    raw = canonical_program_bytes(prepared)
    identifier = str(uuid.uuid4())
    pi.begin_upload(identifier, "surface.gcode", len(raw), prepared.digest)
    pi.append_upload_chunk(identifier, 0, raw)
    args = dict(program_digest=prepared.digest, requires_laser_authorization=False,
                requires_motion=True, guarded_output_polygon_mm=None,
                policy_digest=execution_policy_digest(prepared),
                policy_diagnostic=execution_policy_diagnostic_profile(prepared))
    pi.finalize_upload(identifier, **args)
    machine.settings.raster_travel_envelope = travel_record(coordinate_frame_digest="b" * 64)
    def unexpected_connect():
        raise AssertionError("Changed authority must reject before connecting to a controller")
    monkeypatch.setattr(machine, "ensure_connected", unexpected_connect)
    with pytest.raises(SafetyError, match="changed|does not match"):
        if phase == "start":
            pi.start(identifier, **args)
        else:
            pi.finalize_upload(identifier, **args)


def test_pi_cannot_trust_forged_surface_even_with_correct_upload_digest(tmp_path):
    machine = machine_for()
    pi = PiJobService(machine, PiJobStore(tmp_path / "jobs"))
    prepared = machine.preflight_program(surface_program(["G0 X5 Y5 F1000"]))
    boundary = parse_raster_boundary(prepared.lines[0])
    forged = replace(boundary, travel_surface_polygon_mm=((0, 0), (20, 0), (20, 20), (0, 20)))
    raw = "\n".join((forged.directive, *prepared.lines[1:])).encode()
    digest = hashlib.sha256(raw).hexdigest()
    identifier = str(uuid.uuid4())
    pi.begin_upload(identifier, "forged.gcode", len(raw), digest)
    pi.append_upload_chunk(identifier, 0, raw)
    with pytest.raises(SafetyError):
        pi.finalize_upload(identifier, program_digest=digest, requires_laser_authorization=False,
                           requires_motion=True, guarded_output_polygon_mm=None,
                           policy_digest=execution_policy_digest(prepared),
                           policy_diagnostic=execution_policy_diagnostic_profile(prepared))
