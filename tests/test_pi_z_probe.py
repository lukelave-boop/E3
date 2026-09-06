from __future__ import annotations

import socket
import threading
import uuid

import pytest

from laser_aligner.machine.pi_job_protocol import authenticate_client
from laser_aligner.machine.pi_machine_server import ACTION_MACHINE_PROBE_Z
from laser_aligner.machine.z_probe import CrealityZProbe
from tests import test_pi_machine_server as helpers
from tests.test_z_probe import ready_probe

server_harness = helpers.server_harness


@pytest.fixture
def probe_harness(server_harness):
    harness = server_harness
    serial, _, fan, _, _ = ready_probe()
    machine = harness.machine
    machine._secondary_air_assist = fan
    machine._z_probe = CrealityZProbe(fan.owner, lambda: machine.request_stop(_recover=False))
    harness.service.connect()
    harness.service.prepare_photo_position()
    return harness, serial


def fields(harness, **changes):
    return {
        "client_id": "00000000-0000-4000-8000-000000000001",
        "expected_boot_id": harness.service.boot_id,
        "expected_session_generation": harness.machine.status()["controller_session_generation"],
        "operation": "reference", "confirmed": True, "clearance_z_mm": 20,
        "support_height_mm": -1.5, **changes,
    }


def test_authenticated_reference_and_measure_and_replay_do_not_repeat_motion(probe_harness):
    harness, serial = probe_harness
    request = str(uuid.uuid4())
    response = helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, request_id=request, **fields(harness))
    assert response["ok"], response
    before = list(serial.writes)
    repeated = helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, request_id=request, **fields(harness))
    assert repeated == response
    assert serial.writes == before
    response = helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, **fields(harness, operation="measure"))
    assert response["ok"], response
    assert response["result"]["thickness_above_honeycomb_mm"] == pytest.approx(4.5)


@pytest.mark.parametrize("changes", [
    {"confirmed": 1}, {"operation": "G28"}, {"clearance_z_mm": -1},
    {"expected_session_generation": 9999}, {"unexpected": True},
])
def test_invalid_probe_rpc_does_not_write(probe_harness, changes):
    harness, serial = probe_harness
    before = list(serial.writes)
    response = helpers._rpc(harness, ACTION_MACHINE_PROBE_Z, **fields(harness, **changes))
    assert not response["ok"]
    assert serial.writes == before


def test_closed_rpc_connection_cancels_probe_and_blocks_competing_operation(probe_harness):
    harness, serial = probe_harness
    job_id, program, _ = helpers._upload(harness)
    serial.overrides["G28"] = []
    entered = threading.Event()
    serial.on_write = lambda command: entered.set() if command == "G28" else None
    with socket.create_connection(("127.0.0.1", harness.server.bound_port), timeout=3) as sock:
        channel = authenticate_client(sock, helpers._TOKEN)
        channel.send_json({"action": ACTION_MACHINE_PROBE_Z, "request_id": str(uuid.uuid4()),
                           **fields(harness)})
        assert entered.wait(3)
        blocked = helpers._rpc(harness, helpers.ACTION_MACHINE_JOG, dx_mm=1, dy_mm=0, feed_mm_min=300)
        assert not blocked["ok"]
        assert "active" in str(blocked)
        blocked_start = helpers._rpc(
            harness, helpers.ACTION_JOB_START, **helpers._start_fields(job_id, program)
        )
        assert not blocked_start["ok"]
    helpers._wait_until(lambda: not harness.machine._z_probe_active, timeout=3)
    assert "M112" in serial.writes
    assert harness.machine._z_probe.reference is None
    assert "G30 X110 Y110" not in serial.writes
