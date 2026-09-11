"""Operator controls for the shared Creality Z measurement path."""

from __future__ import annotations

from .controls import NumericDoubleSpinBox
from .qt import require_qt

_, _, QtWidgets = require_qt()


class ZProbePanel(QtWidgets.QGroupBox):
    def __init__(self, context, start_operation, parent=None):
        super().__init__("Material height — CR Touch probe", parent)
        self.context = context
        self.start_operation = start_operation
        self.motion_buttons = []
        layout = QtWidgets.QVBoxLayout(self)
        guidance = QtWidgets.QLabel(
            "1. Home / park over the black border.  2. Reference border.  "
            "3. Jog the raised probe over solid material.  4. Measure Z offset.\n"
            "Do not Home again between reference and measurement. "
            "The result is a height reading; camera correction is still a separate calibration step."
        )
        guidance.setWordWrap(True)
        layout.addWidget(guidance)
        fields = QtWidgets.QHBoxLayout()
        self.clearance = NumericDoubleSpinBox()
        self.clearance.setRange(20, 80)
        self.clearance.setValue(20)
        self.clearance.setSuffix(" mm")
        self.support = NumericDoubleSpinBox()
        self.support.setRange(-20, 20)
        self.support.setDecimals(3)
        self.support.setSuffix(" mm")
        fields.addWidget(QtWidgets.QLabel("Z clearance:"))
        fields.addWidget(self.clearance)
        fields.addWidget(QtWidgets.QLabel("Honeycomb relative to border:"))
        fields.addWidget(self.support)
        fields.addStretch()
        layout.addLayout(fields)
        self.confirm = QtWidgets.QCheckBox(
            "Probe is over a solid surface; the entered Z clearance is available and clears "
            "the material; Creality X/Y motors are disconnected."
        )
        layout.addWidget(self.confirm)
        controls = QtWidgets.QHBoxLayout()
        self.home = QtWidgets.QPushButton("Home / park")
        self.reference = QtWidgets.QPushButton("Reference border")
        self.measure = QtWidgets.QPushButton("Measure Z offset")
        for button in (self.home, self.reference, self.measure):
            controls.addWidget(button)
            self.motion_buttons.append(button)
        self.home.clicked.connect(self._home)
        self.reference.clicked.connect(lambda: self._probe("reference"))
        self.measure.clicked.connect(lambda: self._probe("measure"))
        layout.addLayout(controls)
        jog = QtWidgets.QHBoxLayout()
        jog.addWidget(QtWidgets.QLabel("Laser-off Jog:"))
        self.step = NumericDoubleSpinBox()
        self.step.setRange(0.1, 20)
        self.step.setValue(1)
        self.step.setSuffix(" mm")
        jog.addWidget(self.step)
        for text, dx, dy in (("X−", -1, 0), ("X+", 1, 0), ("Y−", 0, -1), ("Y+", 0, 1)):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(lambda _=False, x=dx, y=dy: self._jog(x, y))
            jog.addWidget(button)
            self.motion_buttons.append(button)
        jog.addStretch()
        layout.addLayout(jog)
        self.result = QtWidgets.QLabel(
            "No measurement in this panel. Enter −1.5 mm for the reported honeycomb offset on this rig."
        )
        self.result.setWordWrap(True)
        layout.addWidget(self.result)

    def _home(self):
        self.result.setText("Home / park requested; any previous probe reference is invalidated.")
        self.start_operation(
            "Home / park", self.context.machine.prepare_photo_position,
            lambda _: self.result.setText("Parked. Confirm the probe is over the border, then reference it."),
            requires_controller=True,
        )

    def _jog(self, dx, dy):
        step = self.step.value()
        self.start_operation(
            "Probe positioning", lambda: self.context.machine.jog(dx * step, dy * step, 300),
            lambda _: self.result.setText("Positioned. Confirm a solid surface under the probe before measuring."),
            requires_controller=True,
        )

    def _probe(self, operation):
        if not self.confirm.isChecked():
            self.result.setText("Confirm the probe surface and available clearance before probing.")
            return
        clearance, support = self.clearance.value(), self.support.value()
        self.confirm.setChecked(False)
        self.result.setText("Probe operation requested. Previous displayed readings are cleared.")
        self.start_operation(
            "Reference border" if operation == "reference" else "Measure Z offset",
            lambda: self.context.machine.probe_z(
                operation, confirmed=True, clearance_z_mm=clearance, support_height_mm=support,
            ),
            self._completed, requires_controller=True,
            on_failure=lambda error: self.result.setText(f"No height accepted: {error}"),
        )

    def _completed(self, result):
        if result["kind"] == "border_reference":
            reference = result["reference"]
            self.result.setText(
                f"Border referenced; probe raised to Z {reference['clearance_z_mm']:.3f} mm. "
                "Jog over the material, then measure."
            )
        else:
            self.result.setText(
                f"Surface above border: {result['surface_height_mm']:+.3f} mm · "
                f"Thickness above honeycomb: {result['thickness_above_honeycomb_mm']:.3f} mm · "
                f"Three-contact spread: {result['spread_mm']:.3f} mm. Probe retracted. "
                "This reading includes any paper or spacers under the material."
            )
