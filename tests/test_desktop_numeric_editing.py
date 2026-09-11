from __future__ import annotations

# Select the Qt test platform before importing the desktop modules.
# ruff: noqa: E402
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.desktop.controls import MeasurementSpinBox, NumericDoubleSpinBox, NumericSpinBox
from laser_aligner.desktop.speed_controls import PercentageSpeedSpinBox


@pytest.fixture(scope="module")
def app():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def clear_then_type(spin, text):
    spin.lineEdit().selectAll()
    QtTest.QTest.keyClick(spin.lineEdit(), QtCore.Qt.Key.Key_Backspace)
    QtTest.QTest.keyClicks(spin.lineEdit(), text)


def commit(spin):
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Return)


@pytest.mark.parametrize("kind", [NumericDoubleSpinBox, NumericSpinBox, MeasurementSpinBox])
@pytest.mark.parametrize("suffix", ["", " mm"])
def test_clear_all_and_type_bounded_value_without_publishing_drafts(app, kind, suffix):
    spin = kind()
    spin.setRange(20, 80)
    spin.setSuffix(suffix)
    spin.setValue(80)
    values = []
    spin.valueChanged.connect(values.append)
    spin.lineEdit().selectAll()
    QtTest.QTest.keyClick(spin.lineEdit(), QtCore.Qt.Key.Key_Backspace)
    assert spin._editable_text(spin.text()) == ""
    assert spin.validate(spin.text(), 0)[0] is QtGui.QValidator.State.Intermediate
    assert spin.value() == 80 and not values
    QtTest.QTest.keyClicks(spin.lineEdit(), "4")
    assert spin._editable_text(spin.text()) == "4"
    assert spin.hasPendingEdit() and not spin.hasAcceptableInput()
    assert spin.value() == 80 and not values
    QtTest.QTest.keyClicks(spin.lineEdit(), "0")
    assert spin.hasAcceptableInput() and spin.hasPendingEdit()
    assert spin.value() == 80 and not values
    commit(spin)
    assert spin.value() == 40 and values == [40]
    assert not spin.hasPendingEdit()


@pytest.mark.parametrize("kind", [NumericDoubleSpinBox, NumericSpinBox, MeasurementSpinBox])
def test_replace_only_first_digit_80_to_40(app, kind):
    spin = kind()
    spin.setRange(20, 80)
    spin.setSuffix(" mm")
    spin.setValue(80)
    values = []
    spin.valueChanged.connect(values.append)
    spin.lineEdit().setSelection(0, 1)
    QtTest.QTest.keyClick(spin.lineEdit(), QtCore.Qt.Key.Key_Backspace)
    assert spin.value() == 80 and not values
    QtTest.QTest.keyClicks(spin.lineEdit(), "4")
    assert spin.text().startswith("40")
    assert spin.value() == 80 and not values
    commit(spin)
    assert spin.value() == 40 and values == [40]


@pytest.mark.parametrize("kind", [NumericDoubleSpinBox, MeasurementSpinBox])
@pytest.mark.parametrize("draft,expected", [("0.1", .1), (".1", .1), ("0.1259", .126)])
def test_decimal_drafts_keep_all_digits_and_commit_once(app, kind, draft, expected):
    spin = kind()
    spin.setDecimals(3)
    spin.setRange(.1, 5)
    spin.setSuffix(" mm")
    spin.setValue(1)
    values = []
    spin.valueChanged.connect(values.append)
    clear_then_type(spin, draft)
    assert spin._editable_text(spin.text()) == draft
    assert spin.value() == 1 and values == []
    commit(spin)
    assert spin.value() == pytest.approx(expected)
    assert values == [pytest.approx(expected)]


@pytest.mark.parametrize("draft", ["", "-", ".", "4", "90", "1e", "1e999", "-30"])
def test_invalid_double_commit_restores_previous_and_never_clamps_or_emits(app, draft):
    spin = NumericDoubleSpinBox()
    spin.setRange(20, 80)
    spin.setSuffix(" mm")
    spin.setValue(40)
    values = []
    spin.valueChanged.connect(values.append)
    clear_then_type(spin, draft)
    assert spin._editable_text(spin.text()) == draft
    assert not spin.hasAcceptableInput()
    commit(spin)
    assert spin.value() == 40 and not values
    assert spin.text() == "40.00 mm"
    assert not spin.hasPendingEdit()


@pytest.mark.parametrize("draft", ["", "+", "-", ".", "0", "999", "1.5", "1e2"])
def test_invalid_integer_commit_restores_previous_without_publishing_partial_value(app, draft):
    spin = NumericSpinBox()
    spin.setRange(1, 80)
    spin.setValue(3)
    values = []
    spin.valueChanged.connect(values.append)
    clear_then_type(spin, draft)
    assert spin.text() == draft
    commit(spin)
    assert spin.value() == 3 and values == []


@pytest.mark.parametrize("kind", [NumericDoubleSpinBox, NumericSpinBox])
def test_negative_range_sign_and_digit_replacement(app, kind):
    spin = kind()
    spin.setRange(-100, -1)
    spin.setValue(-20)
    values = []
    spin.valueChanged.connect(values.append)
    clear_then_type(spin, "-4")
    assert spin.value() == -20 and values == []
    commit(spin)
    assert spin.value() == -4 and values == [-4]


@pytest.mark.parametrize("kind,entered,expected", [
    ("length", "1 in", 25.4), ("length", "-1 in", -25.4),
    ("length", "0.1 mm", .1), ("speed", "1 in/min", 25.4),
])
def test_progressively_typed_measurement_units_remain_valid(app, kind, entered, expected):
    spin = MeasurementSpinBox(kind)
    spin.setRange(-100, 100)
    spin.setSuffix(" mm/min" if kind == "speed" else " mm")
    spin.setValue(2)
    values = []
    spin.valueChanged.connect(values.append)
    clear_then_type(spin, entered)
    assert spin._editable_text(spin.text()) == entered
    assert spin.value() == 2 and values == []
    commit(spin)
    assert spin.value() == pytest.approx(expected)
    assert values == [pytest.approx(expected)]


@pytest.mark.parametrize("kind", [NumericDoubleSpinBox, NumericSpinBox, MeasurementSpinBox])
def test_arrow_discards_invalid_draft_then_steps_previous_valid_value(app, kind):
    spin = kind()
    spin.setRange(20, 80)
    spin.setSingleStep(1)
    spin.setValue(40)
    values = []
    spin.valueChanged.connect(values.append)
    clear_then_type(spin, "999")
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Up)
    assert spin.value() == 41 and values == [41]


def test_focus_loss_commits_valid_draft_once_and_rejects_invalid_draft(app):
    window = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(window)
    spin = NumericDoubleSpinBox()
    other = QtWidgets.QLineEdit()
    layout.addWidget(spin)
    layout.addWidget(other)
    spin.setRange(20, 80)
    spin.setValue(80)
    values = []
    spin.valueChanged.connect(values.append)
    window.show()
    spin.setFocus()
    app.processEvents()
    clear_then_type(spin, "40")
    other.setFocus()
    app.processEvents()
    assert spin.value() == 40 and values == [40]
    spin.setFocus()
    app.processEvents()
    clear_then_type(spin, "4")
    other.setFocus()
    app.processEvents()
    assert spin.value() == 40 and values == [40]
    assert not spin.hasPendingEdit()
    window.close()


def test_percentage_inherits_pending_draft_contract_and_preserves_canonical_feed(app):
    spin = PercentageSpeedSpinBox(6000)
    original = 1500.123456789123
    spin.setValue(original)
    clear_then_type(spin, "0.1")
    assert spin.hasPendingEdit()
    assert spin.value() == original
    commit(spin)
    assert spin.value() == 6 and not spin.hasPendingEdit()


def test_programmatic_update_clears_pending_marker_without_suppressing_new_model_value(app):
    spin = NumericDoubleSpinBox()
    spin.setRange(20, 80)
    spin.setValue(80)
    clear_then_type(spin, "40")
    assert spin.hasPendingEdit()
    spin.setValue(60)
    assert not spin.hasPendingEdit() and spin.value() == 60


@pytest.mark.parametrize("draft", ["80.04", "19.999"])
@pytest.mark.parametrize("kind", [NumericDoubleSpinBox, MeasurementSpinBox])
def test_rounding_cannot_admit_out_of_range_drafts(app, kind, draft):
    spin = kind()
    spin.setDecimals(1)
    spin.setRange(20, 80)
    spin.setSuffix(" mm")
    spin.setValue(40)
    values = []
    spin.valueChanged.connect(values.append)
    clear_then_type(spin, draft)
    assert spin._editable_text(spin.text()) == draft
    assert not spin.hasAcceptableInput()
    commit(spin)
    assert spin.value() == 40 and values == []


def test_exact_z_max_display_clears_and_replaces_80_point_zero(app):
    spin = NumericDoubleSpinBox()
    spin.setDecimals(1)
    spin.setRange(20, 80)
    spin.setSuffix(" mm")
    spin.setValue(80)
    assert spin.text() == "80.0 mm"
    clear_then_type(spin, "40")
    assert spin.value() == 80 and spin.hasPendingEdit()
    commit(spin)
    assert spin.text() == "40.0 mm" and spin.value() == 40


def test_numeric_controls_preserve_locale_decimal_and_integer_group_parsing(app):
    double = NumericDoubleSpinBox()
    double.setLocale(QtCore.QLocale("de_DE"))
    double.setRange(.1, 5)
    double.setValue(1)
    clear_then_type(double, "0,1")
    assert double.text() == "0,1"
    commit(double)
    assert double.value() == .1
    integer = NumericSpinBox()
    integer.setLocale(QtCore.QLocale("en_US"))
    integer.setRange(1, 2000)
    integer.setValue(1)
    clear_then_type(integer, "1,000")
    commit(integer)
    assert integer.value() == 1000


@pytest.mark.parametrize("draft,expected", [("40", 2460), ("105", 1560)])
def test_percentage_arrow_steps_committed_valid_draft_or_previous_valid_value(app, draft, expected):
    spin = PercentageSpeedSpinBox(6000)
    spin.setValue(1500)
    clear_then_type(spin, draft)
    QtTest.QTest.keyClick(spin, QtCore.Qt.Key.Key_Up)
    assert spin.value() == expected
    assert not spin.hasPendingEdit()


@pytest.mark.parametrize("kind,label", [
    (NumericSpinBox, "Automatic"), (NumericDoubleSpinBox, "No maximum"),
    (MeasurementSpinBox, "Any"),
])
def test_existing_special_minimum_labels_remain_valid_and_editable(app, kind, label):
    spin = kind()
    spin.setRange(-1, 80)
    spin.setSpecialValueText(label)
    spin.setValue(-1)
    assert spin.text() == label
    assert spin.hasAcceptableInput()
    assert spin.valueFromText(label) == -1
    spin.interpretText()
    assert spin.value() == -1
    clear_then_type(spin, "40")
    commit(spin)
    assert spin.value() == 40
    clear_then_type(spin, "-1")
    commit(spin)
    assert spin.value() == -1 and spin.text() == label
