from __future__ import annotations

import base64
import copy
import json
from dataclasses import replace
from unittest.mock import Mock

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.gcode.preview import parse_gcode_segments
from laser_aligner.machine.material_surface import (
    CAPABILITY,
    attach,
    parse,
    program_binding,
    validate_program_binding,
)
from laser_aligner.machine.pi_machine_server import SERVER_CAPABILITIES
from tests import test_job_focus as focus_helpers
from tests import test_remote_machine_service as remote_helpers

focus = focus_helpers.focus
focus_machine = focus_helpers.focus_machine
machine_probe = focus_helpers.machine_probe
PROGRAM = focus_helpers.PROGRAM


def metadata(snapshot):
    return {
        "schema_version": 1, "model_id": "a" * 64, "binding_id": "b" * 64,
        "datum_id": "Home border", "capture_pose": [110., 110., None],
        "plan_id": snapshot["id"], "measurement_id": snapshot["measurement_id"],
        "surface_elevation_mm": snapshot["surface_elevation_mm"],
        "honeycomb_height_mm": snapshot["honeycomb_height_mm"],
        "reference": copy.deepcopy(snapshot["reference"]), "session": list(snapshot["session"]),
        "evidence_source_ids": [str(index) * 64 for index in range(3)],
    }


def selected(machine):
    focus_helpers.select(machine)
    snapshot = machine.material_surface_snapshot()
    assert snapshot is not None
    return snapshot, metadata(snapshot)


def test_surface_binding_roundtrip_digest_and_preview(focus_machine):
    machine, _, _ = focus_machine
    snapshot, binding = selected(machine)
    focused = machine.preflight_program(PROGRAM)
    text = attach("\n".join(focused.lines), binding)
    assert text.splitlines()[0].startswith("E3FOCUS ")
    assert text.splitlines()[1].startswith("E3SURFACE 1 ")
    assert program_binding(text.splitlines()) == binding
    assert attach(text, binding) == text
    validated = machine.preflight_program(text)
    assert validated.digest != focused.digest
    assert parse_gcode_segments(text) == parse_gcode_segments(PROGRAM)
    assert validate_program_binding(validated.lines, snapshot) == binding
    with pytest.raises(SafetyError):
        machine._require_validated_program_integrity(replace(validated, digest=focused.digest))


def test_surface_binding_is_never_sent_to_either_controller(focus_machine, monkeypatch):
    machine, focus_state, primary = focus_machine
    _, binding = selected(machine)
    events = []
    for device in (primary, focus_state.serial):
        original = device.write_line
        def write(line, original=original):
            events.append(line)
            return original(line)
        monkeypatch.setattr(device, "write_line", write)
    program = machine.preflight_program(attach(PROGRAM, binding))
    machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    focus_helpers.wait(machine)
    assert machine._job.error is None
    assert "M4 S100" in events
    assert not any(line.startswith(("E3SURFACE", "E3FOCUS")) for line in events)
    assert machine.material_surface_snapshot()["measurement_id"] == binding["measurement_id"]


def test_surface_snapshot_queries_no_controller_and_is_defensive(focus_machine):
    machine, focus_state, primary = focus_machine
    snapshot, _ = selected(machine)
    primary.write_line = Mock(wraps=primary.write_line)
    focus_state.serial.write_line = Mock(wraps=focus_state.serial.write_line)
    snapshot["reference"]["border_z_mm"] = 999
    current = machine.material_surface_snapshot()
    assert current["surface_elevation_mm"] == 5
    assert current["honeycomb_height_mm"] == -1.5
    assert current["reference"]["border_z_mm"] == 0
    primary.write_line.assert_not_called()
    focus_state.serial.write_line.assert_not_called()


def test_material_measurement_is_rechecked_between_validation_and_arm_grant(focus_machine, monkeypatch):
    machine, focus_state, primary = focus_machine
    _, binding = selected(machine)
    program = machine.preflight_program(attach(PROGRAM, binding))
    original = machine._require_current_safety_profile
    def changed_after_validation(candidate):
        original(candidate)
        focus_state.state.job_plan["measurement_id"] = "replacement-after-validation"
        focus_state.state.surface["id"] = "replacement-after-validation"
    monkeypatch.setattr(machine, "_require_current_safety_profile", changed_after_validation)
    primary.write_line = Mock(wraps=primary.write_line)
    with pytest.raises(SafetyError, match="measurement_id changed"):
        machine.arm_program(machine.ARM_PHRASE, program)
    assert not machine.armed
    primary.write_line.assert_not_called()


@pytest.mark.parametrize("phase", ["arm", "start"])
@pytest.mark.parametrize("field", ["plan_id", "measurement_id", "surface_elevation_mm", "honeycomb_height_mm", "reference", "session"])
def test_changed_material_binding_rejects_before_output_or_travel(focus_machine, phase, field):
    machine, _, primary = focus_machine
    _, binding = selected(machine)
    if field == "plan_id":
        # Existing focus must still match; this models a different selected plan.
        machine._laser_focus.job_plan["id"] = "ffffffff-ffff-ffff-ffff-ffffffffffff"
    else:
        changed = {"measurement_id": "other", "surface_elevation_mm": 6., "honeycomb_height_mm": -1.,
                   "reference": {"border_z_mm": -1}, "session": [999, 999, 999]}
        binding[field] = changed[field]
    if field == "plan_id":
        # Preflight auto-attaches the current focus ID and rejects the cross-binding.
        with pytest.raises(SafetyError):
            program = machine.preflight_program(attach(PROGRAM, binding))
            machine.arm_program(machine.ARM_PHRASE, program)
        return
    program = machine.preflight_program(attach(PROGRAM, binding))
    primary.write_line = Mock(wraps=primary.write_line)
    with pytest.raises(MachineError, match="Material surface"):
        if phase == "arm":
            machine.arm_program(machine.ARM_PHRASE, program)
        else:
            machine.start_preflighted_program(program, authorization_phrase=machine.ARM_PHRASE)
    primary.write_line.assert_not_called()


@pytest.mark.parametrize("change", ["clear", "reference", "session", "honeycomb", "disconnect"])
def test_snapshot_disappears_when_measurement_authority_changes(focus_machine, change):
    machine, focus_state, _ = focus_machine
    _, binding = selected(machine)
    program = machine.preflight_program(attach(PROGRAM, binding))
    if change == "clear":
        focus_state.state.clear_surface()
    elif change == "reference":
        focus_state.state.reference = None
    elif change == "session":
        focus_state.state.session = None
    elif change == "honeycomb":
        focus_state.state.honeycomb_height_mm = -2
    else:
        machine._connected = False
    if change in {"clear", "disconnect"}:
        assert machine.material_surface_snapshot() is None
    elif change in {"reference", "session"}:
        with pytest.raises(MachineError, match="stale"):
            machine.material_surface_snapshot()
    with pytest.raises(MachineError, match="Material surface|stale"):
        machine.arm_program(machine.ARM_PHRASE, program)


def test_present_above_range_measurement_is_rejected_not_treated_as_initial_probe(focus_machine):
    machine, focus_state, _ = focus_machine
    selected(machine)
    # Its authoritative focus selection remains present and internally coherent;
    # the camera's supported 20 mm envelope is separately stricter than focus.
    focus_state.state.job_plan["contact_z_mm"] = 19.
    with pytest.raises(SafetyError, match="0 to 20"):
        machine.material_surface_snapshot()


@pytest.mark.parametrize("mode", ["duplicate", "late", "wrong-version", "extra-space", "suffix", "comment", "unknown-field", "nan", "duplicate-json-key"])
def test_surface_directive_rejects_malformed_or_misplaced_program(focus_machine, mode):
    machine, _, _ = focus_machine
    _, binding = selected(machine)
    directive = attach(PROGRAM, binding).splitlines()[0]
    text = directive + "\n" + PROGRAM
    if mode == "duplicate":
        text = directive + "\n" + text
    elif mode == "late":
        text = "G21\n" + text
    elif mode == "wrong-version":
        text = text.replace("E3SURFACE 1 ", "E3SURFACE 2 ")
    elif mode == "extra-space":
        text = text.replace("E3SURFACE 1 ", "E3SURFACE 1  ")
    elif mode == "suffix":
        text = directive + " garbage\n" + PROGRAM
    elif mode == "comment":
        text = directive + " ; trailing text\n" + PROGRAM
    else:
        raw = json.dumps(binding, sort_keys=True, separators=(",", ":"))
        if mode == "unknown-field":
            raw = raw[:-1] + ',"extra":true}'
        elif mode == "nan":
            raw = raw.replace('"surface_elevation_mm":5.0', '"surface_elevation_mm":NaN')
        else:
            raw = raw[:-1] + ',"schema_version":1}'
        text = "E3SURFACE 1 " + base64.urlsafe_b64encode(raw.encode()).decode() + "\n" + PROGRAM
    with pytest.raises(SafetyError):
        machine.preflight_program(text)


def test_surface_preflight_cannot_loosen_existing_motion_guards(focus_machine):
    machine, _, _ = focus_machine
    _, binding = selected(machine)
    invalid = [PROGRAM.replace("X105", "X999"), PROGRAM.replace("G1 X105", "G0 X105"),
               PROGRAM.replace("G21\n", ""), PROGRAM.rsplit("M5", 1)[0], PROGRAM + "\nG91"]
    for text in invalid:
        with pytest.raises(SafetyError):
            machine.preflight_program(attach(text, binding))


def remote_with_surface(monkeypatch, snapshot):
    monkeypatch.setenv("E3_BRIDGE_TOKEN", remote_helpers._TOKEN)
    fake = remote_helpers.FakePi()
    fake.capabilities.extend([CAPABILITY, "pi-workpiece-focus-v1"])
    fake.session_generation = snapshot["session"][1]
    original = fake._machine_status
    def status():
        return {**original(), "workpiece_focus": {
            "job_focus_required": True, "job_focus": copy.deepcopy(snapshot),
            "honeycomb_height_mm": snapshot["honeycomb_height_mm"], "job_focus_block_reason": None,
        }}
    fake._machine_status = status
    remote_helpers._install_fake(monkeypatch, fake)
    service = remote_helpers._service()
    service._refresh_once()
    return service, fake


def test_remote_snapshot_uses_only_fresh_accepted_status_without_rpc(focus_machine, monkeypatch):
    snapshot, binding = selected(focus_machine[0])
    remote, fake = remote_with_surface(monkeypatch, snapshot)
    before = len(fake.requests)
    assert remote.material_surface_snapshot() == snapshot
    assert len(fake.requests) == before
    program = remote.preflight_program(attach(PROGRAM, binding))
    remote.arm_program(remote.ARM_PHRASE, program)
    assert len(fake.requests) == before
    remote._last_machine_status_monotonic -= 4
    with pytest.raises(SafetyError, match="stale"):
        remote.material_surface_snapshot()
    with pytest.raises(SafetyError, match="stale"):
        remote.arm_program(remote.ARM_PHRASE, program)
    assert len(fake.requests) == before


@pytest.mark.parametrize("change", ["disconnect", "detached", "session", "unobserved", "stale"])
def test_remote_snapshot_rejects_changed_authority(focus_machine, monkeypatch, change):
    snapshot, _ = selected(focus_machine[0])
    remote, _ = remote_with_surface(monkeypatch, snapshot)
    if change == "disconnect":
        remote._status_cache["connected"] = False
    elif change == "detached":
        remote._detached = True
    elif change == "session":
        remote._controller_session_generation += 1
    elif change == "unobserved":
        remote._workpiece_focus_observed = False
    else:
        remote._status_cache["status_stale"] = True
    if change in {"disconnect", "detached"}:
        assert remote.material_surface_snapshot() is None
    else:
        with pytest.raises(SafetyError, match="stale|session changed"):
            remote.material_surface_snapshot()


def test_old_pi_rejects_surface_jobs_before_upload(focus_machine, monkeypatch):
    snapshot, binding = selected(focus_machine[0])
    remote, fake = remote_with_surface(monkeypatch, snapshot)
    remote._node_capabilities = tuple(value for value in remote._node_capabilities if value != CAPABILITY)
    before = len(fake.requests)
    with pytest.raises(SafetyError, match="Update the Pi"):
        remote.preflight_program(attach(PROGRAM, binding))
    assert len(fake.requests) == before
    assert CAPABILITY in SERVER_CAPABILITIES


@pytest.mark.parametrize("change_during_upload", [False, True])
def test_remote_upload_retains_binding_and_rechecks_before_start(focus_machine, monkeypatch, change_during_upload):
    snapshot, binding = selected(focus_machine[0])
    remote, fake = remote_with_surface(monkeypatch, snapshot)
    program = remote.preflight_program(attach(PROGRAM, binding))
    remote.arm_program(remote.ARM_PHRASE, program)
    if change_during_upload:
        def change(action, _request):
            if action == remote_helpers.ACTION_JOB_FINALIZE:
                remote._selected_job_focus["measurement_id"] = "changed-after-upload"
        fake.before_request = change
        with pytest.raises(SafetyError, match="measurement_id changed"):
            remote.start_validated_program(program)
        assert not any(request["action"] == remote_helpers.ACTION_JOB_START for request in fake.requests)
    else:
        result = remote.start_validated_program(program)
        assert result["accepted"] is True
    assert len(fake.uploads) == 1
    uploaded = bytes(next(iter(fake.uploads.values()))).decode("utf-8")
    assert program_binding(uploaded.splitlines()) == binding


def test_parse_non_surface_and_attach_none_leave_ordinary_program_unchanged():
    assert parse("G21") is None
    assert attach(PROGRAM, None) == PROGRAM
    assert program_binding(PROGRAM.splitlines()) is None
