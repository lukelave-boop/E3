from __future__ import annotations

import copy
import uuid

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.job_focus import WORKPIECE_FOCUS_CAPABILITY
from laser_aligner.machine.pi_machine_server import ACTION_MACHINE_FOCUS
from tests import test_remote_laser_focus as helpers

remote_focus = helpers.remote_focus

POWERED = "G21\nG90\nM5\nG0 X10 Y10 F1000\nM4 S100\nG1 X20 Y10 F500\nM5"
FRAME = "G21\nG90\nM5\nG0 X10 Y10 F1000\nM5"


def measured(result):
    plan = {"id": str(uuid.uuid4()), "target_z_mm": 12.6, "clearance_z_mm": 30.,
            "gap_mm": 7., "reusable": True}
    result.update(action="measure", job_focus_required=True, job_focus=plan,
                  job_focus_block_reason=None)
    return plan


def test_measure_automatically_binds_repeated_remote_jobs(remote_focus):
    service, _, result = remote_focus
    plan = measured(result)
    service.focus_control("measure", confirmed=True)
    for _ in range(2):
        program = service.preflight_program(POWERED)
        assert program.lines[0] == "E3FOCUS " + plan["id"]
        service._require_current_program(program)
    assert service.preflight_program(FRAME).lines[0] == "G21"


def test_remote_focus_status_restores_workpiece_on_attach(remote_focus):
    service, _, result = remote_focus
    plan = measured(result)
    service._refresh_once()
    assert service.preflight_program(POWERED).lines[0] == "E3FOCUS " + plan["id"]


def test_remote_gap_change_invalidates_prepared_job_without_measurement(remote_focus):
    service, pi, result = remote_focus
    measured(result)
    service.focus_control("measure", confirmed=True)
    old = service.preflight_program(POWERED)
    new = measured(result)
    result["action"] = "set_job_gap"
    new.update(gap_mm=5., target_z_mm=10.6)
    service.focus_control("set_job_gap", confirmed=True, gap_mm=5.)
    assert pi.requests[-1]["control"] == "set_job_gap"
    with pytest.raises(SafetyError, match="changed after preflight"):
        service._require_current_program(old)
    assert service.preflight_program(POWERED).lines[0] == "E3FOCUS " + new["id"]


@pytest.mark.parametrize("action", ["status", "reference", "clear_surface", "forget_z"])
def test_lost_workpiece_cannot_fall_back_to_ordinary_laser_output(remote_focus, action):
    service, _, result = remote_focus
    measured(result)
    service.focus_control("measure", confirmed=True)
    old = service.preflight_program(POWERED)
    result.update(action=action, job_focus=None, job_focus_block_reason="Measure the new workpiece")
    if action == "forget_z":
        result.update(available=False, reference_ready=False,
                      current_readback={"fresh": False, "z_known": False, "z_mm": None})
    service.focus_control(action, confirmed=action != "status")
    with pytest.raises(SafetyError, match="Measure the new workpiece"):
        service.preflight_program(POWERED)
    with pytest.raises(SafetyError, match="Measure the new workpiece"):
        service._require_current_program(old)
    service._require_current_program(service.preflight_program(FRAME))


@pytest.mark.parametrize("change", ["missing", "id", "clearance", "nan", "required", "reusable"])
def test_invalid_pi_focus_evidence_revokes_previous_binding(remote_focus, change):
    service, _, result = remote_focus
    measured(result)
    service.focus_control("measure", confirmed=True)
    if change == "missing":
        del result["job_focus_required"]
    elif change == "required":
        result["job_focus_required"] = False
    elif change == "id":
        result["job_focus"]["id"] = "invalid"
    elif change == "clearance":
        result["job_focus"]["target_z_mm"] = 31.
    elif change == "nan":
        result["job_focus"]["target_z_mm"] = float("nan")
    else:
        result["job_focus"]["reusable"] = False
    with pytest.raises(MachineError):
        service.focus_control("measure", confirmed=True)
    with pytest.raises(SafetyError):
        service.preflight_program(POWERED)


@pytest.mark.parametrize("action", ["measure", "set_job_gap"])
def test_old_pi_rejected_before_measurement_or_powered_upload(remote_focus, action):
    service, pi, _ = remote_focus
    pi.capabilities.remove(WORKPIECE_FOCUS_CAPABILITY)
    with pytest.raises(MachineError, match="Update the Pi companion"):
        service.focus_control(action, confirmed=True)
    assert not any(request["action"] == ACTION_MACHINE_FOCUS for request in pi.requests)
    with pytest.raises(SafetyError, match="Update the Pi companion"):
        service.preflight_program(POWERED)
    service.preflight_program(FRAME)


def test_new_pi_requires_focus_observation_before_powered_preflight(remote_focus):
    service, _, _ = remote_focus
    service._require_capabilities()
    with pytest.raises(SafetyError, match="Refresh"):
        service.preflight_program(POWERED)
    service._refresh_once()
    service.preflight_program(POWERED)


def test_measurement_after_preflight_requires_new_bound_program(remote_focus):
    service, _, result = remote_focus
    service._refresh_once()
    old = service.preflight_program(POWERED)
    measured(result)
    service.focus_control("measure", confirmed=True)
    with pytest.raises(SafetyError, match="changed after preflight"):
        service._require_current_program(old)


def test_new_session_revokes_remote_focus_until_authoritative_observation(remote_focus):
    service, pi, result = remote_focus
    measured(result)
    service.focus_control("measure", confirmed=True)
    pi.session_generation += 1
    service._commit_current_response(pi._response({"request_id": str(uuid.uuid4())}), action="test")
    with pytest.raises(SafetyError, match="Refresh"):
        service.preflight_program(POWERED)
    result.update(job_focus=None, job_focus_block_reason="Reference and measure again")
    service._refresh_once()
    with pytest.raises(SafetyError, match="Reference and measure"):
        service.preflight_program(POWERED)


def test_old_controller_snapshot_cannot_replace_new_measurement(remote_focus):
    service, pi, result = remote_focus
    first = measured(result)
    service.focus_control("measure", confirmed=True)
    stale = pi._response({"request_id": str(uuid.uuid4())})
    raw = pi._machine_status()
    raw["workpiece_focus"] = copy.deepcopy(result)
    pi.state_revision += 1
    second = measured(result)
    service.focus_control("measure", confirmed=True)
    assert second["id"] != first["id"]
    assert service._cache_remote_status(raw, job_record=None, response=stale) is False
    assert service.preflight_program(POWERED).lines[0] == "E3FOCUS " + second["id"]


@pytest.mark.parametrize("read_path", ["monitor", "explicit_refresh"])
def test_delayed_same_revision_status_cannot_replace_new_gap(remote_focus, monkeypatch, read_path):
    from laser_aligner.machine import remote_service
    from laser_aligner.machine.pi_machine_server import ACTION_MACHINE_STATUS

    service, pi, result = remote_focus
    first = measured(result)
    service.focus_control("measure", confirmed=True)
    revision = pi.state_revision
    original_exchange = remote_service.request_response
    changed = []

    def delayed_status(host, port, token, request, **kwargs):
        # Capture the old status, then complete a gap edit before delivering it.
        response = original_exchange(host, port, token, request, **kwargs)
        if request["action"] == ACTION_MACHINE_STATUS and not changed:
            second = measured(result)
            second.update(gap_mm=5.0, target_z_mm=10.6)
            result["action"] = "set_job_gap"
            changed.append(second)
            service.focus_control("set_job_gap", confirmed=True, gap_mm=5.0)
            assert response["state_revision"] == pi.state_revision == revision
        return response

    monkeypatch.setattr(remote_service, "request_response", delayed_status)
    if read_path == "monitor":
        service._refresh_once()
    else:
        service.refresh_status()

    assert changed and changed[0]["id"] != first["id"]
    program = service.preflight_program(POWERED)
    assert program.lines[0] == "E3FOCUS " + changed[0]["id"]
    assert service._selected_job_focus["gap_mm"] == 5.0
    assert service.status()["workpiece_focus"]["job_focus"]["id"] == changed[0]["id"]
    service._require_current_program(program)
