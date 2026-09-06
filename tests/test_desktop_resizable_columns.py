from __future__ import annotations

# ruff: noqa: E402, I001

import ast
import json
import os
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop interaction tests")

from PySide6 import QtCore, QtTest, QtWidgets

from laser_aligner.core import CoreRuntime
from laser_aligner.desktop.coordinate_audit import CoordinateAuditPanel
from laser_aligner.desktop.import_review import ImportReviewDialog
from laser_aligner.desktop.job_preflight import JobPreflightView
from laser_aligner.desktop.job_preview import JobPreviewDialog
from laser_aligner.desktop.machine_setup import MachineSetupDialog
from laser_aligner.desktop.panels import LayerPanel, ObjectPanel, TracePanel
from laser_aligner.desktop.theme import DARK_STYLESHEET
from laser_aligner.gcode.job_plan import build_job_plan
from laser_aligner.project import ImportScanManifest, ProjectDocument
from laser_aligner.project.job_preflight import JobPreflightReport


_VIEW_INVENTORY = {
    ("coordinate_audit.py", "self.tree"),
    ("import_review.py", "self.layer_table"),
    ("job_preflight.py", "self.findings_tree"),
    ("job_preview.py", "tree"),
    ("panels.py", "self.layer_list"),
    ("panels.py", "self.result_tree"),
    ("panels.py", "self.tree"),
    ("machine_setup.py", "self.lens_captures"),
    ("machine_setup.py", "self.lens_view_errors"),
    ("machine_setup.py", "self.points"),
    ("machine_setup.py", "self.registration_results"),
    ("machine_setup.py", "self.validation_results"),
}


@pytest.fixture(scope="module")
def qt_application():
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def test_column_view_inventory_covers_all_desktop_tables_and_trees() -> None:
    """New column panels must join the actual mouse-interaction coverage below."""
    source_root = Path(__file__).resolve().parents[1] / "laser_aligner" / "desktop"
    discovered = set()
    for path in source_root.glob("*.py"):
        module = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(module):
            if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.Call):
                continue
            factory = ast.unparse(node.value.func)
            if factory in {
                "QtWidgets.QTreeWidget", "QtWidgets.QTableWidget",
                "QtWidgets.QTreeView", "QtWidgets.QTableView", "_LayerOperationsTree",
            }:
                discovered.add((path.name, ast.unparse(node.targets[0])))
    assert discovered == _VIEW_INVENTORY


@pytest.fixture
def column_views(qt_application, tmp_path: Path):
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "config" / "default.json").read_text(encoding="utf-8"))
    payload["app"]["data_dir"] = str(tmp_path / "data")
    payload["app"]["open_browser"] = False
    payload["camera"]["autostart"] = False
    payload["machine"]["port"] = "COM_COLUMN_TEST"
    config = tmp_path / "config.json"
    config.write_text(json.dumps(payload), encoding="utf-8")
    runtime = CoreRuntime.from_config(config, hardware_enabled=False)
    setup = MachineSetupDialog(runtime, navigation_only=True)
    layers = LayerPanel()
    objects = ObjectPanel()
    trace = TracePanel()
    audit = CoordinateAuditPanel(QtWidgets.QWidget())
    preflight = JobPreflightView(JobPreflightReport())
    imports = ImportReviewDialog(ImportScanManifest("svg", "outline.svg", ".svg", 12))
    plan = build_job_plan(
        "G21\nG90\nM5\nG0 X5 Y5 F1000\nM4 S100\nG1 X10 Y10 F600\nM5\n",
        power_max=1000,
    )
    preview = JobPreviewDialog(plan, (0, 100, 0, 100), "columns.gcode")
    views = {
        "Cuts / Layers": layers.layer_list,
        "Objects": objects.tree,
        "Detected outlines": trace.result_tree,
        "Coordinate audit": audit.tree,
        "Preflight findings": preflight.findings_tree,
        "Import review": imports.layer_table,
        "Preview operations": preview.layer_tree,
        "Lens captures": setup.lens_captures,
        "Lens errors": setup.lens_view_errors,
        "Bed points": setup.points,
        "Registration": setup.registration_results,
        "Validation": setup.validation_results,
    }
    assert len(views) == len(_VIEW_INVENTORY)
    yield views
    for view in views.values():
        view.close()
        view.deleteLater()
    for widget in (setup, layers, objects, trace, audit, preflight, imports, preview):
        widget.close()
        widget.deleteLater()
    runtime.stop()
    qt_application.processEvents()


def _header(view):
    return view.horizontalHeader() if isinstance(view, QtWidgets.QTableView) else view.header()


def _drag_divider(header, column: int, delta: int) -> None:
    position = QtCore.QPoint(
        header.sectionViewportPosition(column) + header.sectionSize(column) - 1,
        header.height() // 2,
    )
    QtTest.QTest.mousePress(header.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=position)
    QtTest.QTest.mouseMove(header.viewport(), position + QtCore.QPoint(delta, 0), delay=10)
    QtTest.QTest.mouseRelease(
        header.viewport(), QtCore.Qt.MouseButton.LeftButton,
        pos=position + QtCore.QPoint(delta, 0),
    )


def test_every_column_in_every_panel_can_be_dragged_including_the_last(
    qt_application, column_views,
) -> None:
    for name, view in column_views.items():
        view.setParent(None)
        # Empty setup fixtures disable measurement editing until calibration
        # exists. Exercise header interaction in the table's enabled state.
        view.setEnabled(True)
        view.setStyleSheet(DARK_STYLESHEET)
        header = _header(view)
        view.resize(header.length() + 150, 220)
        view.show()
        qt_application.processEvents()
        assert not header.stretchLastSection(), name
        assert view.horizontalScrollBarPolicy() == QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        for column in range(header.count()):
            assert header.sectionResizeMode(column) == QtWidgets.QHeaderView.ResizeMode.Interactive
            before = [header.sectionSize(index) for index in range(header.count())]
            _drag_divider(header, column, 24)
            qt_application.processEvents()
            expected = before.copy()
            expected[column] += 24
            assert [header.sectionSize(index) for index in range(header.count())] == expected, (
                name, column,
            )
        view.hide()


@pytest.mark.parametrize("table", [False, True], ids=["tree", "table"])
def test_divider_double_click_fits_contents_then_remains_manually_resizable(
    qt_application, table: bool,
) -> None:
    from laser_aligner.desktop.columns import configure_resizable_columns

    view = QtWidgets.QTableWidget(1, 2) if table else QtWidgets.QTreeWidget()
    value = "A long operation title that requires more room than the default width"
    if table:
        view.setHorizontalHeaderLabels(("Name", "Details"))
        view.setItem(0, 1, QtWidgets.QTableWidgetItem(value))
    else:
        view.setColumnCount(2)
        view.setHeaderLabels(("Name", "Details"))
        view.addTopLevelItem(QtWidgets.QTreeWidgetItem(("Example", value)))
    configure_resizable_columns(view, (120, 120))
    view.resize(1000, 220)
    view.show()
    qt_application.processEvents()
    header = _header(view)
    position = QtCore.QPoint(header.sectionViewportPosition(1) + header.sectionSize(1) - 1, 10)
    QtTest.QTest.mouseDClick(header.viewport(), QtCore.Qt.MouseButton.LeftButton, pos=position)
    qt_application.processEvents()
    fitted = header.sectionSize(1)
    assert fitted > 250
    _drag_divider(header, 1, -20)
    qt_application.processEvents()
    assert header.sectionSize(1) == fitted - 20
    view.close()
    view.deleteLater()


@pytest.mark.parametrize("panel_type", [LayerPanel, ObjectPanel])
def test_document_refresh_keeps_operator_column_widths(qt_application, panel_type) -> None:
    panel = panel_type()
    document = ProjectDocument.new("Column refresh")
    panel.set_document(document)
    view = panel.layer_list if isinstance(panel, LayerPanel) else panel.tree
    header = _header(view)
    header.resizeSection(0, 370)
    header.resizeSection(header.count() - 1, 118)
    before = [header.sectionSize(index) for index in range(header.count())]
    document.add_layer(name="A newly added operation with a much longer title")
    panel.set_document(document)
    qt_application.processEvents()
    assert [header.sectionSize(index) for index in range(header.count())] == before
    panel.close()
    panel.deleteLater()
