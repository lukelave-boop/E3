"""Taught gauge focus, using the shared Ender owner and measured surface coordinates.

Calibration survives restart; references, surfaces and movement previews never do.
XY transfers use the MachineService callback; no laser output or job integration.
"""
from __future__ import annotations

import copy
import math
import re
import time
import uuid
from pathlib import Path

from ..errors import MachineError, SafetyError
from ..storage import atomic_write_json, strict_json_loads
from .mainboard import parse_status
from .z_probe import finite_number, parse_position

CAPABILITY = "Cap:E3_SURFACE_HEIGHT_V2:1"
PI_CAPABILITY = "pi-laser-focus-v1"
XY_CAPABILITY = "pi-laser-focus-xy-v1"
CLICK_CAPABILITY = "pi-laser-focus-click-v1"
ACTIONS = {"status", "reference", "measure", "jog", "teach", "preview", "move",
           "clearance", "clear_surface", "forget", "set_xy_offset", "align_probe", "align_laser",
           "position_probe"}
_NUM = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
_GEOMETRY = re.compile(rf"E3SG:2 PROBE_Z:({_NUM}) RETRACT:({_NUM}) MIN:({_NUM}) MAX:({_NUM}) CEILING:({_NUM})")
_CONTACT = re.compile(rf"E3MH:2 Z:({_NUM})")


def focus_calibration_path(config_path: Path) -> Path:
    return config_path.with_name(config_path.name + ".laser-focus.json")


def validate_request(action, confirmed=False, value=None, clearance_z_mm=30.0,
                     gap_mm=7.0, measurement_id=None, preview_id=None):
    if type(action) is not str or action not in ACTIONS:
        raise SafetyError("Unknown laser focus action")
    if type(confirmed) is not bool or (action != "status" and not confirmed):
        raise SafetyError("Confirm the laser-off focus setup operation")
    finite_number(clearance_z_mm, "Focus clearance", 20, 80)
    finite_number(gap_mm, "Focus gauge gap", 3, 7)
    if gap_mm not in (3, 5, 7):
        raise SafetyError("Select a 3, 5 or 7 mm focus gap")
    if action == "jog":
        if finite_number(value, "Focus jog", -1, 1) == 0:
            raise SafetyError("Focus jog must be nonzero")
    elif action == "set_xy_offset":
        validate_xy_offset(value)
    elif action == "position_probe":
        validate_probe_target(value)
    elif value is not None:
        raise SafetyError("Only focus jog, XY offset and probe positioning accept a value")
    for label, token in (("measurement", measurement_id), ("preview", preview_id)):
        if token is not None and (type(token) is not str or len(token) != 36):
            raise SafetyError(f"Invalid focus {label} ID")
    if action in {"jog", "teach", "preview", "align_laser"} and measurement_id is None:
        raise SafetyError("Measure this surface before teaching or positioning focus")
    if action == "move" and preview_id is None:
        raise SafetyError("Preview the focus target before moving")
    if action == "teach" and gap_mm != 7:
        raise SafetyError("Teach with the 7 mm gauge; 5 and 3 mm are derived offsets")


def validate_xy_offset(value):
    if type(value) not in {list, tuple} or len(value) != 2:
        raise SafetyError("Provide both measured probe XY offsets")
    return [round(finite_number(v, "Probe XY offset", -100, 100), 3) for v in value]


def validate_probe_target(value):
    if type(value) not in {list, tuple} or len(value) != 2:
        raise SafetyError("Provide both selected probe target coordinates")
    # The service independently checks the selected point and offset carriage
    # target against its configured work area before any XY move.
    return [finite_number(v, "Probe target XY", -1e6, 1e6) for v in value]


def parse_geometry(lines):
    tokens = " ".join(lines).split()
    caps = [s for s in tokens if s.startswith("Cap:E3_SURFACE_HEIGHT")]
    if caps != [CAPABILITY]:
        raise MachineError("Install the E3 surface-height V2 firmware before using laser focus")
    reports = [s.strip() for s in lines if s.strip().startswith("E3SG")]
    match = _GEOMETRY.fullmatch(reports[0]) if len(reports) == 1 else None
    if match is None:
        raise MachineError("Expected one complete E3 surface geometry report")
    values = tuple(float(match[i]) for i in range(1, 6))
    if not all(math.isfinite(v) for v in values) or not -10 <= values[0] <= 0 or not 0 < values[1] <= 5 or values[2:] != (-2, 65, 80):
        raise MachineError("Unsupported E3 surface geometry")
    return dict(zip(("probe_z_mm", "retract_mm", "min_mm", "max_mm", "ceiling_mm"), values, strict=True))


class LaserFocus:
    def __init__(self, path: Path | None, controller_port: str):
        self.path, self.controller_port = path, controller_port
        self.calibration = self._load()
        self.xy_path = None if path is None else path.with_name(path.stem + "-xy.json")
        self.probe_xy_offset_mm = self._load_xy_offset()
        self.requires_clearance = False
        self.selected_clearance = 30.0
        self.invalidate()

    def invalidate(self):
        self.reference = self.surface = self.preview = None
        self.xy_sequence = None
        self.session = None

    def clear_surface(self, *, keep_alignment=False):
        self.surface = self.preview = None
        if not keep_alignment:
            self.xy_sequence = None

    def _load_xy_offset(self):
        if self.xy_path is None:
            return None
        try:
            data = strict_json_loads(self.xy_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, ValueError) as exc:
            raise MachineError(f"Cannot read probe XY offset: {exc}") from exc
        try:
            if (type(data) is not dict or set(data) != {"schema_version", "controller_port", "probe_xy_offset_mm"}
                or type(data["schema_version"]) is not int or data["schema_version"] != 1
                or data["controller_port"] != self.controller_port):
                raise ValueError("Unknown XY offset format or controller binding")
            return validate_xy_offset(data["probe_xy_offset_mm"])
        except (ValueError, TypeError, MachineError) as exc:
            raise MachineError(f"Invalid saved probe XY offset: {exc}") from exc

    def persist_xy_offset(self, value):
        offset = validate_xy_offset(value)
        if self.xy_path is None:
            raise MachineError("Persistent probe XY offset is unavailable; update the E3 node")
        try:
            atomic_write_json(self.xy_path, {"schema_version": 1, "controller_port": self.controller_port,
                                             "probe_xy_offset_mm": offset})
        except OSError as exc:
            raise MachineError(f"Cannot save probe XY offset: {exc}") from exc
        self.probe_xy_offset_mm = offset
        self.clear_surface()

    def _load(self):
        if self.path is None:
            return None
        try:
            raw = strict_json_loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (OSError, UnicodeError, ValueError) as exc:
            raise MachineError(f"Cannot read focus calibration: {exc}") from exc
        try:
            if type(raw) is not dict or set(raw) != {"schema_version", "controller_port", "calibration"} or type(raw["schema_version"]) is not int or raw["schema_version"] != 1 or raw["controller_port"] != self.controller_port:
                raise ValueError("Unknown format or controller binding")
            calibration = raw["calibration"]
            if calibration is None:
                return None
            expected = {"id", "focus_offset_mm", "gauge_mm", "taught_z_mm", "taught_contact_z_mm", "taught_at", "firmware", "geometry"}
            if type(calibration) is not dict or set(calibration) != expected:
                raise ValueError("Incomplete calibration")
            if type(calibration["id"]) is not str or str(uuid.UUID(calibration["id"])) != calibration["id"]:
                raise ValueError("Invalid calibration ID")
            finite_number(calibration["focus_offset_mm"], "Focus offset", -80, 80)
            finite_number(calibration["taught_z_mm"], "Taught Z", 0, 80)
            finite_number(calibration["taught_contact_z_mm"], "Taught contact", -2, 65)
            finite_number(calibration["taught_at"], "Taught time", 0, 1e12)
            if calibration["gauge_mm"] != 7 or type(calibration["gauge_mm"]) not in {int, float} or type(calibration["firmware"]) is not str or not 1 <= len(calibration["firmware"]) <= 8192:
                raise ValueError("Unsupported gauge or firmware identity")
            if type(calibration["geometry"]) is not dict or any(type(v) not in {int, float} or not math.isfinite(v) for v in calibration["geometry"].values()):
                raise ValueError("Invalid geometry number")
            if calibration["geometry"] != parse_geometry(calibration["firmware"].splitlines()):
                raise ValueError("Geometry binding mismatch")
            if abs(calibration["taught_z_mm"] - calibration["taught_contact_z_mm"] - calibration["focus_offset_mm"]) > 1e-6:
                raise ValueError("Inconsistent taught offset")
            return calibration
        except (ValueError, TypeError, AttributeError, MachineError) as exc:
            raise MachineError(f"Invalid saved focus calibration: {exc}") from exc

    def persist(self, calibration):
        if self.path is None:
            raise MachineError("Persistent focus calibration is unavailable; update the E3 node")
        try:
            atomic_write_json(self.path, {"schema_version": 1, "controller_port": self.controller_port,
                                         "calibration": calibration})
        except OSError as exc:
            raise MachineError(f"Cannot save focus calibration: {exc}") from exc
        self.calibration = calibration

    def execute(self, owner, probe, action, *, confirmed, value, clearance_z_mm,
                gap_mm, measurement_id, preview_id, maximum, primary_generation,
                stop_epoch, xy, guard, on_motion_start, on_failure, move_xy=None,
                laser_spot_offset=(0.0, 0.0)):
        validate_request(action, confirmed, value, clearance_z_mm, gap_mm, measurement_id, preview_id)
        spot_offset = validate_probe_target(laser_spot_offset)
        clearance = finite_number(min(clearance_z_mm, maximum) if action in {"status", "forget", "clear_surface"} else clearance_z_mm,
                                  "Focus clearance", 20, maximum)
        transcript = []
        motion_started = False
        session = (owner.generation, primary_generation, stop_epoch)
        if self.session != session:
            self.invalidate()
            self.session = session

        def send(command, motion=False):
            nonlocal motion_started
            with guard():
                if self.session != session or owner.generation != session[0]:
                    raise MachineError("Focus session changed")
            if motion:
                motion_started = True
                on_motion_start()
            lines = owner._execute_acknowledged(command, allow_open=False, timeout=90 if command.startswith("G28") else 35,
                                               write_guard=guard, on_failure=on_failure,
                                               interrupt_on_failure=motion_started)
            transcript.append({"command": command, "responses": list(lines)})
            with guard():
                if self.session != session or owner.generation != session[0]:
                    raise MachineError("Focus session changed")
            return lines

        identity_lines = send("M115")
        geometry = parse_geometry(identity_lines)
        firmware = "\n".join(identity_lines)
        board = parse_status(send("M123"))
        current = parse_position(send("M114"))
        if not board["z_known"]:
            self.invalidate()
            self.session = session
        if self.preview and abs(self.preview["current_z_mm"] - current) > .05:
            self.preview = None
        if self.reference and (self.reference["firmware"] != firmware or self.reference["geometry"] != geometry):
            self.invalidate()
            self.session = session
        if self.surface and tuple(self.surface["carriage_xy_mm"]) != tuple(xy):
            self.clear_surface()
        if self.xy_sequence:
            expected_xy = self.xy_sequence["probe_carriage_xy_mm" if self.xy_sequence["phase"] == "probe" else "laser_target_xy_mm"]
            if tuple(expected_xy) != tuple(xy) or self.xy_sequence["laser_spot_offset_mm"] != spot_offset:
                self.clear_surface()
        compatible = self.calibration is not None and self.calibration["firmware"] == firmware and self.calibration["geometry"] == geometry

        def stowed():
            states = [s.strip().lower() for s in send("M119") if s.strip().lower().startswith("z_min:")]
            if states != ["z_min: triggered"]:
                raise SafetyError("Expected the confirmed stowed probe")

        def known():
            if not board["z_known"] or not 0 <= current <= maximum:
                raise SafetyError("Reference the border before focus positioning")
            stowed()

        def move_to(target, feed=300):
            nonlocal current
            finite_number(target, "Focus target Z", 0, maximum)
            self.selected_clearance = clearance
            # Before a downward move, retain the restriction even if STOP makes
            # the final Z unknown. Only a verified clearance lift releases it.
            if target < clearance - .05:
                self.requires_clearance = True
            send("G21")
            send("G90")
            send(f"G1 Z{target:.3f} F{feed}", True)
            send("M400")
            current = parse_position(send("M114"))
            if abs(current - target) > .05:
                raise MachineError("Focus Z movement was not confirmed")
            if not parse_status(send("M123"))["z_known"]:
                raise MachineError("Focus move lost its Z reference")
            stowed()
            self.requires_clearance = current < clearance - .05

        def contact_at(start):
            maximum_contact = start - 15
            send("G21")
            send("G90")
            self.requires_clearance = True
            reports = [s.strip() for s in send(f"G39 C{start:.3f} H{maximum_contact:.3f}", True) if s.strip().startswith("E3MH")]
            match = _CONTACT.fullmatch(reports[0]) if len(reports) == 1 else None
            if match is None:
                raise MachineError("G39 must report exactly one V2 surface contact")
            contact = finite_number(float(match[1]), "Surface contact", -2, maximum_contact)
            stowed()
            if not parse_status(send("M123"))["z_known"]:
                raise MachineError("Probe lost its Z reference; no further move sent")
            returned = parse_position(send("M114"))
            if not contact - geometry["probe_z_mm"] - .1 <= returned <= start + .05:
                raise MachineError("Unexpected post-probe Z; no further move sent")
            move_to(start)
            return contact

        def measured():
            if self.surface is None or measurement_id != self.surface["id"]:
                raise SafetyError("Surface measurement changed; measure and preview again")
            if clearance < self.surface["contact_z_mm"] + 15:
                raise SafetyError("Selected clearance does not clear the measured surface envelope")
            return self.surface["contact_z_mm"]

        def laser_aligned():
            if self.xy_sequence and self.xy_sequence["phase"] != "laser":
                raise SafetyError("Align the laser over the measured point before lowering or teaching Z")

        if action == "reference":
            self.invalidate()
            self.session = session
            # Reference is admitted only above the confirmed border by service.
            # Re-reference after a prior cycle returned to a higher clearance.
            if board["z_known"] and 20 < current <= maximum:
                if maximum < 25:
                    raise SafetyError("Reference initial lift exceeds the configured ceiling")
                stowed()
                move_to(20)
            if current + 5 > maximum:
                raise SafetyError("Reference initial lift exceeds the configured ceiling")
            self.requires_clearance = True
            result = probe.native_cycle_test(guard=guard, on_motion_start=on_motion_start)
            transcript.extend(result["transcript"])
            board["z_known"] = True
            current = 20.0
            border = contact_at(20)
            if abs(border) > .5:
                raise MachineError("Border contact is not near homed zero")
            move_to(clearance)
            self.reference = {"border_z_mm": border, "firmware": firmware, "geometry": geometry}
        elif action == "set_xy_offset":
            self.persist_xy_offset(value)
        elif action in {"align_probe", "align_laser", "position_probe"}:
            known()
            if self.reference is None:
                raise SafetyError("Reference the border before probe XY alignment")
            if self.requires_clearance or abs(current - clearance) > .05:
                raise SafetyError("Return to the selected clearance before probe XY alignment")
            if self.probe_xy_offset_mm is None:
                raise SafetyError("Apply the measured probe XY offset before alignment")
            if move_xy is None:
                raise MachineError("Shared controller XY alignment is unavailable")
            if action in {"align_probe", "position_probe"}:
                if action == "align_probe" and self.xy_sequence and self.xy_sequence["phase"] == "probe":
                    raise SafetyError("Probe is already aligned; measure before aligning the laser")
                surface_target = (validate_probe_target(value) if action == "position_probe" else
                                  [a+b for a, b in zip(xy, spot_offset, strict=True)])
                laser_target = [a-b for a, b in zip(surface_target, spot_offset, strict=True)]
                target = [round(a-b, 3) for a, b in zip(laser_target, self.probe_xy_offset_mm, strict=True)]
                sequence = {"laser_target_xy_mm": [round(v, 3) for v in laser_target],
                            "surface_target_xy_mm": surface_target,
                            "laser_spot_offset_mm": spot_offset,
                            "probe_carriage_xy_mm": target, "phase": "probe"}
                self.clear_surface()
            else:
                measured()
                if self.xy_sequence is None or self.xy_sequence["phase"] != "probe":
                    raise SafetyError("No measured probe alignment is available to return")
                target = self.xy_sequence["laser_target_xy_mm"]
                sequence = dict(self.xy_sequence, phase="laser")
                laser_target = target
                surface_target = sequence["surface_target_xy_mm"]
            result = move_xy(tuple(target), guard, surface_target=tuple(surface_target),
                             laser_target=tuple(laser_target))
            transcript.extend(result["transcript"])
            xy = tuple(target)
            self.xy_sequence = sequence
            self.preview = None
            if self.surface:
                self.surface["carriage_xy_mm"] = list(xy)
        elif action == "measure":
            known()
            if self.xy_sequence and self.xy_sequence["phase"] == "laser":
                raise SafetyError("Align the probe again before remeasuring the laser target")
            if self.reference is None:
                raise SafetyError("Reference the border before measuring")
            if abs(current - clearance) > .05:
                raise SafetyError("Raise to the selected clearance before measuring this surface")
            # Only alignment created by the explicit action survives measurement.
            self.clear_surface(keep_alignment=self.xy_sequence is not None and self.xy_sequence["phase"] == "probe")
            contact = contact_at(clearance)
            self.surface = {"id": str(uuid.uuid4()), "contact_z_mm": contact,
                            "elevation_mm": contact - self.reference["border_z_mm"],
                            "carriage_xy_mm": list(xy), "measured_at": time.time()}
        elif action in {"jog", "teach", "preview"}:
            known()
            laser_aligned()
            contact = measured()
            floor = max(0.0, contact - geometry["probe_z_mm"])
            if action == "jog":
                self.preview = None
                target = current + value
                if target < floor:
                    raise SafetyError("Focus jog would cross the contacted carriage plane")
                move_to(target, 60)
            elif action == "teach":
                if current < floor:
                    raise SafetyError("Taught position is below the contacted carriage plane")
                self.preview = None
                self.persist({"id": str(uuid.uuid4()), "focus_offset_mm": current - contact,
                              "gauge_mm": 7, "taught_z_mm": current, "taught_contact_z_mm": contact,
                              "taught_at": time.time(), "firmware": firmware, "geometry": geometry})
                compatible = True
            else:
                if not compatible:
                    raise SafetyError("Teach the 7 mm focus gauge with this firmware and probe geometry first")
                target = contact + self.calibration["focus_offset_mm"] + gap_mm - 7
                finite_number(target, "Focus target", floor, maximum)
                self.preview = {"id": str(uuid.uuid4()), "measurement_id": self.surface["id"],
                                "calibration_id": self.calibration["id"], "gap_mm": gap_mm,
                                "target_z_mm": target, "current_z_mm": current,
                                "clearance_z_mm": clearance}
        elif action == "move":
            known()
            laser_aligned()
            preview = self.preview
            if not compatible or preview is None or preview_id != preview["id"] or self.surface is None or preview["measurement_id"] != self.surface["id"] or preview["calibration_id"] != self.calibration["id"] or preview["gap_mm"] != gap_mm or preview["clearance_z_mm"] != clearance or abs(preview["current_z_mm"] - current) > .05:
                raise SafetyError("Focus preview is stale; preview the current surface again")
            self.preview = None
            floor = max(0.0, self.surface["contact_z_mm"] - geometry["probe_z_mm"])
            finite_number(preview["target_z_mm"], "Focus target", floor, maximum)
            move_to(preview["target_z_mm"])
        elif action == "clearance":
            known()
            if self.requires_clearance and clearance < self.selected_clearance:
                raise SafetyError("Return to at least the clearance selected before lowering Z")
            if current > clearance + .05:
                raise SafetyError("Clearance return only raises Z; select clearance above current Z")
            self.preview = None
            move_to(clearance)
        elif action == "clear_surface":
            self.clear_surface()
        elif action == "forget":
            self.preview = None
            self.persist(None)
            compatible = False
        with guard():
            if self.session != session or owner.generation != session[0]:
                raise MachineError("Focus operation was cancelled")
        return copy.deepcopy({"action": action, "available": True, "reference_ready": self.reference is not None,
                              "current_readback": {"z_mm": current, "z_known": board["z_known"], "fresh": True},
                              "surface": self.surface, "calibration": self.calibration,
                              "calibration_compatible": compatible, "calibration_persistent": self.path is not None,
                              "preview": self.preview, "max_z_mm": maximum, "clearance_z_mm": clearance,
                              "contact_min_mm": -2, "contact_max_mm": clearance - 15,
                              "firmware_geometry": geometry, "transcript": transcript,
                              "requires_clearance": self.requires_clearance,
                              "return_clearance_mm": self.selected_clearance,
                              "xy_offset_available": True,
                              "position_probe_available": True,
                              "laser_spot_offset_mm": spot_offset,
                              "probe_xy_offset_mm": self.probe_xy_offset_mm,
                              "xy_sequence": self.xy_sequence,
                              "current_carriage_xy_mm": list(xy),
                              "physical_feedback": False})
