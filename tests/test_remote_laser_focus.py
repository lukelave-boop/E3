from __future__ import annotations

import copy
import uuid

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.laser_focus import CLICK_CAPABILITY, PI_CAPABILITY, RECOVERY_CAPABILITY, XY_CAPABILITY
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
        kwargs["value"] = 2
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
