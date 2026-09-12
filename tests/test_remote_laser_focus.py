from __future__ import annotations

import copy
import uuid

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.laser_focus import (
    CLICK_CAPABILITY,
    PI_CAPABILITY,
    RECOVERY_CAPABILITY,
    XY_CAPABILITY,
    XY_RECOVERY_CAPABILITY,
)
from laser_aligner.machine.pi_machine_server import ACTION_MACHINE_FOCUS, SERVER_CAPABILITIES
from tests import test_laser_focus as focus_helpers
from tests import test_pi_machine_server as helpers
from tests.test_remote_machine_service import FakePi, _service

server_harness = helpers.server_harness
focus = focus_helpers.focus


@pytest.fixture
def remote_focus(monkeypatch):
    monkeypatch.setenv("E3_BRIDGE_TOKEN", "remote-machine-test-token-value")
    pi, service = FakePi(), _service()
    pi.capabilities.append(PI_CAPABILITY)
    result = {"action": "status", "available": True, "reference_ready": False,
              "current_readback": {"z_mm": 20., "z_known": True, "fresh": True}, "max_z_mm": 80.}
    def exchange(host, port, token, request, **kwargs):
        if request["action"] != ACTION_MACHINE_FOCUS:
            return pi(host, port, token, request, **kwargs)
        pi.requests.append(copy.deepcopy(request))
        pi.timeouts.append(kwargs["timeout"])
        if pi.before_request:
            pi.before_request(request["action"], request)
        return pi._response(request, result=copy.deepcopy(result))
    monkeypatch.setattr("laser_aligner.machine.remote_service.request_response", exchange)
    return service, pi, result


def test_focus_capability_advertised():
    assert PI_CAPABILITY in SERVER_CAPABILITIES
    assert XY_CAPABILITY in SERVER_CAPABILITIES
    assert CLICK_CAPABILITY in SERVER_CAPABILITIES
    assert RECOVERY_CAPABILITY in SERVER_CAPABILITIES
    assert XY_RECOVERY_CAPABILITY in SERVER_CAPABILITIES


def test_preview_refreshes_cached_activity_before_returning(remote_focus):
    service, pi, result = remote_focus
    result['action'] = 'preview'
    result['preview'] = {'id': 'preview-1', 'target_z_mm': 2.6}
    def observed_active(action, request):
        if action == ACTION_MACHINE_FOCUS:
            service._status_cache['z_probe'] = {'active': True}
    pi.before_request = observed_active
    response = service.focus_control('preview', confirmed=True, measurement_id=str(uuid.uuid4()))
    assert response['preview'] == result['preview']
    assert not (service.status().get('z_probe') or {}).get('active', False)
    assert [r['action'] for r in pi.requests][-2:] == [ACTION_MACHINE_FOCUS, 'machine.status']


@pytest.mark.parametrize('change', ['stop', 'restart', 'session', 'read_failure'])
def test_preview_rejects_changed_authority_during_completion_refresh(remote_focus, change):
    service, pi, result = remote_focus
    result['action'] = 'preview'
    service._require_capabilities()
    def changed(action, request):
        if action != 'machine.status':
            return
        if change == 'stop':
            with service._stop_epoch_lock:
                service._stop_epoch += 1
        elif change == 'restart':
            pi.boot_id = str(uuid.uuid4())
        elif change == 'session':
            pi.session_generation += 1
        else:
            raise MachineError('Completion status unavailable')
    pi.before_request = changed
    with pytest.raises(MachineError):
        service.focus_control('preview', confirmed=True, measurement_id=str(uuid.uuid4()))


@pytest.mark.parametrize("action", ["status", "reference", "measure", "clearance", "forget", "clear_surface"])
def test_remote_typed_focus_binds_session(remote_focus, action):
    service, pi, result = remote_focus
    result["action"] = action
    response = service.focus_control(action, confirmed=action != "status", clearance_z_mm=40)
    request = pi.requests[-1]
    assert request["control"] == action
    assert request["clearance_z_mm"] == 40
    assert request["expected_boot_id"] == pi.boot_id
    assert request["expected_session_generation"] == pi.session_generation
    assert response["current_readback"]["z_mm"] == 20


def test_old_pi_cannot_receive_focus_motion(remote_focus):
    service, pi, _ = remote_focus
    pi.capabilities.remove(PI_CAPABILITY)
    with pytest.raises(MachineError, match="Update"):
        service.focus_control("reference", confirmed=True)
    assert all(r["action"] != ACTION_MACHINE_FOCUS for r in pi.requests)


def test_old_focus_pi_cannot_receive_xy_alignment(remote_focus):
    service, pi, _ = remote_focus
    with pytest.raises(MachineError, match="Update"):
        service.focus_control("align_probe", confirmed=True)
    assert all(r["action"] != ACTION_MACHINE_FOCUS for r in pi.requests)


def test_remote_offset_vector_is_sent_exactly(remote_focus):
    service, pi, result = remote_focus
    pi.capabilities.append(XY_CAPABILITY)
    result["action"] = "set_xy_offset"
    service.focus_control("set_xy_offset", confirmed=True, value=[3.302, 38.608])
    assert pi.requests[-1]["value"] == [3.302, 38.608]


def test_previous_xy_pi_cannot_receive_selected_probe_target(remote_focus):
    service, pi, _ = remote_focus
    pi.capabilities.append(XY_CAPABILITY)
    with pytest.raises(MachineError, match="Update"):
        service.focus_control("position_probe", confirmed=True, value=[75., 150.])
    assert all(r["action"] != ACTION_MACHINE_FOCUS for r in pi.requests)


def test_remote_selected_probe_target_keeps_absolute_vector_and_session(remote_focus):
    service, pi, result = remote_focus
    pi.capabilities.extend([XY_CAPABILITY, CLICK_CAPABILITY])
    result["action"] = "position_probe"
    service.focus_control("position_probe", confirmed=True, value=[75.123, 150.456], clearance_z_mm=40)
    request = pi.requests[-1]
    assert request["value"] == [75.123, 150.456]
    assert request["clearance_z_mm"] == 40
    assert request["expected_boot_id"] == pi.boot_id
    assert request["expected_session_generation"] == pi.session_generation


@pytest.mark.parametrize("delta", [-5, -2, 2, 5])
def test_remote_approach_jog_forwards_delta_and_measurement(remote_focus, delta):
    service, pi, result = remote_focus
    result["action"] = "jog"
    measurement_id = str(uuid.uuid4())
    response = service.focus_control("jog", confirmed=True, value=delta,
                                     measurement_id=measurement_id)
    request = pi.requests[-1]
    assert request["action"] == ACTION_MACHINE_FOCUS
    assert request["control"] == "jog"
    assert request["value"] == delta
    assert request["measurement_id"] == measurement_id
    assert request["confirmed"] is True
    assert request["expected_boot_id"] == pi.boot_id
    assert request["expected_session_generation"] == pi.session_generation
    assert response["action"] == "jog"


@pytest.mark.parametrize("change", ["hardware", "motion", "confirmation", "delta"])
def test_remote_rejects_without_focus_request(remote_focus, change):
    service, pi, _ = remote_focus
    kwargs = dict(confirmed=True, value=.1, measurement_id=str(uuid.uuid4()))
    if change == "hardware":
        service.hardware_enabled = False
    elif change == "motion":
        service.settings.allow_motion = False
    elif change == "confirmation":
        kwargs["confirmed"] = False
    else:
        kwargs["value"] = 5.01
    with pytest.raises(SafetyError):
        service.focus_control("jog", **kwargs)
    assert all(r["action"] != ACTION_MACHINE_FOCUS for r in pi.requests)


@pytest.mark.parametrize("change", ["action", "fresh", "known", "z", "max", "reference"])
def test_remote_rejects_invalid_readback(remote_focus, change):
    service, _, result = remote_focus
    if change == "action":
        result["action"] = "move"
    elif change == "fresh":
        result["current_readback"]["fresh"] = False
    elif change == "known":
        result["current_readback"]["z_known"] = 1
    elif change == "z":
        result["current_readback"]["z_mm"] = float("nan")
    elif change == "max":
        result["max_z_mm"] = 81
    else:
        result["reference_ready"] = "yes"
    with pytest.raises(MachineError):
        service.focus_control("status")


@pytest.mark.parametrize("change", ["stop", "restart"])
def test_remote_never_publishes_response_after_stop_or_restart(remote_focus, change):
    service, pi, _ = remote_focus
    service._require_capabilities()
    def changed(*args):
        if change == "stop":
            with service._stop_epoch_lock:
                service._stop_epoch += 1
        else:
            pi.boot_id = str(uuid.uuid4())
    pi.before_request = changed
    with pytest.raises(MachineError):
        service.focus_control("status")


@pytest.fixture
def focus_server(server_harness, focus):  # noqa: F811
    harness = server_harness
    harness.machine._secondary_air_assist = focus.fan
    harness.machine._z_probe = focus.probe
    harness.machine._laser_focus = focus.state
    harness.service.connect()
    harness.service.prepare_photo_position()
    return harness, focus


def rpc(harness, action, **kwargs):
    values = dict(client_id=str(uuid.uuid4()), expected_boot_id=harness.service.boot_id,
                  expected_session_generation=harness.machine.status()["controller_session_generation"],
                  control=action, confirmed=action != "status", value=None, clearance_z_mm=30.,
                  gap_mm=7., measurement_id=None, preview_id=None)
    values.update(kwargs)
    return helpers._rpc(harness, ACTION_MACHINE_FOCUS, **values)


def test_authenticated_focus_reference_teach_and_preview(focus_server):
    harness, focus = focus_server
    assert rpc(harness, "reference")["ok"]
    result = rpc(harness, "measure")
    assert result["ok"]
    measurement_id = result["result"]["surface"]["id"]
    assert rpc(harness, "jog", value=-1, measurement_id=measurement_id)["ok"]
    assert rpc(harness, "teach", measurement_id=measurement_id)["ok"]
    preview = rpc(harness, "preview", measurement_id=measurement_id, gap_mm=3)
    assert preview["ok"]
    assert preview["result"]["preview"]["target_z_mm"] == 25
    result = rpc(harness, "move", preview_id=preview["result"]["preview"]["id"], gap_mm=3)
    assert result["ok"] and focus.serial.z == 25


@pytest.mark.parametrize("contact,accepted", [(-10, True), (-10.001, False)])
def test_authenticated_minus_ten_surface_contact_boundary(focus_server, contact, accepted):
    harness, focus = focus_server
    focus.serial.overrides["M115"] = [line.replace("MIN:-2 ", "MIN:-10 ") for line in focus_helpers.IDENTITY]
    focus.serial.contacts = [0, contact]
    assert rpc(harness, "reference")["ok"]
    focus.serial.writes.clear()
    response = rpc(harness, "measure")
    assert response["ok"] is accepted
    assert focus.serial.writes.count("G39 C30.000 H15.000") == 1
    if accepted:
        assert response["result"]["surface"]["contact_z_mm"] == -10
        assert response["result"]["contact_min_mm"] == -10
        assert response["result"]["current_readback"]["z_mm"] == 30
    else:
        assert focus.state.surface is None and focus.state.requires_clearance
        assert not any(line.startswith("G1 ") for line in focus.serial.writes)


def test_remote_client_preserves_new_firmware_minimum_and_negative_measurement(remote_focus):
    service, _, result = remote_focus
    result.update(action="measure", contact_min_mm=-10.,
                  firmware_geometry={"probe_z_mm": 0., "retract_mm": 5., "min_mm": -10.,
                                     "max_mm": 65., "ceiling_mm": 80.},
                  surface={"contact_z_mm": -10., "elevation_mm": -10.})
    response = service.focus_control("measure", confirmed=True)
    assert response["contact_min_mm"] == -10
    assert response["firmware_geometry"] == result["firmware_geometry"]
    assert response["surface"] == result["surface"]


def test_authenticated_focus_rejects_stale_session_before_serial(focus_server):
    harness, focus = focus_server
    before = list(focus.serial.writes)
    generation = harness.machine.status()["controller_session_generation"]
    result = rpc(harness, "reference", expected_session_generation=generation+1)
    assert not result["ok"] and focus.serial.writes == before


def test_authenticated_focus_rejects_unknown_field(focus_server):
    harness, focus = focus_server
    before = list(focus.serial.writes)
    result = rpc(harness, "reference", arbitrary=True)
    assert not result["ok"] and focus.serial.writes == before


def test_authenticated_xy_alignment_and_measured_return(focus_server):
    harness, _ = focus_server
    assert rpc(harness, "reference")["ok"]
    assert rpc(harness, "set_xy_offset", value=[3.302, 38.608])["ok"]
    assert rpc(harness, "align_probe")["ok"]
    measured = rpc(harness, "measure")
    assert measured["ok"]
    measurement_id = measured["result"]["surface"]["id"]
    aligned = rpc(harness, "align_laser", measurement_id=measurement_id)
    assert aligned["ok"]
    assert aligned["result"]["surface"]["id"] == measurement_id
    assert aligned["result"]["xy_sequence"]["phase"] == "laser"


@pytest.mark.parametrize("value", [[True, 0], [0], [1000, 0], "3,4"])
def test_authenticated_xy_offset_rejects_bad_values(focus_server, value):
    harness, focus = focus_server
    before = list(focus.serial.writes)
    result = rpc(harness, "set_xy_offset", value=value)
    assert not result["ok"] and focus.serial.writes == before


def test_authenticated_selected_point_moves_probe_then_returns_laser(focus_server):
    harness, focus = focus_server
    assert rpc(harness, "reference")["ok"]
    assert rpc(harness, "set_xy_offset", value=[3.302, 38.608])["ok"]
    focus.serial.writes.clear()
    positioned = rpc(harness, "position_probe", value=[75., 150.])
    assert positioned["ok"]
    assert positioned["result"]["current_carriage_xy_mm"] == [71.698, 111.392]
    assert not any(s.startswith(("G1", "G28", "G39")) for s in focus.serial.writes)
    measured = rpc(harness, "measure")
    measurement_id = measured["result"]["surface"]["id"]
    aligned = rpc(harness, "align_laser", measurement_id=measurement_id)
    assert aligned["ok"]
    assert aligned["result"]["current_carriage_xy_mm"] == [75., 150.]
    assert aligned["result"]["surface"]["id"] == measurement_id


@pytest.mark.parametrize("change", ["bad_vector", "stale_session", "confirmation"])
def test_authenticated_selected_point_rejects_before_serial(focus_server, change):
    harness, focus = focus_server
    before = list(focus.serial.writes)
    kwargs = dict(value=[75., 150.])
    if change == "bad_vector":
        kwargs["value"] = [True, 150.]
    elif change == "confirmation":
        kwargs["confirmed"] = False
    else:
        kwargs["expected_session_generation"] = harness.machine.status()["controller_session_generation"] + 1
    assert not rpc(harness, "position_probe", **kwargs)["ok"]
    assert focus.serial.writes == before


def unavailable_result(result, action="status"):
    result.update(action=action, available=False, reference_ready=False,
                  current_readback={"fresh": False, "z_known": False, "z_mm": None},
                  reference=None, surface=None, preview=None, xy_sequence=None,
                  max_z_mm=40., recovery_available=True,
                  probe_xy_offset_mm=[3.302, 38.608],
                  ender={"ready": False, "fault": "Ender did not answer M115",
                         "generation": 4, "recovery_required": True})


def test_remote_old_pi_rejects_recovery_before_request(remote_focus):
    service, pi, _ = remote_focus
    with pytest.raises(MachineError, match="Update"):
        service.focus_control("recover", confirmed=True)
    assert not any(r["action"] == ACTION_MACHINE_FOCUS for r in pi.requests)


def test_remote_recovery_without_motion_authority_is_explicit_and_bound(remote_focus):
    service, pi, result = remote_focus
    pi.capabilities.append(RECOVERY_CAPABILITY)
    service.settings.allow_motion = False
    result["action"] = "recover"
    with pytest.raises(SafetyError, match="Confirm"):
        service.focus_control("recover")
    response = service.focus_control("recover", confirmed=True)
    assert response["available"] is True
    assert pi.requests[-1]["expected_session_generation"] == pi.session_generation
    assert pi.requests[-1]["expected_boot_id"] == pi.boot_id
    assert pi.requests[-1]["confirmed"] is True


@pytest.mark.parametrize("action", ["status", "recover"])
def test_remote_unavailable_status_preserves_fault_without_position(remote_focus, action):
    service, pi, result = remote_focus
    pi.capabilities.append(RECOVERY_CAPABILITY)
    unavailable_result(result, action)
    response = service.focus_control(action, confirmed=action == "recover")
    assert response["ender"]["fault"] == "Ender did not answer M115"
    assert response["max_z_mm"] == 40
    assert response["current_readback"]["z_mm"] is None
    assert not response["reference_ready"]


@pytest.mark.parametrize("bad", ["fresh", "z", "reference", "preview", "fault", "ready", "generation", "max", "capability"])
def test_remote_unavailable_status_cannot_smuggle_motion_authority(remote_focus, bad):
    service, pi, result = remote_focus
    pi.capabilities.append(RECOVERY_CAPABILITY)
    unavailable_result(result)
    if bad == "fresh":
        result["current_readback"]["fresh"] = True
    elif bad == "z":
        result["current_readback"]["z_mm"] = 30.
    elif bad == "reference":
        result["reference_ready"] = True
    elif bad == "preview":
        result["preview"] = {"target_z_mm": 20}
    elif bad == "fault":
        result["ender"]["fault"] = ""
    elif bad == "ready":
        result["ender"]["ready"] = True
    elif bad == "generation":
        result["ender"]["generation"] = True
    elif bad == "max":
        result["max_z_mm"] = 90
    else:
        pi.capabilities.remove(RECOVERY_CAPABILITY)
    with pytest.raises(MachineError):
        service.focus_control("status")


def test_remote_unavailable_move_is_failure(remote_focus):
    service, pi, result = remote_focus
    pi.capabilities.append(RECOVERY_CAPABILITY)
    unavailable_result(result, "move")
    with pytest.raises(MachineError):
        service.focus_control("move", confirmed=True, preview_id=str(uuid.uuid4()))


@pytest.mark.parametrize("change", ["stop", "restart"])
def test_recovery_result_is_discarded_when_authority_changes(remote_focus, change):
    service, pi, result = remote_focus
    pi.capabilities.append(RECOVERY_CAPABILITY)
    unavailable_result(result, "recover")
    service._require_capabilities()
    def invalidate(*args):
        if change == "stop":
            with service._stop_epoch_lock:
                service._stop_epoch += 1
        else:
            pi.boot_id = str(uuid.uuid4())
    pi.before_request = invalidate
    with pytest.raises(MachineError):
        service.focus_control("recover", confirmed=True)


def test_authenticated_recovery_checks_confirmation_and_session(focus_server):
    harness, focus = focus_server
    before = list(focus.serial.writes)
    assert not rpc(harness, "recover", confirmed=False)["ok"]
    generation = harness.machine.status()["controller_session_generation"]
    assert not rpc(harness, "recover", expected_session_generation=generation + 1)["ok"]
    assert focus.serial.writes == before


def test_authenticated_recovery_reconnects_without_replaying_motion(focus_server):
    harness, focus = focus_server
    assert rpc(harness, "reference")["ok"]
    assert rpc(harness, "set_xy_offset", value=[3.302, 38.608])["ok"]
    before_generation = focus.owner.generation
    replacement = type(focus.serial)()
    replacement.overrides["M106 P1 S0"] = ["ok"]
    focus.owner._serial_factory = lambda _path, _baud: replacement
    focus.owner.close()
    harness.machine.settings.allow_motion = False
    response = rpc(harness, "recover")
    assert response["ok"], response
    result = response["result"]
    assert result["action"] == "recover" and result["available"], result["ender"]
    assert result["ender"]["ready"] and not result["ender"]["recovery_required"]
    assert result["ender"]["generation"] > before_generation
    assert result["current_readback"]["fresh"]
    assert not result["reference_ready"]
    assert result["surface"] is None and result["preview"] is None
    assert result["probe_xy_offset_mm"] == [3.302, 38.608]
    assert "M115" in replacement.writes and "M106 S0" in replacement.writes
    assert all(command in {"M115", "M106 S0", "M106 P1 S0", "M123", "M114"}
               for command in replacement.writes)


def xy_recovery_result(result):
    result.update(action="recover_xy", requires_clearance=True, reference_ready=False,
                  xy_recovery_pending_reference=True,
                  reference=None, surface=None, preview=None, xy_sequence=None,
                  xy_recovery_available=False,
                  ender={"ready": True, "generation": 5, "recovery_required": False})


def test_remote_xy_recovery_is_capability_gated_and_keeps_full_operation_timeout(remote_focus):
    service, pi, result = remote_focus
    pi.capabilities.extend([XY_CAPABILITY, RECOVERY_CAPABILITY])
    with pytest.raises(MachineError, match="Update"):
        service.focus_control("recover_xy", confirmed=True)
    assert not any(r["action"] == ACTION_MACHINE_FOCUS for r in pi.requests)
    # Invalidate the cached capabilities only to model a new companion connection.
    pi.capabilities.append(XY_RECOVERY_CAPABILITY)
    service._node_capabilities = tuple(pi.capabilities)
    xy_recovery_result(result)
    response = service.focus_control("recover_xy", confirmed=True)
    assert response["requires_clearance"] and not response["reference_ready"]
    assert pi.timeouts[-1] >= 360.0
    assert pi.requests[-1]["expected_session_generation"] == pi.session_generation
    assert pi.requests[-1]["expected_boot_id"] == pi.boot_id


@pytest.mark.parametrize("change", ["confirmation", "motion", "armed", "stop", "restart"])
def test_remote_xy_recovery_rejects_missing_or_changed_authority(remote_focus, change):
    service, pi, result = remote_focus
    pi.capabilities.append(XY_RECOVERY_CAPABILITY)
    xy_recovery_result(result)
    service._require_capabilities()
    if change == "motion":
        service.settings.allow_motion = False
    elif change == "armed":
        service._armed_program_digest = "armed-program"
        service._armed_until = 1e20
        service._armed_until_monotonic = 1e20
    elif change in {"stop", "restart"}:
        def invalidate(*args):
            if change == "stop":
                with service._stop_epoch_lock:
                    service._stop_epoch += 1
            else:
                pi.boot_id = str(uuid.uuid4())
        pi.before_request = invalidate
    with pytest.raises(MachineError):
        service.focus_control("recover_xy", confirmed=change != "confirmation")
    if change in {"confirmation", "motion", "armed"}:
        assert not any(r["action"] == ACTION_MACHINE_FOCUS for r in pi.requests)


@pytest.mark.parametrize("bad", ["clearance", "reference", "surface", "ender", "pending_reference"])
def test_remote_xy_recovery_rejects_result_granting_focus_authority(remote_focus, bad):
    service, pi, result = remote_focus
    pi.capabilities.append(XY_RECOVERY_CAPABILITY)
    xy_recovery_result(result)
    if bad == "clearance":
        result["requires_clearance"] = False
    elif bad == "reference":
        result["reference_ready"] = True
    elif bad == "surface":
        result["surface"] = {"id": "stale-measurement"}
    elif bad == "pending_reference":
        result["xy_recovery_pending_reference"] = False
    else:
        result["ender"]["ready"] = False
    with pytest.raises(MachineError, match="invalid XY recovery"):
        service.focus_control("recover_xy", confirmed=True)


def test_old_pi_status_cannot_offer_xy_recovery(remote_focus):
    service, _, result = remote_focus
    result["xy_recovery_available"] = True
    assert service.focus_control("status")["xy_recovery_available"] is False


@pytest.mark.parametrize("change", ["confirmation", "stale_session", "not_needed"])
def test_authenticated_xy_recovery_rejects_before_serial(focus_server, change):
    harness, focus = focus_server
    before = list(focus.serial.writes)
    kwargs = {}
    if change == "confirmation":
        kwargs["confirmed"] = False
    elif change == "stale_session":
        kwargs["expected_session_generation"] = harness.machine.status()["controller_session_generation"] + 1
    response = rpc(harness, "recover_xy", **kwargs)
    assert not response["ok"]
    assert focus.serial.writes == before


def test_authenticated_probe_failure_reconnect_xy_recovery_then_separate_reference(focus_server):
    harness, focus = focus_server
    assert rpc(harness, "reference")["ok"]
    focus.serial.overrides["G39 C30.000 H15.000"] = [
        "E3PD:1 STAGE:FAST REASON:NO_TRIGGER Z:-2.000 FAST:nan SLOW:nan CLEANUP_FAILED:0",
        "Error:E3MH:2 PROBE_FAILED",
    ]
    failed = rpc(harness, "measure")
    assert not failed["ok"] and "PROBE_FAILED" in str(failed)
    assert "STAGE:FAST REASON:NO_TRIGGER" in str(failed)
    assert "M112" in focus.serial.writes
    assert focus.state.requires_clearance

    replacement = type(focus.serial)()
    focus.owner._serial_factory = lambda _path, _baud: replacement
    harness.service.connect()
    assert rpc(harness, "recover")["ok"]
    with pytest.raises(MachineError, match="clearance"):
        harness.service.prepare_photo_position()
    assert not rpc(harness, "reference")["ok"]
    replacement.writes.clear()
    before_xy = len(harness.transport.commands)
    before_z = replacement.z
    recovered = rpc(harness, "recover_xy")
    assert recovered["ok"], recovered
    result = recovered["result"]
    assert result["requires_clearance"] and not result["reference_ready"]
    assert result["surface"] is None and result["preview"] is None
    assert replacement.z == before_z
    assert set(replacement.writes) <= {"M115", "M123", "M114", "M119", "M106 S0", "M106 P1 S0"}
    assert "$H" in harness.transport.commands[before_xy:]
    assert not rpc(harness, "reference", confirmed=False)["ok"]
    referenced = rpc(harness, "reference")
    assert referenced["ok"], referenced
    assert referenced["result"]["reference_ready"]
    assert not referenced["result"]["requires_clearance"]
    assert replacement.z == 30
