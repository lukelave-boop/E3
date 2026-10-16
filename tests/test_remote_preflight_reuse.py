from __future__ import annotations

import hashlib
import time
from dataclasses import replace

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import SafetyError
from laser_aligner.machine import remote_service as remote_module
from laser_aligner.machine import service as policy_module
from laser_aligner.machine.job_focus import WORKPIECE_FOCUS_CAPABILITY
from laser_aligner.machine.material_surface import CAPABILITY as MATERIAL_SURFACE_CAPABILITY
from laser_aligner.machine.material_surface import attach as attach_surface
from laser_aligner.machine.remote_service import RemoteMachineService
from laser_aligner.operation_focus import CAPABILITY as OPERATION_FOCUS_CAPABILITY

FOCUS_ID = "4674a756-53fb-41af-8722-af2352014fed"
OTHER_FOCUS_ID = "1f783d18-e60d-48c3-87b7-e370a04e2e99"
POWERED = "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S10\nG1 X20 Y20 F500\nM5"
UNPOWERED = "G21\nG90\nM5\nG0 X10 Y10 F1000\nM5"


@pytest.fixture
def remote(monkeypatch):
    service = RemoteMachineService(
        MachineSettings(backend="serial", protocol="grbl", allow_motion=True,
                        port="e3bridge://pi.test:9876"),
        LaserSettings(), hardware_enabled=True,
    )
    service._node_capabilities = (WORKPIECE_FOCUS_CAPABILITY, OPERATION_FOCUS_CAPABILITY)
    service._workpiece_focus_observed = True
    analyses = []
    original = service._policy._analyze_program

    def analyze(text, polygon=None):
        analyses.append(text)
        return original(text, polygon)

    def no_rpc(*args, **kwargs):
        pytest.fail("Desktop preflight must not upload or request controller work")

    monkeypatch.setattr(service._policy, "_analyze_program", analyze)
    monkeypatch.setattr(service, "_rpc", no_rpc)
    return service, analyses


def select_focus(service):
    service._job_focus_required = True
    service._selected_job_focus = {
        "id": FOCUS_ID, "target_z_mm": 20., "gap_mm": 7.,
        "clearance_z_mm": 30., "reusable": True,
    }


def select_surface_program(service):
    select_focus(service)
    service._selected_job_focus.update(
        measurement_id="measured-surface", contact_z_mm=5., surface_elevation_mm=5.,
        honeycomb_height_mm=0., reference={"border_z_mm": 0.}, session=(0, 1, 0),
    )
    service._node_capabilities += (MATERIAL_SURFACE_CAPABILITY,)
    service._capabilities_verified = True
    service._controller_session_generation = 1
    service._controller_state = "READY_MOTION"
    service._last_machine_status_monotonic = time.monotonic()
    service._material_honeycomb_height = 0.
    service._status_cache.update(connected=True, status_stale=False, monitor_connected=True)
    metadata = {
        "schema_version": 1, "model_id": "a" * 64, "binding_id": "b" * 64,
        "datum_id": "Home border", "capture_pose": [10., 10., None],
        "plan_id": FOCUS_ID, "measurement_id": "measured-surface",
        "surface_elevation_mm": 5., "honeycomb_height_mm": 0.,
        "reference": {"border_z_mm": 0.}, "session": [0, 1, 0],
        "evidence_source_ids": [str(index) * 64 for index in range(3)],
    }
    text = "E3FOCUS " + FOCUS_ID + "\n" + POWERED.replace("M5\nG0", "M5\nE3OPFOCUS RASTER\nG0")
    return service.preflight_program(attach_surface(text, metadata))


@pytest.mark.parametrize("text,selected", [
    (POWERED, False),
    (UNPOWERED, False),
    (UNPOWERED, True),
    ("E3FOCUS " + FOCUS_ID + "\n" + POWERED, True),
])
def test_unchanged_program_has_one_full_preflight(remote, text, selected):
    service, analyses = remote
    if selected:
        select_focus(service)
    program = service.preflight_program(text)
    assert len(analyses) == 1
    assert program.lines == tuple(text.splitlines())
    assert program.digest == hashlib.sha256(text.encode()).hexdigest()
    service._require_current_program(program)
    assert len(analyses) == 1


def test_new_focus_binding_gets_its_own_complete_validation(remote):
    service, analyses = remote
    select_focus(service)
    program = service.preflight_program(POWERED)
    expected = "E3FOCUS " + FOCUS_ID + "\n" + POWERED
    assert analyses == [POWERED, expected]
    assert program.lines == tuple(expected.splitlines())
    assert program.digest == hashlib.sha256(expected.encode()).hexdigest()
    service._require_current_program(program)
    assert len(analyses) == 2


def test_changed_text_cannot_reuse_the_original_validation(remote, monkeypatch):
    service, analyses = remote
    monkeypatch.setattr(remote_module, "attach_job_focus",
                        lambda text, _plan: text.replace("G90", "G91"))
    with pytest.raises(SafetyError):
        service.preflight_program(POWERED)
    assert len(analyses) == 2


@pytest.mark.parametrize("invalid", [
    POWERED.replace("G90", "G91"),
    POWERED.replace("X20 Y20", "X9999 Y20"),
    POWERED.replace("G1 X20", "G0 X20"),
    POWERED.removesuffix("\nM5"),
    POWERED + "\nE3FOCUS " + FOCUS_ID,
])
def test_invalid_input_rejects_before_any_reuse(remote, invalid):
    service, analyses = remote
    with pytest.raises(SafetyError):
        service.preflight_program(invalid)
    assert len(analyses) == 1


def test_current_policy_still_checked_before_reusing_result(remote, monkeypatch):
    service, _ = remote

    def changed_policy(text, _plan):
        service.settings.max_work_feed_mm_min = 100.
        return text

    monkeypatch.setattr(remote_module, "attach_job_focus", changed_policy)
    with pytest.raises(SafetyError, match="changed"):
        service.preflight_program(POWERED)


@pytest.mark.parametrize("change", ["digest", "lines", "flags", "profile"])
def test_tampered_first_result_cannot_be_reused(remote, monkeypatch, change):
    service, _ = remote
    original = service._policy.preflight_program

    def tampered(*args, **kwargs):
        program = original(*args, **kwargs)
        field, value = {
            "digest": ("digest", "0" * 64),
            "lines": ("lines", (*program.lines, "M4 S10")),
            "flags": ("requires_motion", False),
            "profile": ("safety_profile", ()),
        }[change]
        object.__setattr__(program, field, value)
        return program

    monkeypatch.setattr(service._policy, "preflight_program", tampered)
    with pytest.raises(SafetyError):
        service.preflight_program(POWERED)


def test_replaced_result_does_not_inherit_cached_authority(remote):
    service, _ = remote
    program = service.preflight_program(POWERED)
    forged = replace(program, requires_laser_authorization=False)
    with pytest.raises(SafetyError):
        service._require_current_program(forged)


@pytest.mark.parametrize("change", ["required", "unobserved"])
def test_live_focus_gate_still_required_for_unchanged_powered_bytes(remote, change):
    service, _ = remote
    if change == "required":
        service._job_focus_required = True
    else:
        service._workpiece_focus_observed = False
    with pytest.raises(SafetyError):
        service.preflight_program(POWERED)


def test_live_focus_change_invalidates_reused_program(remote):
    service, _ = remote
    select_focus(service)
    program = service.preflight_program("E3FOCUS " + FOCUS_ID + "\n" + POWERED)
    service._selected_job_focus["id"] = OTHER_FOCUS_ID
    with pytest.raises(SafetyError, match="focus changed"):
        service._require_current_program(program)


@pytest.mark.parametrize("change", ["capability", "target"])
def test_existing_operation_focus_still_checks_live_target_and_capability(remote, change):
    service, _ = remote
    select_focus(service)
    text = "E3FOCUS " + FOCUS_ID + "\n" + POWERED.replace("M5\nG0", "M5\nE3OPFOCUS RASTER\nG0")
    if change == "capability":
        service._node_capabilities = (WORKPIECE_FOCUS_CAPABILITY,)
    else:
        service._selected_job_focus["target_z_mm"] = 31.
    with pytest.raises(SafetyError):
        service.preflight_program(text)


def test_repeated_current_checks_use_validated_metadata_not_full_program_scans(remote, monkeypatch):
    service, analyses = remote
    program = select_surface_program(service)
    assert len(analyses) == 1

    def no_scan(*args, **kwargs):
        pytest.fail("An unchanged validated program must not be scanned again")

    monkeypatch.setattr(policy_module, "focus_program_metadata", no_scan)
    monkeypatch.setattr(policy_module, "material_surface_binding", no_scan)
    original_focus = remote_module.validate_targets
    original_surface = remote_module.validate_program_binding
    focus_checks, surface_checks = [], []

    def focus_check(lines, plan):
        focus_checks.append(tuple(lines))
        assert tuple(lines) == ("E3OPFOCUS RASTER",)
        return original_focus(lines, plan)

    def surface_check(lines, snapshot):
        surface_checks.append(tuple(lines))
        assert tuple(lines) == program.lines[:2]
        return original_surface(lines, snapshot)

    monkeypatch.setattr(remote_module, "validate_targets", focus_check)
    monkeypatch.setattr(remote_module, "validate_program_binding", surface_check)
    for _ in range(3):
        service._require_current_program(program)
        service._require_current_material_surface(program)
    service.arm_program(service.ARM_PHRASE, program)
    assert len(analyses) == 1
    assert len(focus_checks) == 4
    assert len(surface_checks) == 8


@pytest.mark.parametrize("change", [
    "stale", "session", "measurement", "disconnected", "material_capability",
    "operation_capability", "unobserved", "target",
])
def test_cached_metadata_never_caches_live_authority(remote, change):
    service, _ = remote
    program = select_surface_program(service)
    service._require_current_program(program)
    if change == "stale":
        service._last_machine_status_monotonic -= 10.
    elif change == "session":
        service._controller_session_generation += 1
    elif change == "measurement":
        service._selected_job_focus["measurement_id"] = "different-measurement"
    elif change == "disconnected":
        service._status_cache["connected"] = False
    elif change in {"material_capability", "operation_capability"}:
        removed = MATERIAL_SURFACE_CAPABILITY if change == "material_capability" else OPERATION_FOCUS_CAPABILITY
        service._node_capabilities = tuple(value for value in service._node_capabilities if value != removed)
    elif change == "unobserved":
        service._workpiece_focus_observed = False
    else:
        service._selected_job_focus["target_z_mm"] = 31.
    with pytest.raises(SafetyError):
        service._require_current_program(program)
