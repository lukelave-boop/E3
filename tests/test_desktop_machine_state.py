from __future__ import annotations

import json

import pytest

from laser_aligner.desktop.machine_state import (
    CONTROLLER_STATES,
    controller_node_boot_id,
    format_running_controller_diagnostics,
    project_machine_state,
)

_CAPABILITIES = (
    "connect",
    "reconnect",
    "disconnect",
    "home",
    "jog",
    "arm",
    "start_job",
    "send_diagnostic",
    "motion_calibration",
    "recapture_without_homing",
    "stop",
)


def _enabled_capabilities(controller_state: str) -> set[str]:
    status = {
        "controller_state": controller_state,
        "controller_session_generation": 7,
        "controller_state_revision": 19,
        "backend": "serial",
        "protocol": "grbl",
        "allow_motion": True,
        "jog_ready": True,
        "armed": False,
        "job": {"running": controller_state == "JOB_RUNNING"},
    }
    projection = project_machine_state(status)
    return {
        capability
        for capability in _CAPABILITIES
        if getattr(projection, f"can_{capability}")
    }


@pytest.mark.parametrize(
    ("controller_state", "expected"),
    [
        ("DISCONNECTED", {"connect", "stop"}),
        ("OPENING", {"stop"}),
        ("SYNCHRONIZING", {"stop"}),
        (
            "READY_HOME_REQUIRED",
            {"disconnect", "home", "send_diagnostic", "motion_calibration", "stop"},
        ),
        (
            "READY_MOTION",
            {
                "disconnect",
                "home",
                "motion_calibration",
                "jog",
                "arm",
                "start_job",
                "send_diagnostic",
                "recapture_without_homing",
                "stop",
            },
        ),
        ("JOB_RUNNING", {"stop"}),
        ("STOPPING", {"stop"}),
        ("RECOVERING", {"stop"}),
        ("RECONNECT_REQUIRED", {"reconnect", "stop"}),
        ("FAULTED", {"connect", "stop"}),
        ("SHUTTING_DOWN", {"stop"}),
    ],
)
def test_all_controller_states_have_one_authoritative_action_matrix(
    controller_state: str,
    expected: set[str],
) -> None:
    assert controller_state in CONTROLLER_STATES
    assert _enabled_capabilities(controller_state) == expected


def test_home_is_allowed_from_both_idle_ready_states() -> None:
    for controller_state in CONTROLLER_STATES:
        projection = project_machine_state(
            {
                "controller_state": controller_state,
                "controller_session_generation": 2,
                "controller_state_revision": 3,
                "allow_motion": True,
                "job": {},
            }
        )
        assert projection.can_home is (
            controller_state in {"READY_HOME_REQUIRED", "READY_MOTION"}
        )


def test_reconnect_and_disconnected_states_cannot_claim_motion_ready() -> None:
    reconnect = project_machine_state(
        {
            "controller_state": "RECONNECT_REQUIRED",
            "connected": True,
            "allow_motion": True,
            "coordinate_reference_ready": True,
        }
    )
    disconnected = project_machine_state(
        {
            "controller_state": "DISCONNECTED",
            "connected": False,
            "allow_motion": True,
            "coordinate_reference_ready": True,
        }
    )

    assert "ONLINE" not in reconnect.compact_connection_text
    assert reconnect.compact_motion_text == "RECONNECT REQUIRED"
    assert disconnected.compact_connection_text == "OFFLINE"
    assert disconnected.compact_motion_text == "MOTION OFF"

    remote_reconnect = project_machine_state(
        {
            "controller_state": "RECONNECT_REQUIRED",
            "pi_owned_execution": True,
            "monitor_connected": True,
            "status_stale": False,
        }
    )
    assert "ONLINE" not in remote_reconnect.panel_summary("grbl")
    assert "PI REACHABLE" in remote_reconnect.panel_summary("grbl")


def test_remote_stale_or_disconnected_monitor_revokes_every_ordinary_action() -> None:
    projection = project_machine_state(
        {
            "controller_state": "READY_MOTION",
            "controller_session_generation": 8,
            "state_revision": 27,
            "pi_owned_execution": True,
            "monitor_connected": False,
            "status_stale": True,
            "allow_motion": True,
            "jog_ready": True,
            "job": {},
        }
    )

    assert not projection.status_trusted
    assert projection.compact_connection_text == "PI NOT RESPONDING"
    assert projection.compact_motion_text == "STATE UNKNOWN"
    assert "READY MOTION" not in projection.panel_summary("grbl")
    assert _enabled_capabilities("READY_MOTION") != {"stop"}
    assert {
        capability
        for capability in _CAPABILITIES
        if getattr(projection, f"can_{capability}")
    } == {"stop"}


def test_reachable_pi_with_stale_machine_status_is_not_presented_as_offline():
    status = {
        "controller_state": "READY_MOTION", "pi_owned_execution": True,
        "node_reachable": True, "monitor_connected": False, "status_stale": True,
        "allow_motion": True, "jog_ready": True, "job": {},
        "job_status_error": "job details timed out",
    }
    projection = project_machine_state(status)
    assert projection.compact_connection_text == "STATUS UNAVAILABLE"
    assert projection.compact_motion_text == "STATE UNKNOWN"
    assert "PI REACHABLE" in projection.panel_summary("grbl")
    assert not projection.can_home
    assert not projection.can_start_job
    assert not projection.can_jog
    assert projection.can_stop
    diagnostics = json.loads(format_running_controller_diagnostics(status))
    assert diagnostics["node_reachable"] is True
    assert "fresh Pi machine snapshot" in diagnostics["current_action_required"]
    assert diagnostics["job_status_error"] == "job details timed out"


@pytest.mark.parametrize("blocker", [{"armed": True}, {"job": {"running": True}}])
def test_repeat_home_rejects_armed_or_running_machine(blocker):
    projection = project_machine_state({
        "controller_state": "READY_MOTION", "allow_motion": True, **blocker,
    })
    assert not projection.can_home


def test_boot_id_fallback_accepts_authoritative_pi_name() -> None:
    assert controller_node_boot_id({"boot_id": "boot-123"}) == "boot-123"
    assert controller_node_boot_id({"node_boot_id": "node-456"}) == "node-456"


def test_running_controller_diagnostics_are_bounded_and_sanitized() -> None:
    status = {
        "controller_state": "READY_HOME_REQUIRED",
        "controller_session_generation": 4,
        "controller_state_revision": 11,
        "protocol": "grbl",
        "port": "e3bridge://pi.test:8765",
        "node_protocol": "E3MACHINE/2",
        "node_build": {"version": "1.2.3", "revision": "abc123"},
        "arm_phrase": "DO NOT COPY THIS",
        "log": ["complete G-code must not be copied"],
        "controller_session": {
            "generation": 4,
            "resolved_endpoint": "/dev/ttyACM0",
            "token": "session-secret",
        },
        "controller_diagnostics": {
            "firmware_identity": "Grbl 1.1h",
            "password": "password-secret",
            "gcode": "G1 X1" * 10_000,
            "transcript": ["ok" * 1_000 for _ in range(200)],
        },
    }

    text = format_running_controller_diagnostics(status)
    parsed = json.loads(text)

    assert len(text) <= 32_000
    assert parsed["controller_state"] == "READY_HOME_REQUIRED"
    assert parsed["current_action_required"] == "Home / park"
    assert parsed["node_protocol"] == "E3MACHINE/2"
    assert parsed["node_build"]["revision"] == "abc123"
    assert "DO NOT COPY THIS" not in text
    assert "complete G-code" not in text
    assert "session-secret" not in text
    assert "password-secret" not in text
    assert "G1 X1" not in text
    assert parsed["diagnostics_truncated"] is True


def test_legacy_status_fallback_is_fail_closed_for_motion() -> None:
    offline = project_machine_state(
        {"connected": False, "allow_motion": True, "backend": "serial"}
    )
    missing_reference = project_machine_state(
        {"connected": True, "allow_motion": True, "backend": "serial"}
    )
    untrusted = project_machine_state(
        {
            "connected": True,
            "controller_reconnect_required": True,
            "allow_motion": True,
        }
    )

    assert offline.controller_state == "DISCONNECTED"
    assert not offline.can_home
    assert not offline.can_start_job
    assert missing_reference.controller_state == "READY_HOME_REQUIRED"
    assert missing_reference.can_home
    assert not missing_reference.can_start_job
    assert untrusted.controller_state == "RECONNECT_REQUIRED"
    assert not untrusted.can_send_diagnostic
