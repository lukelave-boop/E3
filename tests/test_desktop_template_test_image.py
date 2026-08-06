from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtWidgets

from laser_aligner.config import WorkArea
from laser_aligner.desktop.template_test_image import (
    TEST_IMAGE_SEED,
    TemplateTestImageDialog,
)
from laser_aligner.desktop.theme import DARK_STYLESHEET


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def test_test_image_dialog_exposes_known_pose_and_deterministic_conditions(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = TemplateTestImageDialog(
        "Alpha label sheet",
        12,
        WorkArea(x_min=10.0, x_max=210.0, y_min=20.0, y_max=180.0),
        initial_center=(84.25, 91.5),
        initial_rotation_deg=7.5,
    )

    assert dialog.center_x_spin.minimum() == pytest.approx(10.0)
    assert dialog.center_x_spin.maximum() == pytest.approx(210.0)
    assert dialog.center_y_spin.minimum() == pytest.approx(20.0)
    assert dialog.center_y_spin.maximum() == pytest.approx(180.0)
    assert dialog.missing_spin.maximum() == 12
    assert dialog.parameters() == {
        "center_x_mm": 84.25,
        "center_y_mm": 91.5,
        "rotation_deg": 7.5,
        "noise_stddev": 0.0,
        "missing_count": 0,
        "seed": TEST_IMAGE_SEED,
    }

    dialog.noise_spin.setValue(8.5)
    dialog.missing_spin.setValue(2)
    assert dialog.parameters()["noise_stddev"] == pytest.approx(8.5)
    assert dialog.parameters()["missing_count"] == 2
    assert "10/12 labels" in dialog.condition_summary.text()
    assert dialog.condition_summary.objectName() == "mutedLabel"

    dialog.missing_spin.setValue(10)
    assert "requires at least three" in dialog.condition_summary.text()
    assert dialog.condition_summary.objectName() == "warningLabel"

    dialog.generate_button.click()
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
    dialog.deleteLater()
    qt_application.processEvents()


def test_test_image_dialog_uses_work_area_center_and_cancel_rejects(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = TemplateTestImageDialog(
        "Centered sheet",
        4,
        WorkArea(x_min=-20.0, x_max=180.0, y_min=5.0, y_max=105.0),
    )

    assert dialog.center_x_spin.value() == pytest.approx(80.0)
    assert dialog.center_y_spin.value() == pytest.approx(55.0)
    dialog.cancel_button.click()
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Rejected
    dialog.deleteLater()
    qt_application.processEvents()


def test_test_image_dialog_keeps_values_open_for_failed_generation_then_retries(
    qt_application: QtWidgets.QApplication,
) -> None:
    attempts: list[dict[str, object]] = []

    def submit(parameters: dict[str, object]) -> None:
        attempts.append(parameters)
        if len(attempts) == 1:
            raise ValueError("the requested pose places 2 labels outside the work area")

    dialog = TemplateTestImageDialog(
        "Retry sheet",
        8,
        WorkArea(),
        submit_handler=submit,
    )
    dialog.center_x_spin.setValue(17.25)
    dialog.center_y_spin.setValue(34.5)
    dialog.rotation_spin.setValue(12.0)
    dialog.noise_spin.setValue(3.0)
    dialog.missing_spin.setValue(2)
    dialog.show()
    qt_application.processEvents()

    dialog.generate_button.click()
    qt_application.processEvents()

    assert dialog.isVisible()
    assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted
    assert dialog.validation_label.isVisible()
    assert "outside the work area" in dialog.validation_label.text()
    assert dialog.generate_button.isEnabled()
    assert dialog.parameters()["center_x_mm"] == pytest.approx(17.25)
    assert dialog.parameters()["center_y_mm"] == pytest.approx(34.5)
    assert dialog.parameters()["rotation_deg"] == pytest.approx(12.0)
    assert dialog.parameters()["noise_stddev"] == pytest.approx(3.0)
    assert dialog.parameters()["missing_count"] == 2

    dialog.center_x_spin.setValue(110.0)
    dialog.generate_button.click()

    assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert len(attempts) == 2
    assert attempts[0]["center_x_mm"] == pytest.approx(17.25)
    assert attempts[1]["center_x_mm"] == pytest.approx(110.0)
    dialog.deleteLater()
    qt_application.processEvents()


@pytest.mark.parametrize(
    ("name", "count", "area", "message"),
    [
        ("", 3, WorkArea(), "template_name"),
        ("Labels", 0, WorkArea(), "feature_count"),
        (
            "Labels",
            3,
            WorkArea(x_min=20.0, x_max=10.0, y_min=0.0, y_max=10.0),
            "positive width",
        ),
    ],
)
def test_test_image_dialog_rejects_invalid_inputs(
    name: str,
    count: int,
    area: WorkArea,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        TemplateTestImageDialog(name, count, area)


def test_test_image_dialog_fits_compact_large_text_layout(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = TemplateTestImageDialog(
        "Long warehouse label-sheet template name for layout stress",
        24,
        WorkArea(),
    )
    dialog.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 13pt; }")
    dialog.resize(360, 390)
    dialog.show()
    qt_application.processEvents()
    qt_application.processEvents()

    assert dialog.width() == 360
    assert dialog.height() == 390
    assert dialog.content_scroll.horizontalScrollBar().maximum() == 0
    assert dialog.content_scroll.verticalScrollBar().maximum() > 0
    assert dialog.content_page.width() <= dialog.content_scroll.viewport().width()
    for spin in (
        dialog.center_x_spin,
        dialog.center_y_spin,
        dialog.rotation_spin,
        dialog.noise_spin,
        dialog.missing_spin,
    ):
        assert spin.height() >= spin.fontMetrics().height() + 6
    for button in (dialog.cancel_button, dialog.generate_button):
        top_left = button.mapTo(dialog, button.rect().topLeft())
        bottom_right = button.mapTo(dialog, button.rect().bottomRight())
        assert top_left.x() >= 0
        assert bottom_right.x() < dialog.width()
        option = QtWidgets.QStyleOptionButton()
        button.initStyleOption(option)
        content = button.style().subElementRect(
            QtWidgets.QStyle.SubElement.SE_PushButtonContents,
            option,
            button,
        )
        assert content.width() >= option.fontMetrics.horizontalAdvance(button.text())
        assert content.height() >= option.fontMetrics.height()

    dialog.close()
    dialog.deleteLater()
    qt_application.processEvents()
