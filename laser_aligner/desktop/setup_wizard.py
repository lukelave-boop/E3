"""Step-by-step navigation over the existing, independently guarded setup tools."""

from __future__ import annotations

from ..setup_workflow import SETUP_STEPS
from .qt import require_qt
from .setup_guide import read_setup_status

QtCore, _, QtWidgets = require_qt()

_TABS = {"camera": 0, "lens": 1, "bed": 2, "datum": 6, "focus": 6,
         "precision": 4, "heights": 2, "qualification": 4}
_TOOLS = {"datum": "Open leveling survey", "precision": "Open precision assessment",
          "heights": "Open height calibration", "qualification": "Open independent XY checks"}


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
        heading = QtWidgets.QHBoxLayout()
        self.steps = QtWidgets.QComboBox()
        for index, step in enumerate(SETUP_STEPS):
            self.steps.addItem(f"{index + 1} / {len(SETUP_STEPS)} · {step.title}")
        self.steps.addItem("Review setup evidence")
        self.steps.activated.connect(self.select_step)
        heading.addWidget(self.steps, 1)
        self.all_tools = QtWidgets.QPushButton("All setup tools")
        self.all_tools.clicked.connect(self.leave)
        heading.addWidget(self.all_tools)
        layout.addLayout(heading)
        self.instructions = QtWidgets.QLabel()
        self.instructions.setWordWrap(True)
        layout.addWidget(self.instructions)
        self.evidence = QtWidgets.QTextBrowser()
        self.evidence.setMaximumHeight(90)
        self.evidence.setMinimumHeight(60)
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
        self.holdout = QtWidgets.QPushButton("Prepare holdout marks")
        self.holdout.clicked.connect(self.show_holdout)
        self.refresh = QtWidgets.QPushButton("Refresh evidence")
        self.refresh.clicked.connect(self.refresh_evidence)
        for button in (self.back, self.next, self.open_tools, self.prerequisite, self.holdout, self.refresh):
            actions.addWidget(button)
        layout.addLayout(actions)
        self._render()

    def _available(self):
        return not self.setup.operation_busy and not self.setup._shutdown_started

    def begin(self):
        if not self._available():
            return
        self.show()
        self.setup.tabs.tabBar().hide()
        self.select_step(self.index)

    def leave(self):
        if not self._available():
            return
        self.hide()
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
        self._read_generation = self.setup.context.machine.operation_generation()
        self.statuses = {}
        self.evidence.setPlainText("Reading current evidence…")
        # Use the dialog's existing operation owner: STOP stays responsive,
        # concurrent mutations are held, and a post-STOP result is discarded.
        self.setup._start_operation(
            "Read setup evidence", lambda: read_setup_status(self.setup), self._received,
            observational_read=True,
        )

    def _received(self, result):
        if self.setup.context.machine.operation_generation() != self._read_generation:
            self.statuses = {}
            self.evidence.setPlainText("Machine state changed during the read; refresh its evidence.")
            return
        self.statuses, errors = result
        self._render()
        if errors:
            self.evidence.append("Some evidence is unavailable; review its setup tool and refresh.")

    def set_busy(self, busy):
        self.steps.setEnabled(not busy)
        for button in (self.back, self.next, self.open_tools, self.prerequisite,
                       self.holdout, self.refresh, self.all_tools):
            button.setEnabled(not busy)
        self.back.setEnabled(not busy and self.index > 0)
        if busy:
            self.statuses = {}
            self.evidence.setPlainText("Evidence needs refreshing after the current operation.")

    def _render(self):
        review = self.index == len(SETUP_STEPS)
        self.next.setText("Close wizard" if review else "Review evidence" if self.index == len(SETUP_STEPS) - 1 else "Next")
        self.back.setEnabled(self.index > 0 and self._available())
        self.open_tools.setVisible(not review and SETUP_STEPS[self.index].action in _TOOLS)
        self.holdout.setVisible(not review and SETUP_STEPS[self.index].action == "precision")
        self.prerequisite.hide()
        if review:
            self.instructions.setText(
                "Review the recorded evidence below. Browsing every step does not establish calibration "
                "or physical placement accuracy. Close wizard returns to all setup tools."
            )
            lines = [f"{step.title}: {self.statuses[step.id].state.upper()} — {self.statuses[step.id].reason}"
                     for step in SETUP_STEPS if step.id in self.statuses]
            self.evidence.setPlainText("\n\n".join(lines) or "Refresh to inspect current evidence.")
            return
        step = SETUP_STEPS[self.index]
        self.instructions.setText(step.instructions + " Next browses the steps; evidence determines their status.")
        self.open_tools.setText(_TOOLS.get(step.action, "Open step tools"))
        status = self.statuses.get(step.id)
        required = "Evidence: " + ", ".join(step.evidence_requirements)
        if status is None:
            self.evidence.setPlainText("Refresh to inspect current evidence.\n" + required)
        else:
            self.evidence.setPlainText(f"Evidence snapshot · {status.state.upper()}: {status.reason}\n{required}")
            self.prerequisite.setVisible(status.next_action != step.action)
