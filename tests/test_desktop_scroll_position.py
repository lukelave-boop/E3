import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets  # noqa: E402

from laser_aligner.desktop.scroll_position import StableScrollArea  # noqa: E402


@pytest.fixture
def form():
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    area = StableScrollArea()
    area.resize(400, 240)
    content = QtWidgets.QWidget()
    layout = QtWidgets.QVBoxLayout(content)
    buttons = []
    for index in range(30):
        button = QtWidgets.QPushButton(f"Action {index}")
        button.setFixedHeight(40)
        layout.addWidget(button)
        buttons.append(button)
    area.setWidget(content)
    area.show()
    app.processEvents()
    yield app, area, buttons
    area.close()
    area.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("index", [0, 8, 20])
def test_disabling_clicked_controls_does_not_reveal_distant_focus(form, index):
    app, area, buttons = form
    bar = area.verticalScrollBar()
    bar.setValue(buttons[index].y() - 30)
    before = bar.value()

    def busy():
        for button in buttons[:-1]:
            button.setEnabled(False)

    buttons[index].clicked.connect(busy)
    QtTest.QTest.mouseClick(buttons[index], QtCore.Qt.MouseButton.LeftButton)
    app.processEvents()
    assert bar.value() == before
    buttons[index].setEnabled(True)
    app.processEvents()
    assert bar.value() == before


def test_keyboard_navigation_still_reveals_controls(form):
    app, area, buttons = form
    buttons[0].setFocus()
    area.verticalScrollBar().setValue(0)
    for _ in range(10):
        QtTest.QTest.keyClick(app.focusWidget(), QtCore.Qt.Key.Key_Tab)
    app.processEvents()
    assert app.focusWidget() is buttons[10]
    assert area.verticalScrollBar().value() > 0


def test_explicit_navigation_and_scrollbar_still_work(form):
    app, area, buttons = form
    buttons[0].setFocus()
    area.ensureWidgetVisible(buttons[20])
    before = area.verticalScrollBar().value()
    buttons[20].setFocus()
    app.processEvents()
    assert area.verticalScrollBar().value() == before
    area.verticalScrollBar().setValue(100)
    app.processEvents()
    assert area.verticalScrollBar().value() == 100


def test_home_reference_panel_keeps_scroll_when_busy(form):
    from laser_aligner.desktop.controls import PanelScrollArea
    from laser_aligner.desktop.laser_focus import LaserFocusCoordinator, LaserFocusPanel
    from tests.test_desktop_laser_focus import FakeController, result

    app, _, _ = form
    panel = LaserFocusPanel()
    controller = FakeController()
    coordinator = LaserFocusCoordinator(panel, controller)
    coordinator._timer.stop()
    coordinator.set_status(controller.machine.snapshot)
    panel.set_result(result(
        reference_ready=False, surface=None, preview=None,
        ender={"ready": True, "generation": 4, "recovery_required": False},
    ))
    panel.path_clear.setChecked(True)
    area = PanelScrollArea(panel)
    area.resize(600, 420)
    area.show()
    app.processEvents()
    try:
        area.ensureWidgetVisible(panel.reference)
        panel.reference.setFocus()
        before = area.verticalScrollBar().value()
        assert panel.reference.isEnabled()
        coordinator.set_busy(True)
        app.processEvents()
        assert not panel.reference.isEnabled()
        assert area.verticalScrollBar().value() == before
        coordinator.set_busy(False)
        app.processEvents()
        assert area.verticalScrollBar().value() == before
    finally:
        coordinator.close()
        area.close()
        area.deleteLater()


def settle(app):
    for _ in range(5):
        app.processEvents()


@pytest.mark.parametrize("width", [360, 600])
def test_clicked_button_stays_put_as_status_wraps_and_appears(form, width):
    app, area, buttons = form
    area.setWidgetResizable(True)
    area.resize(width, 240)
    label = QtWidgets.QLabel("Z 30.000 mm")
    label.setWordWrap(True)
    area.widget().layout().insertWidget(0, label)
    notice = QtWidgets.QLabel("Live controller step counter; not an encoder measurement.")
    notice.setWordWrap(True)
    area.widget().layout().insertWidget(1, notice)
    notice.hide()
    settle(app)
    area.ensureWidgetVisible(buttons[8])
    QtTest.QTest.mouseClick(buttons[8], QtCore.Qt.MouseButton.LeftButton)
    before = buttons[8].mapTo(area.viewport(), QtCore.QPoint())
    for text in ("Z -18.243 mm · live · homing / unreferenced " * 4, "Z 30.000 mm"):
        label.setText(text)
        notice.setVisible(not notice.isVisible())
        buttons[8].setEnabled(False)
        settle(app)
        assert buttons[8].mapTo(area.viewport(), QtCore.QPoint()) == before
    buttons[8].setEnabled(True)
    settle(app)
    assert buttons[8].mapTo(area.viewport(), QtCore.QPoint()) == before


def test_real_home_panel_status_layout_keeps_clicked_button_position(form):
    from laser_aligner.desktop.controls import PanelScrollArea
    from laser_aligner.desktop.laser_focus import LaserFocusPanel

    app, _, _ = form
    panel = LaserFocusPanel()
    area = PanelScrollArea(panel)
    area.resize(600, 440)
    area.show()
    settle(app)
    try:
        # Exercise real layout changes without issuing a machine command.
        panel.reference.clicked.disconnect()
        panel.reference.setEnabled(True)
        area.ensureWidgetVisible(panel.reference)
        QtTest.QTest.mouseClick(panel.reference, QtCore.Qt.MouseButton.LeftButton)
        before = panel.reference.mapTo(area.viewport(), QtCore.QPoint())
        for text in ("Z -18.243 mm · live · homing / unreferenced", "Z 30.000 mm"):
            panel.height.setText(text)
            panel.live_z_note.setText("Live controller step counter · unreferenced coordinate, not a border height.")
            panel.live_z_note.setVisible(not panel.live_z_note.isVisible())
            panel.calibration.setText("Configured laser offset: +4.545 mm · compatibility not checked")
            panel.next_step.setText("Waiting for the current operation to finish.")
            panel.reference.setEnabled(False)
            settle(app)
            assert panel.reference.mapTo(area.viewport(), QtCore.QPoint()) == before
    finally:
        area.close()
        area.deleteLater()


def test_explicit_reveal_releases_layout_anchor(form):
    app, area, buttons = form
    area.ensureWidgetVisible(buttons[8])
    QtTest.QTest.mouseClick(buttons[8], QtCore.Qt.MouseButton.LeftButton)
    area.ensureWidgetVisible(buttons[20])
    after = area.verticalScrollBar().value()
    buttons[0].setFixedHeight(100)
    settle(app)
    assert area.verticalScrollBar().value() == after


def test_deleted_anchor_is_safe(form):
    app, area, buttons = form
    area.ensureWidgetVisible(buttons[8])
    QtTest.QTest.mouseClick(buttons[8], QtCore.Qt.MouseButton.LeftButton)
    buttons[8].deleteLater()
    QtCore.QCoreApplication.sendPostedEvents(None, QtCore.QEvent.Type.DeferredDelete)
    buttons[0].setFixedHeight(100)
    settle(app)


@pytest.mark.parametrize("navigation", ["wheel", "scrollbar", "tab", "direct"])
def test_user_navigation_releases_clicked_anchor(form, navigation):
    app, area, buttons = form
    area.ensureWidgetVisible(buttons[8])
    calls = []
    buttons[8].clicked.connect(lambda: calls.append("clicked"))
    QtTest.QTest.mouseClick(buttons[8], QtCore.Qt.MouseButton.LeftButton)
    if navigation == "wheel":
        event = QtGui.QWheelEvent(
            QtCore.QPointF(20, 20), QtCore.QPointF(20, 20), QtCore.QPoint(),
            QtCore.QPoint(0, -120), QtCore.Qt.MouseButton.NoButton,
            QtCore.Qt.KeyboardModifier.NoModifier, QtCore.Qt.ScrollPhase.NoScrollPhase, False,
        )
        app.sendEvent(area.viewport(), event)
    elif navigation == "scrollbar":
        area.verticalScrollBar().triggerAction(QtWidgets.QAbstractSlider.SliderAction.SliderPageStepAdd)
    elif navigation == "tab":
        QtTest.QTest.keyClick(buttons[8], QtCore.Qt.Key.Key_Tab)
    else:
        area.verticalScrollBar().setValue(100)
    settle(app)
    before = area.verticalScrollBar().value()
    buttons[0].setFixedHeight(100)
    settle(app)
    assert area.verticalScrollBar().value() == before
    assert calls == ["clicked"]
