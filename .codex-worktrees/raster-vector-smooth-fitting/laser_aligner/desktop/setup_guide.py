from __future__ import annotations

from importlib.resources import files

from .qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()

_STEP_HEADINGS = (
    "1. Camera",
    "2. Lens",
    "3. Bed Mapping",
    "4. Fine Registration",
    "5. Accuracy Validation",
    "6. Coordinate Audit",
)


def load_setup_runbook() -> str:
    return (
        files("laser_aligner.operator_docs")
        .joinpath("PERMANENT_CAMERA_SETUP.md")
        .read_text(encoding="utf-8")
    )


class SetupGuideDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Permanent Camera Setup Guide")
        self.setWindowModality(QtCore.Qt.WindowModality.NonModal)
        self.resize(820, 760)
        layout = QtWidgets.QVBoxLayout(self)
        self.browser = QtWidgets.QTextBrowser()
        self.browser.setOpenExternalLinks(True)
        self.browser.setMarkdown(load_setup_runbook())
        layout.addWidget(self.browser)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Close
        )
        buttons.rejected.connect(self.close)
        layout.addWidget(buttons)

    def show_step(self, tab_index: int) -> None:
        index = max(0, min(int(tab_index), len(_STEP_HEADINGS) - 1))
        cursor = self.browser.document().find(_STEP_HEADINGS[index])
        if not cursor.isNull():
            self.browser.setTextCursor(cursor)
            self.browser.ensureCursorVisible()
        self.show()
        self.raise_()
        self.activateWindow()


def show_setup_guide(
    parent: QtWidgets.QWidget,
    tab_index: int = 0,
) -> SetupGuideDialog:
    dialog = getattr(parent, "_setup_guide_dialog", None)
    if not isinstance(dialog, SetupGuideDialog):
        dialog = SetupGuideDialog(parent)
        parent._setup_guide_dialog = dialog
    dialog.show_step(tab_index)
    return dialog
