from __future__ import annotations

from .qt import require_qt
from .text_geometry import (
    TextVectorOptions,
    automatic_bridge_width,
    build_vector_text_path,
)

QtCore, QtGui, QtWidgets = require_qt()


class VectorTextDialog(QtWidgets.QDialog):
    """Create ordinary outline text or connected, stencil-safe vector text."""

    def __init__(
        self,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create vector text")
        self.setModal(True)
        self.resize(560, 430)

        layout = QtWidgets.QVBoxLayout(self)
        intro = QtWidgets.QLabel(
            "Outline text cuts normal font contours. Stencil-safe text adds "
            "material bridges so enclosed centers such as O, A, R, B, and 8 "
            "remain attached after cutting."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QtWidgets.QFormLayout()
        self.text_edit = QtWidgets.QPlainTextEdit("E3")
        self.text_edit.setMaximumHeight(86)
        self.font_combo = QtWidgets.QFontComboBox()
        self.font_combo.setCurrentFont(QtGui.QFont("Arial"))
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItem("Outline cut", "outline")
        self.mode_combo.addItem("Stencil-safe cut", "stencil")
        self.height_spin = QtWidgets.QDoubleSpinBox()
        self.height_spin.setRange(2.0, 1000.0)
        self.height_spin.setDecimals(2)
        self.height_spin.setValue(25.0)
        self.height_spin.setSuffix(" mm")
        self.height_spin.setToolTip("Overall height of the complete text block.")

        bridge_row = QtWidgets.QWidget()
        bridge_layout = QtWidgets.QHBoxLayout(bridge_row)
        bridge_layout.setContentsMargins(0, 0, 0, 0)
        self.auto_bridge = QtWidgets.QCheckBox("Auto")
        self.auto_bridge.setChecked(True)
        self.bridge_spin = QtWidgets.QDoubleSpinBox()
        self.bridge_spin.setRange(0.2, 50.0)
        self.bridge_spin.setDecimals(2)
        self.bridge_spin.setSingleStep(0.25)
        self.bridge_spin.setValue(automatic_bridge_width(self.height_spin.value()))
        self.bridge_spin.setSuffix(" mm")
        self.bridge_spin.setEnabled(False)
        bridge_layout.addWidget(self.auto_bridge)
        bridge_layout.addWidget(self.bridge_spin, 1)

        form.addRow("Text", self.text_edit)
        form.addRow("Font", self.font_combo)
        form.addRow("Mode", self.mode_combo)
        form.addRow("Height", self.height_spin)
        self.bridge_label = QtWidgets.QLabel("Bridge width")
        self.bridge_label.setToolTip(
            "Width of each uncut material bridge connecting a letter counter "
            "to the surrounding stencil sheet."
        )
        form.addRow(self.bridge_label, bridge_row)
        layout.addLayout(form)

        preview_group = QtWidgets.QGroupBox("Vector preview")
        preview_layout = QtWidgets.QVBoxLayout(preview_group)
        self.preview = QtWidgets.QLabel()
        self.preview.setMinimumHeight(150)
        self.preview.setAlignment(QtCore.Qt.AlignmentFlag.AlignCenter)
        self.preview.setStyleSheet(
            "background: #10171B; border: 1px solid #42515A; border-radius: 4px;"
        )
        self.preview_status = QtWidgets.QLabel()
        self.preview_status.setWordWrap(True)
        preview_layout.addWidget(self.preview)
        preview_layout.addWidget(self.preview_status)
        layout.addWidget(preview_group, 1)

        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
            | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.buttons.accepted.connect(self._accept_if_valid)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)

        self.text_edit.textChanged.connect(self._refresh_preview)
        self.font_combo.currentFontChanged.connect(self._refresh_preview)
        self.mode_combo.currentIndexChanged.connect(self._mode_changed)
        self.height_spin.valueChanged.connect(self._height_changed)
        self.auto_bridge.toggled.connect(self._bridge_mode_changed)
        self.bridge_spin.valueChanged.connect(self._refresh_preview)
        self._mode_changed()

    def options(self) -> TextVectorOptions:
        return TextVectorOptions(
            text=self.text_edit.toPlainText(),
            font_family=self.font_combo.currentFont().family(),
            height_mm=self.height_spin.value(),
            mode=str(self.mode_combo.currentData()),
            bridge_width_mm=(
                None if self.auto_bridge.isChecked() else self.bridge_spin.value()
            ),
        )

    def _mode_changed(self, *args: object) -> None:
        del args
        stencil = self.mode_combo.currentData() == "stencil"
        self.bridge_label.setEnabled(stencil)
        self.auto_bridge.setEnabled(stencil)
        self.bridge_spin.setEnabled(stencil and not self.auto_bridge.isChecked())
        self._refresh_preview()

    def _height_changed(self, value: float) -> None:
        if self.auto_bridge.isChecked():
            blocked = self.bridge_spin.blockSignals(True)
            self.bridge_spin.setValue(automatic_bridge_width(value))
            self.bridge_spin.blockSignals(blocked)
        self._refresh_preview()

    def _bridge_mode_changed(self, checked: bool) -> None:
        stencil = self.mode_combo.currentData() == "stencil"
        self.bridge_spin.setEnabled(stencil and not checked)
        if checked:
            blocked = self.bridge_spin.blockSignals(True)
            self.bridge_spin.setValue(
                automatic_bridge_width(self.height_spin.value())
            )
            self.bridge_spin.blockSignals(blocked)
        self._refresh_preview()

    def _refresh_preview(self, *args: object) -> None:
        del args
        try:
            path, bridge_width, bridge_count = build_vector_text_path(
                self.options()
            )
        except Exception as exc:
            self.preview.clear()
            self.preview_status.setText(str(exc))
            self.buttons.button(
                QtWidgets.QDialogButtonBox.StandardButton.Ok
            ).setEnabled(False)
            return
        pixmap = QtGui.QPixmap(500, 150)
        pixmap.fill(QtGui.QColor("#10171B"))
        painter = QtGui.QPainter(pixmap)
        painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
        bounds = path.boundingRect()
        if bounds.width() > 0 and bounds.height() > 0:
            scale = min(460.0 / bounds.width(), 112.0 / bounds.height())
            transform = QtGui.QTransform()
            transform.translate(250.0, 72.0)
            transform.scale(scale, scale)
            painter.setTransform(transform)
            painter.translate(-bounds.center().x(), -bounds.center().y())
            painter.setPen(QtGui.QPen(QtGui.QColor("#6CF2DF"), 0.0))
            fill = QtGui.QColor("#39D6C4")
            fill.setAlpha(55)
            painter.setBrush(fill)
            painter.drawPath(path)
        painter.end()
        self.preview.setPixmap(pixmap)
        if self.mode_combo.currentData() == "stencil":
            self.preview_status.setText(
                f"Stencil-safe: {bridge_count} enclosed area"
                f"{'s' if bridge_count != 1 else ''} bridged at "
                f"{bridge_width:.2f} mm."
            )
        else:
            self.preview_status.setText(
                "Outline cut: enclosed centers are separate pieces. Choose "
                "Stencil-safe cut when the parent sheet must remain one piece."
            )
        self.buttons.button(
            QtWidgets.QDialogButtonBox.StandardButton.Ok
        ).setEnabled(True)

    def _accept_if_valid(self) -> None:
        try:
            build_vector_text_path(self.options())
        except Exception as exc:
            QtWidgets.QMessageBox.warning(self, "Invalid vector text", str(exc))
            return
        self.accept()
