from __future__ import annotations

from laser_aligner.project.alignment import distributed_transforms
from laser_aligner.project.model import ObjectKind, ProjectDocument, Transform

from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class DistributionDialog(QtWidgets.QDialog):
    """Choose the reference area before changing any project objects."""

    def __init__(
        self,
        document: ProjectDocument,
        selected: list[str],
        *,
        horizontal: bool,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.document = document
        self.selected = list(selected)
        self.horizontal = horizontal
        self.transforms: dict[str, Transform] = {}
        self.setWindowTitle("Distribute horizontally" if horizontal else "Distribute vertically")
        self.setMinimumWidth(460)
        layout = QtWidgets.QVBoxLayout(self)
        form = QtWidgets.QFormLayout()
        self.target = QtWidgets.QComboBox()
        self.target.addItem("Full bed (project work area)", "bed")
        self.target.addItem("Chosen rectangle", "rectangle")
        self.target.addItem("Current selection span", "selection")
        form.addRow("Distribute within", self.target)
        self.boundary = QtWidgets.QComboBox()
        self.boundary.setSizeAdjustPolicy(QtWidgets.QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.boundary.setMinimumContentsLength(30)
        self.boundary.addItem("Choose a rectangle…", None)
        for item in document.objects:
            if item.kind == ObjectKind.RECTANGLE:
                box = item.bounds()
                self.boundary.addItem(
                    f"{item.name} — {box.width:g} × {box.height:g} mm "
                    f"at ({box.center[0]:g}, {box.center[1]:g}) [{item.id[-6:]}]",
                    item.id,
                )
        self.boundary_label = QtWidgets.QLabel("Boundary rectangle")
        form.addRow(self.boundary_label, self.boundary)
        layout.addLayout(form)
        self.explanation = QtWidgets.QLabel()
        self.explanation.setWordWrap(True)
        self.explanation.setSizePolicy(QtWidgets.QSizePolicy.Policy.Preferred, QtWidgets.QSizePolicy.Policy.Maximum)
        layout.addWidget(self.explanation)
        layout.addStretch()
        self.status = QtWidgets.QLabel()
        self.status.setWordWrap(True)
        self.status.setAccessibleName("Distribution result")
        layout.addWidget(self.status)
        self.buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.StandardButton.Ok | QtWidgets.QDialogButtonBox.StandardButton.Cancel
        )
        self.apply_button = self.buttons.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self.apply_button.setText("Distribute")
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.target.currentIndexChanged.connect(self._refresh)
        self.boundary.currentIndexChanged.connect(self._refresh)
        self._refresh()

    def _calculate(self) -> dict[str, Transform]:
        transforms = distributed_transforms(
            self.document,
            self.selected,
            horizontal=self.horizontal,
            target=self.target.currentData(),
            boundary_id=self.boundary.currentData(),
        )
        if not transforms:
            raise ValueError("Select at least three objects for current selection span.")
        return transforms

    def _refresh(self) -> None:
        target = self.target.currentData()
        rectangle = target == "rectangle"
        self.boundary.setVisible(rectangle)
        self.boundary_label.setVisible(rectangle)
        if target == "selection":
            explanation = (
                "Equal distances between centers. The two outermost centers stay fixed. "
                "Requires at least three objects."
            )
        else:
            axis = "left and right" if self.horizontal else "bottom and top"
            explanation = (
                f"Equal empty gaps between object edges and at the {axis} margins. "
                "Sizes stay unchanged. Positions on the other axis are kept when inside "
                "the area, or moved just enough to fit. "
            )
            if rectangle:
                explanation += (
                    "Draw a square-cornered rectangle with Draw rectangle before opening this dialog, "
                    "then choose it here. Its sides must be horizontal and vertical. "
                    "The chosen rectangle stays fixed and is excluded from distribution. "
                    "Its layer output setting is unchanged; turn output off for a guide-only layer."
                )
            else:
                area = self.document.work_area
                explanation += (
                    f"Project work area: {area.width:g} × {area.height:g} mm, "
                    f"from ({area.x_min:g}, {area.y_min:g}) to ({area.x_max:g}, {area.y_max:g})."
                )
        self.explanation.setText(explanation)
        try:
            transforms = self._calculate()
        except ValueError as exc:
            self.status.setText(str(exc))
            self.apply_button.setEnabled(False)
        else:
            changed = len(self._changed_transforms(transforms))
            self.status.setText(
                f"{len(transforms)} selected objects; {changed} will move."
                if changed else "Objects are already distributed this way; no movement is needed."
            )
            self.apply_button.setEnabled(bool(changed))

    def _changed_transforms(self, transforms: dict[str, Transform]) -> dict[str, Transform]:
        return {
            object_id: transform for object_id, transform in transforms.items()
            if abs(transform.x_mm - self.document.get_object(object_id).transform.x_mm) > 1e-9
            or abs(transform.y_mm - self.document.get_object(object_id).transform.y_mm) > 1e-9
        }

    def accept(self) -> None:
        # Recheck against the current document before returning an edit to history.
        try:
            self.transforms = self._changed_transforms(self._calculate())
        except ValueError:
            self._refresh()
            return
        if not self.transforms:
            self._refresh()
            return
        super().accept()
