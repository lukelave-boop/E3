import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6 import QtCore, QtTest, QtWidgets  # noqa: E402

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
