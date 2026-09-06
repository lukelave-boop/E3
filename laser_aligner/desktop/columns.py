from __future__ import annotations

from collections.abc import Sequence

from .qt import require_qt

QtCore, _QtGui, QtWidgets = require_qt()


def configure_resizable_columns(
    view: QtWidgets.QTreeView | QtWidgets.QTableView,
    widths: Sequence[int],
) -> None:
    """Set readable initial widths without taking ownership of later resizing.

    Call once, after defining the columns. Interactive headers retain native
    divider dragging and double-click content fitting, including the last column.
    Data refreshes must not call this again: the operator's widths belong to the
    view's lifetime, independent of its rows or current selection.
    """

    header = (
        view.horizontalHeader()
        if isinstance(view, QtWidgets.QTableView)
        else view.header()
    )
    if len(widths) != header.count():
        raise ValueError("Initial widths must cover every table column")
    header.setStretchLastSection(False)
    header.setMinimumSectionSize(28)
    header.setSectionResizeMode(QtWidgets.QHeaderView.ResizeMode.Interactive)
    header.setToolTip("Drag a column divider to resize. Double-click to fit its contents.")
    view.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAsNeeded)
    model = view.model()
    for column, width in enumerate(widths):
        title = model.headerData(column, QtCore.Qt.Orientation.Horizontal)
        title_width = header.fontMetrics().horizontalAdvance(str(title or "")) + 24
        header.resizeSection(column, max(int(width), title_width))
