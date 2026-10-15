"""Step-by-step navigation over the existing, independently guarded setup tools."""

from __future__ import annotations

from ..setup_workflow import SETUP_STEPS
from .qt import require_qt
from .setup_guide import read_setup_status
from .setup_instructions import STEP_HELP, instruction_html, progress_text, step_name

QtCore, _, QtWidgets = require_qt()

_TABS = {"camera": 0, "lens": 1, "bed": 2, "datum": 6, "focus": 6,
         "precision": 4, "heights": 2, "qualification": 4}
_TOOLS = {"datum": "Open bed leveling measurements", "precision": "Open repeated-picture check",
          "heights": "Open height calibration", "qualification": "Open laser placement measurements"}


class SetupWizardPanel(QtWidgets.QWidget):
    """Navigation never completes evidence or invokes a machine action."""

    def __init__(self, setup):
        super().__init__(setup)
        self.setup = setup
        self.index = 0
        self.statuses = {}
        self.setObjectName("machineSetupWizard")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSizeConstraint(QtWidgets.QLayout.SizeConstraint.SetMinimumSize)
        heading = QtWidgets.QHBoxLayout()
        self.steps = QtWidgets.QComboBox()
        for index, step in enumerate(SETUP_STEPS):
            self.steps.addItem(f"{index + 1} / {len(SETUP_STEPS)} · {STEP_HELP[step.id][0]}")
        self.steps.addItem("Review setup progress")
        self.steps.activated.connect(self.select_step)
        heading.addWidget(self.steps, 1)
        self.all_tools = QtWidgets.QPushButton("All setup tools")
        self.all_tools.clicked.connect(self.leave)
        heading.addWidget(self.all_tools)
        layout.addLayout(heading)
        self.instructions = QtWidgets.QTextBrowser()
        self.instructions.setOpenLinks(False)
        self.instructions.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.instructions.setFixedHeight(135)
        self.instructions.anchorClicked.connect(self.follow_instruction)
        layout.addWidget(self.instructions)
        self.evidence = QtWidgets.QTextBrowser()
        self.evidence.setMaximumHeight(58)
        self.evidence.setMinimumHeight(45)
        layout.addWidget(self.evidence)
        actions = QtWidgets.QHBoxLayout()
        self.back = QtWidgets.QPushButton("Back")
        self.back.clicked.connect(lambda: self.select_step(self.index - 1))
        self.next = QtWidgets.QPushButton("Next")
        self.next.clicked.connect(self.advance)
        self.open_tools = QtWidgets.QPushButton()
        self.open_tools.clicked.connect(self.open_current_tools)
        self.prerequisite = QtWidgets.QPushButton("Go to prerequisite")
        self.prerequisite.clicked.connect(self.go_to_prerequisite)
        layout.addWidget(self.prerequisite)
        self.holdout = QtWidgets.QPushButton("Prepare holdout marks")
        self.holdout.clicked.connect(self.show_holdout)
        self.refresh = QtWidgets.QPushButton("Check progress")
        self.refresh.clicked.connect(self.refresh_evidence)
        self.details = QtWidgets.QPushButton("Troubleshooting details")
        self.details.clicked.connect(self.show_details)
        self.errors = ()
        for button in (self.back, self.next, self.open_tools, self.holdout, self.refresh, self.details):
            actions.addWidget(button)
        layout.addLayout(actions)
        self._render()

    def _available(self):
        return not self.setup.operation_busy and not self.setup._shutdown_started

    def begin(self):
        if not self._available():
            return
        if self.isHidden():
            self._tabs_size_policy = self.setup.tabs.sizePolicy()
            self._header_visibility = [(widget, widget.isHidden()) for widget in (
                self.setup.saved_profile_binding_status, self.setup.bind_running_profile_button,
                self.setup.preferences_note,
            )]
        self.setup.tabs.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Ignored)
        self.show()
        self.setup.tabs.tabBar().hide()
        self.select_step(self.index)

    def leave(self):
        if not self._available():
            return
        self.hide()
        self.setup.tabs.setSizePolicy(self._tabs_size_policy)
        for widget, hidden in getattr(self, "_header_visibility", ()):
            widget.setVisible(not hidden)
        self.setup.tabs.tabBar().show()

    def select_step(self, index):
        if not self._available() or not 0 <= index <= len(SETUP_STEPS):
            return
        self.index = index
        self.steps.setCurrentIndex(index)
        if index < len(SETUP_STEPS):
            # Selecting a tab does not call navigate_setup_step: that dispatcher
            # may open a dialog. Only explicit tool buttons call it.
            self.setup.tabs.setCurrentIndex(_TABS[SETUP_STEPS[index].action])
            target = {"datum": "machine_setup.honeycomb_height", "gauge": "machine_setup.focus_measure"}.get(SETUP_STEPS[index].id)
            if target is not None:
                self.setup.focus_navigation_target(target)
        self._render()
        self.refresh_evidence()

    def advance(self):
        if self.index == len(SETUP_STEPS):
            self.leave()
        else:
            self.select_step(self.index + 1)

    def open_current_tools(self):
        if self._available() and self.index < len(SETUP_STEPS):
            self.setup.navigate_setup_step(SETUP_STEPS[self.index].action)

    def show_holdout(self):
        if self._available():
            self.setup.tabs.setCurrentIndex(4)

    def go_to_prerequisite(self):
        if not self._available() or self.index == len(SETUP_STEPS):
            return
        status = self.statuses.get(SETUP_STEPS[self.index].id)
        if status is not None:
            for index, step in enumerate(SETUP_STEPS):
                if step.action == status.next_action:
                    self.select_step(index)
                    return

    def refresh_evidence(self):
        if not self._available():
            return
        self.errors = ()
        self._read_generation = self.setup.context.machine.operation_generation()
        self.statuses = {}
        self.evidence.setPlainText("Checking saved settings and measurements…")
        # Use the dialog's existing operation owner: STOP stays responsive,
        # concurrent mutations are held, and a post-STOP result is discarded.
        self.setup._start_operation(
            "Check setup progress", lambda: read_setup_status(self.setup), self._received,
            observational_read=True,
        )

    def _received(self, result):
        if self.setup.context.machine.operation_generation() != self._read_generation:
            self.statuses = {}
            self.evidence.setPlainText("Machine state changed during the check. Click Check progress again.")
            return
        self.statuses, self.errors = result
        self._render()


    def set_busy(self, busy):
        self.steps.setEnabled(not busy)
        for button in (self.back, self.next, self.open_tools, self.prerequisite,
                       self.holdout, self.refresh, self.all_tools, self.details):
            button.setEnabled(not busy)
        self.back.setEnabled(not busy and self.index > 0)
        if busy:
            self.prerequisite.hide()
            self.statuses = {}
            self.evidence.setPlainText("Wait for the current operation to finish, then click Check progress.")

    def _render(self):
        if not self.isHidden():
            for widget, hidden in getattr(self, "_header_visibility", ()):
                widget.setVisible(not hidden and self.index == 0 and widget is not self.setup.preferences_note)
        review = self.index == len(SETUP_STEPS)
        self.next.setText("Close wizard" if review else "Review progress" if self.index == len(SETUP_STEPS) - 1 else "Next")
        self.back.setEnabled(self.index > 0 and self._available())
        self.open_tools.setVisible(not review and SETUP_STEPS[self.index].action in _TOOLS)
        self.holdout.setVisible(not review and SETUP_STEPS[self.index].action == "precision")
        self.prerequisite.hide()
        if review:
            self.instructions.setPlainText(
                "Review the progress below. Going through every page does not establish calibration "
                "or physical placement accuracy. Close wizard returns to all setup tools."
            )
            self.evidence.setPlainText("\n\n".join(
                step_name(step.id) + ": " + progress_text(step, self.statuses.get(step.id))
                for step in SETUP_STEPS
            ))
            return
        step = SETUP_STEPS[self.index]
        self.instructions.setHtml(instruction_html(step.id))
        self.open_tools.setText(_TOOLS.get(step.action, "Open step tools"))
        status = self.statuses.get(step.id)
        self.evidence.setPlainText(progress_text(step, status))
        if (status is not None and status.next_action != step.action
                and "Current gauge status is unavailable" not in status.reason
                and "reports no compatible gauge calibration" not in status.reason):
            required = next(item for item in SETUP_STEPS if item.action == status.next_action)
            self.prerequisite.setText("Go to " + step_name(required.id))
            self.prerequisite.show()

    def follow_instruction(self, url):
        if not self._available():
            return
        target = url.toString()
        if target.startswith("tool:"):
            action = target[5:]
            if action in _TOOLS:
                self.setup.navigate_setup_step(action)
        elif target.startswith("machine_setup."):
            self.setup.focus_navigation_target(target)

    def show_details(self):
        if not self._available():
            return
        steps = SETUP_STEPS if self.index == len(SETUP_STEPS) else (SETUP_STEPS[self.index],)
        lines = ["These are the saved settings, photos and measurements used to check progress."]
        for step in steps:
            status = self.statuses.get(step.id)
            if status is not None:
                lines.append(step_name(step.id) + ": " + status.reason)
        lines.extend(self.errors)
        QtWidgets.QMessageBox.information(self, "Setup troubleshooting", "\n\n".join(lines))
