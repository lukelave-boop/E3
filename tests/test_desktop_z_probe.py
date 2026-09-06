from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from laser_aligner.desktop.qt import require_qt
from laser_aligner.desktop.z_probe import ZProbePanel

_, _, QtWidgets = require_qt()


def test_panel_requires_confirmation_snapshots_inputs_and_uses_measured_result():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    calls = []
    pending = []

    def probe(operation, **kwargs):
        calls.append((operation, kwargs))
        return {"kind": "surface_measurement", "surface_height_mm": 3,
                "thickness_above_honeycomb_mm": 4.5, "spread_mm": .02}

    def start(name, operation, complete, **kwargs):
        pending.append((operation, complete, kwargs))
        return True

    panel = ZProbePanel(SimpleNamespace(machine=SimpleNamespace(probe_z=probe)), start)
    panel.measure.click()
    assert not pending and not calls
    panel.support.setValue(-1.5)
    panel.confirm.setChecked(True)
    panel.measure.click()
    assert not panel.confirm.isChecked()
    assert pending[0][2]["requires_controller"] is True
    panel.support.setValue(0)
    operation, complete, _ = pending.pop()
    complete(operation())
    assert calls == [("measure", {"confirmed": True, "clearance_z_mm": 20., "support_height_mm": -1.5})]
    assert "+3.000 mm" in panel.result.text()
    assert "4.500 mm" in panel.result.text()
    panel.show()
    app.processEvents()
    panel.close()
