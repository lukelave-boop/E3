from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.config import LaserSettings
from laser_aligner.desktop.panels import LayerPanel, MachinePanel, MaterialPanel
from laser_aligner.desktop.speed_controls import (
    PercentageSpeedSpinBox,
    format_speed_percent,
)
from laser_aligner.materials import MaterialDatabase, MaterialPreset
from laser_aligner.project import ProjectDocument, SceneObject
from laser_aligner.project.toolpath import generate_project_gcode


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _enter(spin, text):
    spin.setFocus()
    spin.lineEdit().selectAll()
    QtTest.QTest.keyClicks(spin.lineEdit(), text)
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Return)


def test_percentage_entry_uses_configured_limit_and_retains_exact_saved_feed(app):
    spin = PercentageSpeedSpinBox(6000)
    original = 1500.123456789123
    spin.setValue(original)
    assert spin.text() == "25 %"
    spin.interpretText()
    assert spin.value() == original
    spin.set_speed_limit(3000)
    assert spin.text() == "50 %"
    assert spin.value() == original
    assert "100% = 3000 mm/min" in spin.toolTip()

    # Retyping the rounded value is an explicit request for that exact percent.
    _enter(spin, "50")
    assert spin.value() == 1500
    _enter(spin, "33.33")
    assert spin.value() == pytest.approx(999.9)
    spin.interpretText()
    assert spin.value() == pytest.approx(999.9)


@pytest.mark.parametrize("text", ["0%", "-1%", "101%", "nan", "inf", "1500 mm/min", "50 mm", "1e9", "0.001%"])
def test_percentage_control_rejects_invalid_commands_without_rewriting_saved_feed(app, text):
    spin = PercentageSpeedSpinBox(6000)
    spin.setValue(1500.125)
    assert spin.validate(text, len(text))[0] != QtGui.QValidator.State.Acceptable
    spin.lineEdit().setText(text)
    spin.interpretText()
    assert spin.value() == 1500.125


@pytest.mark.parametrize("text", ["0", "-1", "101", "105", "150", "0.001", "1e9"])
def test_invalid_typed_percentage_reverts_without_dropping_digits(app, text):
    spin = PercentageSpeedSpinBox(6000)
    original = 1500.123456789123
    spin.setValue(original)
    spin.lineEdit().selectAll()
    QtTest.QTest.keyClicks(spin.lineEdit(), text)
    assert spin.lineEdit().text().removesuffix(" %") == text
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Return)
    assert spin.value() == original
    # A later focus/commit must not round the old source after rejection.
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Return)
    assert spin.value() == original


@pytest.mark.parametrize(("text", "feed"), [("0.05", 3), ("0.02", 1.2), ("1e1", 600)])
def test_typed_small_percentages_preserve_every_digit(app, text, feed):
    spin = PercentageSpeedSpinBox(6000)
    spin.setValue(1500)
    _enter(spin, text)
    assert spin.value() == pytest.approx(feed)


def test_over_limit_saved_feed_is_visible_preserved_and_not_implicitly_reduced(app):
    spin = PercentageSpeedSpinBox(6000)
    spin.setValue(9000.123456789)
    assert spin.text() == "150 %"
    assert "exceeds the limit" in spin.toolTip()
    spin.interpretText()
    assert spin.value() == 9000.123456789
    spin.set_speed_limit(12000)
    assert spin.text() == "75 %"
    assert spin.value() == 9000.123456789
    _enter(spin, "50")
    assert spin.value() == 6000


def test_unavailable_percentage_reference_does_not_invent_maximum(app):
    spin = PercentageSpeedSpinBox()
    spin.setValue(1723.987654321)
    assert spin.isReadOnly()
    assert format_speed_percent(1723, None) == "—"
    assert spin.value() == 1723.987654321
    spin.stepBy(1)
    assert spin.value() == 1723.987654321


def test_small_positive_feed_never_displays_zero_speed(app):
    spin = PercentageSpeedSpinBox(100000)
    spin.setValue(1)
    assert spin.text() == "0.001 %"
    spin.interpretText()
    assert spin.value() == 1


def test_valid_legacy_feed_below_editor_minimum_is_preserved_on_load(app):
    spin = PercentageSpeedSpinBox(6000)
    spin.setValue(0.123456789123)
    spin.interpretText()
    spin.set_speed_limit(3000)
    spin.interpretText()
    assert spin.value() == 0.123456789123
    assert spin.text() != "0 %"


def test_unchanged_status_reference_does_not_interrupt_percentage_typing(app):
    spin = PercentageSpeedSpinBox(6000)
    spin.setValue(1500)
    spin.lineEdit().selectAll()
    QtTest.QTest.keyClicks(spin.lineEdit(), "40")
    spin.set_speed_limit(6000)
    assert spin.lineEdit().text().strip() == "40 %"
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Return)
    assert spin.value() == 2400


def test_layer_display_and_other_edits_preserve_feeds_and_generated_program(app, monkeypatch):
    # Compare the complete program with stable metadata even across a wall-clock
    # second boundary. Only the toolpath module's clock reference is replaced.
    monkeypatch.setattr(
        "laser_aligner.project.toolpath.time",
        SimpleNamespace(strftime=lambda _format: "2026-09-06 12:00:00"),
    )
    document = ProjectDocument.new()
    layer = document.layers[0]
    layer.speed_mm_min = 1500.123456789123
    layer.power_percent = 0
    shape = SceneObject.rectangle(layer.id)
    shape.transform.x_mm = shape.transform.y_mm = 30
    document.add_object(shape)
    original = generate_project_gcode(document, LaserSettings()).text
    panel = LayerPanel(max_work_feed_mm_min=6000)
    panel.set_document(document)
    edits = []
    panel.layerEdited.connect(lambda _identity, values: edits.append(values))
    panel._emit_edit()
    assert edits[-1]["speed_mm_min"] == layer.speed_mm_min
    assert panel.layer_list.topLevelItem(0).text(2).startswith("25% / ")
    assert generate_project_gcode(document, LaserSettings()).text == original
    _enter(panel.speed_spin, "50")
    layer.speed_mm_min = edits[-1]["speed_mm_min"]
    assert layer.speed_mm_min == 3000
    assert "F3000" in generate_project_gcode(document, LaserSettings()).text


def test_material_recipe_open_and_save_keep_canonical_speed(app, tmp_path):
    database = MaterialDatabase(tmp_path / "materials.sqlite3")
    preset = database.save(MaterialPreset(material="Test", name="Exact", speed_mm_min=1500.123456789123))
    panel = MaterialPanel(database, max_work_feed_mm_min=6000)
    panel._show_preset(preset)
    assert panel._form_preset().speed_mm_min == preset.speed_mm_min
    _enter(panel.speed_spin, "40")
    assert panel._form_preset().speed_mm_min == 2400


def test_jog_percentage_uses_travel_ceiling_and_limit_change_never_clamps_command(app):
    panel = MachinePanel(max_travel_feed_mm_min=6000)
    panel.jog_group.setEnabled(True)
    requests = []
    panel.jogRequested.connect(lambda *args: requests.append(args))
    _enter(panel.jog_speed, "50")
    panel._jog(1, 0)
    assert requests[-1] == (0.1, 0, 3000)
    panel.set_status({"max_travel_feed_mm_min": 1200})
    assert panel.jog_speed.value() == 3000
    assert panel.jog_speed.text() == "250 %"
    panel._jog(1, 0)
    assert len(requests) == 1
    panel.jog_group.setEnabled(True)
    _enter(panel.jog_speed, "50")
    panel._jog(1, 0)
    assert requests[-1] == (0.1, 0, 600)
