from __future__ import annotations

import copy

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.pi_machine_server import ACTION_MACHINE_MAINBOARD, SERVER_CAPABILITIES
from laser_aligner.machine.z_limits import PI_Z_CAPABILITY, MainboardZLimits
from tests import test_pi_machine_server as server_helpers
from tests.test_marlin_mainboard import mainboard_serial
from tests.test_remote_machine_service import FakePi, _service
from tests.test_z_probe import ready_probe

server_harness = server_helpers.server_harness


@pytest.fixture
def remote_z(monkeypatch):
    monkeypatch.setenv("E3_BRIDGE_TOKEN", "remote-machine-test-token-value")
    pi = FakePi()
    pi.capabilities.append(PI_Z_CAPABILITY)
    service = _service()
    result = {
        "action": "status", "z_mm": 30.0, "z_known": True,
        "max_z_mm": 60.0, "min_z_mm": 20.0, "hard_max_z_mm": 80.0,
        "max_z_persistent": True, "firmware_z_limit_verified": False,
        "fresh": True, "available": True,
    }

    def exchange(host, port, token, request, **kwargs):
        if request["action"] != ACTION_MACHINE_MAINBOARD:
            return pi(host, port, token, request, **kwargs)
        pi.requests.append(copy.deepcopy(request))
        if pi.before_request is not None:
            pi.before_request(request["action"], request)
        return pi._response(request, result=copy.deepcopy(result))

    monkeypatch.setattr("laser_aligner.machine.remote_service.request_response", exchange)
    return service, pi, result


def test_node_advertises_z_protocol():
    assert PI_Z_CAPABILITY in SERVER_CAPABILITIES


@pytest.mark.parametrize("action,value,confirmed", [
    ("status", None, False), ("z_jog", -1, True), ("z_max", 60, True),
])
def test_remote_typed_action_preserves_node_limit_and_binds_session(remote_z, action, value, confirmed):
    service, pi, response = remote_z
    response["action"] = action
    result = service.mainboard_control(action, value, confirmed=confirmed)
    request = pi.requests[-1]
    assert request["control"] == action and request["value"] == value
    assert request["confirmed"] is confirmed
    assert request["expected_boot_id"] == pi.boot_id
    assert request["expected_session_generation"] == pi.session_generation
    assert result["max_z_mm"] == 60
    # Windows' default never overwrites authoritative Pi limits.
    assert service.settings.mainboard_max_z_mm == 80


def test_old_node_rejects_without_any_mainboard_request(remote_z):
    service, pi, _ = remote_z
    pi.capabilities.remove(PI_Z_CAPABILITY)
    with pytest.raises(MachineError, match="Update the E3 Pi service"):
        service.mainboard_control("z_jog", 1, confirmed=True)
    assert all(r["action"] != ACTION_MACHINE_MAINBOARD for r in pi.requests)


@pytest.mark.parametrize("field,value", [
    ("z_known", "true"), ("max_z_mm", 81), ("max_z_mm", True),
    ("max_z_mm", float("nan")), ("max_z_mm", None),
    ("hard_max_z_mm", 270), ("min_z_mm", 0), ("fresh", False),
    ("available", False), ("z_mm", "30"), ("z_mm", float("inf")),
    ("firmware_z_limit_verified", None), ("max_z_persistent", "true"),
    ("action", "z"),
])
def test_remote_rejects_incomplete_or_malformed_live_state(remote_z, field, value):
    service, _, result = remote_z
    result[field] = value
    with pytest.raises(MachineError):
        service.mainboard_control("status")


@pytest.mark.parametrize("value,persisted", [(59, True), (60, False)])
def test_remote_setting_requires_matching_persisted_acknowledgment(remote_z, value, persisted):
    service, _, result = remote_z
    result.update(action="z_max", max_z_mm=value, max_z_persistent=persisted)
    with pytest.raises(MachineError, match="confirm the saved"):
        service.mainboard_control("z_max", 60, confirmed=True)


def test_remote_stop_during_status_does_not_publish_stale_z(remote_z):
    service, pi, _ = remote_z

    def stop(action, request):
        with service._stop_epoch_lock:
            service._stop_epoch += 1

    pi.before_request = stop
    with pytest.raises(MachineError, match="STOP"):
        service.mainboard_control("status")


def test_remote_restart_during_status_is_rejected(remote_z):
    service, pi, _ = remote_z
    service._require_capabilities()

    def restart(action, request):
        import uuid
        pi.boot_id = str(uuid.uuid4())

    pi.before_request = restart
    with pytest.raises(MachineError, match="restarted"):
        service.mainboard_control("status")


def test_remote_motion_permission_rejects_before_mainboard_rpc(remote_z):
    service, pi, _ = remote_z
    service.settings.allow_motion = False
    with pytest.raises(SafetyError, match="allow_motion"):
        service.mainboard_control("z_jog", 1, confirmed=True)
    assert all(r["action"] != ACTION_MACHINE_MAINBOARD for r in pi.requests)


def test_remote_invalid_delta_never_opens_network(remote_z):
    service, pi, _ = remote_z
    with pytest.raises(SafetyError):
        service.mainboard_control("z_jog", 6, confirmed=True)
    assert pi.requests == []


@pytest.fixture
def z_server(server_harness, tmp_path):
    harness = server_harness
    serial, _, fan, probe, _ = ready_probe()
    mainboard_serial(serial)
    harness.machine._secondary_air_assist = fan
    harness.machine._z_probe = probe
    harness.machine._mainboard_z_limits = MainboardZLimits(tmp_path / "pi-z.json", "")
    harness.service.connect()
    return harness, serial


def z_rpc(harness, action, value=None, *, expected_generation=None):
    return server_helpers._rpc(harness, ACTION_MACHINE_MAINBOARD,
        client_id="00000000-0000-4000-8000-000000000001",
        expected_boot_id=harness.service.boot_id,
        expected_session_generation=(harness.machine.status()["controller_session_generation"]
                                     if expected_generation is None else expected_generation),
        control=action, value=value, confirmed=action != "status")


def test_authenticated_server_persists_ceiling_and_enforces_jog_at_boundary(z_server):
    harness, serial = z_server
    response = z_rpc(harness, "z_max", 60)
    assert response["ok"] and response["result"]["max_z_persistent"]
    assert harness.machine._mainboard_z_limits.load(80) == 60
    assert z_rpc(harness, "status")["result"]["max_z_mm"] == 60
    serial.z = 59
    response = z_rpc(harness, "z_jog", 1)
    assert response["ok"] and response["result"]["z_mm"] == 60
    response = z_rpc(harness, "z_jog", .1)
    assert response["ok"] is False and "configured maximum" in response["error"]
    assert "G1 Z60.100 F300" not in serial.writes


def test_authenticated_server_rejects_stale_session_before_serial_or_save(z_server):
    harness, serial = z_server
    generation = harness.machine.status()["controller_session_generation"]
    before = list(serial.writes)
    response = z_rpc(harness, "z_max", 60, expected_generation=generation + 1)
    assert response["ok"] is False
    assert serial.writes == before
    assert not harness.machine._mainboard_z_limits.path.exists()
