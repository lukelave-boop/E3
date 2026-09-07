from __future__ import annotations

import uuid

import pytest

from tests import test_pi_machine_server as helpers
from tests.test_marlin_mainboard import mainboard_serial
from tests.test_z_probe import ready_probe

server_harness = helpers.server_harness


@pytest.fixture
def mainboard_harness(server_harness):
    harness = server_harness
    serial, _, fan, _, _ = ready_probe()
    mainboard_serial(serial)
    harness.machine._secondary_air_assist = fan
    harness.service.connect()
    return harness, serial


def fields(harness, **changes):
    return {"client_id": "00000000-0000-4000-8000-000000000001",
            "expected_boot_id": harness.service.boot_id,
            "expected_session_generation": harness.machine.status()["controller_session_generation"],
            "control": "fan1", "value": 50, "confirmed": True, **changes}


def test_authenticated_control_and_replay(mainboard_harness):
    harness, serial = mainboard_harness
    request_id = str(uuid.uuid4())
    response = helpers._rpc(harness, "machine.mainboard", request_id=request_id, **fields(harness))
    assert response["ok"], response
    assert response["result"]["fan1_pwm"] == 128
    before = list(serial.writes)
    assert helpers._rpc(harness, "machine.mainboard", request_id=request_id, **fields(harness)) == response
    assert serial.writes == before


@pytest.mark.parametrize("changes", [
    {"confirmed": 1}, {"value": True}, {"value": 101}, {"control": "M280"},
    {"unexpected": 1}, {"expected_session_generation": 999},
])
def test_invalid_or_stale_request_cannot_write(mainboard_harness, changes):
    harness, serial = mainboard_harness
    before = list(serial.writes)
    response = helpers._rpc(harness, "machine.mainboard", **fields(harness, **changes))
    assert not response["ok"]
    assert serial.writes == before
