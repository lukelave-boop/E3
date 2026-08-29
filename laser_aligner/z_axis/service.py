from __future__ import annotations

import glob
import logging
import math
import os
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from ..config import ZAxisSettings
from ..errors import MachineError, SafetyError
from .model import ZReferenceMode, ZState, effective_safe_z_max, parse_m114, parse_m119

LOGGER = logging.getLogger(__name__)
_AUTO_DEVICE = "auto"
_CH340_BY_ID_MARKER = "1a86_usb_serial"
_HEALTH_INTERVAL_SECONDS = 2.0
_READ_SLICE_SECONDS = 0.2


class MarlinSerial(Protocol):
    def open(self) -> None: ...
    def close(self) -> None: ...
    def write_line(self, line: str) -> None: ...
    def read_line(self, timeout: float = 1.0) -> str | None: ...
    def drain(self) -> list[str]: ...


def resolve_z_serial_device(
    configured: str,
    *,
    by_id_paths: list[str] | tuple[str, ...] | None = None,
) -> str:
    """Resolve the configured Pi-local device, preferring the stable CH340 link."""

    if type(configured) is not str or not configured.strip():
        raise MachineError("S1 Pro Z serial device must be configured or set to auto")
    value = configured.strip()
    if value.lower() != _AUTO_DEVICE:
        return value
    candidates = (
        sorted(glob.glob("/dev/serial/by-id/*"))
        if by_id_paths is None
        else sorted(str(path) for path in by_id_paths)
    )
    matches = [
        path
        for path in candidates
        if _CH340_BY_ID_MARKER in Path(path).name.lower()
    ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise MachineError(
            "Could not auto-detect the S1 Pro 1a86 USB serial device under "
            "/dev/serial/by-id; configure machine.z_axis.endpoint explicitly"
        )
    raise MachineError(
        "More than one 1a86 USB serial device was found; configure the exact "
        "stable /dev/serial/by-id path"
    )


def _default_serial_factory(path: str, baudrate: int) -> MarlinSerial:
    if os.name != "posix":
        raise MachineError("The S1 Pro Z serial service requires Raspberry Pi OS/Linux")
    from ..machine.serial_posix import PosixSerial

    return PosixSerial(path, baudrate)


class ZAxisHardwareService:
    """Pi-owned persistent serial controller for S1 Pro Z and CR Touch.

    High-level operations are the only public command surface.  The old mesh is
    disabled on every homing and Z-motion path.  No laser-enable command exists
    in this service, and Marlin-reported X/Y is never consumed.
    """

    def __init__(
        self,
        settings: ZAxisSettings,
        *,
        allow_motion: bool,
        serial_factory: Callable[[str, int], MarlinSerial] = _default_serial_factory,
        sleep: Callable[[float], None] = time.sleep,
        start_health_monitor: bool = True,
    ) -> None:
        if type(allow_motion) is not bool:
            raise TypeError("allow_motion must be an exact boolean")
        self.settings = settings
        self.allow_motion = allow_motion
        self._serial_factory = serial_factory
        self._sleep = sleep
        self._start_health_monitor = bool(start_health_monitor)
        self._serial: MarlinSerial | None = None
        self._serial_device: str | None = None
        self._connected = False
        self._state = ZState.UNKNOWN
        self._current_z_mm: float | None = None
        self._effective_safe_max_mm = float(settings.safe_max_mm)
        self._reference_mode = ZReferenceMode(settings.reference_mode)
        self._surface_height_mm = settings.work_surface_height_mm
        self._last_error: str | None = None
        self._operation = "idle"
        self._state_lock = threading.RLock()
        self._connection_lock = threading.RLock()
        self._operation_lock = threading.Lock()
        self._cancel = threading.Event()
        self._home_token: str | None = None
        self._home_requested_effective_max_mm: float | None = None
        self._health_stop = threading.Event()
        self._health_thread: threading.Thread | None = None

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            return {
                "enabled": bool(self.settings.enabled),
                "connected": self._connected,
                "state": self._state.value,
                "z_known": self._state is ZState.KNOWN,
                "current_z_mm": self._current_z_mm,
                "effective_safe_max_mm": self._effective_safe_max_mm,
                "safe_max_mm": float(self.settings.safe_max_mm),
                "reference_mode": self._reference_mode.value,
                "work_surface_height_mm": self._surface_height_mm,
                "serial_device": self._serial_device,
                "operation": self._operation,
                "last_error": self._last_error,
                "focus_calibrated": (
                    self.settings.laser_focus_offset_from_probe_mm is not None
                ),
            }

    def _set_state(
        self,
        state: ZState,
        *,
        current_z_mm: float | None = None,
        error: str | None = None,
        operation: str | None = None,
    ) -> None:
        with self._state_lock:
            self._state = state
            self._current_z_mm = current_z_mm
            self._last_error = error
            if operation is not None:
                self._operation = operation

    def _mark_fault(self, message: str, *, close_serial: bool = False) -> None:
        self._set_state(ZState.FAULT, error=message, operation="fault")
        if close_serial:
            with self._connection_lock:
                serial = self._serial
                self._serial = None
                self._connected = False
                if serial is not None:
                    try:
                        serial.close()
                    except Exception:
                        LOGGER.exception("Could not close failed S1 Pro Z serial transport")

    def invalidate_position(self, reason: str, *, fault: bool = False) -> None:
        state = ZState.FAULT if fault else ZState.UNKNOWN
        self._set_state(state, error=reason if fault else None, operation="idle")

    def _require_motion_authority(self) -> None:
        if self.allow_motion is not True:
            raise SafetyError("S1 Pro Z motion is blocked by machine.allow_motion")

    def _require_connected(self) -> MarlinSerial:
        with self._connection_lock:
            if not self._connected or self._serial is None:
                raise MachineError("S1 Pro Z controller is disconnected")
            return self._serial

    def connect(self) -> dict[str, Any]:
        if not self.settings.enabled:
            raise MachineError("S1 Pro Z / CR Touch support is disabled for this machine")
        with self._connection_lock:
            if self._connected and self._serial is not None:
                self._cancel.clear()
            else:
                device = resolve_z_serial_device(self.settings.endpoint)
                serial = self._serial_factory(device, int(self.settings.baudrate))
                try:
                    serial.open()
                    if self.settings.startup_delay > 0:
                        self._sleep(float(self.settings.startup_delay))
                    try:
                        serial.drain()
                    except AttributeError:
                        pass
                    self._serial = serial
                    self._serial_device = device
                    self._connected = True
                    self._cancel.clear()
                    responses = self._execute(
                        "M115",
                        timeout=max(float(self.settings.read_timeout), 2.0),
                        fault_on_failure=False,
                        allow_startup=True,
                    )
                    identity = " ".join(responses).lower()
                    if "marlin" not in identity or "ender-3 s1 pro" not in identity:
                        raise MachineError(
                            "The Z serial device did not identify as Marlin on an "
                            "Ender-3 S1 Pro"
                        )
                except Exception:
                    self._serial = None
                    self._connected = False
                    try:
                        serial.close()
                    except Exception:
                        pass
                    raise
        self.invalidate_position("Z must be homed after controller connection")
        self._ensure_health_thread()
        return self.status()

    def close(self) -> None:
        self._health_stop.set()
        thread = self._health_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        self._best_effort_line("M280 P0 S90")
        self._best_effort_line("M420 S0")
        with self._connection_lock:
            serial = self._serial
            self._serial = None
            self._connected = False
        if serial is not None:
            try:
                serial.close()
            except Exception:
                LOGGER.exception("Could not close S1 Pro Z serial transport")
        self._set_state(ZState.UNKNOWN, operation="idle")

    def client_disconnected(self) -> None:
        """Invalidate E3-session knowledge while retaining the Pi serial owner."""

        with self._state_lock:
            failed_session = self._state in {ZState.HOMING, ZState.FAULT}
        failed_session = failed_session or self._home_token is not None
        self._cancel.set()
        if self._home_token is not None:
            self._best_effort_line("M280 P0 S90")
            self._best_effort_line("M420 S0")
            self._release_home_session()
        self.invalidate_position(
            "E3 Z client disconnected during an uncertain operation"
            if failed_session
            else "E3 Z client disconnected",
            fault=failed_session,
        )

    def request_stop(self) -> None:
        """Interrupt an in-flight Z operation after STOP or client loss."""

        self._cancel.set()
        self._best_effort_line("M112")
        self._mark_fault(
            "S1 Pro Z operation interrupted by software STOP",
            close_serial=True,
        )

    def _best_effort_line(self, line: str) -> None:
        with self._connection_lock:
            serial = self._serial
        if serial is None:
            return
        try:
            serial.write_line(line)
        except Exception:
            LOGGER.exception("Best-effort S1 Pro Z cleanup command failed")

    def _execute(
        self,
        command: str,
        *,
        timeout: float | None = None,
        fault_on_failure: bool = True,
        allow_startup: bool = False,
    ) -> list[str]:
        serial = self._require_connected()
        deadline = time.monotonic() + float(
            timeout if timeout is not None else self.settings.read_timeout
        )
        responses: list[str] = []
        try:
            try:
                serial.drain()
            except AttributeError:
                pass
            serial.write_line(command)
            while True:
                if self._cancel.is_set():
                    raise MachineError("S1 Pro Z operation was interrupted")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise MachineError(f"Timed out waiting for {command} completion")
                line = serial.read_line(min(_READ_SLICE_SECONDS, remaining))
                if line is None:
                    continue
                normalized = line.strip()
                if not normalized:
                    continue
                lower = normalized.lower()
                if not allow_startup and (
                    lower == "start"
                    or lower.startswith("echo:marlin")
                    or lower.startswith("marlin ")
                ):
                    raise MachineError(
                        "Marlin reset/startup was detected; Z position is no longer known"
                    )
                if lower == "ok" or lower.startswith("ok "):
                    if lower != "ok":
                        responses.append(normalized[3:].strip())
                    return responses
                if lower.startswith("error:") or lower.startswith("!!"):
                    raise MachineError(f"Marlin rejected {command}: {normalized}")
                # Repeated echo:busy: processing is expected during G28 and is
                # retained in the transcript while the bounded deadline remains.
                responses.append(normalized)
        except Exception as exc:
            message = str(exc) or f"S1 Pro Z command {command} failed"
            if fault_on_failure:
                if command.split(maxsplit=1)[0].upper() in {"G0", "G1", "G28"}:
                    try:
                        serial.write_line("M112")
                    except Exception:
                        LOGGER.exception(
                            "Could not interrupt uncertain S1 Pro Z motion"
                        )
                self._mark_fault(message, close_serial=True)
            raise MachineError(message) from exc

    def _query_z(self) -> tuple[float, list[str]]:
        responses = self._execute("M114")
        z_mm = float(parse_m114(responses)["Z"])
        with self._state_lock:
            self._current_z_mm = z_mm
        return z_mm, responses

    def _ensure_health_thread(self) -> None:
        if not self._start_health_monitor:
            return
        thread = self._health_thread
        if thread is not None and thread.is_alive():
            return
        self._health_stop.clear()
        thread = threading.Thread(
            target=self._health_loop,
            name="s1pro-z-health",
            daemon=True,
        )
        self._health_thread = thread
        thread.start()

    def _health_loop(self) -> None:
        while not self._health_stop.wait(_HEALTH_INTERVAL_SECONDS):
            if not self._connected or not self._operation_lock.acquire(blocking=False):
                continue
            try:
                self._cancel.clear()
                z_mm, _responses = self._query_z()
                with self._state_lock:
                    above_ceiling = (
                        self._state is ZState.KNOWN
                        and z_mm > self._effective_safe_max_mm + 1e-6
                    )
                if above_ceiling:
                    self._mark_fault(
                        "Reported Z exceeds the active application ceiling"
                    )
            except Exception as exc:
                self._mark_fault(str(exc), close_serial=True)
            finally:
                self._operation_lock.release()

    def test_probe(self) -> dict[str, Any]:
        self._require_motion_authority()
        if not self._operation_lock.acquire(blocking=False):
            raise MachineError("Another S1 Pro Z operation is already active")
        transcript: list[dict[str, Any]] = []
        self._cancel.clear()
        with self._state_lock:
            self._operation = "probe_test"
        try:
            for command in ("M280 P0 S10", "M119"):
                responses = self._execute(command)
                transcript.append({"command": command, "responses": responses})
            states = parse_m119(transcript[-1]["responses"])
            if states["z_min"] != "open":
                raise MachineError(
                    f"CR Touch readiness failed: expected z_min open, got {states['z_min']}"
                )
            responses = self._execute("M280 P0 S90")
            transcript.append({"command": "M280 P0 S90", "responses": responses})
            with self._state_lock:
                self._operation = "idle"
            return {"passed": True, "z_min": "open", "transcript": transcript}
        except Exception as exc:
            self._best_effort_line("M280 P0 S90")
            self._mark_fault(str(exc))
            raise
        finally:
            self._operation_lock.release()

    def prepare_home(
        self,
        *,
        confirmed_unknown: bool,
        effective_max_mm: float,
    ) -> dict[str, Any]:
        self._require_motion_authority()
        if type(confirmed_unknown) is not bool:
            raise TypeError("confirmed_unknown must be an exact boolean")
        maximum = float(effective_max_mm)
        if not math.isfinite(maximum) or not 0 < maximum <= self.settings.safe_max_mm:
            raise SafetyError("Effective Z maximum is outside the configured safe range")
        if not self._operation_lock.acquire(blocking=False):
            raise MachineError("Another S1 Pro Z operation is already active")
        with self._state_lock:
            was_known = self._state is ZState.KNOWN
            clearance_maximum = self._effective_safe_max_mm
        if not was_known and not confirmed_unknown:
            self._operation_lock.release()
            raise SafetyError(
                "Z position is unknown; explicit gantry-clear confirmation is required"
            )
        self._cancel.clear()
        self._set_state(ZState.HOMING, operation="prehome_clearance")
        token = uuid.uuid4().hex
        self._home_token = token
        self._home_requested_effective_max_mm = maximum
        transcript: list[dict[str, Any]] = []

        def execute(command: str, timeout: float | None = None) -> list[str]:
            responses = self._execute(command, timeout=timeout)
            transcript.append({"command": command, "responses": responses})
            return responses

        try:
            execute("M280 P0 S90")
            execute("M420 S0")
            if was_known:
                current_z, responses = self._query_z()
                transcript.append({"command": "M114", "responses": responses})
                if (
                    current_z
                    > clearance_maximum + self.settings.verification_tolerance_mm
                ):
                    raise SafetyError(
                        f"Current Z {current_z:.3f} mm exceeds the active "
                        f"{clearance_maximum:.3f} mm ceiling"
                    )
                target = min(
                    current_z + float(self.settings.prehome_lift_mm),
                    clearance_maximum,
                )
                if target > current_z + 1e-6:
                    execute("G90")
                    execute(
                        f"G1 Z{target:.3f} F{float(self.settings.prehome_feed_mm_min):.3f}",
                        timeout=max(30.0, float(self.settings.read_timeout)),
                    )
                    measured, responses = self._query_z()
                    transcript.append({"command": "M114", "responses": responses})
                    if abs(measured - target) > self.settings.verification_tolerance_mm:
                        raise MachineError(
                            "Pre-home Z lift did not reach its verified absolute target"
                        )
                clearance = {"kind": "absolute", "target_z_mm": target}
            else:
                execute("G91")
                move_error: Exception | None = None
                try:
                    execute(
                        f"G1 Z{float(self.settings.prehome_lift_mm):.3f} "
                        f"F{float(self.settings.prehome_feed_mm_min):.3f}",
                        timeout=max(30.0, float(self.settings.read_timeout)),
                    )
                except Exception as exc:
                    move_error = exc
                    raise
                finally:
                    if move_error is None:
                        execute("G90")
                    else:
                        self._best_effort_line("G90")
                clearance = {
                    "kind": "confirmed_unknown_relative",
                    "lift_mm": float(self.settings.prehome_lift_mm),
                }
            with self._state_lock:
                self._operation = "waiting_for_real_xy"
            return {
                "token": token,
                "was_known": was_known,
                "clearance": clearance,
                "transcript": transcript,
            }
        except Exception as exc:
            self._best_effort_line("G90")
            self._best_effort_line("M280 P0 S90")
            self._best_effort_line("M420 S0")
            self._mark_fault(str(exc))
            self._release_home_session()
            raise

    def complete_home(
        self,
        token: str,
        *,
        reference_mode: ZReferenceMode | str,
        surface_height_mm: float | None,
        effective_max_mm: float,
    ) -> dict[str, Any]:
        if token != self._home_token or not token:
            raise MachineError("S1 Pro Z homing session is missing or stale")
        try:
            mode = ZReferenceMode(reference_mode)
            maximum = effective_safe_z_max(
                float(self.settings.safe_max_mm),
                mode,
                surface_height_mm,
                minimum_homed_z_mm=float(self.settings.expected_homed_z_mm),
            )
            if abs(maximum - float(effective_max_mm)) > 1e-6:
                raise SafetyError("Effective Z ceiling changed during homing")
            if (
                self._home_requested_effective_max_mm is None
                or abs(maximum - self._home_requested_effective_max_mm) > 1e-6
            ):
                raise SafetyError("Requested Z reference ceiling changed during homing")
        except Exception as exc:
            self._mark_fault(str(exc))
            self._release_home_session()
            raise
        transcript: list[dict[str, Any]] = []

        def execute(command: str, timeout: float | None = None) -> list[str]:
            responses = self._execute(command, timeout=timeout)
            transcript.append({"command": command, "responses": responses})
            return responses

        self._set_state(ZState.HOMING, operation="probe_and_home")
        try:
            execute("M280 P0 S10")
            probe_responses = execute("M119")
            probe = parse_m119(probe_responses)
            if probe["z_min"] != "open":
                raise MachineError(
                    f"CR Touch readiness failed: expected z_min open, got {probe['z_min']}"
                )
            execute("M280 P0 S90")
            execute("G28", timeout=float(self.settings.homing_timeout))
            execute("M420 S0")
            final_z, responses = self._query_z()
            transcript.append({"command": "M114", "responses": responses})
            expected = float(self.settings.expected_homed_z_mm)
            tolerance = float(self.settings.verification_tolerance_mm)
            if abs(final_z - expected) > tolerance:
                raise MachineError(
                    f"G28 verification failed: expected Z {expected:.3f} ± "
                    f"{tolerance:.3f} mm, received {final_z:.3f} mm"
                )
            if final_z > maximum + tolerance:
                raise MachineError("Verified home lies above the active Z safety ceiling")
            with self._state_lock:
                self._effective_safe_max_mm = maximum
                self._reference_mode = mode
                self._surface_height_mm = surface_height_mm
                self._state = ZState.KNOWN
                self._current_z_mm = final_z
                self._last_error = None
                self._operation = "idle"
            return {
                "z_known": True,
                "current_z_mm": final_z,
                "effective_safe_max_mm": maximum,
                "reference_mode": mode.value,
                "transcript": transcript,
            }
        except Exception as exc:
            self._best_effort_line("M280 P0 S90")
            self._best_effort_line("M420 S0")
            self._mark_fault(str(exc))
            raise
        finally:
            self._release_home_session()

    def abort_home(self, token: str, reason: str) -> None:
        if token != self._home_token or not token:
            return
        self._best_effort_line("M280 P0 S90")
        self._best_effort_line("M420 S0")
        self._mark_fault(reason)
        self._release_home_session()

    def _release_home_session(self) -> None:
        if self._home_token is None:
            return
        self._home_token = None
        self._home_requested_effective_max_mm = None
        if self._operation_lock.locked():
            self._operation_lock.release()

    def move_absolute(self, target_z_mm: float, *, effective_max_mm: float) -> dict[str, Any]:
        """Guard one ordinary absolute Z move for current/future UI consumers."""

        self._require_motion_authority()
        if type(target_z_mm) not in {int, float} or not math.isfinite(float(target_z_mm)):
            raise SafetyError("Requested Z target must be a finite number")
        target = float(target_z_mm)
        maximum = min(float(effective_max_mm), float(self.settings.safe_max_mm))
        if target < 0.0 or target > maximum:
            raise SafetyError(
                f"Requested Z {target:g} mm exceeds the active 0..{maximum:g} mm range"
            )
        if not self._operation_lock.acquire(blocking=False):
            raise MachineError("Another S1 Pro Z operation is already active")
        self._cancel.clear()
        try:
            with self._state_lock:
                if self._state is not ZState.KNOWN:
                    raise SafetyError("Z position is unknown; home before absolute Z motion")
                active_maximum = self._effective_safe_max_mm
            if target > active_maximum:
                raise SafetyError(
                    f"Requested Z {target:g} mm exceeds the active {active_maximum:g} mm ceiling"
                )
            self._set_state(ZState.HOMING, operation="absolute_move")
            transcript: list[dict[str, Any]] = []
            for command in (
                "M420 S0",
                "G90",
                f"G1 Z{target:.3f} F{float(self.settings.prehome_feed_mm_min):.3f}",
            ):
                responses = self._execute(command, timeout=max(30.0, self.settings.read_timeout))
                transcript.append({"command": command, "responses": responses})
            measured, responses = self._query_z()
            transcript.append({"command": "M114", "responses": responses})
            if abs(measured - target) > self.settings.verification_tolerance_mm:
                raise MachineError("Absolute Z move did not reach its verified target")
            self._set_state(ZState.KNOWN, current_z_mm=measured, operation="idle")
            return {"current_z_mm": measured, "transcript": transcript}
        except Exception as exc:
            self._mark_fault(str(exc))
            raise
        finally:
            self._operation_lock.release()
