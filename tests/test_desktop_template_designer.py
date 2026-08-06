from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.template_designer import GridTemplateDesignerDialog
from laser_aligner.desktop.template_panel import TemplatePanel


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _spec(**changes: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "name": "Shipping labels",
        "description": "Four by three grid",
        "rows": 3,
        "columns": 4,
        "width_mm": 20.0,
        "height_mm": 10.0,
        "corner_radius_mm": 2.0,
        "spacing_mode": "gap",
        "horizontal_spacing_mm": 3.0,
        "vertical_spacing_mm": 4.0,
    }
    payload.update(changes)
    return payload


def test_dialog_calculates_live_grid_footprint_and_preview(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = GridTemplateDesignerDialog(initial_spec=_spec())

    payload = dialog.spec()

    assert payload["cut_count"] == 12
    assert payload["horizontal_pitch_mm"] == pytest.approx(23.0)
    assert payload["vertical_pitch_mm"] == pytest.approx(14.0)
    assert payload["footprint_width_mm"] == pytest.approx(89.0)
    assert payload["footprint_height_mm"] == pytest.approx(38.0)
    assert "12 cuts" in dialog.footprint_status.text()
    assert len(dialog.preview.scene().items()) == 2
    assert dialog.save_button.isEnabled()
    assert dialog.add_project_button.isEnabled()
    dialog.close()
    dialog.deleteLater()


def test_spacing_mode_switch_preserves_layout_and_dimension_changes_keep_gap(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = GridTemplateDesignerDialog(initial_spec=_spec())
    before = dialog.spec()

    dialog.spacing_mode_combo.setCurrentIndex(
        dialog.spacing_mode_combo.findData("pitch")
    )
    pitch = dialog.spec()
    assert pitch["horizontal_spacing_mm"] == pytest.approx(23.0)
    assert pitch["vertical_spacing_mm"] == pytest.approx(14.0)
    assert pitch["footprint_width_mm"] == pytest.approx(
        before["footprint_width_mm"]
    )

    dialog.spacing_mode_combo.setCurrentIndex(
        dialog.spacing_mode_combo.findData("gap")
    )
    dialog.width_spin.setValue(25.0)
    resized = dialog.spec()
    assert resized["horizontal_gap_mm"] == pytest.approx(3.0)
    assert resized["horizontal_pitch_mm"] == pytest.approx(28.0)
    dialog.close()
    dialog.deleteLater()


def test_radius_is_capped_and_small_grid_warns_without_blocking(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = GridTemplateDesignerDialog(
        initial_spec=_spec(
            rows=1,
            columns=2,
            width_mm=8.0,
            height_mm=6.0,
            corner_radius_mm=100.0,
        )
    )

    assert dialog.corner_radius_spin.maximum() == pytest.approx(3.0)
    assert dialog.spec()["corner_radius_mm"] == pytest.approx(3.0)
    assert "requires at least 3 cuts" in dialog.validation_label.text()
    assert dialog.save_button.isEnabled()
    assert dialog.add_project_button.isEnabled()
    dialog.close()
    dialog.deleteLater()


def test_name_and_work_area_validation_block_both_actions(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = GridTemplateDesignerDialog(
        initial_spec=_spec(name="", columns=10),
        max_width_mm=100.0,
        max_height_mm=100.0,
    )

    assert not dialog.save_button.isEnabled()
    assert not dialog.add_project_button.isEnabled()
    assert "template name" in dialog.validation_label.text()

    dialog.name_edit.setText("Too wide")
    assert not dialog.save_button.isEnabled()
    assert "does not fit" in dialog.validation_label.text()
    assert "exceeds" in dialog.validation_label.text()
    dialog.close()
    dialog.deleteLater()


def test_exact_fit_tolerates_float_noise_but_real_overflow_is_blocked(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = GridTemplateDesignerDialog(
        initial_spec=_spec(
            rows=1,
            columns=3,
            width_mm=66.668,
            height_mm=10.0,
            horizontal_spacing_mm=9.998,
            vertical_spacing_mm=0.0,
        ),
        max_width_mm=220.0,
        max_height_mm=220.0,
    )

    assert dialog.spec()["footprint_width_mm"] == pytest.approx(220.0)
    assert dialog.save_button.isEnabled()
    assert dialog.add_project_button.isEnabled()

    dialog.horizontal_spacing_spin.setValue(9.999)
    assert not dialog.save_button.isEnabled()
    assert not dialog.add_project_button.isEnabled()
    assert "does not fit" in dialog.validation_label.text()
    dialog.close()
    dialog.deleteLater()


def test_grid_object_limit_is_rejected_inline(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = GridTemplateDesignerDialog(
        initial_spec=_spec(rows=1, columns=501),
    )

    assert not dialog.save_button.isEnabled()
    assert not dialog.add_project_button.isEnabled()
    assert "501 cuts" in dialog.validation_label.text()
    assert "maximum is 500" in dialog.validation_label.text()
    dialog.close()
    dialog.deleteLater()


def test_modal_actions_expose_payload_and_selected_action(
    qt_application: QtWidgets.QApplication,
) -> None:
    save_dialog = GridTemplateDesignerDialog(
        initial_spec={**_spec(), "template_id": "template-123"},
        editing=True,
    )
    saved: list[dict[str, object]] = []
    save_dialog.saveRequested.connect(saved.append)

    assert save_dialog.save_button.text() == "Update template"
    save_dialog.save_button.click()

    assert save_dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert save_dialog.selected_action() == "save"
    assert saved[0]["template_id"] == "template-123"
    save_dialog.deleteLater()

    project_dialog = GridTemplateDesignerDialog(initial_spec=_spec())
    added: list[dict[str, object]] = []
    project_dialog.addToProjectRequested.connect(added.append)
    project_dialog.add_project_button.click()

    assert project_dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert project_dialog.selected_action() == "project"
    assert added[0]["cut_count"] == 12
    project_dialog.deleteLater()


def test_failed_submission_keeps_values_visible_and_allows_retry(
    qt_application: QtWidgets.QApplication,
) -> None:
    attempts: list[tuple[str, dict[str, object]]] = []

    def submit(action: str, payload: dict[str, object]) -> None:
        attempts.append((action, payload))
        if len(attempts) == 1:
            raise OSError("template folder is read-only")

    dialog = GridTemplateDesignerDialog(
        initial_spec=_spec(name="Keep my values"),
        submit_handler=submit,
    )
    dialog.show()
    qt_application.processEvents()

    dialog.save_button.click()

    assert dialog.isVisible()
    assert dialog.selected_action() is None
    assert dialog.name_edit.text() == "Keep my values"
    assert "template folder is read-only" in dialog.validation_label.text()
    assert "still here" in dialog.validation_label.text()

    dialog.save_button.click()
    assert len(attempts) == 2
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted
    assert dialog.selected_action() == "save"
    dialog.deleteLater()


def test_cancel_clears_selected_action(
    qt_application: QtWidgets.QApplication,
) -> None:
    dialog = GridTemplateDesignerDialog(initial_spec=_spec())
    dialog.reject()

    assert dialog.result() == QtWidgets.QDialog.DialogCode.Rejected
    assert dialog.selected_action() is None
    dialog.deleteLater()


def test_template_panel_exposes_grid_authoring_actions_only_when_editable(
    qt_application: QtWidgets.QApplication,
) -> None:
    panel = TemplatePanel()
    panel.set_templates(
        [
            {
                "id": "custom",
                "name": "Custom artwork",
                "description": "Imported paths",
                "feature_count": 4,
                "width_mm": 80.0,
                "height_mm": 40.0,
                "grid_editable": False,
            },
            {
                "id": "grid",
                "name": "Grid labels",
                "description": "Parameter grid",
                "feature_count": 12,
                "width_mm": 90.0,
                "height_mm": 60.0,
                "grid_editable": True,
            },
        ],
        selected_id="custom",
    )
    new_requests: list[bool] = []
    edit_requests: list[str] = []
    project_requests: list[bool] = []
    panel.newGridRequested.connect(lambda: new_requests.append(True))
    panel.editGridRequested.connect(edit_requests.append)
    panel.saveRequested.connect(lambda: project_requests.append(True))

    assert panel.save_button.text() == "From current project…"
    assert not panel.edit_grid_button.isEnabled()
    assert "no editable grid parameters" in panel.edit_grid_button.toolTip()
    panel.new_grid_button.click()
    panel.save_button.click()

    panel.template_combo.setCurrentIndex(panel.template_combo.findData("grid"))
    assert panel.edit_grid_button.isEnabled()
    panel.edit_grid_button.click()

    assert new_requests == [True]
    assert project_requests == [True]
    assert edit_requests == ["grid"]

    panel.set_busy(True)
    assert not panel.new_grid_button.isEnabled()
    assert not panel.edit_grid_button.isEnabled()
    assert not panel.save_button.isEnabled()
    panel.close()
    panel.deleteLater()
