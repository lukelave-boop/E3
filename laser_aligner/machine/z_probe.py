"""CR Touch contact measurements using the existing Creality Marlin commands.

This client has no independent serial connection or primary XY authority.
MachineService admits each complete operation. Reported measurements are
observations, never autofocus, laser arming, or camera-calibration authority.
"""

from __future__ import annotations

import math
import re
import statistics
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass

from ..errors import MachineError, SafetyError
from .secondary_controller import CrealityControllerOwner, WriteGuardFactory

_NUMBER = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[Ee][+-]?\d+)?"
_POSITION = re.compile(
    rf"^X:\s*({_NUMBER})\s+Y:\s*({_NUMBER})\s+Z:\s*({_NUMBER})"
    rf"(?:\s+E:\s*{_NUMBER})?(?:\s+Count\s+.*)?\s*$", re.I
)
_CONTACT = re.compile(
    rf"^Bed X:\s*({_NUMBER})\s+Y:\s*({_NUMBER})\s+Z:\s*({_NUMBER})\s*$", re.I
)
_REPEATABILITY_MM = 0.10


def finite_number(value: object, label: str, lower: float, upper: float) -> float:
    if type(value) not in {int, float} or not math.isfinite(value):
        raise SafetyError(f"{label} must be a finite number")
    if not lower <= value <= upper:
        raise SafetyError(f"{label} must be between {lower:g} and {upper:g} mm")
    return float(value)


def parse_position(lines: tuple[str, ...]) -> float:
    positions = [match for line in lines if (match := _POSITION.fullmatch(line.strip()))]
    if len(positions) != 1:
        raise MachineError("M114 must report exactly one complete logical position")
    return finite_number(float(positions[0][3]), "Reported Z", -1000, 1000)


def parse_contact(lines: tuple[str, ...]) -> float:
    contacts = [match for line in lines if (match := _CONTACT.fullmatch(line.strip()))]
    if len(contacts) != 1:
        raise MachineError(
            "G30 did not report exactly one probe contact height. An OK alone is not "
            "a measurement. Keep the full controller response for diagnosis."
        )
    contact = contacts[0]
    if any(abs(float(contact[i]) - 110.0) > 0.01 for i in (1, 2)):
        raise MachineError("G30 reported an unexpected virtual probe point")
    return finite_number(float(contact[3]), "Probe contact Z", -80, 80)


@dataclass(frozen=True)
class ProbeReference:
    controller_generation: int
    primary_generation: int
    stop_epoch: int
    border_z_mm: float
    border_samples_mm: tuple[float, ...]
    clearance_z_mm: float
    support_height_mm: float
    carriage_xy_mm: tuple[float, float]
    firmware: str
    measured_at: float


class CrealityZProbe:
    def __init__(self, owner: CrealityControllerOwner, on_failure: Callable[[], None]):
        self.owner = owner
        self._on_failure = on_failure
        self.reference: ProbeReference | None = None
        self.transcript: list[dict[str, object]] = []
        self._generation: int | None = None
        self.native_motion_started = False

    def invalidate(self) -> None:
        self.reference = None

    def _execute(
        self, command: str, guard: WriteGuardFactory, *, motion_possible: bool = True,
    ) -> tuple[str, ...]:
        if self._generation != self.owner.generation:
            raise MachineError("Creality session changed; reference the border again")
        entry: dict[str, object] = {"command": command, "responses": []}
        self.transcript.append(entry)
        try:
            lines = self.owner._execute_acknowledged(
                command, write_guard=guard, allow_open=False,
                timeout=90.0 if command.split()[0] == "G28" else 30.0,
                on_failure=self._on_failure if motion_possible else lambda: None,
                interrupt_on_failure=motion_possible,
            )
        except Exception as exc:
            entry["error"] = str(exc)
            raise
        entry["responses"] = list(lines)
        with guard():
            if self._generation != self.owner.generation:
                raise MachineError("Creality session changed during probing")
        return lines

    def _raise(self, clearance: float, guard: WriteGuardFactory) -> None:
        self._execute(f"G1 Z{clearance:.3f} F300", guard)
        self._execute("M400", guard)
        actual = parse_position(self._execute("M114", guard))
        if abs(actual - clearance) > 0.05:
            raise MachineError("Probe retract did not reach the requested Z clearance")

    def native_cycle_test(
        self, *, guard: WriteGuardFactory, on_motion_start: Callable[[], None] = lambda: None,
    ) -> dict[str, object]:
        """One operator-confirmed native Z homing cycle, without a height result.

        The operator confirms space for the 5 mm initial lift and final Z 20,
        a retracted normal pin,
        solid border beneath it and disconnected secondary XY motors. Native
        G28 may home its virtual XY; only the secondary Z motor is connected.
        Homing establishes a new origin, so this cannot measure material height.
        """
        self.invalidate()
        self.transcript = []
        self.native_motion_started = False
        self.owner.raise_if_faulted()
        if not self.owner.ready:
            raise MachineError("The shared Creality controller must be connected before probing")
        self._generation = self.owner.generation
        def precheck(command: str) -> tuple[str, ...]:
            return self._execute(command, guard, motion_possible=False)

        firmware = " ".join(precheck("M115"))
        if "marlin" not in firmware.lower() or "ender-3 s1 pro" not in firmware.lower():
            raise MachineError("Expected the existing Ender-3 S1 Pro / Marlin controller")
        flags = [line.strip().lower() for line in precheck("M119")]
        states = [line for line in flags if line.startswith("z_min:")]
        if states != ["z_min: triggered"]:
            raise MachineError("Native test requires the operator-observed retracted pin and z_min: TRIGGERED")
        precheck("G21")
        precheck("G90")
        initial = parse_position(precheck("M114"))
        known = [line for line in flags if line.startswith("test_axis_known_z_flag")]
        reset_position = -0.25 <= initial <= 0.25 and known == ["test_axis_known_z_flag = false"]
        homed_clearance = abs(initial - 20.0) <= 0.05 and known == ["test_axis_known_z_flag = true"]
        # Accept the observed reset state or an already-homed clearance. Neither
        # numerical state proves the operator-confirmed physical headroom.
        if not (reset_position or homed_clearance):
            raise SafetyError(
                f"Cannot start here: the Ender reports Z {initial:.3f} mm. "
                "Start from its reset position or homed Z 20 mm clearance. No Z move was sent."
            )
        precheck("M84 S0")
        precheck("G91")
        self.native_motion_started = True
        on_motion_start()
        self._execute("G1 Z5.000 F300", guard)
        self._execute("G90", guard)
        self._execute("M400", guard)
        raised = parse_position(self._execute("M114", guard))
        if abs(raised - initial - 5.0) > 0.05:
            raise MachineError("Initial upward clearance lift was not confirmed; native homing not started")
        # R0 omits G28's redundant initial clearance; the host just completed it.
        # Firmware owns deployment, fast approach, bump, slow approach and stow.
        self._execute("G28 Z R0", guard)
        self._execute("M420 S0", guard)
        homed = parse_position(self._execute("M114", guard))
        flags = [line.strip().lower() for line in self._execute("M119", guard)]
        if [line for line in flags if line.startswith("test_axis_known_z_flag")] != ["test_axis_known_z_flag = true"]:
            raise MachineError("Native homing did not report a known Z axis")
        if [line for line in flags if line.startswith("z_min:")] != ["z_min: triggered"] or abs(homed - 5.0) > 0.25:
            raise MachineError("Native homing did not finish in the expected retracted Z 5 mm state")
        self._raise(20.0, guard)
        return {
            "kind": "native_cycle_test", "command": "G28 Z R0",
            "homed_z_mm": homed, "clearance_z_mm": 20.0,
            "reference_ready": False, "operator_observation_required": True,
            "firmware": firmware, "transcript": list(self.transcript),
        }

    def _samples(self, clearance: float, guard: WriteGuardFactory) -> tuple[float, ...]:
        samples: list[float] = []
        for index in range(3):
            # Creality XY motors are disconnected. The virtual bed centre avoids
            # G30's silent can_reach rejection; primary XY remains stationary.
            lines = self._execute("G30 X110 Y110", guard)
            contact = parse_contact(lines)
            if not -2.0 <= contact <= clearance - 10.0:
                raise MachineError("Probe contact is outside the bounded height interval; no retract attempted")
            samples.append(contact)
            if index < 2:
                # G30 performs its own stow/retract. Keep that short return
                # between samples instead of adding a full travel-height lift.
                # Read back before another firmware-controlled contact cycle.
                current = parse_position(self._execute("M114", guard))
                if not contact + 1.0 <= current <= clearance + 0.05:
                    raise MachineError(
                        "Post-probe Z is outside the repeat clearance interval; "
                        "no further contact attempted"
                    )
        self._raise(clearance, guard)
        if max(samples) - min(samples) > _REPEATABILITY_MM:
            raise MachineError("Probe contacts disagree by more than 0.10 mm; no height accepted")
        return tuple(samples)

    def establish_reference(
        self, *, clearance_z_mm: float, support_height_mm: float,
        primary_generation: int, stop_epoch: int, carriage_xy_mm: tuple[float, float],
        guard: WriteGuardFactory,
    ) -> dict[str, object]:
        clearance = finite_number(clearance_z_mm, "Z clearance", 20, 80)
        support = finite_number(support_height_mm, "Honeycomb height relative to border", -20, 20)
        self.invalidate()
        self.transcript = []
        self.owner.raise_if_faulted()
        if not self.owner.ready:
            raise MachineError("The shared Creality controller must be connected before probing")
        self._generation = self.owner.generation
        firmware = " ".join(self._execute("M115", guard))
        if "marlin" not in firmware.lower() or "ender-3 s1 pro" not in firmware.lower():
            raise MachineError("Expected the existing Ender-3 S1 Pro / Marlin controller")
        self._execute("G21", guard)
        self._execute("G90", guard)
        # Keep the referenced Z from becoming unpowered while the operator
        # positions primary XY. This is volatile; no EEPROM settings are saved.
        self._execute("M84 S0", guard)
        self._execute("M280 P0 S10", guard)
        states = [line.strip().lower() for line in self._execute("M119", guard)
                  if line.strip().lower().startswith("z_min:")]
        if states != ["z_min: open"]:
            raise MachineError("The deployed CR Touch must report z_min: open before homing")
        self._execute("M280 P0 S90", guard)
        self._execute("G28", guard)
        self._execute("M420 S0", guard)
        homed = parse_position(self._execute("M114", guard))
        if abs(homed - 5.0) > 0.25:
            raise MachineError("Border homing did not finish at the archived Z 5 mm position")
        samples = self._samples(clearance, guard)
        border = statistics.mean(samples)
        if abs(border) > 0.5:
            raise MachineError("The border contact is unexpectedly far from the homed zero")
        reference = ProbeReference(
            self.owner.generation, primary_generation, stop_epoch, border, samples,
            clearance, support, carriage_xy_mm, firmware, time.time(),
        )
        with guard():
            self.reference = reference
        return {"kind": "border_reference", "reference": asdict(reference),
                "transcript": list(self.transcript)}

    def measure(
        self, *, primary_generation: int, stop_epoch: int,
        carriage_xy_mm: tuple[float, float], guard: WriteGuardFactory,
    ) -> dict[str, object]:
        reference = self.reference
        if reference is None or (
            reference.controller_generation != self.owner.generation
            or reference.primary_generation != primary_generation
            or reference.stop_epoch != stop_epoch
        ):
            self.invalidate()
            raise SafetyError("Reference the border in this controller session before measuring")
        self.transcript = []
        self._generation = reference.controller_generation
        current = parse_position(self._execute("M114", guard))
        if abs(current - reference.clearance_z_mm) > 0.05:
            raise MachineError("Z moved since the verified retract; reference the border again")
        samples = self._samples(reference.clearance_z_mm, guard)
        height = statistics.mean(samples) - reference.border_z_mm
        result = {
            "kind": "surface_measurement", "surface_height_mm": height,
            "thickness_above_honeycomb_mm": height - reference.support_height_mm,
            "samples_mm": list(samples), "spread_mm": max(samples) - min(samples),
            "carriage_xy_mm": list(carriage_xy_mm), "measured_at": time.time(),
            "reference": asdict(reference), "transcript": list(self.transcript),
        }
        with guard():
            return result
