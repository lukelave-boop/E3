from __future__ import annotations

import copy
import threading
from unittest.mock import Mock

import pytest

from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.laser_focus import validate_setup_evidence
from tests import test_material_surface as helpers

focus = helpers.focus
focus_machine = helpers.focus_machine
machine_probe = helpers.machine_probe


def test_local_setup_snapshot_reads_no_controller_and_survives_normal_home(focus_machine):
    machine, focus_state, primary = focus_machine
    helpers.selected(machine)
    before = machine.setup_evidence_snapshot()
    assert before["calibration_compatible"] is True
    assert before["calibration"]["gauge_mm"] == 7
    assert before["reference_id"] == focus_state.state._reference_id
    machine.prepare_photo_position()
    assert focus_state.state.reference is None
    primary.write_line = Mock(wraps=primary.write_line)
    focus_state.serial.write_line = Mock(wraps=focus_state.serial.write_line)
    assert machine.setup_evidence_snapshot() == before
    assert machine.status()["setup_evidence"] == before
    changed = machine.setup_evidence_snapshot()
    changed["calibration"]["id"] = "changed"
    changed["reference"]["border_z_mm"] = 999
    assert machine.setup_evidence_snapshot() == before
    primary.write_line.assert_not_called()
    focus_state.serial.write_line.assert_not_called()


@pytest.mark.parametrize("change", ["disconnect", "reference_lost", "secondary_session", "stop", "recovery", "invalid_gauge"])
def test_local_setup_snapshot_never_restores_lost_authority(focus_machine, change):
    machine, focus_state, _ = focus_machine
    helpers.selected(machine)
    assert machine.setup_evidence_snapshot() is not None
    if change == "disconnect":
        machine._connected = False
    elif change == "reference_lost":
        focus_state.state.drop_z_reference()
    elif change == "secondary_session":
        focus_state.owner._generation += 1
    elif change == "stop":
        machine.request_stop(_recover=False)
    elif change == "recovery":
        focus_state.state.xy_recovery_pending_reference = True
    else:
        focus_state.state.calibration["focus_offset_mm"] += .1
    assert machine.setup_evidence_snapshot() is None


def test_explicit_reference_and_gauge_edits_change_setup_identity(focus_machine):
    machine, focus_state, _ = focus_machine
    helpers.selected(machine)
    before = machine.setup_evidence_snapshot()
    focus_state.serial.contacts = [0.]
    machine.focus_control("reference", confirmed=True)
    after = machine.setup_evidence_snapshot()
    assert after["reference_id"] != before["reference_id"]
    assert after["reference"] == before["reference"]
    focus_state.state.calibration = None
    assert machine.setup_evidence_snapshot()["calibration_compatible"] is False
    assert machine.setup_evidence_snapshot()["calibration"] is None


def test_setup_snapshot_never_waits_for_secondary_controller_ownership(focus_machine):
    machine, focus_state, _ = focus_machine
    helpers.selected(machine)
    entered, release = threading.Event(), threading.Event()
    def secondary_operation():
        with focus_state.owner._lock:
            entered.set()
            assert release.wait(2.)
    worker = threading.Thread(target=secondary_operation)
    worker.start()
    assert entered.wait(1.)
    try:
        assert machine.setup_evidence_snapshot() is not None
        assert worker.is_alive()
    finally:
        release.set()
        worker.join(1.)


def remote_snapshot(focus_machine, monkeypatch):
    machine = focus_machine[0]
    surface, _ = helpers.selected(machine)
    setup = machine.setup_evidence_snapshot()
    remote, fake = helpers.remote_with_surface(monkeypatch, surface)
    original = fake._machine_status
    fake._machine_status = lambda: {**original(), "setup_evidence": copy.deepcopy(setup)}
    remote._refresh_once()
    return remote, fake, setup


def test_remote_setup_snapshot_accepts_only_fresh_status_without_rpc(focus_machine, monkeypatch):
    remote, fake, expected = remote_snapshot(focus_machine, monkeypatch)
    requests = len(fake.requests)
    assert remote.setup_evidence_snapshot() == expected
    result = remote.setup_evidence_snapshot()
    result["reference"]["border_z_mm"] = 999
    assert remote.setup_evidence_snapshot() == expected
    assert len(fake.requests) == requests
    remote._last_machine_status_monotonic -= 4
    assert remote.setup_evidence_snapshot() is None


@pytest.mark.parametrize("change", ["session", "stale", "disconnect", "capability", "detached"])
def test_remote_setup_snapshot_drops_stale_or_incompatible_evidence(focus_machine, monkeypatch, change):
    remote, _, _ = remote_snapshot(focus_machine, monkeypatch)
    if change == "session":
        remote._controller_session_generation += 1
    elif change == "stale":
        remote._status_cache["status_stale"] = True
    elif change == "disconnect":
        remote._status_cache["connected"] = False
    elif change == "capability":
        remote._node_capabilities = ()
    else:
        remote._detached = True
    assert remote.setup_evidence_snapshot() is None


def test_older_inflight_status_cannot_restore_setup_after_a_focus_edit(focus_machine, monkeypatch):
    remote, fake, _ = remote_snapshot(focus_machine, monkeypatch)
    request_epoch = remote._focus_observation_epoch
    remote._focus_observation_epoch += 1
    remote._setup_evidence = None
    remote._cache_remote_status(fake._machine_status(), job_record=None, focus_observation_epoch=request_epoch)
    assert remote.setup_evidence_snapshot() is None


@pytest.mark.parametrize("field,value", [("schema_version", True), ("reference_id", "bad"),
                                        ("controller_session", 900), ("calibration_compatible", False),
                                        ("session", [1, 2, False]), ("firmware_geometry", {})])
def test_malformed_setup_provenance_rejects_before_remote_acceptance(focus_machine, monkeypatch, field, value):
    remote, fake, setup = remote_snapshot(focus_machine, monkeypatch)
    setup[field] = value
    with pytest.raises(SafetyError):
        validate_setup_evidence(setup)
    with pytest.raises(MachineError):
        remote._cache_remote_status(fake._machine_status(), job_record=None)
    assert remote.setup_evidence_snapshot() is None
