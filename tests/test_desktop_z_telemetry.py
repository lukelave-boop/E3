"""Live counter readouts remain observational while typed controls are busy."""

import copy
import os
import time
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtWidgets

from laser_aligner.desktop import z_telemetry
from laser_aligner.desktop.laser_focus import LaserFocusCoordinator, LaserFocusPanel
from laser_aligner.desktop.mainboard_z import MainboardZCoordinator, MainboardZPanel
from laser_aligner.desktop.z_telemetry import EnderZTelemetry
from tests.test_desktop_laser_focus import FakeController, status
from tests.test_desktop_laser_focus import result as focus_result
from tests.test_desktop_mainboard_z import result as z_result


def sample(**changes):
    value = {
        "supported": True, "valid": True, "z_mm": 25.5, "known": True,
        "homing": False, "moving": True, "sequence": 12,
        "controller_generation": 3, "age_seconds": .1,
    }
    value.update(changes)
    return value


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


@pytest.fixture
def clock(monkeypatch):
    value = SimpleNamespace(now=time.monotonic())
    monkeypatch.setattr(z_telemetry, "time", SimpleNamespace(monotonic=lambda: value.now))
    return value


def test_sample_age_advances_without_more_ui_status_polls(clock):
    observation = EnderZTelemetry()
    observation.update(status(ender_z_telemetry=sample(age_seconds=1)))
    assert "25.500" in observation.readout(active=True, readback_at=None)[0]
    clock.now += .6
    assert observation.readout(active=True, readback_at=None)[0] == "Z — mm"


def test_duplicate_cached_snapshot_cannot_keep_a_counter_sample_fresh(clock):
    observation = EnderZTelemetry()
    snapshot = status(ender_z_telemetry=sample(age_seconds=.1))
    observation.update(snapshot)
    clock.now += 1
    observation.update(snapshot)
    clock.now += .5
    observation.update(snapshot)
    assert observation.readout(active=True, readback_at=None)[0] == "Z — mm"
    observation.update(status(ender_z_telemetry=sample(sequence=13, z_mm=24)))
    assert "24.000" in observation.readout(active=True, readback_at=None)[0]


@pytest.mark.parametrize("field,value", [
    ("valid", False), ("valid", 1), ("supported", False), ("supported", "true"),
    ("known", None), ("homing", 0), ("moving", "false"),
    ("z_mm", None), ("z_mm", True), ("z_mm", float("nan")), ("z_mm", float("inf")),
    ("z_mm", 10**500), ("age_seconds", None), ("age_seconds", -1),
    ("age_seconds", 1.501), ("age_seconds", True),
    ("sequence", None), ("sequence", True), ("sequence", -1), ("sequence", 2**32),
    ("controller_generation", None), ("controller_generation", True), ("controller_generation", -1),
])
def test_invalid_telemetry_clears_numeric_readout(field, value):
    observation = EnderZTelemetry()
    observation.update(status(ender_z_telemetry=sample()))
    observation.update(status(ender_z_telemetry=sample(**{field: value})))
    assert observation.readout(active=True, readback_at=None)[0] == "Z — mm"


@pytest.mark.parametrize("change", [
    {"connected": False}, {"controller_state": "RECOVERING"},
    {"controller_state": "FAULTED"}, {"controller_state": "DISCONNECTED"},
    {"status_stale": True}, {"status_refresh_error": "timeout"},
])
def test_unusable_machine_status_never_displays_a_numeric_sample(change):
    observation = EnderZTelemetry()
    observation.update(status(ender_z_telemetry=sample()))
    observation.update(status(ender_z_telemetry=sample(), **change))
    assert observation.readout(active=True, readback_at=None)[0] == "Z — mm"


def test_ender_reset_discards_old_idle_number_until_new_typed_readback(clock):
    observation = EnderZTelemetry()
    observation.update(status(ender_z_telemetry=sample()))
    previous_readback = clock.now
    clock.now += .5
    observation.update(status(ender_z_telemetry=sample(
        controller_generation=4, valid=False, z_mm=None, sequence=None, age_seconds=None,
    )))
    assert not observation.readback_current(previous_readback)
    assert observation.readout(active=False, readback_at=previous_readback)[0] == "Z — mm"
    assert observation.readout(active=False, readback_at=clock.now) is None


@pytest.mark.parametrize("panel_kind", ["focus", "machine"])
def test_busy_panels_display_actual_samples_without_changing_authority(app, clock, panel_kind):
    if panel_kind == "focus":
        panel = LaserFocusPanel()
        controller = FakeController()
        coordinator = LaserFocusCoordinator(panel, controller)
        coordinator._timer.stop()
        controller.statusChanged.emit(status())
        panel.set_result(focus_result())
        controller.busyChanged.emit(True)
        emit = controller.statusChanged.emit
        note = panel.live_z_note
    else:
        panel = MainboardZPanel()
        coordinator = None
        panel.set_machine_status(status())
        panel.set_result(z_result(z_mm=30))
        panel.set_busy(True)
        emit = panel.set_machine_status
        note = panel.readback_note
    try:
        old_result = copy.deepcopy(panel._result)
        received_at = panel._received_at
        old_preview = getattr(panel, "_preview_id", None)
        emit(status(ender_z_telemetry=sample(z_mm=29.375)))
        assert panel.height.text() == "Z 29.375 mm · live"
        assert "step counter" in note.text()
        emit(status(ender_z_telemetry=sample(sequence=13, z_mm=28.125)))
        assert panel.height.text() == "Z 28.125 mm · live"
        assert panel._result == old_result
        assert panel._received_at == received_at
        assert getattr(panel, "_preview_id", None) == old_preview
        assert not panel.up.isEnabled() and not panel.down.isEnabled()
        if panel_kind == "focus":
            assert not panel.move.isEnabled() and not panel.teach.isEnabled()
            assert controller.machine.calls == [] and controller.work == []
        else:
            assert not panel.apply.isEnabled()
        clock.now += 2
        panel._sync_z_display()  # A label timer alone can expire the sample.
        assert panel.height.text() == "Z — mm"
        assert "Live Z unavailable" in note.text()
        emit(status(ender_z_telemetry=sample(sequence=14, z_mm=-.375, known=False, homing=True)))
        assert "-0.375" in panel.height.text()
        assert "homing / unreferenced" in panel.height.text()
        assert "above border" not in panel.height.text() + note.text() + panel.height.toolTip()
        assert not panel.up.isEnabled() and not panel.down.isEnabled()
        emit(status(ender_z_telemetry=sample(valid=False, z_mm=-.375, known=False, homing=True)))
        assert panel.height.text() == "Z — mm"
        # Only the separately completed typed result restores ordinary idle display.
        if panel_kind == "focus":
            controller.busyChanged.emit(False)
            panel.set_result(focus_result(current_readback={"z_mm": 20., "z_known": True, "fresh": True}))
        else:
            panel.set_busy(False)
            panel.set_result(z_result(z_mm=20))
        assert panel.height.text() == "Z 20.000 mm"
    finally:
        if coordinator is not None:
            coordinator.close()
        panel.close()
        panel.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("kind", ["focus", "machine"])
def test_legacy_firmware_movement_explicitly_reports_live_unavailable(app, kind):
    panel = LaserFocusPanel() if kind == "focus" else MainboardZPanel()
    try:
        if kind == "focus":
            panel._status = status()
            panel._busy = True
            panel.observe_z_status(status())
            note = panel.live_z_note
        else:
            panel.set_machine_status(status())
            panel.set_busy(True)
            note = panel.readback_note
        assert panel.height.text() == "Z — mm"
        assert "Live Z unavailable" in note.text()
        assert not panel.up.isEnabled() and not panel.down.isEnabled()
    finally:
        panel.close()
        panel.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("kind", ["focus", "machine"])
def test_display_timer_reads_new_cached_sample_without_authority_status_event(app, kind):
    panel = LaserFocusPanel() if kind == "focus" else MainboardZPanel()
    controller = FakeController()
    coordinator = (LaserFocusCoordinator if kind == "focus" else MainboardZCoordinator)(panel, controller)
    coordinator._timer.stop()
    panel._display_timer.stop()
    controller.statusChanged.emit(status())
    panel.set_result(focus_result() if kind == "focus" else z_result())
    panel.show()
    controller.busyChanged.emit(True)
    try:
        authority = copy.deepcopy(coordinator._status)
        result = copy.deepcopy(panel._result)
        received_at = panel._received_at
        generation = coordinator._epoch
        controller.machine.snapshot = status(ender_z_telemetry=sample(z_mm=22.125))
        panel._display_timer.timeout.emit()
        assert panel.height.text() == "Z 22.125 mm · live"
        controller.machine.snapshot = status(ender_z_telemetry=sample(sequence=13, z_mm=21.75))
        panel._display_timer.timeout.emit()
        assert panel.height.text() == "Z 21.750 mm · live"
        assert coordinator._status == authority
        assert coordinator._epoch == generation
        assert panel._result == result and panel._received_at == received_at
        assert not panel.up.isEnabled() and not panel.down.isEnabled()
        assert controller.machine.calls == [] and controller.work == []
        # Cache loss clears the display immediately; no reconnect or command is attempted.
        controller.machine.snapshot = status(connected=False, ender_z_telemetry=sample())
        panel._display_timer.timeout.emit()
        assert panel.height.text() == "Z — mm"
        assert coordinator._status == authority
        assert controller.machine.calls == [] and controller.work == []
    finally:
        if kind == "focus":
            coordinator.close()
        coordinator.deleteLater()
        panel.close()
        panel.deleteLater()
        app.processEvents()


@pytest.mark.parametrize("kind", ["focus", "machine"])
def test_fast_cache_observation_is_paused_when_idle_or_hidden(app, kind):
    panel = LaserFocusPanel() if kind == "focus" else MainboardZPanel()
    controller = FakeController()
    coordinator = (LaserFocusCoordinator if kind == "focus" else MainboardZCoordinator)(panel, controller)
    coordinator._timer.stop()
    panel._display_timer.stop()
    controller.statusChanged.emit(status())
    panel.set_result(focus_result() if kind == "focus" else z_result())
    reads = []
    controller.machine.status = lambda: reads.append(True) or status(ender_z_telemetry=sample())
    panel.show()
    try:
        panel._display_timer.timeout.emit()
        assert reads == []
        controller.busyChanged.emit(True)
        panel.hide()
        panel._display_timer.timeout.emit()
        assert reads == []
        panel.show()
        panel._display_timer.timeout.emit()
        assert reads == [True]
    finally:
        if kind == "focus":
            coordinator.close()
        coordinator.deleteLater()
        panel.close()
        panel.deleteLater()
        app.processEvents()
