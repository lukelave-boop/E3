from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtWidgets

from laser_aligner.desktop.import_review import ImportReviewDialog
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
    )

    dialog = ImportReviewDialog(manifest)
    labels = _source_label_text(dialog)

    assert "LightBurn Project (lightburn)" in labels
    assert "fixture-review.lbrn2" in labels
    assert ".lbrn2" in labels
    assert "4,096 bytes (4.0 KiB)" in labels
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
