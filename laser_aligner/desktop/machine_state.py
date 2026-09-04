from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from ..deployment import load_build_info

CONTROLLER_STATES = frozenset(
    {
        "DISCONNECTED",
        "OPENING",
        "SYNCHRONIZING",
        "READY_HOME_REQUIRED",
        "READY_MOTION",
        "JOB_RUNNING",
        "STOPPING",
        "RECOVERING",
        "RECONNECT_REQUIRED",
        "FAULTED",
        "SHUTTING_DOWN",
    }
)

_STABLE_SESSION_STATES = frozenset(
    {"READY_HOME_REQUIRED", "READY_MOTION", "JOB_RUNNING"}
)
_DIAGNOSTIC_STATES = frozenset({"READY_HOME_REQUIRED", "READY_MOTION"})
_SENSITIVE_KEY_PARTS = (
    "authorization_phrase",
    "arm_phrase",
    "gcode",
    "password",
    "program_text",
    "secret",
    "token",
)


def machine_payload(status: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return the machine portion of either a runtime or machine status."""

    if status is None:
        return {}
    nested = status.get("machine")
    if isinstance(nested, Mapping):
        return nested
    return status


def _nonnegative_int(value: object) -> int | None:
    if type(value) is not int or int(value) < 0:
        return None
    return int(value)


def controller_session_generation(status: Mapping[str, Any] | None) -> int | None:
    machine = machine_payload(status)
    return _nonnegative_int(
        machine.get(
            "controller_session_generation",
            machine.get("session_generation"),
        )
    )


def controller_state_revision(status: Mapping[str, Any] | None) -> int | None:
    machine = machine_payload(status)
    return _nonnegative_int(
        machine.get("controller_state_revision", machine.get("state_revision"))
    )


def controller_node_boot_id(status: Mapping[str, Any] | None) -> str | None:
    machine = machine_payload(status)
    value = machine.get("node_boot_id", machine.get("boot_id"))
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()


def _legacy_controller_state(machine: Mapping[str, Any]) -> str:
    job = machine.get("job")
    job_running = isinstance(job, Mapping) and job.get("running") is True
    if machine.get("controller_reconnect_required") is True:
        return "RECONNECT_REQUIRED"
    if machine.get("connecting") is True:
        return "OPENING"
    if machine.get("connected") is not True:
        return "DISCONNECTED"
    if job_running:
        return "JOB_RUNNING"
    if (
        str(machine.get("backend") or "").strip().lower() == "serial"
        and machine.get("allow_motion") is True
        and machine.get("coordinate_reference_ready") is not True
    ):
        return "READY_HOME_REQUIRED"
    return "READY_MOTION"


@dataclass(frozen=True, slots=True)
class ControllerUiState:
    """Fail-closed desktop projection of one authoritative controller snapshot."""

    controller_state: str
    explicit_state: bool
    state_valid: bool
    session_generation: int | None
    state_revision: int | None
    node_boot_id: str | None
    remote: bool
    node_reachable: bool
    status_stale: bool
    allow_motion: bool
    armed: bool
    job_running: bool
    jog_ready: bool
    operation_busy: bool = False

    @property
    def status_trusted(self) -> bool:
        return self.state_valid and self.node_reachable and not self.status_stale

    @property
    def session_synchronized(self) -> bool:
        return self.status_trusted and self.controller_state in _STABLE_SESSION_STATES

    @property
    def can_stop(self) -> bool:
        # STOP is deliberately outside ordinary operation/busy gating.
        return True

    @property
    def can_connect(self) -> bool:
        return (
            self.status_trusted
            and not self.operation_busy
            and self.controller_state in {"DISCONNECTED", "FAULTED"}
        )

    @property
    def can_reconnect(self) -> bool:
        return (
            self.status_trusted
            and not self.operation_busy
            and self.controller_state == "RECONNECT_REQUIRED"
        )

    @property
    def can_disconnect(self) -> bool:
        return (
            self.status_trusted
            and not self.operation_busy
            and not self.job_running
            and self.controller_state in {"READY_HOME_REQUIRED", "READY_MOTION"}
        )

    @property
    def can_home(self) -> bool:
        return (
            self.status_trusted
            and not self.operation_busy
            and self.allow_motion
            and not self.armed
            and not self.job_running
            and self.controller_state == "READY_HOME_REQUIRED"
        )

    @property
    def can_jog(self) -> bool:
        return (
            self.status_trusted
            and not self.operation_busy
            and self.allow_motion
            and not self.armed
            and not self.job_running
            and self.jog_ready
            and self.controller_state == "READY_MOTION"
        )

    @property
    def can_arm(self) -> bool:
        return (
            self.status_trusted
            and not self.operation_busy
            and self.allow_motion
            and not self.armed
            and not self.job_running
            and self.controller_state == "READY_MOTION"
        )

    @property
    def can_start_job(self) -> bool:
        return self.can_arm

    @property
    def can_send_diagnostic(self) -> bool:
        return (
            self.status_trusted
            and not self.operation_busy
            and not self.armed
            and not self.job_running
            and self.controller_state in _DIAGNOSTIC_STATES
        )

    @property
    def can_motion_calibration(self) -> bool:
        return self.can_home

    @property
    def can_recapture_without_homing(self) -> bool:
        return (
            self.status_trusted
            and not self.operation_busy
            and self.allow_motion
            and not self.armed
            and not self.job_running
            and self.controller_state == "READY_MOTION"
            and (self.session_generation is not None or not self.explicit_state)
        )

    def with_busy(self, busy: bool) -> ControllerUiState:
        return replace(self, operation_busy=bool(busy))

    @property
    def compact_connection_text(self) -> str:
        if self.remote and not self.node_reachable:
            return "PI OFFLINE"
        return {
            "DISCONNECTED": "OFFLINE",
            "OPENING": "OPENING",
            "SYNCHRONIZING": "SYNCHRONIZING",
            "READY_HOME_REQUIRED": "SESSION READY",
            "READY_MOTION": "SESSION READY",
            "JOB_RUNNING": "JOB RUNNING",
            "STOPPING": "STOPPING",
            "RECOVERING": "RECOVERING",
            "RECONNECT_REQUIRED": "SESSION UNTRUSTED",
            "FAULTED": "FAULTED",
            "SHUTTING_DOWN": "SHUTTING DOWN",
        }.get(self.controller_state, "STATE INVALID")

    @property
    def connection_text(self) -> str:
        if self.remote and not self.node_reachable:
            return "Pi offline"
        return {
            "DISCONNECTED": "Disconnected",
            "OPENING": "Opening controller",
            "SYNCHRONIZING": "Synchronizing controller",
            "READY_HOME_REQUIRED": "Session synchronized",
            "READY_MOTION": "Session synchronized",
            "JOB_RUNNING": "Job running",
            "STOPPING": "Stopping",
            "RECOVERING": "Recovering communication",
            "RECONNECT_REQUIRED": "Session untrusted",
            "FAULTED": "Controller faulted",
            "SHUTTING_DOWN": "Shutting down",
        }.get(self.controller_state, "Controller state invalid")

    @property
    def compact_motion_text(self) -> str:
        if not self.status_trusted:
            return "STATE UNKNOWN"
        if self.controller_state == "READY_HOME_REQUIRED":
            return "HOME REQUIRED"
        if self.controller_state == "READY_MOTION":
            return "MOTION READY" if self.allow_motion else "MOTION OFF"
        if self.controller_state == "JOB_RUNNING":
            return "JOB ACTIVE"
        if self.controller_state == "RECONNECT_REQUIRED":
            return "RECONNECT REQUIRED"
        if self.controller_state in {"OPENING", "SYNCHRONIZING", "STOPPING", "RECOVERING"}:
            return "MOTION BLOCKED"
        return "MOTION OFF"

    @property
    def motion_text(self) -> str:
        return {
            "STATE UNKNOWN": "State unknown",
            "HOME REQUIRED": "Home required",
            "MOTION READY": "Motion ready",
            "MOTION OFF": "Motion off",
            "JOB ACTIVE": "Job active",
            "RECONNECT REQUIRED": "Reconnect required",
            "MOTION BLOCKED": "Motion blocked",
        }[self.compact_motion_text]

    @property
    def connection_style(self) -> str:
        if not self.status_trusted:
            return "statusBad"
        if self.controller_state in {"READY_HOME_REQUIRED", "READY_MOTION", "JOB_RUNNING"}:
            return "statusGood"
        if self.controller_state in {"OPENING", "SYNCHRONIZING", "RECOVERING", "STOPPING"}:
            return "statusWarning"
        return "statusBad"

    @property
    def motion_style(self) -> str:
        if self.controller_state == "READY_MOTION" and self.allow_motion and self.status_trusted:
            return "statusWarning"
        if self.controller_state == "DISCONNECTED" and self.status_trusted:
            return "statusGood"
        return "statusBad" if self.allow_motion or not self.status_trusted else "statusGood"

    def blocked_reason(self, action: str) -> str:
        label = str(action).strip() or "This action"
        if self.operation_busy:
            return f"{label} is unavailable while another machine operation is active."
        if self.remote and not self.node_reachable:
            return f"{label} is unavailable because Raspberry Pi status is offline or stale."
        if not self.state_valid:
            return f"{label} is unavailable because the controller reported an invalid state."
        if self.controller_state == "RECONNECT_REQUIRED":
            return f"{label} is unavailable until the controller is reconnected."
        if self.controller_state in {"OPENING", "SYNCHRONIZING", "RECOVERING", "STOPPING"}:
            return f"{label} is unavailable while the controller is {self.controller_state.lower().replace('_', ' ')}."
        if self.controller_state == "READY_HOME_REQUIRED":
            return f"{label} requires Home / park for the current controller session."
        if self.controller_state == "JOB_RUNNING" or self.job_running:
            return f"{label} is unavailable while a controller job is active."
        if self.armed:
            return f"Disarm laser control before using {label}."
        if not self.allow_motion and label.lower() not in {"diagnostic command", "disconnect"}:
            return f"{label} is blocked by machine.allow_motion."
        if self.controller_state == "DISCONNECTED":
            return f"{label} requires a synchronized controller session."
        return f"{label} is unavailable in controller state {self.controller_state}."

    def panel_summary(self, protocol: object = "unknown") -> str:
        parts: list[str] = []
        if self.remote:
            # Reachability is intentionally distinct from controller-session
            # authority; never render the old contradictory ONLINE + RECONNECT
            # combination.
            parts.append("PI REACHABLE" if self.node_reachable else "PI OFFLINE")
        parts.append(
            "CONTROLLER STATE UNTRUSTED"
            if not self.status_trusted
            else "RECONNECT REQUIRED · SESSION UNTRUSTED"
            if self.controller_state == "RECONNECT_REQUIRED"
            else self.controller_state.replace("_", " ")
        )
        parts.append(str(protocol or "unknown"))
        parts.append("ARMED" if self.armed else "SAFE")
        parts.append(self.compact_motion_text)
        return " | ".join(parts)


def project_machine_state(
    status: Mapping[str, Any] | None,
    *,
    operation_busy: bool = False,
) -> ControllerUiState:
    machine = machine_payload(status)
    explicit_value = machine.get("controller_state")
    explicit = explicit_value is not None
    state = str(explicit_value or "").strip().upper()
    state_valid = state in CONTROLLER_STATES
    if not explicit:
        state = _legacy_controller_state(machine)
        state_valid = True
    elif not state_valid:
        state = "FAULTED"

    job = machine.get("job")
    job_running = isinstance(job, Mapping) and job.get("running") is True
    remote = bool(
        machine.get("pi_owned_execution") is True
        or machine.get("execution_target") == "pi"
        or str(machine.get("port") or "").lower().startswith("e3bridge://")
    )
    monitor_present = "monitor_connected" in machine
    stale_present = "status_stale" in machine
    status_stale = remote and machine.get("status_stale") is True
    node_reachable = not remote or (
        (not monitor_present or machine.get("monitor_connected") is True)
        and (not stale_present or not status_stale)
    )
    return ControllerUiState(
        controller_state=state,
        explicit_state=explicit,
        state_valid=state_valid,
        session_generation=controller_session_generation(machine),
        state_revision=controller_state_revision(machine),
        node_boot_id=controller_node_boot_id(machine),
        remote=remote,
        node_reachable=node_reachable,
        status_stale=status_stale,
        allow_motion=machine.get("allow_motion") is True,
        armed=machine.get("armed") is True,
        job_running=job_running or state == "JOB_RUNNING",
        jog_ready=machine.get("jog_ready") is True,
        operation_busy=bool(operation_busy),
    )


def _sanitized_value(value: object, *, depth: int = 0) -> object:
    if depth >= 5:
        return "<depth limit>"
    if value is None or type(value) in {bool, int, float}:
        return value
    if isinstance(value, str):
        return value if len(value) <= 512 else value[:509] + "…"
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for index, (raw_key, item) in enumerate(value.items()):
            if index >= 80:
                result["<truncated>"] = f"{len(value) - index} additional fields"
                break
            key = str(raw_key)
            normalized = key.lower()
            if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
                continue
            result[key] = _sanitized_value(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        items = list(value[:80]) if hasattr(value, "__getitem__") else list(value)[:80]
        return [_sanitized_value(item, depth=depth + 1) for item in items]
    return str(value)[:512]


def running_controller_diagnostics(status: Mapping[str, Any] | None) -> dict[str, object]:
    """Return bounded, secret-free controller diagnostics for display/copy."""

    machine = machine_payload(status)
    projection = project_machine_state(machine)
    local_build = load_build_info()
    current_action_required = {
        "DISCONNECTED": "Connect",
        "OPENING": "Wait for controller opening",
        "SYNCHRONIZING": "Wait for controller synchronization",
        "READY_HOME_REQUIRED": "Home / park",
        "READY_MOTION": None,
        "JOB_RUNNING": "Monitor job or use Software STOP",
        "STOPPING": "Wait for STOP cleanup",
        "RECOVERING": "Wait for controller recovery",
        "RECONNECT_REQUIRED": "Reconnect",
        "FAULTED": "Correct configuration, then Connect",
        "SHUTTING_DOWN": "None; application is shutting down",
    }[projection.controller_state]
    payload: dict[str, object] = {
        "desktop_build": {
            "version": local_build.version,
            "revision": local_build.revision,
            "platform": local_build.platform_key,
        },
        "controller_state": projection.controller_state,
        "controller_session_generation": projection.session_generation,
        "controller_state_revision": projection.state_revision,
        "current_action_required": current_action_required,
        "node_boot_id": projection.node_boot_id,
        "node_build": _sanitized_value(machine.get("node_build")),
        "node_protocol": _sanitized_value(machine.get("node_protocol")),
        "node_capabilities": _sanitized_value(machine.get("node_capabilities")),
        "monitor_connected": machine.get("monitor_connected"),
        "status_stale": machine.get("status_stale"),
        "status_error": _sanitized_value(machine.get("status_error")),
        "protocol": _sanitized_value(machine.get("protocol")),
        "configured_endpoint": _sanitized_value(machine.get("port")),
    }
    for key in ("controller_session", "controller_diagnostics"):
        value = machine.get(key)
        if isinstance(value, Mapping):
            payload[key] = _sanitized_value(value)
    return payload


def format_running_controller_diagnostics(
    status: Mapping[str, Any] | None,
    *,
    maximum_characters: int = 32_000,
) -> str:
    payload = running_controller_diagnostics(status)
    text = json.dumps(
        payload,
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    if len(text) <= maximum_characters:
        return text
    payload.pop("controller_session", None)
    payload.pop("controller_diagnostics", None)
    payload["diagnostics_truncated"] = True
    payload["diagnostics_truncation_reason"] = "diagnostics exceeded display limit"
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
