from __future__ import annotations

import threading
import time
from contextlib import nullcontext

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine import mainboard
from laser_aligner.machine.remote_service import _validated_ender_z_telemetry
from laser_aligner.machine.secondary_controller import LIVE_Z_CAPABILITY
from tests.test_laser_focus import IDENTITY
from tests.test_laser_focus import focus as focus
from tests.test_laser_focus import focus_machine as focus_machine
from tests.test_laser_focus import machine_probe as machine_probe
from tests.test_pi_machine_server import _rpc
from tests.test_remote_laser_focus import focus_server as focus_server
from tests.test_remote_laser_focus import remote_focus as remote_focus
from tests.test_remote_laser_focus import rpc as focus_rpc
from tests.test_remote_laser_focus import server_harness as server_harness
from tests.test_remote_machine_service import _service
from tests.test_secondary_controller import FakeSerial, _controller


def report(sequence=1, z=12.345, *, known=1, homing=0, moving=1):
    return f"E3Z:1 N:{sequence} Z:{z:.3f} K:{known} H:{homing} M:{moving}"


def telemetry_owner(lines):
    serial = FakeSerial(["ok"])
    owner, fan = _controller([serial])
    fan.initialize_off()
    owner.set_live_z_support([LIVE_Z_CAPABILITY])

    def reply(command):
        serial.responses.extend(lines if command == "M400" else ["ok"])

    serial.on_write = reply
    def execute(command):
        return owner._execute_acknowledged(command, allow_open=False, timeout=.02)
    return owner, serial, execute


def test_live_step_samples_are_demultiplexed_and_never_acknowledge_a_command():
    owner, serial, execute = telemetry_owner([report(), "ok"])
    with owner.live_z_stream(execute):
        assert execute("M400") == ("ok",)
        sample = owner.live_z_telemetry()
        assert sample["valid"] and sample["z_mm"] == 12.345 and sample["moving"]
    assert serial.writes[-3:] == ["M154 S1", "M400", "M154 S0"]
    owner, serial, execute = telemetry_owner([report()])
    with pytest.raises(MachineError, match="timed out"):
        with owner.live_z_stream(execute):
            execute("M400")
    assert owner.live_z_telemetry()["valid"] is False
    assert serial.close_calls == 1


@pytest.mark.parametrize("bad", [
    "E3Z:2 N:1 Z:2.000 K:1 H:0 M:1", "E3Z:1 N:1 Z:nan K:1 H:0 M:1",
    report(0x100000000), report(z=1000.001), report(known=1, homing=1), report(moving=2),
    report() + " ok", "E3Z:1 N:1 Z:2.000 K:1 H:0", "E3Z:1 N:-1 Z:2.000 K:1 H:0 M:1",
])
def test_malformed_telemetry_closes_the_uncertain_session(bad):
    owner, _, execute = telemetry_owner([bad, "ok"])
    with pytest.raises(MachineError):
        with owner.live_z_stream(execute):
            execute("M400")
    assert not owner.live_z_telemetry()["valid"]
    assert not owner.ready


def test_homing_samples_remain_provisional_and_duplicate_reports_do_not_refresh(monkeypatch):
    owner, _, execute = telemetry_owner([report(9, known=0, homing=1), "ok"])
    with owner.live_z_stream(execute):
        execute("M400")
        sample = owner._live_z_sample
        monkeypatch.setattr("laser_aligner.machine.secondary_controller.time.monotonic", lambda: sample.received_at + 2)
        execute("M400")
        result = owner.live_z_telemetry()
        assert result["homing"] and not result["known"]
        assert result["age_seconds"] == 2 and result["valid"] is False


def test_sequence_wrap_and_session_invalidation():
    owner, _, execute = telemetry_owner([report(0xFFFFFFFF), report(0, z=13), "ok"])
    with owner.live_z_stream(execute):
        execute("M400")
        assert owner.live_z_telemetry()["sequence"] == 0
        assert owner.live_z_telemetry()["z_mm"] == 13
    previous_generation = owner.generation
    owner.close()
    result = owner.live_z_telemetry()
    assert result["controller_generation"] > previous_generation
    assert not result["valid"] and result["z_mm"] is None and not result["supported"]


def test_position_stream_does_not_exhaust_normal_ack_transcript_budget():
    owner, _, _ = telemetry_owner([report(n) for n in range(600)] + ["ok"])
    def execute(command):
        return owner._execute_acknowledged(command, allow_open=False, timeout=35)
    with owner.live_z_stream(execute):
        assert execute("M400") == ("ok",)
        assert owner.live_z_telemetry()["sequence"] == 599


def test_position_stream_has_an_independent_bounded_traffic_budget():
    owner, _, execute = telemetry_owner([report(n) for n in range(33)] + ["ok"])
    with pytest.raises(MachineError, match="bounded rate"):
        with owner.live_z_stream(execute):
            execute("M400")


def test_telemetry_outside_owned_scope_is_rejected():
    owner, _, execute = telemetry_owner([report(), "ok"])
    with pytest.raises(MachineError, match="outside an enabled operation"):
        execute("M400")


def enable_focus_telemetry(focus):
    focus.serial.overrides["M115"] = IDENTITY[:-1] + [LIVE_Z_CAPABILITY, "ok"]
    focus.serial.overrides["M154 S1"] = ["ok"]
    focus.serial.overrides["M154 S0"] = ["ok"]
    focus.serial.overrides["M400"] = [report(), "ok"]


def test_focus_reference_stream_is_scoped_and_does_not_replace_final_readback(focus):
    enable_focus_telemetry(focus)
    result = focus.run("reference")
    assert result["current_readback"]["z_mm"] == 30  # M114 authority, never telemetry 12.345.
    assert focus.serial.writes.count("M154 S1") == focus.serial.writes.count("M154 S0") == 1
    assert focus.serial.writes[-1] == "M154 S0"
    assert all(not any(s.startswith("E3Z") for s in entry["responses"]) for entry in result["transcript"])
    assert focus.owner.live_z_telemetry()["z_mm"] == 12.345


def test_old_firmware_sends_no_telemetry_configuration(focus):
    focus.run("reference")
    assert not any(command.startswith("M154") for command in focus.serial.writes)
    assert not focus.owner.live_z_telemetry()["supported"]


def test_capability_loss_discards_previous_sample(focus):
    enable_focus_telemetry(focus)
    focus.run("reference")
    focus.serial.overrides["M115"] = IDENTITY
    focus.run("status")
    assert not focus.owner.live_z_telemetry()["valid"]
    assert focus.owner.live_z_telemetry()["z_mm"] is None


def test_preflight_rejection_stops_stream_without_bypassing_the_guard(focus):
    enable_focus_telemetry(focus)
    with pytest.raises(SafetyError):
        focus.run("jog", value=-1, measurement_id="00000000-0000-0000-0000-000000000000")
    assert focus.serial.writes[-1] == "M154 S0"
    assert not any(command.startswith("G1 ") for command in focus.serial.writes)


def test_cancelled_disable_never_writes_through_failed_guard():
    owner, serial, execute = telemetry_owner([report(), "ok"])

    def guarded(command):
        if command == "M154 S0":
            raise MachineError("cancelled by STOP")
        return execute(command)

    with pytest.raises(MachineError, match="cancelled"):
        with owner.live_z_stream(guarded):
            execute("M400")
    assert "M154 S0" not in serial.writes
    assert not owner.live_z_telemetry()["valid"]
    assert not owner.ready


def test_mainboard_z_jog_uses_same_scoped_stream(focus):
    enable_focus_telemetry(focus)
    focus.serial.homed = True
    focus.serial.z = 20
    result = mainboard.control(focus.owner, "z_jog", 1, confirmed=True,
                               guard=nullcontext, on_failure=lambda: None, max_z_mm=40)
    assert result["z_mm"] == 21
    assert focus.serial.writes[-1] == "M154 S0"
    assert focus.serial.writes.count("M154 S1") == 1


def test_machine_status_reads_telemetry_while_focus_holds_serial_owner(focus_machine):
    machine, focus, _ = focus_machine
    enable_focus_telemetry(focus)
    focus.run("reference")
    entered, release = threading.Event(), threading.Event()

    def moving():
        with focus.owner._lock:
            machine._focus_operation_active = True
            entered.set()
            release.wait(3)
            machine._focus_operation_active = False

    thread = threading.Thread(target=moving)
    thread.start()
    try:
        assert entered.wait(1)
        before = list(focus.serial.writes)
        started = time.monotonic()
        result = machine.status()
        assert time.monotonic() - started < .25
        assert result["ender_z_telemetry"]["valid"]
        assert result["z_probe"]["active"]
        assert focus.serial.writes == before
    finally:
        release.set()
        thread.join(2)


def test_remote_cached_sample_expires_without_new_rpc(monkeypatch):
    service = _service()
    sample = {"supported": True, "valid": True, "z_mm": 5., "known": True,
              "homing": False, "moving": True, "sequence": 1,
              "controller_generation": 2, "age_seconds": .1}
    service._cache_remote_status({"ender_z_telemetry": sample}, job_record=None)
    received_at = service._last_machine_status_monotonic
    monkeypatch.setattr("laser_aligner.machine.remote_service.time.monotonic", lambda: received_at + .5)
    assert service.status()["ender_z_telemetry"]["age_seconds"] == pytest.approx(.6)
    monkeypatch.setattr("laser_aligner.machine.remote_service.time.monotonic", lambda: received_at + 2)
    assert not service.status()["ender_z_telemetry"]["valid"]


@pytest.mark.parametrize("change", [{"z_mm": float("nan")}, {"sequence": True}, {"age_seconds": -1},
                                   {"known": True, "homing": True}, {"controller_generation": -1},
                                   {"moving": "yes"}])
def test_remote_rejects_malformed_observations_without_touching_authority(change):
    sample = {"supported": True, "valid": True, "z_mm": 5., "known": True,
              "homing": False, "moving": True, "sequence": 1,
              "controller_generation": 2, "age_seconds": .1}
    assert _validated_ender_z_telemetry(dict(sample, **change)) is None


def test_live_monitor_scope_restores_ordinary_cadence_after_failure():
    service = _service()
    with pytest.raises(RuntimeError):
        with service._live_z_monitor_scope(True):
            assert service._live_z_poll_count == 1
            raise RuntimeError("cancelled")
    assert service._live_z_poll_count == 0


def test_authenticated_status_observes_z_before_reference_rpc_completes(focus_server):
    harness, focus = focus_server
    enable_focus_telemetry(focus)
    entered, release = threading.Event(), threading.Event()
    result = []

    def read():
        if (focus.serial.responses and focus.serial.responses[0] == "ok"
                and focus.serial.writes[-1] == "M400" and not release.is_set()):
            entered.set()
            release.wait(.02)
            return None
        return focus.serial.responses.pop(0) if focus.serial.responses else None

    focus.serial.on_read = read
    worker = threading.Thread(target=lambda: result.append(focus_rpc(harness, "reference")))
    worker.start()
    try:
        assert entered.wait(3)
        before = list(focus.serial.writes)
        response = _rpc(harness, "machine.status")
        assert response["ok"] and response["status"]["ender_z_telemetry"]["valid"]
        assert response["status"]["ender_z_telemetry"]["z_mm"] == 12.345
        assert not result and focus.serial.writes == before
    finally:
        release.set()
        worker.join(3)
    assert result and result[0]["ok"], result


def test_remote_focus_rpc_leaves_machine_status_monitor_unblocked(remote_focus):
    service, pi, result = remote_focus
    result["action"] = "reference"
    entered, release = threading.Event(), threading.Event()
    results = []

    def before_request(action, request):
        if action == "machine.focus_control":
            entered.set()
            release.wait(3)

    pi.before_request = before_request
    worker = threading.Thread(target=lambda: results.append(service.focus_control("reference", confirmed=True)))
    worker.start()
    try:
        assert entered.wait(2)
        assert service._live_z_poll_count == 1
        service._refresh_once()
        assert not results
        assert pi.requests[-1]["action"] == "machine.status"
        assert service.status()["node_reachable"]
    finally:
        release.set()
        worker.join(3)
    assert results and service._live_z_poll_count == 0
