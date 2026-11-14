"""Restart reuse must preserve material settings without reviving job authority."""
from __future__ import annotations

import copy
import json
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.job_focus import selection_snapshot
from laser_aligner.machine.laser_focus import LaserFocus, validate_request
from laser_aligner.machine.z_retention import ZRetention, capture_z, restore_z, validate_snapshot
from tests import test_z_retention as helpers

clean_store = helpers.clean_store
focus = helpers.focus
retained = helpers.retained


@pytest.fixture
def saved(retained):
    f = retained
    measured = f.run("measure")["surface"]["id"]
    for _ in range(10):
        f.run("jog", value=-1, measurement_id=measured)
    f.run("teach", measurement_id=measured)
    f.run("clearance")
    f.serial.contacts = [2.0]
    f.run("measure_workpiece", value=1.0)
    snapshot = capture_z(f.owner, f.state, 80, nullcontext)
    assert snapshot["workpiece"]["spacer_thickness_mm"] == 1
    return f, snapshot


def execute(f, state, action, **changes):
    options = dict(confirmed=action != "status", value=None, clearance_z_mm=30., gap_mm=7.,
                   measurement_id=None, preview_id=None, maximum=80., primary_generation=2,
                   stop_epoch=0, xy=(10, 190), guard=nullcontext,
                   on_motion_start=lambda: pytest.fail("Reuse must not move"), on_failure=lambda: None)
    options.update(changes)
    with f.owner._lock:
        return state.execute(f.owner, f.probe, action, **options)


def restore(f, snapshot):
    state = LaserFocus(f.state.path, "ender")
    f.serial.z = 0
    f.serial.homed = False
    restore_z(f.owner, state, snapshot, 80, nullcontext)
    return state


def test_restart_reuse_creates_fresh_guarded_binding_without_measurement_or_motion(saved, tmp_path):
    f, snapshot = saved
    old = copy.deepcopy(f.state.job_plan)
    store = clean_store(tmp_path, snapshot)
    assert json.loads(store.path.read_text())["schema_version"] == 2
    consumed = ZRetention(store.path, store.binding).take()
    state = restore(f, consumed)
    assert state.job_plan is None and state.surface is None and state.preview is None
    with pytest.raises(SafetyError):
        state.require_job_focus()
    before = len(f.serial.writes)
    status = execute(f, state, "status")
    assert status["saved_workpiece"] == snapshot["workpiece"]
    assert state.job_plan is None
    result = execute(f, state, "reuse_workpiece")
    plan = result["job_focus"]
    assert plan["id"] != old["id"] and plan["measurement_id"] != old["measurement_id"]
    assert plan["session"] == (f.owner.generation, 2, 0)
    assert plan["target_z_mm"] == old["target_z_mm"]
    assert plan["spacer_thickness_mm"] == 1
    assert plan["source"] == "saved_workpiece"
    assert state.surface is None and state.preview is None and state.saved_workpiece is None
    assert not any(command.startswith(("G", "M3", "M4")) for command in f.serial.writes[before:])
    machine = SimpleNamespace(_laser_focus=state, _z_probe=f.probe,
                              _session=SimpleNamespace(generation=2), _operation_stop_epoch=lambda: 0,
                              _jog_position_mm=(10, 190), settings=SimpleNamespace(mainboard_max_z_mm=80))
    assert selection_snapshot(machine, plan["id"]) == plan
    machine._session.generation = 3
    with pytest.raises(SafetyError):
        selection_snapshot(machine, plan["id"])


@pytest.mark.parametrize("change", ["unknown_z", "firmware", "calibration", "honeycomb", "clearance",
                                    "below_clearance", "recovery", "stop", "owner", "clear", "maximum"])
def test_reuse_rejects_changed_authority_without_motion(saved, change):
    f, snapshot = saved
    state = restore(f, snapshot)
    execute(f, state, "status")
    options = {}
    if change == "unknown_z":
        f.serial.homed = False
    elif change == "firmware":
        f.serial.overrides["M115"] = ["changed"]
    elif change == "calibration":
        state.calibration["focus_offset_mm"] += 1
    elif change == "honeycomb":
        state.honeycomb_height_mm += 1
    elif change == "clearance":
        options["clearance_z_mm"] = 35
    elif change == "maximum":
        options["maximum"] = 70
    elif change == "below_clearance":
        f.serial.z = 29
    elif change == "recovery":
        state.xy_recovery_pending_reference = True
    elif change == "stop":
        options["stop_epoch"] = 1
    elif change == "owner":
        state._z_reference_generation -= 1
    elif change == "clear":
        execute(f, state, "clear_surface")
    before = len(f.serial.writes)
    with pytest.raises(MachineError):
        execute(f, state, "reuse_workpiece", **options)
    assert state.job_plan is None
    assert not any(command.startswith(("G", "M3", "M4")) for command in f.serial.writes[before:])


@pytest.mark.parametrize("field,value", [("surface_elevation_mm", float("nan")),
                                        ("spacer_thickness_mm", -1), ("focus_offset_mm", 70),
                                        ("calibration_id", "bad"), ("schema_version", True),
                                        ("focus_policy", "unknown")])
def test_corrupt_material_checkpoint_rejected(saved, field, value):
    _, snapshot = saved
    snapshot["workpiece"][field] = value
    with pytest.raises(MachineError):
        validate_snapshot(snapshot)


def test_checkpoint_cannot_be_replayed_after_crash(saved, tmp_path):
    _, snapshot = saved
    store = clean_store(tmp_path, snapshot)
    assert ZRetention(store.path, store.binding).take() is not None
    assert ZRetention(store.path, store.binding).take() is None


def test_wrong_schema_cannot_smuggle_material_into_legacy_checkpoint(saved, tmp_path):
    _, snapshot = saved
    store = clean_store(tmp_path, snapshot)
    data = json.loads(store.path.read_text())
    data["schema_version"] = 1
    store.path.write_text(json.dumps(data))
    assert ZRetention(store.path, store.binding).take() is None


def test_compatible_z_restores_but_changed_gauge_does_not_restore_material(saved):
    f, snapshot = saved
    f.state.persist(dict(f.state.calibration, focus_offset_mm=16, taught_z_mm=21))
    state = restore(f, snapshot)
    assert state.retain_z_reference(f.owner.generation) is not None
    assert state.saved_workpiece is None and state.job_plan is None


def test_pending_material_survives_another_clean_shutdown_and_home_invalidation(saved):
    f, snapshot = saved
    state = restore(f, snapshot)
    state.invalidate()  # XY Home invalidates temporary focus state, not Z.
    assert state.saved_workpiece == snapshot["workpiece"]
    assert capture_z(f.owner, state, 80, nullcontext)["workpiece"] == snapshot["workpiece"]
    state.drop_z_reference()
    assert state.saved_workpiece is None


def test_reuse_requires_explicit_confirmation():
    with pytest.raises(SafetyError):
        validate_request("reuse_workpiece", confirmed=False)
