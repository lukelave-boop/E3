from __future__ import annotations

import copy
import json
from contextlib import contextmanager, nullcontext

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.machine import z_retention
from laser_aligner.machine.z_retention import ZRetention, capture_z, restore_z, validate_snapshot
from tests import test_laser_focus as helpers

focus = helpers.focus


@pytest.fixture
def retained(focus):
    focus.serial.overrides["M115"] = helpers.IDENTITY[:-1] + [z_retention.CAPABILITY, "ok"]
    original = focus.serial.write_line

    def write(line):
        if line in focus.serial.overrides:
            return original(line)
        if line == "M17 Z" or line.startswith("M124 "):
            focus.serial.writes.append(line)
            if focus.serial.on_write:
                focus.serial.on_write(line)
            if line.startswith("M124 "):
                focus.serial.z = float(line.split()[1][1:])
                focus.serial.homed = True
                focus.serial.responses.append(f"E3ZR:1 Z:{focus.serial.z:.3f}")
            focus.serial.responses.append("ok")
        else:
            original(line)

    focus.serial.write_line = write
    focus.run("reference")
    focus.snapshot = capture_z(focus.owner, focus.state, 80, nullcontext)
    focus.serial.writes.clear()
    return focus


def clean_store(tmp_path, snapshot):
    store = ZRetention(tmp_path / "retained.json", {"primary": "grbl", "secondary": "ender"})
    assert store.take() is None
    assert store.save(snapshot)
    return store


def test_checkpoint_consumed_before_reuse_and_crash_cannot_reuse_it(tmp_path, retained):
    store = clean_store(tmp_path, retained.snapshot)
    restarted = ZRetention(store.path, store.binding)
    assert restarted.take() == retained.snapshot
    restarted.assert_claim()
    assert json.loads(store.path.read_text())["clean"] is False
    crashed = ZRetention(store.path, store.binding)
    assert crashed.take() is None
    with pytest.raises(MachineError, match="session"):
        restarted.assert_claim()
    with pytest.raises(MachineError, match="session"):
        restarted.save(retained.snapshot)


def test_clean_second_session_can_publish_new_position(tmp_path, retained):
    store = clean_store(tmp_path, retained.snapshot)
    resumed = ZRetention(store.path, store.binding)
    saved = resumed.take()
    saved["z_mm"] = 40
    assert resumed.save(saved)
    assert ZRetention(store.path, store.binding).take()["z_mm"] == 40


def test_restoration_lease_excludes_another_process_claim(tmp_path, retained):
    store = clean_store(tmp_path, retained.snapshot)
    store.take()
    other = ZRetention(store.path, store.binding)
    with store.claim_guard():
        with pytest.raises(MachineError, match="another E3 process"):
            other.take()
        assert json.loads(store.path.read_text())["session"] == store._claim
    assert other.take() is None


def test_stop_revokes_lease_without_waiting_on_file_lock(tmp_path, retained):
    store = clean_store(tmp_path, retained.snapshot)
    store.take()
    with pytest.raises(MachineError, match="cancelled"):
        with store.claim_guard():
            store.invalidate()
    assert not json.loads(store.path.read_text())["clean"]


def test_discard_cannot_steal_a_newer_sessions_claim(tmp_path, retained):
    first = clean_store(tmp_path, retained.snapshot)
    first.take()
    second = ZRetention(first.path, first.binding)
    second.take()
    first.discard()
    with pytest.raises(MachineError, match="session"):
        first.save(retained.snapshot)
    assert not json.loads(first.path.read_text())["clean"]


def test_binding_change_and_explicit_discard_prevent_restore(tmp_path, retained):
    store = clean_store(tmp_path, retained.snapshot)
    changed = ZRetention(store.path, {"primary": "different"})
    assert changed.take() is None
    assert "configuration changed" in changed.reason
    store.take()
    store.save(retained.snapshot)
    store.discard()
    assert ZRetention(store.path, store.binding).take() is None


@pytest.mark.parametrize("change", ["schema", "boolean_schema", "boolean_clean", "unknown_field", "missing_field",
                                   "snapshot", "nonfinite", "duplicate", "oversized", "bad_session", "huge_integer"])
def test_malformed_checkpoint_never_restores_and_is_consumed(tmp_path, retained, change):
    store = clean_store(tmp_path, retained.snapshot)
    data = json.loads(store.path.read_text())
    if change == "schema":
        data["schema_version"] = 2
    elif change == "boolean_schema":
        data["schema_version"] = True
    elif change == "boolean_clean":
        data["clean"] = 1
    elif change == "unknown_field":
        data["extra"] = True
    elif change == "missing_field":
        del data["snapshot"]
    elif change == "snapshot":
        data["snapshot"]["z_mm"] = True
    elif change == "nonfinite":
        data["snapshot"]["z_mm"] = float("nan")
    elif change == "bad_session":
        data["session"] = "not-a-session"
    elif change == "huge_integer":
        data["snapshot"]["z_mm"] = 10 ** 400
    raw = json.dumps(data)
    if change == "duplicate":
        raw = raw.replace('"clean": true', '"clean": false, "clean": true')
    elif change == "oversized":
        raw += " " * 32769
    store.path.write_text(raw)
    assert ZRetention(store.path, store.binding).take() is None
    assert json.loads(store.path.read_text())["clean"] is False


def test_failed_dirty_write_blocks_admission_without_returning_checkpoint(tmp_path, retained, monkeypatch):
    store = clean_store(tmp_path, retained.snapshot)

    def fail(*args, **kwargs):
        raise OSError("disk unavailable")

    monkeypatch.setattr(z_retention, "atomic_write_json", fail)
    with pytest.raises(MachineError, match="before connecting"):
        ZRetention(store.path, store.binding).take()
    # The old directory entry itself cannot resurrect a valid checkpoint even
    # when the replacement fails or its rename is lost in a power failure.
    assert store.path.read_bytes().startswith(b"!")


def test_failed_old_inode_flush_blocks_restore_candidate(tmp_path, retained, monkeypatch):
    store = clean_store(tmp_path, retained.snapshot)

    def fail(descriptor):
        raise OSError("flush failed")

    monkeypatch.setattr(z_retention.os, "fsync", fail)
    with pytest.raises(MachineError, match="before connecting"):
        ZRetention(store.path, store.binding).take()


def test_stop_during_staging_prevents_clean_publication(tmp_path, retained, monkeypatch):
    store = clean_store(tmp_path, retained.snapshot)
    store.take()
    revision = store.revision()
    write = z_retention.atomic_write_json

    def interrupt(path, data):
        write(path, data)
        if data.get("clean"):
            store.invalidate()

    monkeypatch.setattr(z_retention, "atomic_write_json", interrupt)
    assert store.save(retained.snapshot, expected_revision=revision) is False
    assert not json.loads(store.path.read_text())["clean"]


def test_stop_publication_guard_rejects_after_staging(tmp_path, retained):
    store = clean_store(tmp_path, retained.snapshot)
    store.take()

    @contextmanager
    def stopped():
        raise MachineError("STOP raced with shutdown")
        yield

    with pytest.raises(MachineError, match="STOP"):
        store.save(retained.snapshot, guard=stopped)
    assert not json.loads(store.path.read_text())["clean"]


@pytest.mark.parametrize("field,value", [("z_mm", 19.999), ("z_mm", 81), ("z_mm", True),
                                         ("z_mm", float("inf")), ("clearance_z_mm", 19), ("max_z_mm", 81)])
def test_saved_coordinate_validation_rejects_unsafe_values(retained, field, value):
    snapshot = copy.deepcopy(retained.snapshot)
    snapshot[field] = value
    with pytest.raises(MachineError):
        validate_snapshot(snapshot)


@pytest.mark.parametrize("change", ["lowered", "recovery", "below_clearance", "above_max", "unknown", "no_reference",
                                   "firmware", "unstowed", "flag_mismatch", "fan_on", "generation"])
def test_capture_rejects_uncertain_or_unsupported_state_without_motion(retained, change):
    state, serial = retained.state, retained.serial
    if change == "lowered":
        state.requires_clearance = True
    elif change == "recovery":
        state.xy_recovery_pending_reference = True
    elif change == "below_clearance":
        serial.z = 29.999
    elif change == "above_max":
        serial.z = 81
    elif change == "unknown":
        serial.homed = False
    elif change == "no_reference":
        state.drop_z_reference()
    elif change == "firmware":
        serial.overrides["M115"] = helpers.IDENTITY
    elif change == "unstowed":
        serial.overrides["M119"] = ["z_min: open", "test_axis_known_z_flag = true", "ok"]
    elif change == "flag_mismatch":
        serial.overrides["M119"] = ["z_min: TRIGGERED", "test_axis_known_z_flag = false", "ok"]
    elif change == "fan_on":
        serial.overrides["M123"] = ["E3MB:1 FAN1:0 FAN2:255 Z_KNOWN:1", "ok"]
    elif change == "generation":
        retained.owner._generation += 1
    with pytest.raises(MachineError):
        capture_z(retained.owner, state, 80, nullcontext)
    assert not any(line.startswith(("G1", "G28", "G39", "M124")) for line in serial.writes)


@pytest.mark.parametrize("already_known", [False, True])
def test_restore_reestablishes_reference_and_hold_without_travel_or_measurement(retained, already_known):
    retained.serial.z = 30 if already_known else 0
    retained.serial.homed = already_known
    retained.state.surface = {"stale": True}
    retained.state.preview = {"stale": True}
    retained.state.job_plan = {"stale": True}
    restore_z(retained.owner, retained.state, retained.snapshot, 80, nullcontext)
    assert retained.serial.z == 30 and retained.serial.homed
    assert "M124 Z30.000" in retained.serial.writes
    assert "M84 S0" in retained.serial.writes and "M17 Z" in retained.serial.writes
    assert not any(line.startswith(("G1 ", "G28", "G39", "G92", "M3", "M4 ")) for line in retained.serial.writes)
    assert retained.state.surface is retained.state.preview is retained.state.job_plan is None
    # Fresh XY Home/session invalidation must not throw away the restored datum.
    retained.state.invalidate()
    result = retained.run("status", primary_generation=2)
    assert result["reference_ready"]
    assert result["surface"] is result["preview"] is result["job_focus"] is None


@pytest.mark.parametrize("change", ["maximum", "firmware", "known_changed", "unknown_nonzero", "unknown_near_zero",
                                   "unstowed", "fan_on", "missing_ack", "wrong_ack", "duplicate_ack", "not_known_after"])
def test_restore_rejects_changed_state_and_does_not_grant_reference(retained, change):
    serial = retained.serial
    serial.homed, serial.z = False, 0
    maximum = 80
    if change == "maximum":
        maximum = 40
    elif change == "firmware":
        serial.overrides["M115"] = helpers.IDENTITY
    elif change == "known_changed":
        serial.homed, serial.z = True, 31
    elif change == "unknown_nonzero":
        serial.z = 30
    elif change == "unknown_near_zero":
        serial.z = .002
    elif change == "unstowed":
        serial.overrides["M119"] = ["z_min: open", "test_axis_known_z_flag = false", "ok"]
    elif change == "fan_on":
        serial.overrides["M123"] = ["E3MB:1 FAN1:255 FAN2:255 Z_KNOWN:0", "ok"]
    elif change == "missing_ack":
        serial.overrides["M124 Z30.000"] = ["ok"]
    elif change == "wrong_ack":
        serial.overrides["M124 Z30.000"] = ["E3ZR:1 Z:29.000", "ok"]
    elif change == "duplicate_ack":
        serial.overrides["M124 Z30.000"] = ["E3ZR:1 Z:30.000", "E3ZR:1 Z:30.000", "ok"]
    else:
        serial.overrides["M124 Z30.000"] = ["E3ZR:1 Z:30.000", "ok"]
    retained.state.drop_z_reference()
    with pytest.raises(MachineError):
        restore_z(retained.owner, retained.state, retained.snapshot, maximum, nullcontext)
    assert retained.state.retain_z_reference(retained.owner.generation) is None
    assert retained.state.z_reference_required
    assert not any(line.startswith(("G1 ", "G28", "G39", "G92")) for line in serial.writes)


def test_forgetting_datum_blocks_known_z_focus_until_explicit_reference(retained):
    calibration = copy.deepcopy(retained.state.calibration)
    retained.state.drop_z_reference()
    status = retained.run("status")
    assert status["current_readback"]["z_known"] and not status["reference_ready"]
    with pytest.raises(MachineError, match="Reference the border"):
        retained.run("clearance")
    assert retained.state.calibration == calibration
    retained.serial.contacts = [0]
    assert retained.run("reference")["reference_ready"]
