from __future__ import annotations

import os
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from laser_aligner.config import ZAxisSettings
from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.panels import MachinePanel
from laser_aligner.desktop.qt import require_qt

_QtCore, _QtGui, QtWidgets = require_qt()


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application


def test_machine_panel_exposes_dynamic_surface_ceiling(qt_application: object) -> None:
    panel = MachinePanel()
    panel.set_z_settings(ZAxisSettings(enabled=True))
    panel.set_status(
        {
            "connected": True,
            "allow_motion": True,
            "controller_reconnect_required": False,
            "jog_ready": True,
            "armed": False,
            "backend": "serial",
            "protocol": "grbl",
            "coordinate_reference_ready": True,
            "job": {"running": False},
        },
        {
            "enabled": True,
            "connected": True,
            "state": "UNKNOWN",
            "z_known": False,
            "current_z_mm": None,
            "effective_safe_max_mm": 80.0,
        },
    )
    panel.z_reference_mode.setCurrentIndex(
        panel.z_reference_mode.findData("work_surface")
    )
    panel.z_surface_height.setText("20")
    assert panel.z_effective_max.text() == "60.000 mm"
    assert panel.z_home_button.isEnabled()


class _FakeMessageBox:
    class Icon:
        Warning = object()

    class ButtonRole:
        RejectRole = object()
        AcceptRole = object()

    instances: list[_FakeMessageBox] = []
    choose_continue = False

    def __init__(self, _parent: object) -> None:
        self.title = ""
        self.text = ""
        self.informative = ""
        self.cancel: object | None = None
        self.proceed: object | None = None
        self.clicked: object | None = None
        self.instances.append(self)

    def setIcon(self, _icon: object) -> None:
        pass

    def setWindowTitle(self, value: str) -> None:
        self.title = value

    def setText(self, value: str) -> None:
        self.text = value

    def setInformativeText(self, value: str) -> None:
        self.informative = value

    def addButton(self, label: str, _role: object) -> object:
        button = object()
        if label == "Cancel":
            self.cancel = button
        else:
            assert label == "Gantry Is Clear — Continue"
            self.proceed = button
        return button

    def setDefaultButton(self, _button: object) -> None:
        pass

    def setEscapeButton(self, _button: object) -> None:
        pass

    def exec(self) -> None:
        self.clicked = self.proceed if self.choose_continue else self.cancel

    def clickedButton(self) -> object | None:
        return self.clicked


def _fake_window(*, known: bool = False) -> SimpleNamespace:
    calls: list[tuple[dict[str, object], bool]] = []
    z_axis = SimpleNamespace(status=lambda: {"z_known": known})
    runtime = SimpleNamespace(
        context=SimpleNamespace(z_axis=z_axis),
        settings=SimpleNamespace(
            machine=SimpleNamespace(z_axis=ZAxisSettings(enabled=True))
        ),
    )
    controller = SimpleNamespace(
        home_z=lambda request, *, confirmed_unknown: calls.append(
            (request, confirmed_unknown)
        )
    )
    return SimpleNamespace(runtime=runtime, controller=controller, calls=calls)


def test_canceling_unknown_z_warning_dispatches_no_hardware_work(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeMessageBox.instances.clear()
    _FakeMessageBox.choose_continue = False
    monkeypatch.setattr(
        main_window_module,
        "QtWidgets",
        SimpleNamespace(QMessageBox=_FakeMessageBox),
    )
    window = _fake_window()
    E3MainWindow._request_z_home(window, {"reference_mode": "fixed_edge"})
    assert window.calls == []
    prompt = _FakeMessageBox.instances[-1]
    assert prompt.title == "Z Position Unknown"
    assert "at least 10 mm" in prompt.informative


def test_confirming_unknown_z_warning_passes_explicit_authority(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeMessageBox.instances.clear()
    _FakeMessageBox.choose_continue = True
    monkeypatch.setattr(
        main_window_module,
        "QtWidgets",
        SimpleNamespace(QMessageBox=_FakeMessageBox),
    )
    window = _fake_window()
    request = {"reference_mode": "fixed_edge"}
    E3MainWindow._request_z_home(window, request)
    assert window.calls == [(request, True)]


def test_known_z_snapshot_does_not_grant_unknown_clearance_authority() -> None:
    window = _fake_window(known=True)
    request = {"reference_mode": "fixed_edge"}

    E3MainWindow._request_z_home(window, request)

    assert window.calls == [(request, False)]
