from __future__ import annotations

from collections.abc import Mapping
from heapq import nsmallest

from ..project.job_preflight import JobPreflightReport
from .qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()

MAX_DISPLAYED_PREFLIGHT_FINDINGS = 200
MAX_DISPLAYED_CONTEXT_ENTRIES = 200


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _severity_key(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).strip().casefold()


def _severity_label(value: object) -> str:
    return _severity_key(value).replace("_", " ").title()


def _context_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value

    if isinstance(value, Mapping):
        entries = [
            f"{key}={item}"
            for key, item in nsmallest(
                MAX_DISPLAYED_CONTEXT_ENTRIES,
                value.items(),
                key=lambda pair: (str(pair[0]).casefold(), str(pair[0])),
            )
        ]
        entry_count = len(value)
    elif isinstance(value, (set, frozenset)):
        entries = nsmallest(
            MAX_DISPLAYED_CONTEXT_ENTRIES,
            (str(item) for item in value),
            key=lambda item: (item.casefold(), item),
        )
        entry_count = len(value)
    elif isinstance(value, (tuple, list)):
        entries = [str(item) for item in value[:MAX_DISPLAYED_CONTEXT_ENTRIES]]
        entry_count = len(value)
    else:
        return str(value)

    visible = entries
    omitted = entry_count - len(visible)
    if omitted:
        visible.append(f"{omitted:,} additional context entries not shown.")
    return "; ".join(visible)


def _normalized_counts(report: JobPreflightReport) -> dict[str, int]:
    counts = report.counts
    if isinstance(counts, Mapping):
        normalized: dict[str, int] = {}
        for severity, count in counts.items():
            key = _severity_key(severity)
            if key == "warnings":
                key = "warning"
            elif key == "blockers":
                key = "blocker"
            normalized[key] = int(count)
        return normalized
    return {
        "info": int(getattr(counts, "info", 0)),
        "warning": int(getattr(counts, "warnings", 0)),
        "blocker": int(getattr(counts, "blockers", 0)),
    }


class JobPreflightView(QtWidgets.QWidget):
    """Embeddable, read-only, bounded presentation of a preflight report."""

    def __init__(
        self,
        report: JobPreflightReport,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.report = report
        self.setMinimumWidth(0)
        self.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        heading = QtWidgets.QLabel("Job preflight")
        heading.setObjectName("panelHeading")
        layout.addWidget(heading)

        explanation = QtWidgets.QLabel(
            "Review this bounded finding summary alongside the authoritative job "
            "preview. This review does not arm the laser, enable output, or start motion."
        )
        explanation.setObjectName("mutedLabel")
        explanation.setWordWrap(True)
        explanation.setMinimumWidth(0)
        explanation.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        explanation.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(explanation)

        counts = _normalized_counts(report)
        blocker_count = counts.get("blocker", 0)
        warning_count = counts.get("warning", 0)
        info_count = counts.get("info", 0)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setMinimumWidth(0)
        self.status_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        if report.has_blockers or not report.ready:
            self.status_label.setObjectName("statusBad")
            self.status_label.setText(
                "Preview blocked — resolve every blocking finding before continuing."
            )
        elif warning_count:
            self.status_label.setObjectName("statusWarning")
            self.status_label.setText(
                "Review required — warnings were found. Review them in the exact job "
                "preview before proceeding."
            )
        else:
            self.status_label.setObjectName("statusGood")
            self.status_label.setText(
                "Preflight ready — no blocking findings were reported."
            )
        layout.addWidget(self.status_label)

        self.counts_label = QtWidgets.QLabel(
            f"Blockers: {blocker_count:,}  ·  Warnings: {warning_count:,}  ·  "
            f"Info: {info_count:,}  ·  Total: {len(report.findings):,}"
        )
        self.counts_label.setObjectName("mutedLabel")
        self.counts_label.setWordWrap(True)
        self.counts_label.setMinimumWidth(0)
        self.counts_label.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.counts_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(self.counts_label)

        self.findings_tree = QtWidgets.QTreeWidget()
        self.findings_tree.setObjectName("jobPreflightFindingsTree")
        self.findings_tree.setColumnCount(6)
        self.findings_tree.setHeaderLabels(
            ("Severity", "Code", "Title", "Message", "Detail", "Context")
        )
        self.findings_tree.setRootIsDecorated(False)
        self.findings_tree.setUniformRowHeights(True)
        self.findings_tree.setAlternatingRowColors(True)
        self.findings_tree.setMinimumWidth(0)
        self.findings_tree.setSizeAdjustPolicy(
            QtWidgets.QAbstractScrollArea.SizeAdjustPolicy.AdjustIgnored
        )
        self.findings_tree.setHorizontalScrollBarPolicy(
            QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.findings_tree.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored,
            QtWidgets.QSizePolicy.Policy.Preferred,
        )
        self.findings_tree.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.findings_tree.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.ExtendedSelection
        )

        visible_findings = report.findings[:MAX_DISPLAYED_PREFLIGHT_FINDINGS]
        for finding in visible_findings:
            values = (
                _severity_label(finding.severity),
                _text(finding.code),
                _text(finding.title),
                _text(finding.message),
                _text(finding.detail),
                _context_text(finding.context),
            )
            item = QtWidgets.QTreeWidgetItem(values)
            for column, cell in enumerate(values):
                item.setToolTip(column, cell)
            self.findings_tree.addTopLevelItem(item)

        header = self.findings_tree.header()
        header.setSectionResizeMode(
            0,
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents,
        )
        header.setSectionResizeMode(
            1,
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents,
        )
        header.setSectionResizeMode(2, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(3, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(4, QtWidgets.QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(5, QtWidgets.QHeaderView.ResizeMode.Interactive)
        self.findings_tree.setColumnWidth(2, 180)
        self.findings_tree.setColumnWidth(4, 220)
        self.findings_tree.setColumnWidth(5, 220)
        layout.addWidget(self.findings_tree, 1)

        self.empty_label = QtWidgets.QLabel("No preflight findings were reported.")
        self.empty_label.setObjectName("mutedLabel")
        self.empty_label.setMinimumWidth(0)
        self.empty_label.setHidden(bool(visible_findings))
        layout.addWidget(self.empty_label)

        omitted_count = len(report.findings) - len(visible_findings)
        self.omission_label = QtWidgets.QLabel()
        self.omission_label.setObjectName("mutedLabel")
        self.omission_label.setWordWrap(True)
        self.omission_label.setMinimumWidth(0)
        self.omission_label.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.omission_label.setText(
            f"{omitted_count:,} additional preflight findings not shown."
            if omitted_count
            else ""
        )
        self.omission_label.setHidden(not omitted_count)
        layout.addWidget(self.omission_label)


class JobPreflightDialog(QtWidgets.QDialog):
    """Standalone preflight gate, primarily used to explain blocked jobs."""

    def __init__(
        self,
        report: JobPreflightReport,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.report = report

        self.setWindowTitle("Job preflight review")
        self.setMinimumSize(780, 480)
        self.resize(1040, 620)
        self.setSizeGripEnabled(True)

        layout = QtWidgets.QVBoxLayout(self)
        self.preflight_view = JobPreflightView(report, self)
        layout.addWidget(self.preflight_view, 1)

        self.findings_tree = self.preflight_view.findings_tree
        self.status_label = self.preflight_view.status_label
        self.counts_label = self.preflight_view.counts_label
        self.empty_label = self.preflight_view.empty_label
        self.omission_label = self.preflight_view.omission_label

        self.button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.cancel_button = self.button_box.button(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.continue_button = self.button_box.addButton(
            "Continue to Preview",
            QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.continue_button.setObjectName("primaryActionButton")
        self.continue_button.setEnabled(report.ready)
        self.continue_button.setDefault(report.ready)
        self.continue_button.setAutoDefault(report.ready)
        if not report.ready:
            self.continue_button.setToolTip(
                "Preview is unavailable while preflight reports blocking findings."
            )
            self.cancel_button.setDefault(True)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def accept(self) -> None:
        """Accept only reports that passed the core preflight gate."""

        if not self.report.ready:
            return
        super().accept()


__all__ = [
    "MAX_DISPLAYED_CONTEXT_ENTRIES",
    "MAX_DISPLAYED_PREFLIGHT_FINDINGS",
    "JobPreflightDialog",
    "JobPreflightView",
]
