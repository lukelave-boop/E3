from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
from collections.abc import Iterator

import pytest


os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop widget tests")

from PySide6 import QtCore, QtWidgets

from laser_aligner.desktop.job_preflight import (
    MAX_DISPLAYED_CONTEXT_ENTRIES,
    MAX_DISPLAYED_PREFLIGHT_FINDINGS,
    JobPreflightDialog,
    JobPreflightView,
)
from laser_aligner.project.job_preflight import (
    JobPreflightReport,
    PreflightFinding,
    PreflightSeverity,
)


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _finding(
    code: str,
    severity: PreflightSeverity,
    *,
    title: str | None = None,
    message: str | None = None,
    detail: str = "",
    context: dict[str, object] | None = None,
    resolution_steps: tuple[str, ...] = (),
    navigation_target: str | None = None,
    navigation_label: str | None = None,
) -> PreflightFinding:
    return PreflightFinding(
        code=code,
        severity=severity,
        title=title or f"Title {code}",
        message=message or f"Message {code}",
        detail=detail,
        context={} if context is None else context,
        resolution_steps=resolution_steps,
        navigation_target=navigation_target,
        navigation_label=navigation_label,
    )


def _row(tree: QtWidgets.QTreeWidget, index: int) -> tuple[str, ...]:
    item = tree.topLevelItem(index)
    assert item is not None
    return tuple(item.text(column) for column in range(tree.columnCount()))


def test_clean_report_view_and_modeless_dialog_are_ready(
    qt_application: QtWidgets.QApplication,
) -> None:
    report = JobPreflightReport()
    view = JobPreflightView(report)

    assert view.report is report
    assert view.status_label.objectName() == "statusGood"
    assert view.findings_tree.topLevelItemCount() == 0
    assert not view.empty_label.isHidden()
    assert view.omission_label.isHidden()
    assert view.counts_label.text() == (
        "Blockers: 0  ·  Warnings: 0  ·  Info: 0  ·  Total: 0"
    )

    dialog = JobPreflightDialog(report)
    accepted: list[bool] = []
    dialog.accepted.connect(lambda: accepted.append(True))

    assert dialog.report is report
    assert dialog.preflight_view.report is report
    assert dialog.findings_tree is dialog.preflight_view.findings_tree
    assert dialog.continue_button.text() == "Continue to Preview"
    assert dialog.continue_button.isEnabled() is report.ready is True
    assert dialog.cancel_button.isEnabled()

    dialog.show()
    qt_application.processEvents()
    dialog.continue_button.click()
    qt_application.processEvents()

    assert accepted == [True]
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Accepted

    cancelled = JobPreflightDialog(report)
    rejected: list[bool] = []
    cancelled.rejected.connect(lambda: rejected.append(True))
    cancelled.show()
    qt_application.processEvents()
    cancelled.cancel_button.click()
    qt_application.processEvents()

    assert rejected == [True]
    assert cancelled.result() == QtWidgets.QDialog.DialogCode.Rejected


def test_warning_report_is_ready_and_supported_by_ordinary_exec(
    qt_application: QtWidgets.QApplication,
) -> None:
    finding = _finding(
        "bounds.near_edge",
        PreflightSeverity.WARNING,
        title="Near work-area edge",
        message="The exact job approaches the configured work-area edge.",
    )
    report = JobPreflightReport(findings=(finding,))
    dialog = JobPreflightDialog(report)

    assert report.ready
    assert not report.has_blockers
    assert dialog.status_label.objectName() == "statusWarning"
    assert dialog.continue_button.isEnabled()
    assert dialog.counts_label.text() == (
        "Blockers: 0  ·  Warnings: 1  ·  Info: 0  ·  Total: 1"
    )
    assert _row(dialog.findings_tree, 0) == (
        "Warning",
        "bounds.near_edge",
        "Near work-area edge",
        "The exact job approaches the configured work-area edge.",
        "",
        "",
    )

    QtCore.QTimer.singleShot(0, dialog.continue_button.click)
    assert dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted


def test_blocker_disables_continue_and_cannot_be_programmatically_accepted(
    qt_application: QtWidgets.QApplication,
) -> None:
    report = JobPreflightReport(
        findings=(
            _finding(
                "bounds.outside",
                PreflightSeverity.BLOCKER,
                title="Outside work area",
                message="The exact job exceeds the configured work area.",
            ),
        )
    )
    dialog = JobPreflightDialog(report)

    assert report.has_blockers
    assert not report.ready
    assert dialog.status_label.objectName() == "statusBad"
    assert not dialog.continue_button.isEnabled()
    assert dialog.continue_button.toolTip()
    assert dialog.counts_label.text() == (
        "Blockers: 1  ·  Warnings: 0  ·  Info: 0  ·  Total: 1"
    )

    dialog.continue_button.click()
    dialog.accept()
    assert dialog.result() != QtWidgets.QDialog.DialogCode.Accepted

    dialog.cancel_button.click()
    assert dialog.result() == QtWidgets.QDialog.DialogCode.Rejected


def test_finding_detail_and_context_are_complete_and_deterministic(
    qt_application: QtWidgets.QApplication,
) -> None:
    report = JobPreflightReport(
        findings=(
            _finding(
                "asset.identity",
                PreflightSeverity.INFO,
                title="Raster identity captured",
                message="The prepared raster is bound to exact source content.",
                detail="SHA-256 will be checked again before execution.",
                context={"zeta": "last", "Alpha": 7, "enabled": True},
            ),
        )
    )
    view = JobPreflightView(report)

    assert _row(view.findings_tree, 0) == (
        "Info",
        "asset.identity",
        "Raster identity captured",
        "The prepared raster is bound to exact source content.",
        "SHA-256 will be checked again before execution.",
        "Alpha=7; enabled=True; zeta=last",
    )
    item = view.findings_tree.topLevelItem(0)
    assert item is not None
    assert item.toolTip(4) == "SHA-256 will be checked again before execution."
    assert item.toolTip(5) == "Alpha=7; enabled=True; zeta=last"


def test_actionable_finding_renders_numbered_steps_and_navigation(
    qt_application: QtWidgets.QApplication,
) -> None:
    report = JobPreflightReport(
        findings=(
            _finding(
                "honeycomb.support_not_current",
                PreflightSeverity.BLOCKER,
                title="Honeycomb frame is not current",
                message=(
                    "E3 does not have a current saved honeycomb frame for this "
                    "camera-to-machine map."
                ),
                detail="The camera-to-machine bed map changed after this frame was saved.",
                resolution_steps=(
                    "Open Tools → Machine Setup.",
                    "Select 3. Bed Mapping.",
                    "Capture a current ruler overlay.",
                    "Detect and save the honeycomb frame.",
                ),
                navigation_target="machine_setup.bed_mapping",
                navigation_label="Open Bed Mapping",
            ),
        )
    )
    dialog = JobPreflightDialog(report)
    requested: list[str] = []
    dialog.navigationRequested.connect(requested.append)

    assert not dialog.preflight_view.remediation_group.isHidden()
    assert dialog.preflight_view.resolution_steps_label.text() == (
        "1. Open Tools → Machine Setup.\n"
        "2. Select 3. Bed Mapping.\n"
        "3. Capture a current ruler overlay.\n"
        "4. Detect and save the honeycomb frame."
    )
    assert dialog.preflight_view.technical_reason_label.text() == (
        "Technical reason: The camera-to-machine bed map changed after this frame "
        "was saved."
    )
    assert dialog.preflight_view.navigation_button.text() == "Open Bed Mapping"

    dialog.preflight_view.navigation_button.click()
    qt_application.processEvents()

    assert requested == ["machine_setup.bed_mapping"]


def test_findings_and_repeated_context_rendering_are_bounded_and_deterministic(
    qt_application: QtWidgets.QApplication,
) -> None:
    finding_count = 205
    context_count = 205
    findings = tuple(
        _finding(
            f"finding.{index:03d}",
            PreflightSeverity.INFO,
            context=(
                {f"context-{context_index:03d}": context_index for context_index in range(context_count)}
                if index == 0
                else {"index": index}
            ),
        )
        for index in range(finding_count)
    )
    report = JobPreflightReport(findings=findings)
    original_findings = report.findings

    view = JobPreflightView(report)

    assert view.report is report
    assert (
        view.findings_tree.topLevelItemCount()
        == MAX_DISPLAYED_PREFLIGHT_FINDINGS
        == 200
    )
    assert _row(view.findings_tree, 0)[1] == "finding.000"
    assert _row(view.findings_tree, 199)[1] == "finding.199"
    assert view.omission_label.text() == (
        "5 additional preflight findings not shown."
    )
    assert not view.omission_label.isHidden()

    context_text = _row(view.findings_tree, 0)[5]
    context_entries = context_text.split("; ")
    assert len(context_entries) == MAX_DISPLAYED_CONTEXT_ENTRIES + 1 == 201
    assert context_entries[0] == "context-000=0"
    assert context_entries[199] == "context-199=199"
    assert context_entries[-1] == "5 additional context entries not shown."
    assert "context-200=200" not in context_entries

    assert report.findings is original_findings
    assert len(report.findings) == finding_count

    second_view = JobPreflightView(report)
    assert [
        _row(second_view.findings_tree, index)
        for index in range(second_view.findings_tree.topLevelItemCount())
    ] == [
        _row(view.findings_tree, index)
        for index in range(view.findings_tree.topLevelItemCount())
    ]
    assert second_view.omission_label.text() == view.omission_label.text()
