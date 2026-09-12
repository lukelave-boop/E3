"""One-use focus binding carried in immutable job bytes, never sent to GRBL."""
from __future__ import annotations

import copy
import re
from contextlib import contextmanager

from ..errors import MachineError, SafetyError
from .focus_bounds import FocusXYBounds
from .mainboard import parse_status, validate_max_z
from .z_probe import parse_position

PREFIX = "E3FOCUS"
CAPABILITY = "pi-job-focus-v1"
_LINE = re.compile(r"E3FOCUS ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def binding_id(line: str) -> str | None:
    if not line.startswith(PREFIX):
        return None
    match = _LINE.fullmatch(line)
    if match is None:
        raise SafetyError("Invalid measured job focus binding")
    return match[1]


def program_binding(lines) -> str | None:
    found = None
    for index, line in enumerate(lines):
        token = binding_id(line)
        if token is not None:
            if index != 0 or found is not None:
                raise SafetyError("Measured job focus must occur once, at the start of the program")
            found = token
    return found


def attach(text: str, plan: dict | None) -> str:
    if plan is None or any(line.strip().startswith(PREFIX) for line in text.splitlines()):
        return text
    directive = f"{PREFIX} {plan['id']}"
    binding_id(directive)
    return directive + "\n" + text


def selected_plan(machine, program):
    token = program_binding(program.lines)
    if token is None:
        return None
    state, probe = machine._laser_focus, machine._z_probe
    plan = state.job_plan
    if (plan is None or plan["id"] != token or probe is None or machine._session is None
        or plan["session"] != (probe.owner.generation, machine._session.generation, machine._operation_stop_epoch())
        or state.session != plan["session"] or state.calibration != plan["calibration"]
        or state.reference != plan["reference"] or state.surface is None
        or state.surface["id"] != plan["measurement_id"]
        or machine._jog_position_mm != tuple(plan["xy"])
        or state.selected_clearance != plan["clearance_z_mm"]
        or validate_max_z(machine.settings.mainboard_max_z_mm) != plan["maximum"]):
        raise SafetyError("Job focus selection is stale; measure, preview and select it again")
    if state.xy_recovery_pending_reference:
        raise SafetyError("Reference the border before starting the measured-focus job")
    # A first-move endpoint check alone cannot validate travel from the measured spot.
    from ..gcode.preview import parse_words
    for line in program.lines[1:]:
        if line.startswith(("G0 ", "G1 ")):
            words = {w.letter: w.value for w in parse_words(line)}
            bounds = FocusXYBounds(machine.settings.work_area, program.guarded_output_polygon_mm)
            if not bounds.contains_segment(tuple(plan["xy"]), (words["X"], words["Y"])):
                raise SafetyError("Travel from the measured spot to the job start leaves the positioning area")
            break
    return copy.deepcopy(plan)


def check_context(machine, context):
    plan = context.focus_plan
    if plan is None:
        return
    probe = machine._z_probe
    if (probe is None or not probe.owner.ready or probe.owner.generation != plan["session"][0]
        or machine._stop_epoch != plan["session"][2]
        or context.session.generation != plan["session"][1]
        or validate_max_z(machine.settings.mainboard_max_z_mm) != plan["maximum"]
        or machine._laser_focus.calibration != plan["calibration"]):
        raise MachineError("Measured job focus authority changed during execution")


def move(machine, context, *, clearance):
    """Move only Z under this exact Pi-owned running job and both sessions."""
    plan, probe = context.focus_plan, machine._z_probe
    if plan is None or probe is None:
        raise MachineError("Measured job focus context is unavailable")
    owner = probe.owner

    @contextmanager
    def guard():
        with machine._secondary_write_gate, machine._stop_epoch_lock:
            if (context.stop_event.is_set() or machine._active_job_context is not context
                or not machine._same_controller_session(machine._session, context.session)
                or machine._stop_epoch != plan["session"][2]
                or not owner.ready or owner.generation != plan["session"][0]
                or machine._z_probe is not probe
                or machine.hardware_enabled is not True or machine.settings.allow_motion is not True
                or validate_max_z(machine.settings.mainboard_max_z_mm) != plan["maximum"]):
                raise MachineError("Measured job focus cancelled or controller/configuration changed")
            machine._raise_session_failure(context.session)
            yield

    def send(command):
        with guard():
            pass
        result = owner._execute_acknowledged(
            command, allow_open=False, timeout=35, write_guard=guard,
            on_failure=lambda: machine.request_stop(_recover=False), interrupt_on_failure=True,
        )
        with guard():
            pass
        return result

    def stowed():
        states = [line.strip().lower() for line in send("M119") if line.strip().lower().startswith("z_min:")]
        if states != ["z_min: triggered"]:
            raise SafetyError("Job focus requires the confirmed stowed probe")

    target = plan["clearance_z_mm"] if clearance else plan["target_z_mm"]
    if not 0 <= target <= plan["maximum"]:
        raise SafetyError("Job focus target exceeds the configured Z range")
    with owner._lock:
        if "\n".join(send("M115")) != plan["firmware"]:
            raise MachineError("Job focus firmware changed")
        if not parse_status(send("M123"))["z_known"]:
            raise SafetyError("Job focus requires known Z; no automatic recovery")
        current = parse_position(send("M114"))
        if not 0 <= current <= plan["maximum"]:
            raise SafetyError("Job focus received an invalid Z position")
        if clearance and current > target + .05:
            raise SafetyError("Automatic clearance return cannot lower Z")
        if not clearance and abs(current - plan["clearance_z_mm"]) > .05:
            raise SafetyError("Job focus descent must begin at verified clearance")
        stowed()
        machine._set_running_job_phase("lifting" if clearance else "focusing")
        with guard():
            machine._z_probe_active = True
            machine._laser_focus.selected_clearance = plan["clearance_z_mm"]
            if not clearance:
                machine._laser_focus.requires_clearance = True
        try:
            send("G21")
            send("G90")
            send(f"G1 Z{target:.3f} F300")
            send("M400")
            if abs(parse_position(send("M114")) - target) > .05:
                raise MachineError("Job focus Z movement was not confirmed")
            if not parse_status(send("M123"))["z_known"]:
                raise MachineError("Job focus lost its Z reference")
            stowed()
            with guard():
                machine._laser_focus.requires_clearance = not clearance
        finally:
            machine._z_probe_active = False
        machine._set_running_job_phase("streaming")
