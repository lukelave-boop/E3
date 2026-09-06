from __future__ import annotations

import json
import uuid
from copy import deepcopy

import pytest

from laser_aligner import probe_diagnostic as cli
from laser_aligner.errors import MachineError

_TOKEN = "a-test-token-never-shown-in-cli-output"
_BOOT_ID = "00000000-0000-4000-8000-000000000012"


def test_native_motion_requires_its_own_confirmation_before_credential_access(monkeypatch, capsys):
    monkeypatch.setattr(cli, "read_bridge_token", lambda: pytest.fail("Credential read before confirmation"))
    assert cli.main(["native-cycle", "--confirm-pin-clearance"]) == 1
    assert "--confirm-native-cycle" in capsys.readouterr().out


def test_native_cycle_sends_one_typed_request_without_automatic_home_or_connect(rpc, capsys):
    calls, responses = rpc
    responses["service.capabilities"]["actions"]["machine.probe_z"] = {}
    responses["machine.probe_z"] = {"ok": True, "result": {"kind": "native_cycle_test"}}
    assert cli.main(["native-cycle", "--confirm-native-cycle"]) == 0
    assert [call[2]["action"] for call in calls] == ["service.capabilities", "machine.status", "machine.probe_z"]
    request = calls[-1][2]
    assert request["operation"] == "native_test"
    assert request["confirmed"] is True
    assert request["clearance_z_mm"] == 20
    assert calls[-1][3]["timeout"] == 120


@pytest.fixture
def rpc(monkeypatch):
    calls = []
    responses = {
        "service.capabilities": {"ok": True, "actions": {"machine.probe_pin": {}}},
        "machine.status": {"ok": True, "status": {
            "connected": True, "boot_id": _BOOT_ID, "controller_session_generation": 7,
        }},
        "machine.probe_pin": {"ok": True, "result": {"transcript": ["example controller response"]}},
    }

    def exchange(host, port, token, request, **kwargs):
        assert token == _TOKEN
        calls.append((host, port, dict(request), kwargs))
        response = responses[request["action"]]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)

    monkeypatch.setattr(cli, "read_bridge_token", lambda: _TOKEN)
    monkeypatch.setattr(cli, "request_response", exchange)
    return calls, responses


@pytest.mark.parametrize("action", ["deploy", "stow"])
def test_missing_confirmation_never_reads_token_or_calls_rpc(rpc, monkeypatch, capsys, action):
    monkeypatch.setattr(cli, "read_bridge_token", lambda: pytest.fail("Credential read without confirmation"))
    assert cli.main([action]) == 1
    assert rpc[0] == []
    assert "--confirm-pin-clearance" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("action", ["inspect", "deploy", "stow"])
def test_one_operator_action_uses_current_session_and_never_connects_or_homes(rpc, capsys, action):
    args = [action] + ([] if action == "inspect" else ["--confirm-pin-clearance"])
    assert cli.main(args) == 0
    calls, _ = rpc
    assert [call[2]["action"] for call in calls] == [
        "service.capabilities", "machine.status", "machine.probe_pin",
    ]
    assert all(call[:2] == ("127.0.0.1", 8765) for call in calls)
    assert len({call[2]["request_id"] for call in calls}) == 3
    for call in calls:
        assert str(uuid.UUID(call[2]["request_id"])) == call[2]["request_id"]
    request = calls[-1][2]
    assert str(uuid.UUID(request["client_id"])) == request["client_id"]
    assert request == {
        "action": "machine.probe_pin", "request_id": request["request_id"],
        "pin_action": action, "confirmed": action != "inspect", "client_id": request["client_id"],
        "expected_boot_id": _BOOT_ID, "expected_session_generation": 7,
    }
    assert json.loads(capsys.readouterr().out) == rpc[1]["machine.probe_pin"]


def test_explicit_token_file_and_endpoint(rpc, monkeypatch, tmp_path, capsys):
    token_file = tmp_path / "token.txt"
    token_file.write_text(_TOKEN + "\n", encoding="utf-8")
    monkeypatch.setattr(cli, "read_bridge_token", lambda: pytest.fail("Unexpected token fallback"))
    assert cli.main(["inspect", "--token-file", str(token_file), "--host", "pi.test", "--port", "9876"]) == 0
    assert all(call[:2] == ("pi.test", 9876) for call in rpc[0])
    assert _TOKEN not in capsys.readouterr().out


@pytest.mark.parametrize("change", [{"connected": False}, {"connected": 1}, {}])
def test_disconnected_or_missing_status_does_not_connect_or_actuate(rpc, capsys, change):
    rpc[1]["machine.status"]["status"] = change
    assert cli.main(["deploy", "--confirm-pin-clearance"]) == 1
    assert len(rpc[0]) == 2
    assert "Connect machine yourself" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("change", [
    {"boot_id": None}, {"boot_id": "invalid"},
    {"controller_session_generation": None}, {"controller_session_generation": True},
    {"controller_session_generation": -1},
])
def test_missing_or_invalid_session_fields_block_actuation(rpc, capsys, change):
    rpc[1]["machine.status"]["status"].update(change)
    assert cli.main(["stow", "--confirm-pin-clearance"]) == 1
    assert len(rpc[0]) == 2
    assert json.loads(capsys.readouterr().out)["ok"] is False


@pytest.mark.parametrize("actions", [{}, [], None])
def test_unsupported_service_stops_before_status_or_actuation(rpc, capsys, actions):
    rpc[1]["service.capabilities"]["actions"] = actions
    assert cli.main(["inspect"]) == 1
    assert len(rpc[0]) == 1
    assert "update the Pi service" in json.loads(capsys.readouterr().out)["error"]


@pytest.mark.parametrize("action", ["service.capabilities", "machine.status", "machine.probe_pin"])
def test_rejection_is_reported_without_retry(rpc, capsys, action):
    response = {"ok": False, "error": "Controller busy", "retryable": True}
    rpc[1][action] = response
    assert cli.main(["deploy", "--confirm-pin-clearance"]) == 1
    assert rpc[0][-1][2]["action"] == action
    assert sum(call[2]["action"] == action for call in rpc[0]) == 1
    assert json.loads(capsys.readouterr().out) == response


def test_unknown_pin_outcome_is_not_retried_or_automatically_stowed(rpc, capsys):
    rpc[1]["machine.probe_pin"] = MachineError("Connection closed after sending request")
    assert cli.main(["deploy", "--confirm-pin-clearance"]) == 1
    assert len(rpc[0]) == 3
    assert rpc[0][-1][2]["pin_action"] == "deploy"
    assert "Connection closed" in json.loads(capsys.readouterr().out)["error"]


def test_error_cannot_echo_saved_token(rpc, capsys):
    rpc[1]["machine.probe_pin"] = {"ok": False, "error": f"Unexpected token {_TOKEN}"}
    assert cli.main(["inspect"]) == 1
    output = capsys.readouterr().out
    assert _TOKEN not in output
    assert "[REDACTED]" in json.loads(output)["error"]


@pytest.mark.parametrize("token", [None, "", "short"])
def test_missing_credentials_make_no_requests(rpc, monkeypatch, capsys, token):
    monkeypatch.setattr(cli, "read_bridge_token", lambda: token)
    assert cli.main(["inspect"]) == 1
    assert rpc[0] == []
    assert json.loads(capsys.readouterr().out)["ok"] is False


def test_help_is_explicit_about_pin_actuation(rpc, capsys):
    with pytest.raises(SystemExit) as stopped:
        cli.main(["--help"])
    assert stopped.value.code == 0
    output = capsys.readouterr().out
    assert "NO AXIS MOTION" in output
    assert "move the PIN" in output
    assert "--confirm-pin-clearance" in output
    assert rpc[0] == []
