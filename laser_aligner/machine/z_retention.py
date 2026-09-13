"""One-use clean-disconnect Z checkpoints and typed, non-motion restoration.

The file is dirtied before controller admission. Only MachineService can bless
a clean disconnect; a cached position or a normal Windows exit is insufficient.
The operator assumption is that the unpowered axis and its datum have not moved.
"""
from __future__ import annotations

import copy
import os
import re
import tempfile
import threading
import uuid
from contextlib import contextmanager, nullcontext
from pathlib import Path

from ..errors import MachineError, SafetyError
from ..storage import _fsync_parent_directory, atomic_write_json, strict_json_loads
from .laser_focus import parse_geometry
from .mainboard import parse_status
from .z_probe import finite_number, parse_position

CAPABILITY = "Cap:E3_Z_RESTORE_V1:1"
_RESTORED = re.compile(r"E3ZR:1 Z:(\d+\.\d{3})")
_MAX_BYTES = 32768


def z_retention_path(focus_path: Path | None) -> Path | None:
    return None if focus_path is None else focus_path.with_name(focus_path.name + ".z-retention.json")


def validate_snapshot(snapshot):
    if type(snapshot) is not dict or set(snapshot) != {"z_mm", "clearance_z_mm", "max_z_mm", "reference"}:
        raise SafetyError("Invalid saved Z checkpoint")
    maximum = _number(snapshot["max_z_mm"], "Saved Z maximum", 20, 80)
    clearance = _number(snapshot["clearance_z_mm"], "Saved Z clearance", 20, maximum)
    _number(snapshot["z_mm"], "Saved Z", clearance, maximum)
    reference = snapshot["reference"]
    if type(reference) is not dict or set(reference) != {"border_z_mm", "firmware", "geometry"}:
        raise SafetyError("Invalid saved Z border reference")
    _number(reference["border_z_mm"], "Saved border contact", -.5, .5)
    firmware = reference["firmware"]
    if type(firmware) is not str or not 1 <= len(firmware) <= 8192:
        raise SafetyError("Invalid saved Z firmware identity")
    geometry = parse_geometry(firmware.splitlines())
    if (type(reference["geometry"]) is not dict
        or any(type(v) not in {int, float} for v in reference["geometry"].values())
        or reference["geometry"] != geometry or not restore_supported(firmware.splitlines())):
        raise SafetyError("Saved Z firmware or geometry does not support restoration")
    return copy.deepcopy(snapshot)


def _number(value, label, lower, upper):
    try:
        return finite_number(value, label, lower, upper)
    except OverflowError as exc:
        raise SafetyError(f"{label} is outside the supported range") from exc


def restore_supported(lines):
    return [word for word in " ".join(lines).split() if word.startswith("Cap:E3_Z_RESTORE")] == [CAPABILITY]


class ZRetention:
    """Atomic consume-before-use storage, separate from saved gauge calibration.

    invalidate() is memory-only for priority STOP. The service calls discard()
    after priority controller cleanup. save() stages/fsyncs before entering the
    service's final publication guard; that guard checks its STOP epoch.
    """

    def __init__(self, path: Path | None, binding=None):
        self.path = path
        self.binding = copy.deepcopy({} if binding is None else binding)
        self._revision = 0
        self._claim = None
        self._lock = threading.RLock()
        self.restored = False
        self.reason = "No clean saved Z; reference the border."

    def revision(self):
        return self._revision

    def invalidate(self):
        self._revision += 1
        self.restored = False
        self.reason = "Z reference discarded; reference the border."

    def status(self):
        return {"available": self.path is not None, "restored": self.restored, "reason": self.reason}

    @contextmanager
    def _file_guard(self):
        assert self.path is not None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        with lock_path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                raise MachineError("Saved Z is in use by another E3 process") from exc
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _dirty(self):
        return {"schema_version": 1, "clean": False, "binding": self.binding,
                "snapshot": None, "session": self._claim}

    def _require_claim_locked(self):
        data = self._read_locked()
        if data != self._dirty() or self._claim is None:
            raise MachineError("Another E3 session replaced the saved Z claim; reference the border")

    def _read_locked(self):
        with self.path.open("rb") as handle:
            raw = handle.read(_MAX_BYTES + 1)
        if len(raw) > _MAX_BYTES:
            raise ValueError("Checkpoint is too large")
        return strict_json_loads(raw.decode("utf-8"))

    def _consume_inode_locked(self):
        # Invalidate the OLD inode durably before replacing its directory entry.
        # Even a lost/rolled-back rename must never resurrect yesterday's clean
        # coordinates. fsync failure blocks admission on Windows and the Pi.
        try:
            handle = self.path.open("r+b")
        except FileNotFoundError:
            return
        with handle:
            handle.write(b"!")
            handle.flush()
            os.fsync(handle.fileno())

    def assert_claim(self):
        """Reject a delayed consumed checkpoint if another process has used E3."""
        if self.path is not None:
            try:
                with self._lock, self._file_guard():
                    self._require_claim_locked()
            except (OSError, ValueError) as exc:
                raise MachineError(f"Cannot verify saved Z session claim: {exc}") from exc

    @contextmanager
    def claim_guard(self):
        """Lease a consumed claim through controller verification/publication."""
        if self.path is None:
            raise MachineError("Persistent saved Z is unavailable")
        revision = self._revision
        with self._file_guard():
            self._require_claim_locked()
            yield
            if revision != self._revision:
                raise MachineError("Saved Z restoration was cancelled")
            self._require_claim_locked()

    def take(self):
        """Consume even corrupt/mismatched data; fail admission if invalidation fails."""
        self.invalidate()
        if self.path is None:
            return None
        candidate = None
        try:
            with self._lock, self._file_guard():
                self._claim = str(uuid.uuid4())
                try:
                    data = self._read_locked()
                    if (type(data) is not dict or set(data) != {"schema_version", "clean", "binding", "snapshot", "session"}
                        or type(data["schema_version"]) is not int or data["schema_version"] != 1
                        or type(data["clean"]) is not bool or type(data["session"]) is not str
                        or str(uuid.UUID(data["session"])) != data["session"]):
                        raise ValueError("Unknown checkpoint format")
                    if data["clean"] and data["binding"] == self.binding:
                        candidate = validate_snapshot(data["snapshot"])
                    elif data["clean"]:
                        self.reason = "Machine configuration changed; reference the border."
                    else:
                        self.reason = "Previous session did not leave a clean saved Z; reference the border."
                except FileNotFoundError:
                    self.reason = "No clean saved Z; reference the border."
                except (ValueError, UnicodeError, MachineError, RecursionError):
                    self.reason = "Invalid saved Z; reference the border."
                self._consume_inode_locked()
                atomic_write_json(self.path, self._dirty())
        except OSError as exc:
            raise MachineError(f"Cannot invalidate saved Z before connecting: {exc}") from exc
        if candidate is not None:
            self.reason = "Clean saved Z awaits controller verification."
        return candidate

    def discard(self):
        self.invalidate()
        if self.path is not None:
            try:
                with self._lock, self._file_guard():
                    try:
                        current = self._read_locked()
                    except (OSError, ValueError, RecursionError):
                        current = None
                    if (type(current) is not dict or self._claim is None
                        or current.get("session") != self._claim or current.get("binding") != self.binding):
                        # STOP can revoke a file, but cannot steal another
                        # process's claim and later bless its own old snapshot.
                        self._claim = None
                    dirty = self._dirty()
                    if self._claim is None:
                        dirty["session"] = str(uuid.uuid4())
                    self._consume_inode_locked()
                    atomic_write_json(self.path, dirty)
            except OSError as exc:
                raise MachineError(f"Cannot discard saved Z: {exc}") from exc

    def save(self, snapshot, *, expected_revision=None, guard=nullcontext):
        snapshot = validate_snapshot(snapshot)
        if self.path is None:
            return False
        revision = self._revision if expected_revision is None else expected_revision
        payload = {"schema_version": 1, "clean": True, "binding": self.binding,
                   "snapshot": snapshot, "session": self._claim}
        # Stage all potentially slow serialization/fsync outside the STOP gate.
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        staged = Path(name)
        os.close(descriptor)
        try:
            atomic_write_json(staged, payload)
            with self._lock, self._file_guard(), guard():
                if revision != self._revision:
                    return False
                self._require_claim_locked()
                os.replace(staged, self.path)
            _fsync_parent_directory(self.path)
            if revision != self._revision:
                self.discard()
                return False
            self.restored = False
            self.reason = f"Clean shutdown saved Z{snapshot['z_mm']:.3f} mm."
            return True
        except OSError as exc:
            raise MachineError(f"Cannot save clean Z checkpoint: {exc}") from exc
        finally:
            staged.unlink(missing_ok=True)


def _sender(owner, guard):
    generation = owner.generation

    def send(command):
        with guard():
            if not owner.ready or owner.generation != generation:
                raise MachineError("Ender session changed during saved Z verification")
        lines = owner._execute_acknowledged(command, allow_open=False, timeout=3,
                                          write_guard=guard, interrupt_on_failure=False)
        with guard():
            if not owner.ready or owner.generation != generation:
                raise MachineError("Ender session changed during saved Z verification")
        return lines

    return send


def _stowed(send, known):
    flags = [line.strip().lower() for line in send("M119")]
    if ([line for line in flags if line.startswith("z_min:")] != ["z_min: triggered"]
        or [line for line in flags if line.startswith("test_axis_known_z_flag")]
        != [f"test_axis_known_z_flag = {str(known).lower()}"]):
        raise SafetyError("Saved Z requires consistent known-axis and stowed-probe reports")


def capture_z(owner, focus, maximum, guard):
    """Read a complete reference at clearance, without moving or probing."""
    with owner._lock:
        reference = focus.retain_z_reference(owner.generation)
        if reference is None or focus.requires_clearance or focus.xy_recovery_pending_reference:
            raise SafetyError("No trusted border reference at clearance to save")
        send = _sender(owner, guard)
        identity = send("M115")
        if not restore_supported(identity) or "\n".join(identity) != reference["firmware"]:
            raise SafetyError("Firmware does not match the restorable Z reference")
        send("M400")
        board = parse_status(send("M123"))
        if not board["z_known"] or board["fan2_pwm"] != 0:
            raise SafetyError("Known Z and acknowledged work fan OFF are required to save Z")
        z = parse_position(send("M114"))
        _stowed(send, True)
        final = parse_position(send("M114"))
        if abs(final - z) > .001 or not parse_status(send("M123"))["z_known"]:
            raise SafetyError("Z changed during clean shutdown verification")
        return validate_snapshot({"z_mm": z, "clearance_z_mm": focus.selected_clearance,
                                  "max_z_mm": maximum, "reference": reference})


def restore_z(owner, focus, snapshot, maximum, guard):
    """Restore coordinates and motor hold. No travel, probing or laser output."""
    focus.drop_z_reference()
    snapshot = validate_snapshot(snapshot)
    if snapshot["max_z_mm"] != maximum:
        raise SafetyError("Z maximum changed; reference the border")
    with owner._lock:
        send = _sender(owner, guard)
        identity = send("M115")
        if "\n".join(identity) != snapshot["reference"]["firmware"] or not restore_supported(identity):
            raise SafetyError("Ender firmware changed; reference the border")
        before = parse_status(send("M123"))
        initial = parse_position(send("M114"))
        _stowed(send, before["z_known"])
        if before["fan2_pwm"] != 0:
            raise SafetyError("Work fan must be off before restoring Z")
        target = snapshot["z_mm"]
        if before["z_known"]:
            if abs(initial - target) > .001:
                raise SafetyError("Live Z differs from saved Z; reference the border")
        else:
            if abs(initial) > .001:
                raise SafetyError("Only fresh reset Z can accept the saved coordinate")
        send("G21")
        send("G90")
        # Firmware also verifies an already-known matching coordinate, without
        # rewriting it. This checks native offsets/leveling/queue state that an
        # M114 logical-coordinate match alone cannot prove.
        reports = [line.strip() for line in send(f"M124 Z{target:.3f}") if line.strip().startswith("E3ZR")]
        match = _RESTORED.fullmatch(reports[0]) if len(reports) == 1 else None
        if match is None or abs(float(match[1]) - target) > .001:
            raise MachineError("Firmware did not confirm restored Z; reference the border")
        send("M84 S0")
        send("M17 Z")
        send("M400")
        if abs(parse_position(send("M114")) - target) > .001 or not parse_status(send("M123"))["z_known"]:
            raise MachineError("Restored Z readback failed; reference the border")
        _stowed(send, True)
        with guard():
            focus.restore_z_reference(snapshot["reference"], owner.generation, snapshot["clearance_z_mm"])
