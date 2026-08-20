from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.import_review import (
    MAX_DISPLAYED_LAYER_ROWS,
    MAX_DISPLAYED_TEXT_ENTRIES,
    ImportReviewDialog,
)
from laser_aligner.project import (
    ImportCapability,
    ImportLayerManifest,
    ImportScanManifest,
)


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _source_label_text(dialog: ImportReviewDialog) -> str:
    return "\n".join(label.text() for label in dialog.findChildren(QtWidgets.QLabel))


def test_review_dialog_shows_every_manifest_field_and_blocks_import(
    qt_application: QtWidgets.QApplication,
) -> None:
    manifest = ImportScanManifest(
        importer_id="lightburn",
        source_name="fixture-review.lbrn2",
        source_suffix=".lbrn2",
        source_size_bytes=4096,
        capabilities=frozenset(
            {
                ImportCapability.VECTOR_GEOMETRY,
                ImportCapability.SOURCE_LAYERS,
            }
        ),
        format_version="1.7",
        natural_width_mm=120.5,
        natural_height_mm=80.25,
        layers=(
            ImportLayerManifest(
                source_key="cut:3",
                name="Perimeter",
                mode_hint="line",
                object_count=2,
            ),
        ),
        source_facts=("4 source shapes",),
        coordinate_facts=("Transforms will be resolved",),
        warnings=("Output remains disabled",),
        approximations=("Bezier curves will be flattened",),
        unsupported_features=("Embedded bitmap is unsupported",),
        errors=("Malformed source value",),
        source_sha256="ab" * 32,
    )

    dialog = ImportReviewDialog(manifest)
    labels = _source_label_text(dialog)

    assert "LightBurn Project (lightburn)" in labels
    assert "fixture-review.lbrn2" in labels
    assert ".lbrn2" in labels
    assert "4,096 bytes (4.0 KiB)" in labels
    assert "ab" * 32 in labels
    assert "1.7" in labels
    assert "120.5 mm" in labels
    assert "80.25 mm" in labels
    assert "Source Layers" in labels
    assert "Vector Geometry" in labels

    assert dialog.layer_table.rowCount() == 1
    assert [dialog.layer_table.item(0, column).text() for column in range(4)] == [
        "cut:3",
        "Perimeter",
        "line",
        "2",
    ]
    assert "4 source shapes" in dialog.section_text["source_facts"].toPlainText()
    assert (
        "Transforms will be resolved"
        in dialog.section_text["coordinate_facts"].toPlainText()
    )
    assert "Output remains disabled" in dialog.section_text["warnings"].toPlainText()
    assert (
        "Bezier curves will be flattened"
        in dialog.section_text["approximations"].toPlainText()
    )
    assert (
        "Embedded bitmap is unsupported"
        in dialog.section_text["unsupported_features"].toPlainText()
    )
    assert "Malformed source value" in dialog.section_text["errors"].toPlainText()

    assert dialog.status_label.objectName() == "statusBad"
    assert not dialog.import_button.isEnabled()
    assert dialog.cancel_button.isEnabled()
    dialog.accept()
    assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted
    dialog.cancel_button.click()
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Rejected


def test_warning_only_manifest_requires_explicit_import_approval(
    qt_application: QtWidgets.QApplication,
) -> None:
    manifest = ImportScanManifest(
        importer_id="gcode",
        source_name="review.gcode",
        source_suffix=".gcode",
        source_size_bytes=120,
        warnings=("Review reconstructed power",),
        approximations=("Travel commands are omitted",),
    )
    dialog = ImportReviewDialog(manifest)

    assert dialog.status_label.objectName() == "statusWarning"
    assert dialog.import_button.isEnabled()
    assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted

    dialog.show()
    qt_application.processEvents()
    dialog.import_button.click()
    qt_application.processEvents()

    assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted


@pytest.mark.parametrize(
    "blockers",
    (
        {"unsupported_features": ("Unsupported controller feature",)},
        {"errors": ("Malformed source",)},
    ),
)
def test_each_blocker_category_independently_disables_import(
    qt_application: QtWidgets.QApplication,
    blockers: dict[str, tuple[str, ...]],
) -> None:
    manifest = ImportScanManifest(
        importer_id="gcode",
        source_name="blocked.nc",
        source_suffix=".nc",
        source_size_bytes=12,
        **blockers,
    )
    dialog = ImportReviewDialog(manifest)

    assert not dialog.import_button.isEnabled()
    dialog.import_button.click()
    dialog.accept()
    assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted

    dialog.reject()


def test_empty_manifest_sections_are_explicit(
    qt_application: QtWidgets.QApplication,
) -> None:
    manifest = ImportScanManifest(
        importer_id="gcode",
        source_name="plain.nc",
        source_suffix=".nc",
        source_size_bytes=0,
    )
    dialog = ImportReviewDialog(manifest)

    assert dialog.status_label.objectName() == "statusGood"
    assert dialog.import_button.isEnabled()
    assert dialog.layer_table.item(0, 0).text() == "No layers or operations discovered"
    assert all(
        text.toPlainText() == "None reported"
        for text in dialog.section_text.values()
    )
    labels = _source_label_text(dialog)
    assert "Format version:" in labels
    assert labels.count("Not reported") >= 3

    dialog.reject()


def test_large_manifest_rendering_is_bounded_visible_and_deterministic(
    qt_application: QtWidgets.QApplication,
) -> None:
    entry_count = 205
    layers = tuple(
        ImportLayerManifest(
            source_key=f"operation:{index:03d}",
            name=f"Operation {index:03d}",
            mode_hint="line",
            object_count=index,
        )
        for index in range(entry_count)
    )
    section_values = {
        key: tuple(f"{key} entry {index:03d}" for index in range(entry_count))
        for key in (
            "source_facts",
            "coordinate_facts",
            "warnings",
            "approximations",
            "unsupported_features",
            "errors",
        )
    }
    manifest = ImportScanManifest(
        importer_id="gcode",
        source_name="large.gcode",
        source_suffix=".gcode",
        source_size_bytes=1234,
        layers=layers,
        **section_values,
    )
    original_layers = manifest.layers
    original_sections = {
        key: getattr(manifest, key) for key in section_values
    }

    dialog = ImportReviewDialog(manifest)

    assert dialog.manifest is manifest
    assert dialog.layer_table.rowCount() == MAX_DISPLAYED_LAYER_ROWS == 200
    assert dialog.layer_table.item(0, 0).text() == "operation:000"
    assert dialog.layer_table.item(199, 0).text() == "operation:199"
    assert dialog.layer_omission_label.text() == (
        "5 additional layers / operations not shown."
    )
    assert not dialog.layer_omission_label.isHidden()

    for key in section_values:
        lines = dialog.section_text[key].toPlainText().splitlines()
        assert len(lines) == MAX_DISPLAYED_TEXT_ENTRIES == 200
        assert lines[0] == f"• {key} entry 000"
        assert lines[-1] == f"• {key} entry 199"
        assert f"• {key} entry 200" not in lines
        assert dialog.section_omission_labels[key].text() == (
            "5 additional entries not shown."
        )
        assert not dialog.section_omission_labels[key].isHidden()

    assert manifest.layers is original_layers
    assert len(manifest.layers) == entry_count
    assert all(
        getattr(manifest, key) is original_sections[key]
        and len(getattr(manifest, key)) == entry_count
        for key in section_values
    )

    second_dialog = ImportReviewDialog(manifest)
    assert [
        second_dialog.layer_table.item(row, 0).text()
        for row in range(second_dialog.layer_table.rowCount())
    ] == [
        dialog.layer_table.item(row, 0).text()
        for row in range(dialog.layer_table.rowCount())
    ]
    assert {
        key: text.toPlainText() for key, text in second_dialog.section_text.items()
    } == {key: text.toPlainText() for key, text in dialog.section_text.items()}

    second_dialog.reject()
    dialog.reject()


@pytest.mark.parametrize(("entry_count", "omitted"), ((200, 0), (201, 1)))
def test_review_display_limit_boundary_reports_exact_omission_count(
    qt_application: QtWidgets.QApplication,
    entry_count: int,
    omitted: int,
) -> None:
    manifest = ImportScanManifest(
        importer_id="gcode",
        source_name="boundary.gcode",
        source_suffix=".gcode",
        source_size_bytes=100,
        layers=tuple(
            ImportLayerManifest(
                source_key=f"operation:{index}",
                name=f"Operation {index}",
            )
            for index in range(entry_count)
        ),
        warnings=tuple(f"Warning {index}" for index in range(entry_count)),
    )
    dialog = ImportReviewDialog(manifest)

    assert dialog.layer_table.rowCount() == 200
    assert dialog.section_text["warnings"].document().blockCount() == 200
    assert dialog.layer_omission_label.isHidden() is (omitted == 0)
    assert dialog.section_omission_labels["warnings"].isHidden() is (omitted == 0)
    if omitted:
        assert dialog.layer_omission_label.text().startswith(f"{omitted} additional")
        assert dialog.section_omission_labels["warnings"].text() == (
            f"{omitted} additional entries not shown."
        )

    dialog.reject()
