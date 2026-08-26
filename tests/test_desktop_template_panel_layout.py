from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.controls import PanelScrollArea
from laser_aligner.desktop.template_panel import TemplatePanel
from laser_aligner.desktop.theme import DARK_STYLESHEET


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _show_panel(
    application: QtWidgets.QApplication,
    *,
    width: int,
) -> tuple[QtWidgets.QWidget, PanelScrollArea, TemplatePanel]:
    host = QtWidgets.QWidget()
    host.setStyleSheet(DARK_STYLESHEET + "\nQWidget { font-size: 13pt; }")
    layout = QtWidgets.QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    panel = TemplatePanel()
    scroll = PanelScrollArea(panel)
    layout.addWidget(scroll)
    host.resize(width, 800)
    host.show()
    application.processEvents()
    application.processEvents()
    return host, scroll, panel


def _assert_push_button_text_fits(button: QtWidgets.QPushButton) -> None:
    option = QtWidgets.QStyleOptionButton()
    button.initStyleOption(option)
    content_rect = button.style().subElementRect(
        QtWidgets.QStyle.SubElement.SE_PushButtonContents,
        option,
        button,
    )
    assert content_rect.width() >= option.fontMetrics.horizontalAdvance(button.text())
    assert content_rect.height() >= option.fontMetrics.height()


def test_template_panel_actions_fit_360px_with_large_windows_text(
    qt_application: QtWidgets.QApplication,
) -> None:
    host, scroll, panel = _show_panel(qt_application, width=360)
    qt_application.processEvents()
    qt_application.processEvents()

    assert scroll.horizontalScrollBar().maximum() == 0
    assert panel.width() <= scroll.viewport().width()
    assert (
        panel.designer_buttons.direction()
        == QtWidgets.QBoxLayout.Direction.TopToBottom
    )
    assert [
        panel.save_button.text(),
        panel.auto_button.text(),
        panel.match_selected_button.text(),
        panel.apply_button.text(),
        panel.generate_button.text(),
        panel.clear_button.text(),
    ] == [
        "Save project",
        "Auto align",
        "Align selected",
        "Create cuts",
        "Generate",
        "Clear preview",
    ]
    panel_layout = panel.layout()
    assert panel_layout is not None
    assert (
        panel_layout.indexOf(panel.apply_button)
        < panel_layout.indexOf(panel.generate_button)
        < panel_layout.indexOf(panel.clear_button)
    )

    for button in panel.findChildren(QtWidgets.QPushButton):
        top_left = button.mapTo(scroll.viewport(), button.rect().topLeft())
        assert top_left.x() >= 0
        assert top_left.x() + button.width() <= scroll.viewport().width()
        _assert_push_button_text_fits(button)

    for button in (panel.refresh_button, panel.delete_button):
        assert button.width() >= button.fontMetrics().horizontalAdvance(button.text())

    host.close()
    host.deleteLater()


def test_template_panel_restores_full_actions_when_space_is_available(
    qt_application: QtWidgets.QApplication,
) -> None:
    host, scroll, panel = _show_panel(qt_application, width=700)
    qt_application.processEvents()
    qt_application.processEvents()

    assert scroll.horizontalScrollBar().maximum() == 0
    assert (
        panel.designer_buttons.direction()
        == QtWidgets.QBoxLayout.Direction.LeftToRight
    )
    assert panel.save_button.text() == "From current project…"
    assert panel.auto_button.text() == "Auto identify and align"
    assert panel.match_selected_button.text() == "Align selected template"
    assert panel.apply_button.text() == "Create aligned cut objects"
    assert panel.generate_button.text() == "Generate"
    assert panel.clear_button.text() == "Clear template preview"
    panel_layout = panel.layout()
    assert panel_layout is not None
    assert (
        panel_layout.indexOf(panel.apply_button)
        < panel_layout.indexOf(panel.generate_button)
        < panel_layout.indexOf(panel.clear_button)
    )

    host.close()
    host.deleteLater()


def test_template_generate_button_emits_and_preserves_explicit_action_gate(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TemplatePanel()
    generated: list[bool] = []
    panel.generateRequested.connect(lambda: generated.append(True))

    assert panel.generate_button.objectName() == "templateGenerateButton"
    assert panel.generate_button.isEnabled()
    panel.generate_button.click()
    assert generated == [True]

    panel.set_generate_enabled(False)
    assert not panel.generate_button.isEnabled()
    panel.set_busy(True)
    panel.set_busy(False)
    assert not panel.generate_button.isEnabled()
    panel.generate_button.click()
    assert generated == [True]

    panel.set_generate_enabled(True)
    assert panel.generate_button.isEnabled()
    panel.generate_button.click()
    assert generated == [True, True]

    panel.close()
    panel.deleteLater()
    qt_application.processEvents()


def test_template_combo_tooltips_expose_full_names_when_display_is_truncated(
    qt_application: QtWidgets.QApplication,
) -> None:
    host, _scroll, panel = _show_panel(qt_application, width=360)
    long_name = (
        "Warehouse inventory labels — narrow red rounded rectangles — revision 12"
    )
    panel.set_templates(
        [
            {
                "id": "long-template",
                "name": long_name,
                "feature_count": 24,
                "width_mm": 180.0,
                "height_mm": 120.0,
                "grid_editable": True,
            }
        ]
    )
    qt_application.processEvents()

    assert panel.template_combo.fontMetrics().horizontalAdvance(long_name) > (
        panel.template_combo.width()
    )
    assert panel.template_combo.toolTip() == long_name
    assert (
        panel.template_combo.itemData(0, QtCore.Qt.ItemDataRole.ToolTipRole)
        == long_name
    )

    panel.set_rankings([{"template_id": "long-template", "confidence": 0.91}])
    assert "91%" in panel.template_combo.currentText()
    assert panel.template_combo.toolTip() == long_name
    assert (
        panel.template_combo.itemData(0, QtCore.Qt.ItemDataRole.ToolTipRole)
        == long_name
    )

    host.close()
    host.deleteLater()
