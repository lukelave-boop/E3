"""Desktop controls for saved cut/layer sets."""

from __future__ import annotations

from ..materials.layer_profiles import LayerProfileStore, load_profile_command
from .qt import require_qt

QtCore, QtGui, QtWidgets = require_qt()


class LayerProfilesBar(QtWidgets.QWidget):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.window = window
        self.store = LayerProfileStore()
        self.setObjectName("layerProfilesBar")
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self._selector_row = QtWidgets.QHBoxLayout()
        self._selector_row.setSpacing(4)
        self._layout.addLayout(self._selector_row)
        self._label = QtWidgets.QLabel("Profiles")
        self._selector_row.addWidget(self._label)
        self.selector = QtWidgets.QComboBox()
        self.selector.setObjectName("layerProfileSelector")
        self.selector.setMinimumContentsLength(8)
        self.selector.setMinimumWidth(120)
        self.selector.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Ignored, QtWidgets.QSizePolicy.Policy.Fixed,
        )
        self.selector.setToolTip("Saved layer sets for the running machine and tool head")
        self._selector_row.addWidget(self.selector, 1)
        self._actions = QtWidgets.QWidget()
        actions_layout = QtWidgets.QHBoxLayout(self._actions)
        actions_layout.setContentsMargins(0, 0, 0, 0)
        actions_layout.setSpacing(4)
        self._selector_row.addWidget(self._actions)
        self.load_button = QtWidgets.QToolButton()
        self.overwrite_button = QtWidgets.QToolButton()
        self.overwrite_button.setObjectName("saveLayerProfileButton")
        self.save_button = QtWidgets.QToolButton()
        self.delete_button = QtWidgets.QToolButton()
        for button, label in (
            (self.load_button, "Load"),
            (self.overwrite_button, "Save"),
            (self.save_button, "Save As…"),
            (self.delete_button, "Delete"),
        ):
            button.setText(label)
            actions_layout.addWidget(button)
        self._actions_inline = True
        self.load_button.setToolTip("Replace the layer list with this profile; undo is available")
        self.save_button.setToolTip("Save all current layers as a named profile")
        self.overwrite_button.setToolTip("Overwrite the selected profile with all current layers after confirmation")
        self.load_button.clicked.connect(self.load_profile)
        self.save_button.clicked.connect(self.save_profile)
        self.overwrite_button.clicked.connect(self.overwrite_profile)
        self.delete_button.clicked.connect(self.delete_profile)
        self.selector.currentIndexChanged.connect(self._update_buttons)
        self.refresh()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        required = (
            self._label.sizeHint().width() + self.selector.minimumWidth()
            + self._actions.sizeHint().width() + 2 * self._selector_row.spacing()
        )
        inline = self.width() >= required
        if inline == self._actions_inline:
            return
        if inline:
            self._layout.removeWidget(self._actions)
            self._selector_row.addWidget(self._actions)
        else:
            self._selector_row.removeWidget(self._actions)
            self._layout.addWidget(self._actions, 0, QtCore.Qt.AlignmentFlag.AlignRight)
        self._actions_inline = inline

    def _scope(self) -> tuple[str, str]:
        return self.window._running_material_profile_ids()

    def _update_buttons(self) -> None:
        selected = bool(self.selector.currentData())
        self.load_button.setEnabled(selected)
        self.overwrite_button.setEnabled(selected)
        self.delete_button.setEnabled(selected)

    def refresh(self, selected: str = "") -> None:
        selected = selected or self.selector.currentData()
        self.selector.clear()
        self.selector.addItem("Choose a profile…", "")
        try:
            profiles = self.store.read()
            for name in sorted(profiles, key=str.casefold):
                if profiles[name]["scope"] == list(self._scope()):
                    self.selector.addItem(name, name)
            self.save_button.setEnabled(True)
            self.selector.setToolTip("Choose a saved layer set, then press Load")
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.selector.setItemText(0, "Profile library unavailable")
            self.selector.setToolTip(str(exc))
            self.save_button.setEnabled(False)
        index = self.selector.findData(selected)
        self.selector.setCurrentIndex(max(0, index))
        self._update_buttons()

    def save_profile(self) -> None:
        self.window._commit_project_numeric_edit()
        name, accepted = QtWidgets.QInputDialog.getText(
            self, "Save layer profile", "Profile name:",
        )
        if not accepted:
            return
        try:
            self.store.save(name, self._scope(), self.window.document.layers)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.window.show_error(str(exc))
            return
        self.refresh(name.strip())
        self.window.show_notice(f"Saved layer profile: {name.strip()}")

    def overwrite_profile(self) -> None:
        name = self.selector.currentData()
        if not name:
            return
        self.window._commit_project_numeric_edit()
        answer = QtWidgets.QMessageBox.question(
            self, "Overwrite layer profile?",
            f"Overwrite the existing profile '{name}' with all current cut/layer settings?",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.save(name, self._scope(), self.window.document.layers, overwrite=True)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.window.show_error(str(exc))
            return
        self.refresh(name)
        self.window.show_notice(f"Saved changes to layer profile: {name}")

    def load_profile(self) -> None:
        name = self.selector.currentData()
        if not name:
            return
        self.window._commit_project_numeric_edit()
        try:
            profile = self.store.read()[name]
            command = load_profile_command(
                self.window.document, profile, self._scope(),
            )
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.window.show_error(str(exc))
            return
        self.window.history.execute(command)
        self.window.show_notice(f"Loaded layer profile: {name}. Review settings before Preview.")

    def delete_profile(self) -> None:
        name = self.selector.currentData()
        if not name:
            return
        answer = QtWidgets.QMessageBox.question(
            self, "Delete layer profile", f"Delete saved profile '{name}'?\n"
            "The current project's layers will stay as they are.",
            QtWidgets.QMessageBox.StandardButton.Yes | QtWidgets.QMessageBox.StandardButton.No,
            QtWidgets.QMessageBox.StandardButton.No,
        )
        if answer != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            self.store.delete(name)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            self.window.show_error(str(exc))
            return
        self.refresh()
