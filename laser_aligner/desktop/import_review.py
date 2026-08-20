from __future__ import annotations

from ..project import DEFAULT_IMPORTER_REGISTRY, ImportScanManifest
from .qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()


def _format_bytes(value: int) -> str:
    exact = f"{value:,} bytes"
    if value < 1024:
        return exact
    units = ("KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        amount /= 1024.0
        if amount < 1024.0 or unit == units[-1]:
            return f"{exact} ({amount:.1f} {unit})"
    return exact


def _capability_label(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).replace("_", " ").strip().title()


class ImportReviewDialog(QtWidgets.QDialog):
    """Read-only approval gate for a bounded foreign-file scan manifest."""

    def __init__(
        self,
        manifest: ImportScanManifest,
        parent: QtWidgets.QWidget | None = None,
        *,
        importer_display_name: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.manifest = manifest
        spec = DEFAULT_IMPORTER_REGISTRY.get(manifest.importer_id)
        self.importer_display_name = (
            str(importer_display_name).strip()
            if importer_display_name is not None
            else (spec.display_name if spec is not None else manifest.importer_id)
        )
        if not self.importer_display_name:
            self.importer_display_name = manifest.importer_id

        self.setWindowTitle(f"Review {self.importer_display_name} import")
        self.setWindowModality(QtCore.Qt.WindowModality.WindowModal)
        self.setModal(True)
        self.setMinimumSize(700, 560)
        self.resize(780, 680)
        self.setSizeGripEnabled(True)

        layout = QtWidgets.QVBoxLayout(self)

        heading = QtWidgets.QLabel("Pre-import review")
        heading.setObjectName("panelHeading")
        layout.addWidget(heading)

        explanation = QtWidgets.QLabel(
            "Review the bounded scan below. Nothing is added to the project "
            "unless you explicitly choose Import and the strict importer succeeds."
        )
        explanation.setObjectName("mutedLabel")
        explanation.setWordWrap(True)
        explanation.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(explanation)

        self.status_label = QtWidgets.QLabel()
        self.status_label.setWordWrap(True)
        if not manifest.ready_for_parse:
            self.status_label.setObjectName("statusBad")
            self.status_label.setText(
                "Import blocked — unsupported features or errors must be resolved "
                "in the source file before strict import can start."
            )
        elif manifest.warnings or manifest.approximations:
            self.status_label.setObjectName("statusWarning")
            self.status_label.setText(
                "Review required — warnings or approximations were discovered. "
                "Choose Import only if you approve the reported conversion."
            )
        else:
            self.status_label.setObjectName("statusGood")
            self.status_label.setText(
                "Ready for approval — choose Import to run the strict importer."
            )
        layout.addWidget(self.status_label)

        self.content_scroll = QtWidgets.QScrollArea()
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setFrameShape(QtWidgets.QFrame.Shape.NoFrame)
        content = QtWidgets.QWidget()
        content_layout = QtWidgets.QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)

        content_layout.addWidget(self._build_source_group())
        content_layout.addWidget(self._build_layers_group())

        self.section_text: dict[str, QtWidgets.QPlainTextEdit] = {}
        for key, title, values in (
            ("source_facts", "Source facts", manifest.source_facts),
            ("coordinate_facts", "Coordinate facts", manifest.coordinate_facts),
            ("warnings", "Warnings", manifest.warnings),
            ("approximations", "Approximations", manifest.approximations),
            (
                "unsupported_features",
                "Unsupported features",
                manifest.unsupported_features,
            ),
            ("errors", "Errors", manifest.errors),
        ):
            group, text = self._build_text_group(title, values)
            self.section_text[key] = text
            content_layout.addWidget(group)

        content_layout.addStretch(1)
        self.content_scroll.setWidget(content)
        layout.addWidget(self.content_scroll, 1)

        authority_note = QtWidgets.QLabel(
            "The existing strict importer remains authoritative and may still "
            "reject details that the bounded scan did not fully parse."
        )
        authority_note.setObjectName("mutedLabel")
        authority_note.setWordWrap(True)
        authority_note.setTextInteractionFlags(
            QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
        )
        layout.addWidget(authority_note)

        self.button_box = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.cancel_button = self.button_box.button(
            QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.import_button = self.button_box.addButton(
            "Import",
            QtWidgets.QDialogButtonBox.ButtonRole.AcceptRole,
        )
        self.import_button.setObjectName("primaryActionButton")
        self.import_button.setEnabled(manifest.ready_for_parse)
        self.import_button.setDefault(manifest.ready_for_parse)
        self.import_button.setAutoDefault(manifest.ready_for_parse)
        if not manifest.ready_for_parse:
            self.import_button.setToolTip(
                "Import is unavailable while the scan reports unsupported "
                "features or errors."
            )
            self.cancel_button.setDefault(True)
        self.button_box.accepted.connect(self.accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

    def _build_source_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Source information")
        form = QtWidgets.QFormLayout(group)
        form.setFieldGrowthPolicy(
            QtWidgets.QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
        )

        capabilities = sorted(
            (_capability_label(value) for value in self.manifest.capabilities),
            key=str.casefold,
        )
        natural_size = self.manifest.natural_size_mm
        rows = (
            (
                "Importer",
                f"{self.importer_display_name} ({self.manifest.importer_id})",
            ),
            ("File", self.manifest.source_name),
            ("Extension", self.manifest.source_suffix),
            ("File size", _format_bytes(self.manifest.source_size_bytes)),
            ("Format version", self.manifest.format_version or "Not reported"),
            (
                "Natural width",
                "Not reported"
                if natural_size is None
                else f"{natural_size[0]:g} mm",
            ),
            (
                "Natural height",
                "Not reported"
                if natural_size is None
                else f"{natural_size[1]:g} mm",
            ),
            ("Capabilities", ", ".join(capabilities) or "None declared"),
        )
        for label, value in rows:
            value_label = QtWidgets.QLabel(value)
            value_label.setWordWrap(True)
            value_label.setTextInteractionFlags(
                QtCore.Qt.TextInteractionFlag.TextSelectableByMouse
            )
            form.addRow(f"{label}:", value_label)
        return group

    def _build_layers_group(self) -> QtWidgets.QGroupBox:
        group = QtWidgets.QGroupBox("Discovered layers / operations")
        layout = QtWidgets.QVBoxLayout(group)
        self.layer_table = QtWidgets.QTableWidget()
        self.layer_table.setObjectName("importReviewLayerTable")
        self.layer_table.setColumnCount(4)
        self.layer_table.setHorizontalHeaderLabels(
            ("Source key", "Name", "Mode", "Objects")
        )
        self.layer_table.setEditTriggers(
            QtWidgets.QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.layer_table.setSelectionMode(
            QtWidgets.QAbstractItemView.SelectionMode.NoSelection
        )
        self.layer_table.setAlternatingRowColors(True)
        self.layer_table.verticalHeader().setVisible(False)

        layers = self.manifest.layers
        self.layer_table.setRowCount(max(1, len(layers)))
        if layers:
            for row, layer in enumerate(layers):
                values = (
                    layer.source_key,
                    layer.name,
                    layer.mode_hint or "Not reported",
                    "Not reported"
                    if layer.object_count is None
                    else f"{layer.object_count:,}",
                )
                for column, value in enumerate(values):
                    item = QtWidgets.QTableWidgetItem(value)
                    item.setToolTip(value)
                    self.layer_table.setItem(row, column, item)
        else:
            item = QtWidgets.QTableWidgetItem("No layers or operations discovered")
            self.layer_table.setItem(0, 0, item)
            self.layer_table.setSpan(0, 0, 1, 4)

        header = self.layer_table.horizontalHeader()
        header.setSectionResizeMode(
            0,
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents,
        )
        header.setSectionResizeMode(1, QtWidgets.QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(
            2,
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents,
        )
        header.setSectionResizeMode(
            3,
            QtWidgets.QHeaderView.ResizeMode.ResizeToContents,
        )
        visible_rows = min(max(1, len(layers)), 5)
        table_height = (
            self.layer_table.horizontalHeader().sizeHint().height()
            + visible_rows * self.layer_table.verticalHeader().defaultSectionSize()
            + self.layer_table.frameWidth() * 2
        )
        self.layer_table.setMinimumHeight(min(180, table_height))
        self.layer_table.setMaximumHeight(max(90, min(210, table_height)))
        layout.addWidget(self.layer_table)
        return group

    @staticmethod
    def _build_text_group(
        title: str,
        values: tuple[str, ...],
    ) -> tuple[QtWidgets.QGroupBox, QtWidgets.QPlainTextEdit]:
        group = QtWidgets.QGroupBox(title)
        layout = QtWidgets.QVBoxLayout(group)
        text = QtWidgets.QPlainTextEdit()
        text.setReadOnly(True)
        text.setUndoRedoEnabled(False)
        text.setLineWrapMode(QtWidgets.QPlainTextEdit.LineWrapMode.WidgetWidth)
        text.setPlainText(
            "None reported" if not values else "\n".join(f"• {value}" for value in values)
        )
        visible_lines = min(max(1, len(values)), 4)
        line_height = text.fontMetrics().lineSpacing()
        text.setMinimumHeight(max(46, line_height * visible_lines + 18))
        text.setMaximumHeight(max(70, min(132, line_height * visible_lines + 24)))
        layout.addWidget(text)
        return group, text

    def accept(self) -> None:
        """Accept only manifests whose bounded scan has no known blockers."""

        if not self.manifest.ready_for_parse:
            return
        super().accept()


def review_import_manifest(
    manifest: ImportScanManifest,
    parent: QtWidgets.QWidget | None = None,
) -> bool:
    """Show the shared review gate and return explicit, non-blocked approval."""

    dialog = ImportReviewDialog(manifest, parent)
    result = dialog.exec()
    return (
        result == QtWidgets.QDialog.DialogCode.Accepted
        and manifest.ready_for_parse
    )


__all__ = ["ImportReviewDialog", "review_import_manifest"]
