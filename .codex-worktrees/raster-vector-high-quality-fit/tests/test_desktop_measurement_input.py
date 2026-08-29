from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.controls import MeasurementSpinBox
from laser_aligner.desktop.panels import MachinePanel


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


@pytest.mark.parametrize(
    ("kind", "suffix", "entered", "expected_mm"),
    [
        ("length", " mm", "1 in mm", 25.4),
        ("length", " mm", "25.4 mm", 25.4),
        ("area", " mm²", "1 in² mm²", 645.16),
        ("speed", " mm/min", "40 in/min mm/min", 1016.0),
    ],
)
def test_measurement_spin_accepts_either_unit_without_changing_canonical_storage(
    qt_application: QtWidgets.QApplication,
    kind: str,
    suffix: str,
    entered: str,
    expected_mm: float,
) -> None:
    spin = MeasurementSpinBox(kind)
    spin.setRange(-100_000.0, 1_000_000.0)
    spin.setSuffix(suffix)
    spin.lineEdit().setText(entered)
    spin.interpretText()

    assert spin.value() == pytest.approx(expected_mm)


def test_display_storage_converts_explicit_mm_while_showing_inches(
    qt_application: QtWidgets.QApplication,
) -> None:
    spin = MeasurementSpinBox(storage="display")
    spin.setRange(-1000.0, 1000.0)
    spin.setDisplayUnit("in")
    spin.setSuffix(" in")
    spin.lineEdit().setText("25.4 mm in")
    spin.interpretText()

    assert spin.value() == pytest.approx(1.0)


def test_jog_step_accepts_inches_and_emits_canonical_mm(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = MachinePanel()
    requests: list[tuple[float, float, float]] = []
    panel.jogRequested.connect(
        lambda x, y, feed: requests.append((x, y, feed))
    )
    panel.jog_step.setEditText("1 in")
    panel._jog(1.0, 0.0)

    assert requests == [pytest.approx((25.4, 0.0, 2000.0))]


def test_invalid_jog_step_never_emits_motion_request(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = MachinePanel()
    requests: list[tuple[float, float, float]] = []
    panel.jogRequested.connect(
        lambda x, y, feed: requests.append((x, y, feed))
    )
    panel.jog_step.setEditText("one inch")
    panel._jog(1.0, 0.0)

    assert requests == []
    assert "Jog step must be" in panel.state_label.text()
