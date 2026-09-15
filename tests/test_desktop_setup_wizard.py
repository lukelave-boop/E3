from __future__ import annotations

import os
import threading
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from laser_aligner.desktop import setup_wizard
from laser_aligner.desktop.machine_setup import MachineSetupDialog
from laser_aligner.desktop.qt import require_qt
from laser_aligner.setup_workflow import SETUP_STEPS, StepStatus, evaluate_setup_steps
from tests.test_desktop_machine_setup import _runtime, _wait_until

_, _, QtWidgets = require_qt()


@pytest.fixture
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def dialog(app, tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    setup = MachineSetupDialog(runtime)
    monkeypatch.setattr(setup_wizard, "read_setup_status", lambda parent: (
        evaluate_setup_steps(readiness={}, evidence={}), (),
    ))
    yield setup
    _wait_until(app, lambda: not setup.operation_busy)
    setup.close()
    runtime.stop()


def settle(app, dialog):
    _wait_until(app, lambda: not dialog.operation_busy)


def test_navigation_and_review_do_not_dispatch_actions_or_write_evidence(app, dialog, monkeypatch):
    calls = []
    monkeypatch.setattr(dialog, "navigate_setup_step", calls.append)
    before = list(dialog.context.surface_calibration.path.parent.glob("*precision*"))
    wizard = dialog.setup_wizard
    dialog.show()
    dialog.setup_wizard_button.click()
    settle(app, dialog)
    assert wizard.isVisible()
    assert not dialog.tabs.tabBar().isVisible()
    assert dialog.machine_stop_button.isVisible()
    assert dialog.machine_stop_button.isEnabled()
    for index in range(1, len(SETUP_STEPS) + 1):
        wizard.next.click()
        settle(app, dialog)
        assert wizard.index == index
    assert not calls
    assert "does not establish" in wizard.instructions.text()
    assert "BLOCKED" in wizard.evidence.toPlainText()
    assert list(dialog.context.surface_calibration.path.parent.glob("*precision*")) == before
    wizard.next.click()
    assert not wizard.isVisible()
    assert dialog.tabs.tabBar().isVisible()
    dialog.setup_wizard_button.click()
    settle(app, dialog)
    assert wizard.index == len(SETUP_STEPS)


def test_explicit_tools_and_prerequisite_route_separately(app, dialog, monkeypatch):
    calls = []
    monkeypatch.setattr(dialog, "navigate_setup_step", calls.append)
    wizard = dialog.setup_wizard
    wizard.begin()
    settle(app, dialog)
    for index, action in ((3, "datum"), (5, "precision"), (6, "heights"), (7, "qualification")):
        wizard.select_step(index)
        settle(app, dialog)
        assert calls == [item[1] for item in ((3, "datum"), (5, "precision"), (6, "heights"), (7, "qualification")) if item[0] < index]
        wizard.open_tools.click()
        assert calls[-1] == action
    wizard.select_step(2)
    settle(app, dialog)
    wizard.go_to_prerequisite()
    settle(app, dialog)
    assert wizard.index == 1
    wizard.select_step(5)
    settle(app, dialog)
    wizard.show_holdout()
    assert dialog.tabs.currentIndex() == 4
    assert len(calls) == 4


@pytest.mark.parametrize("external_stop", [False, True])
def test_busy_navigation_rejects_direct_calls_and_stop_discards_late_evidence(app, dialog, monkeypatch, external_stop):
    entered, release = threading.Event(), threading.Event()
    def read(parent):
        entered.set()
        assert release.wait(5)
        return ({step.id: StepStatus(step.id, "complete", "Old snapshot", step.action)
                 for step in SETUP_STEPS}, ())
    monkeypatch.setattr(setup_wizard, "read_setup_status", read)
    wizard = dialog.setup_wizard
    try:
        wizard.begin()
        _wait_until(app, entered.is_set)
        wizard.select_step(3)
        wizard.open_current_tools()
        wizard.leave()
        dialog.navigate_setup_step("datum")
        assert wizard.index == 0
        assert dialog.tabs.currentIndex() == 0
        assert not wizard.next.isEnabled()
        assert dialog.machine_stop_button.isEnabled()
        if external_stop:
            dialog.context.machine.request_stop(emergency=True)
        else:
            dialog.machine_stop_button.click()
    finally:
        release.set()
    settle(app, dialog)
    assert not wizard.statuses
    assert "Old snapshot" not in wizard.evidence.toPlainText()
    assert wizard.next.isEnabled()


def test_refresh_replaces_accepted_snapshot_with_stale_reason(app, dialog, monkeypatch):
    current = {step.id: StepStatus(step.id, "complete", "Current evidence", step.action)
               for step in SETUP_STEPS}
    monkeypatch.setattr(setup_wizard, "read_setup_status", lambda parent: (dict(current), ()))
    wizard = dialog.setup_wizard
    wizard.begin()
    settle(app, dialog)
    wizard.select_step(7)
    settle(app, dialog)
    assert "COMPLETE" in wizard.evidence.toPlainText()
    current["qualification"] = StepStatus("qualification", "stale", "Setup identity changed", "qualification")
    wizard.refresh.click()
    settle(app, dialog)
    assert "STALE: Setup identity changed" in wizard.evidence.toPlainText()
    assert "COMPLETE" not in wizard.evidence.toPlainText()


def test_observational_refresh_preserves_focus_evidence(app, dialog):
    busy_transitions = []
    payload = {"measurement": "current"}
    def external_busy(busy):
        busy_transitions.append(busy)
        if busy:
            payload.clear()
    dialog.focus_workspace = SimpleNamespace(
        coordinator=SimpleNamespace(mutation_busy=False, set_external_busy=external_busy),
        set_machine_status=lambda status: None,
        shutdown=lambda **kwargs: True,
    )
    dialog.setup_wizard.begin()
    settle(app, dialog)
    assert True not in busy_transitions
    assert payload == {"measurement": "current"}
