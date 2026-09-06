from __future__ import annotations

import json
from contextlib import contextmanager, nullcontext

import pytest

from laser_aligner.errors import SafetyError
from laser_aligner.machine import probe_pin
from laser_aligner.machine.probe_pin import ProbePinDiagnosticError, run_pin_diagnostic
from tests.test_secondary_controller import FakeSerial, _controller

_IDENTITY = "FIRMWARE_NAME:Marlin 2.0.8.26F4 MACHINE_TYPE:Ender-3 S1 Pro"
_POSITION = "X:150.00 Y:150.00 Z:20.00 E:0.00 Count X:0 Y:0 Z:8000"


class PinSerial(FakeSerial):
    def __init__(self):
        super().__init__()
        self.overrides = {}

    def write_line(self, line):
        super().write_line(line)
        if line in self.overrides:
            self.responses.extend(self.overrides[line])
            return
        replies = {
            "M106 S0": ["ok"],  # Fixture setup only, through the existing owner.
            "M115": [_IDENTITY, "ok"],
            "M119": ["Reporting endstop status", "z_min: open", "ok"],
            "M114": [_POSITION, "ok"],
            "M280 P0 S10": ["ok"],
            "M280 P0 S90": ["ok"],
        }
        if line not in replies:
            raise AssertionError(f"Pin diagnostic sent forbidden command {line}")
        self.responses.extend(replies[line])


def ready_pin():
    serial = PinSerial()
    owner, fan = _controller([serial])
    fan.initialize_off()
    serial.writes.clear()
    return serial, owner


@pytest.mark.parametrize(("action", "commands", "delays"), [
    ("inspect", ["M115", "M119", "M114"], []),
    ("deploy", ["M115", "M280 P0 S10", "M119", "M114"], [0.8]),
    ("stow", ["M115", "M280 P0 S90", "M119", "M114"], [0.8]),
])
def test_exact_pin_diagnostic_uses_one_owner_and_never_claims_pin_position(
    action, commands, delays, monkeypatch,
):
    serial, owner = ready_pin()
    waits = []
    failures = []
    exchanges = []
    original_exchange = owner._execute_acknowledged

    def recording_exchange(command, **kwargs):
        exchanges.append((command, kwargs))
        return original_exchange(command, **kwargs)

    monkeypatch.setattr(owner, "_execute_acknowledged", recording_exchange)
    result = run_pin_diagnostic(owner, action, nullcontext, lambda: failures.append(True), sleep=waits.append)

    assert serial.writes == commands
    assert serial.open_calls == 1
    assert serial.close_calls == 0
    assert owner.ready
    assert waits == delays
    assert failures == []
    for command, options in exchanges:
        assert command in commands
        assert options["allow_open"] is False
        assert options["interrupt_on_failure"] is False
        assert options["timeout"] == 3.0
        assert callable(options["write_guard"])
        assert callable(options["on_failure"])
    assert result["kind"] == "probe_pin_diagnostic"
    assert result["action"] == action
    assert result["completed"] is True
    assert result["physical_pin_state"] == "unknown"
    assert result["operator_observation_required"] is True
    assert result["controller_generation"] == owner.generation
    assert _IDENTITY in result["firmware"]
    assert [entry["command"] for entry in result["transcript"]] == commands
    assert json.loads(json.dumps(result, allow_nan=False)) == result


@pytest.mark.parametrize("action", [None, True, 1, "", "DEPLOY", " deploy", "G28", "M280 P0 S10", [], {}])
def test_invalid_action_is_rejected_before_owner_access(action):
    with pytest.raises(SafetyError, match="inspect, deploy, or stow"):
        run_pin_diagnostic(object(), action, nullcontext, lambda: None)


@pytest.mark.parametrize("identity", [
    "FIRMWARE_NAME:Other MACHINE_TYPE:Ender-3 S1 Pro",
    "FIRMWARE_NAME:Marlin MACHINE_TYPE:Different printer",
    "FIRMWARE_NAME:Marlin MACHINE_TYPE:Ender-3 S1 Professional",
    "FIRMWARE_NAME:NotMarlin MACHINE_TYPE:Ender-3 S1 Pro",
    "Marlin Ender-3 S1 Pro",
    "ok",
])
def test_wrong_or_missing_identity_blocks_pin_command(identity):
    serial, owner = ready_pin()
    responses = [identity] if identity == "ok" else [identity, "ok"]
    serial.overrides["M115"] = responses
    with pytest.raises(ProbePinDiagnosticError, match="Expected") as error:
        run_pin_diagnostic(owner, "deploy", nullcontext, lambda: None, sleep=lambda _delay: None)
    assert serial.writes == ["M115"]
    assert error.value.diagnostic["completed"] is False
    assert error.value.transcript[0]["responses"] == responses
    assert json.loads(json.dumps(error.value.diagnostic, allow_nan=False)) == error.value.diagnostic


@pytest.mark.parametrize("state", ["z_min: open", "z_min: TRIGGERED", "unknown input", "ok"])
def test_endstop_reply_is_an_observation_and_never_proof_of_pin_position(state):
    serial, owner = ready_pin()
    responses = [state] if state == "ok" else [state, "ok"]
    serial.overrides["M119"] = responses
    result = run_pin_diagnostic(owner, "deploy", nullcontext, lambda: None, sleep=lambda _delay: None)
    assert result["transcript"][2]["responses"] == responses
    assert result["physical_pin_state"] == "unknown"
    assert result["operator_observation_required"] is True
    assert serial.writes == ["M115", "M280 P0 S10", "M119", "M114"]


@pytest.mark.parametrize("command", ["M115", "M280 P0 S10", "M119", "M114"])
@pytest.mark.parametrize("response", [[], ["Error:servo error"], ["start"]])
def test_failed_exchange_has_context_and_never_retries_stows_or_emergency_resets(
    command, response, monkeypatch,
):
    serial, owner = ready_pin()
    serial.overrides[command] = response
    failures = []
    monkeypatch.setattr(probe_pin, "_EXCHANGE_TIMEOUT_SECONDS", 0.002)
    with pytest.raises(ProbePinDiagnosticError) as error:
        run_pin_diagnostic(
            owner, "deploy", nullcontext, lambda: failures.append(True), sleep=lambda _delay: None,
        )
    sequence = ["M115", "M280 P0 S10", "M119", "M114"]
    assert serial.writes == sequence[:sequence.index(command) + 1]
    assert serial.writes.count(command) == 1
    assert serial.close_calls == 1
    assert failures == [True]
    assert not owner.ready
    assert error.value.diagnostic["completed"] is False
    assert error.value.transcript[-1]["command"] == command
    assert command in error.value.transcript[-1]["error"]
    assert error.value.diagnostic["physical_pin_state"] == "unknown"


def test_initial_guard_rejects_without_secondary_write():
    serial, owner = ready_pin()

    @contextmanager
    def cancelled():
        raise SafetyError("Operation cancelled")
        yield

    with pytest.raises(ProbePinDiagnosticError, match="cancelled"):
        run_pin_diagnostic(owner, "deploy", cancelled, lambda: None)
    assert serial.writes == []
    assert owner.ready


def test_stop_during_servo_settling_prevents_observation_queries():
    serial, owner = ready_pin()
    cancelled = False

    @contextmanager
    def guard():
        if cancelled:
            raise SafetyError("Operation cancelled")
        yield

    def settling(_delay):
        nonlocal cancelled
        cancelled = True

    with pytest.raises(ProbePinDiagnosticError, match="cancelled"):
        run_pin_diagnostic(owner, "deploy", guard, lambda: None, sleep=settling)
    assert serial.writes == ["M115", "M280 P0 S10"]


def test_owner_generation_change_during_settling_prevents_queries_or_reopen():
    serial, owner = ready_pin()
    with pytest.raises(ProbePinDiagnosticError, match="session changed"):
        run_pin_diagnostic(owner, "deploy", nullcontext, lambda: None, sleep=lambda _delay: owner.close())
    assert serial.writes == ["M115", "M280 P0 S10"]
    assert serial.open_calls == 1


def test_uninitialized_owner_is_not_opened_by_diagnostic():
    serial = PinSerial()
    owner, _fan = _controller([serial])
    with pytest.raises(ProbePinDiagnosticError, match="initialized"):
        run_pin_diagnostic(owner, "inspect", nullcontext, lambda: None)
    assert serial.open_calls == 0
    assert serial.writes == []


def test_transcript_is_bounded_while_full_identity_is_validated():
    serial, owner = ready_pin()
    serial.overrides["M115"] = [_IDENTITY, *("x" * 600 for _ in range(40)), "ok"]
    result = run_pin_diagnostic(owner, "inspect", nullcontext, lambda: None)
    entry = result["transcript"][0]
    assert len(entry["responses"]) == 32
    assert max(map(len, entry["responses"])) <= 512
    assert entry["response_lines_count"] == 42
    assert entry["responses_truncated"] is True
    assert len(json.dumps(result, allow_nan=False)) < 20000
