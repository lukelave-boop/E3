from __future__ import annotations

# ruff: noqa: E402
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")
from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.desktop.controls import InspectorTabs, NumericDoubleSpinBox, NumericSpinBox
from laser_aligner.desktop.theme import apply_dark_theme


@pytest.fixture(scope="session")
def application():
    yield QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


@pytest.fixture
def app(application):
    old_sheet = application.styleSheet()
    old_palette = application.palette()
    old_filter = application.property("e3ConfirmationButtonColorFilter")
    old_widgets = set(application.topLevelWidgets())
    apply_dark_theme(application)
    yield application
    for widget in set(application.topLevelWidgets()) - old_widgets:
        widget.close()
        widget.deleteLater()
    application.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    if old_filter is None:
        event_filter = application.property("e3ConfirmationButtonColorFilter")
        application.removeEventFilter(event_filter)
        application.setProperty("e3ConfirmationButtonColorFilter", None)
        event_filter.deleteLater()
        application.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    application.setPalette(old_palette)
    application.setStyleSheet(old_sheet)
    application.processEvents()


def _pixels(widget, rect):
    image = widget.grab().toImage()
    scale = image.devicePixelRatio()
    left, top = int(rect.left() * scale), int(rect.top() * scale)
    right, bottom = int((rect.right() + 1) * scale), int((rect.bottom() + 1) * scale)
    return [image.pixelColor(x, y) for x in range(left, right) for y in range(top, bottom)]


def _light_count(widget, rect):
    return sum(min(pixel.red(), pixel.green(), pixel.blue()) > 185
               for pixel in _pixels(widget, rect))


def _subcontrol(widget, option, control, subcontrol):
    widget.initStyleOption(option)
    return widget.style().subControlRect(control, option, subcontrol, widget)


@pytest.mark.parametrize("editable", [False, True])
def test_combo_arrow_is_visible_and_opens_a_selectable_popup(app, editable):
    combo = QtWidgets.QComboBox()
    combo.setEditable(editable)
    combo.addItems(["First choice", "Second choice"])
    combo.resize(240, 34)
    combo.show()
    app.processEvents()
    arrow = _subcontrol(combo, QtWidgets.QStyleOptionComboBox(),
                        QtWidgets.QStyle.ComplexControl.CC_ComboBox,
                        QtWidgets.QStyle.SubControl.SC_ComboBoxArrow)
    assert arrow.width() >= 24
    center = QtCore.QRect(arrow.center().x() - 5, arrow.center().y() - 4, 11, 9)
    assert _light_count(combo, center) >= 8
    QtTest.QTest.mouseClick(combo, QtCore.Qt.MouseButton.LeftButton, pos=arrow.center())
    app.processEvents()
    assert combo.view().isVisible()
    combo.view().setCurrentIndex(combo.model().index(1, 0))
    QtTest.QTest.keyClick(combo.view(), QtCore.Qt.Key.Key_Return)
    assert combo.currentIndex() == 1
    combo.setEnabled(False)
    app.processEvents()
    assert _light_count(combo, center) == 0
    assert max(pixel.lightness() for pixel in _pixels(combo, center)) > 100
    QtTest.QTest.mouseClick(combo, QtCore.Qt.MouseButton.LeftButton, pos=arrow.center())
    assert not combo.view().isVisible()


@pytest.mark.parametrize("kind", [QtWidgets.QSpinBox, QtWidgets.QDoubleSpinBox,
                                  NumericSpinBox, NumericDoubleSpinBox])
def test_spinner_arrows_are_visible_and_preserve_bounded_step_behavior(app, kind):
    spin = kind()
    spin.setRange(0, 2)
    spin.setValue(1)
    spin.resize(180, 36)
    spin.show()
    app.processEvents()
    option = QtWidgets.QStyleOptionSpinBox()
    up = _subcontrol(spin, option, QtWidgets.QStyle.ComplexControl.CC_SpinBox,
                     QtWidgets.QStyle.SubControl.SC_SpinBoxUp)
    down = _subcontrol(spin, option, QtWidgets.QStyle.ComplexControl.CC_SpinBox,
                       QtWidgets.QStyle.SubControl.SC_SpinBoxDown)
    for button in [up, down]:
        assert button.width() >= 21
        center = QtCore.QRect(button.center().x() - 5, button.center().y() - 4, 11, 9)
        assert _light_count(spin, center) >= 8
    QtTest.QTest.mouseClick(spin, QtCore.Qt.MouseButton.LeftButton, pos=up.center())
    assert spin.value() == 2
    QtTest.QTest.mouseClick(spin, QtCore.Qt.MouseButton.LeftButton, pos=up.center())
    assert spin.value() == 2
    QtTest.QTest.mouseClick(spin, QtCore.Qt.MouseButton.LeftButton, pos=down.center())
    assert spin.value() == 1
    spin.setEnabled(False)
    QtTest.QTest.mouseClick(spin, QtCore.Qt.MouseButton.LeftButton, pos=down.center())
    assert spin.value() == 1


def test_compact_transform_spin_keeps_a_visible_small_stepper(app):
    toolbar = QtWidgets.QToolBar()
    toolbar.setObjectName("contextToolbar")
    spin = NumericDoubleSpinBox()
    spin.setRange(0, 100)
    spin.setValue(42)
    spin.setFixedWidth(78)
    toolbar.addWidget(spin)
    toolbar.show()
    app.processEvents()
    up = _subcontrol(spin, QtWidgets.QStyleOptionSpinBox(),
                     QtWidgets.QStyle.ComplexControl.CC_SpinBox,
                     QtWidgets.QStyle.SubControl.SC_SpinBoxUp)
    assert 14 <= up.width() <= 16
    assert _light_count(spin, up.adjusted(2, 1, -2, -1)) >= 8
    assert spin.width() == 78


def test_radio_and_disabled_checked_toggle_preserve_state_cues(app):
    host = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(host)
    radio = QtWidgets.QRadioButton("Choice")
    checkbox = QtWidgets.QCheckBox("Included")
    layout.addWidget(radio)
    layout.addWidget(checkbox)
    host.show()
    app.processEvents()
    option = QtWidgets.QStyleOptionButton()
    radio.initStyleOption(option)
    indicator = radio.style().subElementRect(
        QtWidgets.QStyle.SubElement.SE_RadioButtonIndicator, option, radio)
    before = radio.grab().toImage()
    radio.setChecked(True)
    app.processEvents()
    after = radio.grab().toImage()
    assert before != after
    pixel = _pixels(radio, QtCore.QRect(indicator.center(), QtCore.QSize(1, 1)))[0]
    assert pixel.green() > pixel.red() + 30
    checkbox.setChecked(True)
    checkbox.setEnabled(False)
    app.processEvents()
    checked = checkbox.grab().toImage()
    checkbox.setChecked(False)
    app.processEvents()
    assert checked != checkbox.grab().toImage()


def test_native_palette_has_readable_menu_and_disabled_text(app):
    palette = app.palette()
    roles = QtGui.QPalette.ColorRole
    assert palette.color(roles.ButtonText).lightness() > 220
    assert palette.color(roles.Button).lightness() < 80
    disabled = palette.color(QtGui.QPalette.ColorGroup.Disabled, roles.Text)
    assert 140 <= disabled.lightness() <= 180


@pytest.mark.parametrize("position", [QtWidgets.QTabWidget.TabPosition.North,
                                      QtWidgets.QTabWidget.TabPosition.South])
def test_panels_menu_exposes_every_inspector_and_tracks_selection(app, position):
    tabs = InspectorTabs()
    tabs.setTabPosition(position)
    titles = ["Design", "Trace", "Camera", "Alignment", "Machine", "Focus", "Console", "Project"]
    for index, title in enumerate(titles):
        tabs.add_panel(str(index), title, QtWidgets.QWidget(), tooltip=f"Open {title}")
    tabs.resize(300, 220)
    tabs.show()
    app.processEvents()
    chooser = tabs.findChild(QtWidgets.QToolButton, "inspectorPanelMenuButton")
    assert chooser.isVisible()
    assert chooser.accessibleName() == "Choose inspector panel"
    assert tabs.rect().contains(chooser.geometry())
    actions = chooser.menu().actions()
    assert [action.text() for action in actions] == titles
    assert actions[0].isChecked()
    actions[0].trigger()
    assert actions[0].isChecked()
    actions[-1].trigger()
    assert tabs.currentIndex() == 7
    assert [action.isChecked() for action in actions] == [False] * 7 + [True]
    tabs.setCurrentIndex(2)
    assert actions[2].isChecked()
    assert [tabs.tabText(index) for index in range(tabs.count())] == titles
