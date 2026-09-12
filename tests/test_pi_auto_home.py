from __future__ import annotations

import socket
import threading
import uuid

import pytest

from laser_aligner.machine.pi_job_protocol import ACTION_JOB_START, ACTION_JOB_STOP, authenticate_client
from tests.test_pi_machine_server import (
    _GATED_COMMAND,
    _TOKEN,
    RecordingGatedTransport,
    _close_harness,
    _new_harness,
    _rpc,
    _start_fields,
    _upload,
    _wait_until,
)


@pytest.mark.parametrize("stop_during_home", [False, True])
def test_pi_owns_auto_home_after_start_connection_disappears(tmp_path, monkeypatch, stop_during_home):
    transport = RecordingGatedTransport()
    harness = _new_harness(tmp_path, monkeypatch, transport)
    entered = threading.Event()
    release = threading.Event()
    try:
        job_id, program, _ = _upload(harness, home=False)
        home = harness.machine.prepare_photo_position

        def gated_home():
            entered.set()
            assert release.wait(5)
            return home()

        monkeypatch.setattr(harness.machine, "prepare_photo_position", gated_home)
        with socket.create_connection(("127.0.0.1", harness.server.bound_port), timeout=3) as sock:
            channel = authenticate_client(sock, _TOKEN)
            channel.send_json({
                "action": ACTION_JOB_START,
                "request_id": str(uuid.uuid4()),
                "client_id": "00000000-0000-4000-8000-000000000001",
                "expected_boot_id": harness.service.boot_id,
                "expected_session_generation": harness.machine.status()["controller_session_generation"],
                **_start_fields(job_id, program),
            })
            assert entered.wait(3)
        assert harness.service.get(job_id)["state"] == "starting"
        if stop_during_home:
            assert _rpc(harness, ACTION_JOB_STOP, job_id=job_id)["ok"]
        release.set()
        if stop_during_home:
            _wait_until(lambda: harness.service.get(job_id)["state"] == "stopped")
            assert _GATED_COMMAND not in transport.commands
            assert "M4 S5" not in transport.commands
            assert not harness.machine.status()["armed"]
        else:
            assert transport.gated.wait(3)
            assert transport.commands.count("$H") == 1
            assert transport.commands.count(_GATED_COMMAND) == 1
            transport.release()
            _wait_until(lambda: harness.service.get(job_id)["state"] == "complete")
            assert harness.service.get(job_id)["program_digest"] == program.digest
        # The durable terminal record is published before the detached START
        # handler releases ownership. Finish this ownership/STOP scenario before
        # teardown introduces a separate concurrent service-shutdown scenario.
        _wait_until(lambda: harness.service._active_physical_operation is None
                    and harness.service._active_job_id is None)
    finally:
        release.set()
        _close_harness(harness)
