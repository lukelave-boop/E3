"""In-process Z continuity across an explicit job stop; never a disk checkpoint."""
from __future__ import annotations

import copy
import os
import time
from contextlib import contextmanager

from ..errors import MachineError
from .controller_dialects import GRBL_DIALECT
from .mainboard import parse_status
from .z_probe import parse_position

CAPABILITY = "pi-job-stop-retain-z-v1"


def capture(machine):
    """Called under the STOP gate. No serial I/O, waits, or coordinate grants."""
    context, probe, state = machine._active_job_context, machine._z_probe, machine._laser_focus
    if (context is None or probe is None or not machine._z_retention_configured
            or machine._z_probe_active or machine._focus_operation_active
            or context.stop_event.is_set() or not context.status.running
            or context.status.phase not in {"streaming", "draining"}
            or not machine._same_controller_session(machine._session, context.session)
            or context.session.dialect is not GRBL_DIALECT
            or machine._secondary_air_assist is None
            or machine._secondary_air_assist.owner is not probe.owner
            or os.path.normcase(os.path.normpath(context.session.resolved_endpoint))
            == os.path.normcase(os.path.normpath(probe.owner.session.port))
            or "z_mm" not in context.verified_z):
        return None
    reference = state.retain_z_reference(probe.owner.generation)
    plan = context.focus_plan or context.retained_workpiece_plan
    if (reference is None or plan is None or plan["reference"] != reference
            or plan["session"] != (probe.owner.generation, context.session.generation, machine._stop_epoch)
            or plan["maximum"] != machine.settings.mainboard_max_z_mm
            or plan["calibration"] != state.calibration):
        return None
    return {
        "epoch": machine._stop_epoch + 1, "owner": probe.owner,
        "generation": probe.owner.generation, "reference": reference,
        "binding": copy.deepcopy(machine._retained_z_binding()),
        "z_mm": context.verified_z["z_mm"], "clearance": plan["clearance_z_mm"],
        "calibration": copy.deepcopy(state.calibration),
        "endpoint": context.session.configured_endpoint,
        "baudrate": context.session.baudrate,
    }


def connection_matches(machine, snapshot, port, protocol, baudrate, epoch):
    return bool(snapshot is not None and snapshot["epoch"] == epoch
                and snapshot["binding"] == machine._retained_z_binding()
                and snapshot["endpoint"] == port and protocol == "grbl"
                and snapshot["baudrate"] == baudrate)


def restore(machine, snapshot, session, epoch):
    """Read the unchanged Ender after primary recovery, then publish only its datum."""
    owner = snapshot["owner"]
    deadline = time.monotonic() + 8.0

    @contextmanager
    def guard():
        with owner._lock, machine._secondary_write_gate, machine._stop_epoch_lock:
            if (machine._stop_epoch != epoch or snapshot["epoch"] != epoch
                    or machine._stopped_z is not snapshot
                    or machine._shutdown_requested or machine._z_probe is None
                    or machine._z_probe.owner is not owner or not owner.ready
                    or owner.generation != snapshot["generation"]
                    or not machine._same_controller_session(machine._session, session)
                    or session.dialect is not GRBL_DIALECT
                    or machine._retained_z_binding() != snapshot["binding"]
                    or machine._laser_focus.calibration != snapshot["calibration"]
                    or machine.hardware_enabled is not True or machine.settings.allow_motion is not True
                    or time.monotonic() >= deadline):
                raise MachineError("Stopped Z continuity changed; reference the border again")
            yield

    def read(command):
        with guard():
            pass
        return owner._execute_acknowledged(command, allow_open=False, timeout=2, write_guard=guard)

    with owner._lock:
        if "\n".join(read("M115")) != snapshot["reference"]["firmware"]:
            raise MachineError("Ender firmware changed after Stop job")
        if not parse_status(read("M123"))["z_known"]:
            raise MachineError("Ender lost Z after Stop job")
        current = parse_position(read("M114"))
        if (not 0 <= current <= machine.settings.mainboard_max_z_mm
                or abs(current - snapshot["z_mm"]) > .05):
            raise MachineError("Ender Z changed after Stop job")
        flags = [line.strip().lower() for line in read("M119")]
        if ([line for line in flags if line.startswith("z_min:")] != ["z_min: triggered"]
                or [line for line in flags if line.startswith("test_axis_known_z_flag")]
                != ["test_axis_known_z_flag = true"]):
            raise MachineError("Ender probe is not stowed after Stop job")
        if abs(parse_position(read("M114")) - current) > .001 or not parse_status(read("M123"))["z_known"]:
            raise MachineError("Ender Z changed during Stop job recovery")
        with guard():
            state = machine._laser_focus
            state.restore_z_reference(snapshot["reference"], owner_generation=owner.generation,
                                      clearance=snapshot["clearance"])
            state.requires_clearance = current < snapshot["clearance"] - .05
            machine._z_retention_restored = True
            machine._z_retention_reason = "Z reference retained after Stop job; Home / park XY before restarting"
