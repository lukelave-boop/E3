from __future__ import annotations

import hashlib
import logging
import math
import re
import threading
import time
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .._compat import add_exception_note
from ..config import LaserSettings, MachineSettings
from ..errors import MachineError, SafetyError
from ..gcode.preview import contains_motion, parse_words, strip_comment
from ..geometry.polygon import (
    ConvexPolygon,
    convex_polygon_contains_normalized,
    normalize_convex_polygon,
)
from .serial_backend import (
    MachineTransport,
    create_serial_transport,
)
from .serial_backend import list_serial_ports as list_serial_ports
from .simulator import SimulatedTransport

LOGGER = logging.getLogger(__name__)
_QUERY_COMMANDS = {"$I", "$$", "$G", "$#", "M105", "M114", "M115", "M503"}
_STREAM_G_CODES = {0, 1, 21, 90}
_STREAM_M_CODES = {3, 4, 5}
_STREAM_LETTERS = {"G", "M", "X", "Y", "F", "S"}
_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS = 6.0
_JOB_COMMAND_ACK_TIMEOUT_SECONDS = 120.0
_GRBL_COORDINATE_EPSILON_MM = 0.001
_REALTIME_STOP_WRITE_DEADLINE_SECONDS = 0.35
_MAX_ARM_TIMEOUT_SECONDS = 600
_GRBL_WORK_COORDINATE_CODES = {f"G{number}" for number in range(54, 60)}
_GRBL_OFFSET_PATTERN = re.compile(
    r"^\[(G5[4-9]|G92):\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)),\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)),\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\]$",
    re.IGNORECASE,
)
_GRBL_STEP_IDLE_PATTERN = re.compile(
    r"^\s*\$1\s*=\s*(\d+)(?:\.0*)?(?:\s+\([^)]*\))?\s*$",
    re.IGNORECASE,
)
_PROGRAM_DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class _ControllerCommandRejected(MachineError):
    """A complete controller rejection whose response has been consumed."""


@dataclass(slots=True)
class JobStatus:
    running: bool = False
    phase: str = "idle"
    name: str = ""
    total_lines: int = 0
    completed_lines: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None
    program_digest: str = ""
    powered: bool = False

    def to_dict(self) -> dict[str, Any]:
        elapsed = None
        if self.started_at is not None:
            end = self.finished_at or time.time()
            elapsed = max(0.0, end - self.started_at)
        return {
            "running": self.running,
            "phase": self.phase,
            "name": self.name,
            "total_lines": self.total_lines,
            "completed_lines": self.completed_lines,
            "progress": 0.0 if self.total_lines == 0 else self.completed_lines / self.total_lines,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": elapsed,
            "error": self.error,
            "program_digest": self.program_digest or None,
            "powered": self.powered,
        }


@dataclass(frozen=True, slots=True)
class ValidatedProgram:
    lines: tuple[str, ...]
    digest: str
    requires_laser_authorization: bool
    requires_motion: bool
    safety_profile: tuple[Any, ...]
    guarded_output_polygon_mm: ConvexPolygon | None = None


class MachineService:
    ARM_PHRASE = "ENABLE LASER CONTROL"

    def __init__(
        self,
        settings: MachineSettings,
        laser_settings: LaserSettings,
        hardware_enabled: bool = False,
        laser_lockout: bool = False,
    ):
        if type(laser_lockout) is not bool:
            raise TypeError("laser_lockout must be an exact boolean")
        self.settings = settings
        self.laser_settings = laser_settings
        self.hardware_enabled = hardware_enabled
        self.laser_lockout = laser_lockout
        self._transport: MachineTransport | None = None
        self._protocol = "simulator" if settings.backend == "simulator" else settings.protocol
        self._active_port = settings.port
        self._active_baudrate = settings.baudrate
        self._connected = False
        self._connecting = False
        self._controller_reconnect_required = False
        self._coordinate_reference_ready = settings.backend == "simulator"
        self._coordinate_state_reference: dict[str, Any] | None = None
        # Incremental UI jogging is translated to absolute moves.  This
        # position is published only by Home / park or a completed jog and is
        # invalidated whenever another operation can change machine position.
        self._jog_position_mm: tuple[float, float] | None = None
        self._armed_until = 0.0
        self._armed_until_monotonic = 0.0
        self._armed_program_digest: str | None = None
        self._lock = threading.RLock()
        # Own each command/ack exchange, and allow a multi-command operation
        # to retain that ownership through its complete controller sequence.
        # Emergency stop intentionally bypasses this lock.
        self._command_lock = threading.RLock()
        # This tiny gate orders software STOP after any in-flight job write.
        # It is never held while waiting for a controller acknowledgement.
        self._transport_write_lock = threading.RLock()
        self._job = JobStatus()
        self._job_thread: threading.Thread | None = None
        self._job_stop = threading.Event()
        self._stop_epoch_lock = threading.Lock()
        self._stop_epoch = 0
        self._authorization_epoch = 0
        self._operation_context = threading.local()
        self._job_laser_authorized = False
        self._last_successful_job: dict[str, Any] | None = None
        self._log: deque[str] = deque(maxlen=200)

    @property
    def connected(self) -> bool:
        return self._connected and not self._connecting

    @property
    def armed(self) -> bool:
        return self._connected and time.monotonic() < self._armed_until_monotonic

    def _clear_arm_authorization(self) -> None:
        self._armed_until = 0.0
        self._armed_until_monotonic = 0.0
        self._armed_program_digest = None

    def _mark_controller_command_state_untrusted(self) -> None:
        """Revoke authority after an exchange whose ACK position is unknown."""

        with self._stop_epoch_lock:
            if self._transport is not None:
                self._controller_reconnect_required = True
            self._coordinate_reference_ready = False
            self._coordinate_state_reference = None
            self._jog_position_mm = None
            self._authorization_epoch += 1
            self._clear_arm_authorization()
            self._job_laser_authorized = False

    def operation_generation(self) -> int:
        """Capture the STOP generation when an operation is requested.

        Queue owners should retain this value and bind it in
        :meth:`operation_scope` inside the eventual worker. That makes a STOP
        cancel work which was already queued but had not begun executing.
        """

        with self._stop_epoch_lock:
            return self._stop_epoch

    @contextmanager
    def operation_scope(self, generation: int):
        """Bind a request-time STOP generation to machine calls on this thread."""

        if isinstance(generation, bool) or not isinstance(generation, int):
            raise TypeError("Machine operation generation must be an integer")
        previous = getattr(self._operation_context, "stop_epoch", None)
        self._operation_context.stop_epoch = generation
        try:
            yield
        finally:
            if previous is None:
                try:
                    del self._operation_context.stop_epoch
                except AttributeError:
                    pass
            else:
                self._operation_context.stop_epoch = previous

    def _operation_stop_epoch(self) -> int:
        bound = getattr(self._operation_context, "stop_epoch", None)
        if bound is not None:
            return int(bound)
        return self.operation_generation()

    def _append_log(self, direction: str, line: str) -> None:
        entry = f"{time.strftime('%H:%M:%S')} {direction} {line}"
        self._log.append(entry)
        LOGGER.debug("machine %s %s", direction, line)

    def connect(self, port: str | None = None, protocol: str | None = None, baudrate: int | None = None) -> dict[str, Any]:
        self._require_safety_configuration()
        connect_stop_epoch = self._operation_stop_epoch()
        with self._command_lock, self._lock:
            if self._connected:
                return self.status()
            if self.settings.backend == "serial" and self.hardware_enabled is not True:
                raise SafetyError(
                    "Serial hardware is disabled for this process. Start with --hardware after reviewing the configuration."
                )
            selected_protocol = self.settings.protocol if protocol is None else protocol
            if type(selected_protocol) is not str:
                raise MachineError("Protocol must be auto, grbl, or marlin")
            selected = selected_protocol.lower()
            if selected not in {"auto", "grbl", "marlin"}:
                raise MachineError("Protocol must be auto, grbl, or marlin")
            active_port = self.settings.port if port is None else port
            if type(active_port) is not str or not active_port.strip():
                raise MachineError("Controller port must be a non-empty string")
            active_baudrate = self.settings.baudrate if baudrate is None else baudrate
            if type(active_baudrate) is not int or active_baudrate <= 0:
                raise MachineError("Controller baud rate must be a positive integer")
            if self.settings.backend == "simulator":
                transport: MachineTransport = SimulatedTransport()
            else:
                transport = create_serial_transport(active_port, active_baudrate)
            self._connecting = True
            try:
                transport.open()
                with self._stop_epoch_lock:
                    if self._stop_epoch != connect_stop_epoch:
                        raise MachineError(
                            "Controller connection was cancelled by software STOP"
                        )
                    self._transport = transport
                    self._connected = True
                    self._controller_reconnect_required = False
                self._active_port = "simulator" if self.settings.backend == "simulator" else active_port
                self._active_baudrate = active_baudrate
                self._coordinate_reference_ready = self.settings.backend == "simulator"
                self._coordinate_state_reference = None
                self._jog_position_mm = None
                self._clear_arm_authorization()
                if self.settings.backend == "simulator":
                    self._protocol = "grbl"
                elif selected == "auto":
                    self._protocol = self._identify_protocol(
                        expected_stop_epoch=connect_stop_epoch
                    )
                else:
                    self._wait_for_controller_startup(
                        expected_stop_epoch=connect_stop_epoch
                    )
                    self._protocol = selected
                if self.settings.backend == "serial" and self._protocol == "grbl":
                    self._normalize_and_release_grbl_after_connect()
                elif self.settings.backend == "serial" and self._protocol == "marlin":
                    self._send_command_locked(
                        "M5",
                        timeout=max(
                            _PHOTO_COMMAND_ACK_TIMEOUT_SECONDS,
                            self.settings.read_timeout,
                        ),
                        _expected_stop_epoch=connect_stop_epoch,
                    )
                with self._stop_epoch_lock:
                    if (
                        self._stop_epoch != connect_stop_epoch
                        or self._controller_reconnect_required
                    ):
                        raise MachineError(
                            "Controller connection was cancelled by software STOP"
                        )
            except Exception:
                with self._transport_write_lock:
                    # The transport may have physically opened just as STOP
                    # cancelled publication. It is not yet visible to
                    # request_stop(), so this cleanup owns the best-effort M5.
                    try:
                        transport.write_line("M5")
                        self._append_log("TX", "M5 (connection cleanup)")
                    except Exception:
                        pass
                    transport.close()
                self._transport = None
                self._connected = False
                self._controller_reconnect_required = False
                self._coordinate_reference_ready = False
                self._jog_position_mm = None
                self._clear_arm_authorization()
                raise
            finally:
                self._connecting = False
            self._append_log("INFO", f"connected using {self._protocol}")
            return self.status()

    def ensure_connected(self) -> dict[str, Any]:
        """Return a trusted connection, reconnecting when necessary."""

        # Keep the observe-disconnect-connect sequence under the same ownership
        # used by controller operations. Otherwise two queued operations can
        # both observe one untrusted connection and the later caller can tear
        # down the fresh connection established by the first caller.
        with self._command_lock:
            with self._lock:
                connected = self._connected and self._transport is not None
                reconnect_required = self._controller_reconnect_required
            if connected and not reconnect_required:
                return self.status()
            if connected:
                self.disconnect()
            return self.connect()

    def _wait_for_controller_startup(
        self,
        *,
        expected_stop_epoch: int,
    ) -> list[str]:
        assert self._transport is not None
        time.sleep(max(0.0, self.settings.controller_startup_delay))
        with self._stop_epoch_lock:
            if self._stop_epoch != expected_stop_epoch:
                raise MachineError("Controller connection was cancelled by software STOP")
        startup = self._transport.drain()
        for line in startup:
            self._append_log("RX", line)
        return startup

    def _identify_protocol(self, *, expected_stop_epoch: int) -> str:
        startup = self._wait_for_controller_startup(
            expected_stop_epoch=expected_stop_epoch
        )
        joined = "\n".join(startup).lower()
        if "grbl" in joined:
            return "grbl"
        try:
            responses = self._send_command_locked(
                "$I",
                timeout=1.0,
                _expected_stop_epoch=expected_stop_epoch,
                _terminal_error_consumed=True,
            )
        except _ControllerCommandRejected:
            responses = []
        if any("grbl" in line.lower() or "[ver:" in line.lower() for line in responses):
            return "grbl"
        try:
            responses = self._send_command_locked(
                "M115",
                timeout=1.5,
                _expected_stop_epoch=expected_stop_epoch,
                _terminal_error_consumed=True,
            )
        except _ControllerCommandRejected:
            responses = []
        if any("firmware_name" in line.lower() or "marlin" in line.lower() for line in responses):
            return "marlin"
        raise MachineError(
            "Controller protocol could not be identified. Set machine.protocol explicitly after running tools/controller_probe.py."
        )

    def disconnect(self) -> None:
        self.stop_job(emergency=False)
        with self._command_lock, self._lock:
            if self._transport is not None:
                try:
                    self._transport.write_line("M5")
                except Exception:
                    pass
                self._transport.close()
            self._transport = None
            self._connected = False
            self._connecting = False
            self._controller_reconnect_required = False
            self._coordinate_reference_ready = False
            self._coordinate_state_reference = None
            self._jog_position_mm = None
            self._clear_arm_authorization()
            self._job_laser_authorized = False

    def arm(
        self,
        phrase: str,
        *,
        program_digest: str | None = None,
        _expected_stop_epoch: int | None = None,
        _expected_authorization_epoch: int | None = None,
    ) -> float:
        # Arming participates in controller-operation ownership even though it
        # does not write to the transport. This keeps a jog or Home / park from
        # passing its unarmed gate and then becoming armed mid-motion.
        requested_stop_epoch = (
            self._operation_stop_epoch()
            if _expected_stop_epoch is None
            else _expected_stop_epoch
        )
        with self._stop_epoch_lock:
            if self._stop_epoch != requested_stop_epoch:
                raise MachineError("Arming was cancelled by software STOP")
            requested_authorization_epoch = (
                self._authorization_epoch
                if _expected_authorization_epoch is None
                else _expected_authorization_epoch
            )
            if self._authorization_epoch != requested_authorization_epoch:
                raise MachineError("Arming was cancelled by disarm")
            # Preserve the request-time one-use semantics while waiting for a
            # controller operation to finish.
            self._clear_arm_authorization()
        with self._command_lock:
            return self._arm_locked(
                phrase,
                program_digest=program_digest,
                _expected_stop_epoch=requested_stop_epoch,
                _expected_authorization_epoch=requested_authorization_epoch,
            )

    def _arm_locked(
        self,
        phrase: str,
        *,
        program_digest: str | None,
        _expected_stop_epoch: int | None,
        _expected_authorization_epoch: int | None,
    ) -> float:
        # Every attempt consumes an earlier grant, even when this attempt is
        # malformed or rejected. A temporary grant must not survive for replay.
        requested_stop_epoch = (
            self._operation_stop_epoch()
            if _expected_stop_epoch is None
            else _expected_stop_epoch
        )
        with self._stop_epoch_lock:
            arm_stop_epoch = requested_stop_epoch
            if self._stop_epoch != arm_stop_epoch:
                raise MachineError("Arming was cancelled by software STOP")
            arm_authorization_epoch = (
                self._authorization_epoch
                if _expected_authorization_epoch is None
                else _expected_authorization_epoch
            )
            if self._authorization_epoch != arm_authorization_epoch:
                raise MachineError("Arming was cancelled by disarm")
            self._clear_arm_authorization()
        self._require_safety_configuration()
        if self.laser_lockout:
            raise SafetyError("Laser output is locked out for this process")
        if phrase.strip() != self.ARM_PHRASE:
            raise SafetyError("Arming phrase did not match")
        self._require_connection()
        if self.settings.backend == "serial" and not self._coordinate_reference_ready:
            raise SafetyError(
                "Home / park must complete after this controller connection or reset "
                "before laser control can be armed"
            )
        if self._job.running:
            raise MachineError("Cannot change arming state while a job is running")
        timeout = float(self.laser_settings.arm_timeout_seconds)
        with self._stop_epoch_lock:
            if self._stop_epoch != arm_stop_epoch:
                raise MachineError("Arming was cancelled by software STOP")
            if self._authorization_epoch != arm_authorization_epoch:
                raise MachineError("Arming was cancelled by disarm")
            if self._controller_reconnect_required:
                raise MachineError(
                    "Controller command state is untrusted after software STOP; "
                    "disconnect and reconnect before arming"
                )
            self._armed_until = time.time() + timeout
            self._armed_until_monotonic = time.monotonic() + timeout
            self._armed_program_digest = program_digest
        self._append_log("INFO", "laser control armed temporarily")
        return self._armed_until

    def arm_program(self, phrase: str, program: ValidatedProgram) -> float:
        arm_stop_epoch = self._operation_stop_epoch()
        with self._stop_epoch_lock:
            if self._stop_epoch != arm_stop_epoch:
                raise MachineError("Arming was cancelled by software STOP")
            arm_authorization_epoch = self._authorization_epoch
            self._clear_arm_authorization()
        self._require_current_safety_profile(program)
        return self.arm(
            phrase,
            program_digest=program.digest,
            _expected_stop_epoch=arm_stop_epoch,
            _expected_authorization_epoch=arm_authorization_epoch,
        )

    def disarm(self) -> None:
        # Authorization has its own generation so disarm can defeat an arm
        # currently being validated without cancelling unrelated laser-off motion.
        with self._stop_epoch_lock:
            self._authorization_epoch += 1
            self._clear_arm_authorization()
            self._job_laser_authorized = False
        if self._job.running:
            self.stop_job(emergency=False)
            return
        if self._transport is None:
            return
        # No powered job is active, so wait for command ownership. A trusted
        # stream consumes the M5 acknowledgement; an already-untrusted stream
        # receives a fresh best-effort M5 and remains reconnect-only.
        with self._command_lock:
            if self._job.running:
                self.stop_job(emergency=False)
                return
            transport = self._transport
            if transport is None:
                return
            if self._controller_reconnect_required:
                try:
                    with self._transport_write_lock:
                        transport.write_line("M5")
                    self._append_log("TX", "M5 (disarm on untrusted connection)")
                except Exception as exc:
                    self._append_log("ERROR", f"Disarm laser-off request failed: {exc}")
                return
            try:
                self._send_command_locked(
                    "M5",
                    timeout=_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                self._append_log("ERROR", f"Disarm laser-off request failed: {exc}")
                with self._stop_epoch_lock:
                    self._controller_reconnect_required = True
                    self._coordinate_reference_ready = False
                    self._coordinate_state_reference = None
                    self._jog_position_mm = None

    def _require_connection(self) -> MachineTransport:
        if not self._connected or self._transport is None:
            raise MachineError("Controller is not connected")
        if self._controller_reconnect_required:
            raise MachineError(
                "Controller command state is untrusted after STOP or an uncertain "
                "controller exchange; "
                "disconnect and reconnect before issuing more commands"
            )
        return self._transport

    @staticmethod
    def _codes(words: list[Any], letter: str) -> set[int]:
        output: set[int] = set()
        for word in words:
            if word.letter != letter:
                continue
            rounded = round(word.value)
            if abs(word.value - rounded) < 1e-9:
                output.add(int(rounded))
        return output

    def _check_line_safety(
        self,
        line: str,
        *,
        job_execution: bool = False,
        preflight: bool = False,
    ) -> None:
        cleaned = strip_comment(line)
        if not cleaned:
            return
        words = parse_words(cleaned)
        m_codes = self._codes(words, "M")
        g_codes = self._codes(words, "G")
        powers = [word.value for word in words if word.letter == "S"]
        requests_laser = bool(m_codes.intersection({3, 4}) or any(value > 0 for value in powers))
        is_homing = cleaned.upper() == "$H" or 28 in g_codes
        requests_motion = contains_motion(cleaned) or is_homing
        if requests_laser or requests_motion:
            self._require_safety_configuration()
        if g_codes in ({0}, {1}):
            feeds = [word.value for word in words if word.letter == "F"]
            if len(feeds) != 1 or not math.isfinite(feeds[0]) or feeds[0] <= 0:
                raise SafetyError("Every G0/G1 line must include one positive finite F feed rate")
            maximum_feed = (
                self.settings.max_travel_feed_mm_min
                if g_codes == {0}
                else self.settings.max_work_feed_mm_min
            )
            if feeds[0] > maximum_feed:
                motion = "travel" if g_codes == {0} else "work"
                raise SafetyError(
                    f"{motion.capitalize()} feed F{feeds[0]:g} exceeds the "
                    f"configured {motion} ceiling of {maximum_feed:g} mm/min"
                )
        # Static program preflight must be possible before homing and temporary
        # arming. Runtime command paths still require live authorization.
        laser_authorized = preflight or self.armed or (
            job_execution and self._job_laser_authorized
        )
        if requests_laser and not laser_authorized:
            raise SafetyError("Laser-enable or positive-power command blocked because control is not armed")
        if requests_motion and not (
            self.settings.allow_motion is True or self.settings.backend == "simulator"
        ):
            raise SafetyError("Motion commands are disabled in machine.allow_motion")
        if (
            (requests_laser or requests_motion)
            and self.settings.backend == "serial"
            and self.hardware_enabled is not True
        ):
            raise SafetyError("Hardware control is not enabled for this process")

    def _validate_manual_command(self, line: str) -> str:
        cleaned = strip_comment(line).upper()
        if not cleaned:
            raise MachineError("Controller command is empty")
        if cleaned in _QUERY_COMMANDS or cleaned == "M5":
            return cleaned
        raise SafetyError(
            "Manual commands are limited to read-only identity/status queries and M5. "
            "Use the guarded photography-position action or a validated generated job for motion."
        )

    def send_command(
        self,
        line: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
        _expected_stop_epoch: int | None = None,
    ) -> list[str]:
        expected_stop_epoch = (
            self._operation_stop_epoch()
            if _expected_stop_epoch is None
            else _expected_stop_epoch
        )
        with self._command_lock:
            return self._send_command_locked(
                line,
                timeout=timeout,
                _internal_motion=_internal_motion,
                _expected_stop_epoch=expected_stop_epoch,
            )

    def _send_command_locked(
        self,
        line: str,
        timeout: float | None = None,
        *,
        _internal_motion: bool = False,
        _expected_stop_epoch: int | None = None,
        _terminal_error_consumed: bool = False,
    ) -> list[str]:
        if len(line) > 256:
            raise MachineError("Single controller command exceeds 256 characters")
        if self._job.running:
            raise MachineError("Manual commands are disabled while a job is running")
        cleaned = strip_comment(line).upper() if _internal_motion else self._validate_manual_command(line)
        if not cleaned:
            raise MachineError("Controller command is empty")
        with self._transport_write_lock:
            if _expected_stop_epoch is not None:
                with self._stop_epoch_lock:
                    if self._stop_epoch != _expected_stop_epoch:
                        raise MachineError("Operation was cancelled by software STOP")
            self._check_line_safety(cleaned)
            transport = self._require_connection()
            try:
                transport.write_line(cleaned)
                self._append_log("TX", cleaned)
            except Exception as exc:
                # A partial write can still produce a delayed response. Do not
                # let that response acknowledge a later command after retry.
                self._mark_controller_command_state_untrusted()
                raise MachineError(
                    f"Command {cleaned!r} failed while writing: {exc}"
                ) from exc
        acknowledgement_timeout = timeout or self.settings.read_timeout
        try:
            return self._wait_for_ack(
                acknowledgement_timeout,
                expected_stop_epoch=_expected_stop_epoch,
                terminal_error_consumed=_terminal_error_consumed,
            )
        except Exception as exc:
            if not isinstance(exc, _ControllerCommandRejected):
                # A timeout, read failure, cancellation, or disconnect leaves
                # the command/response position unknowable. An explicit
                # controller error/alarm is different: that terminal response
                # has been consumed and callers may apply a documented fallback.
                self._mark_controller_command_state_untrusted()
            if isinstance(exc, _ControllerCommandRejected) and _terminal_error_consumed:
                raise
            raise MachineError(f"Command {cleaned!r} failed: {exc}") from exc

    def _wait_for_ack(
        self,
        timeout: float,
        *,
        expected_stop_epoch: int | None = None,
        terminal_error_consumed: bool = False,
    ) -> list[str]:
        if self._job.running and self._job_stop.is_set():
            raise MachineError("Job stopped")
        try:
            transport = self._require_connection()
        except MachineError:
            if self._job.running and self._job_stop.is_set():
                raise MachineError("Job stopped") from None
            raise
        deadline = time.monotonic() + timeout
        responses: list[str] = []
        while time.monotonic() < deadline:
            if expected_stop_epoch is not None:
                with self._stop_epoch_lock:
                    if self._stop_epoch != expected_stop_epoch:
                        raise MachineError("Operation was cancelled by software STOP")
            if self._job.running and self._job_stop.is_set():
                raise MachineError("Job stopped")
            response = transport.read_line(timeout=min(0.2, max(0.0, deadline - time.monotonic())))
            if not response:
                continue
            if self._job.running and self._job_stop.is_set():
                raise MachineError("Job stopped")
            if expected_stop_epoch is not None:
                with self._stop_epoch_lock:
                    if self._stop_epoch != expected_stop_epoch:
                        raise MachineError("Operation was cancelled by software STOP")
            responses.append(response)
            self._append_log("RX", response)
            lower = response.lower()
            if lower == "ok" or lower.startswith("ok "):
                return responses
            if lower.startswith("error") and (
                self._protocol == "grbl" or terminal_error_consumed
            ):
                raise _ControllerCommandRejected(response)
            if lower.startswith("error") or lower.startswith("alarm"):
                raise MachineError(response)
            if lower.startswith("busy") or response.startswith("<") or response.startswith("["):
                continue
        raise MachineError(f"Controller did not acknowledge command within {timeout:g} seconds")

    def query_identity(self) -> list[str]:
        command = "$I" if self._protocol == "grbl" else "M115"
        return self.send_command(command, timeout=3.0)

    @staticmethod
    def _reported_grbl_step_idle_delay(responses: list[str]) -> int | None:
        for response in responses:
            match = _GRBL_STEP_IDLE_PATTERN.match(response.strip())
            if match:
                return int(match.group(1))
        return None

    def _normalize_and_release_grbl_after_connect(self) -> None:
        """Normalize a newly connected controller without resetting it."""

        timeout = _PHOTO_COMMAND_ACK_TIMEOUT_SECONDS
        normal = int(self.settings.grbl_step_idle_delay_ms)
        settings_error: Exception | None = None
        try:
            responses = self.send_command("$$", timeout=timeout)
            current = self._reported_grbl_step_idle_delay(responses)
            if current is None:
                settings_error = MachineError("GRBL $$ did not report the $1 step-idle delay")
        except Exception as exc:
            current = None
            settings_error = exc
        # An ordinary connection has not moved the machine, so it does not need
        # the $SLP/soft-reset fallback used after a held capture or powered job.
        # Some controllers audibly announce that reset. M5 plus restoration of
        # the configured finite idle delay is sufficient to clear laser and
        # stale $1=255 state without disturbing the controller session.
        self.send_command("M5", timeout=timeout, _internal_motion=True)
        if current == 255 or settings_error is not None:
            self.send_command(
                f"$1={normal}",
                timeout=timeout,
                _internal_motion=True,
            )
        self._coordinate_reference_ready = False
        self._coordinate_state_reference = None
        self._jog_position_mm = None
        if settings_error is not None:
            raise MachineError(
                "Controller connection normalized laser output and restored the configured "
                f"step-idle delay, but GRBL settings could not be read: {settings_error}"
            ) from settings_error
        if current == 255:
            self._append_log(
                "INFO",
                f"Recovered stale camera motor hold at connection; restored $1={normal}",
            )

    def _release_grbl_motors(
        self,
        *,
        restore_idle_delay: int | None,
        job_execution: bool,
        context: str,
    ) -> None:
        """Restore scoped hold state and explicitly release GRBL motors."""

        timeout = _PHOTO_COMMAND_ACK_TIMEOUT_SECONDS
        failures: list[str] = []

        def execute(command: str) -> list[str]:
            if job_execution:
                return self._execute_running_job_command(command, timeout=timeout)
            return self.send_command(
                command,
                timeout=timeout,
                _internal_motion=True,
            )

        try:
            execute("M5")
        except Exception as exc:
            failures.append(f"M5 failed: {exc}")

        if restore_idle_delay is not None:
            try:
                execute(f"$1={int(restore_idle_delay)}")
            except Exception as exc:
                failures.append(f"idle-delay restore failed: {exc}")

        try:
            execute("$MD")
            self._append_log("INFO", f"{context}: motors released with $MD")
        except MachineError as disable_error:
            self._append_log(
                "INFO",
                f"{context}: $MD unavailable ({disable_error}); falling back to GRBL sleep",
            )
            try:
                execute("$SLP")
                time.sleep(0.1)
                transport = self._require_connection()
                transport.write_raw(b"\x18")
                self._append_log("TX", f"GRBL soft reset after {context} $SLP")
                startup_delay = self.settings.controller_startup_delay
                if type(startup_delay) not in {int, float} or not math.isfinite(
                    float(startup_delay)
                ):
                    startup_delay = 0.1
                time.sleep(min(5.0, max(0.1, float(startup_delay))))
                for line in transport.drain():
                    self._append_log("RX", line)
                execute("$X")
                self._append_log("INFO", f"{context}: motors released with $SLP/reset")
            except Exception as exc:
                failures.append(f"explicit motor release failed: {exc}")
        finally:
            self._coordinate_reference_ready = False
            self._coordinate_state_reference = None
            self._jog_position_mm = None

        if failures:
            raise MachineError(f"{context} cleanup incomplete: {'; '.join(failures)}")

    @contextmanager
    def temporary_stepper_hold(self):
        """Keep GRBL steppers energized only for a scoped camera operation."""

        with self._command_lock:
            with self._temporary_stepper_hold_locked():
                yield

    @contextmanager
    def _temporary_stepper_hold_locked(self):
        """Implement a camera hold while the caller owns controller replies."""

        self._require_safety_configuration()
        if self.settings.backend != "serial" or self._protocol != "grbl":
            yield
            return
        responses = self.send_command("$$", timeout=max(6.0, self.settings.read_timeout))
        original = self._reported_grbl_step_idle_delay(responses)
        if original is None:
            raise MachineError("GRBL did not report $1, so temporary stepper holding was not started")
        restore_delay = (
            int(self.settings.grbl_step_idle_delay_ms)
            if original == 255
            else original
        )
        operation_error: BaseException | None = None
        try:
            if original != 255:
                self.send_command(
                    "$1=255",
                    timeout=max(6.0, self.settings.read_timeout),
                    _internal_motion=True,
                )
                self._append_log("INFO", f"Temporary camera hold enabled; saved $1={original}")
            yield
        except BaseException as exc:
            operation_error = exc
            raise
        finally:
            if self._transport is not None:
                try:
                    self._release_grbl_motors(
                        restore_idle_delay=restore_delay,
                        job_execution=False,
                        context="temporary camera hold",
                    )
                except Exception as cleanup_error:
                    self._append_log(
                        "ERROR",
                        f"Temporary camera motor-release cleanup failed: {cleanup_error}",
                    )
                    if operation_error is None:
                        raise
                    add_exception_note(
                        operation_error,
                        f"Temporary camera motor-release cleanup also failed: {cleanup_error}",
                    )
                else:
                    self._append_log(
                        "INFO",
                        f"Temporary camera hold released; restored $1={restore_delay}",
                    )

    def _wait_until_idle(self, timeout: float = 120.0) -> list[str]:
        transport = self._require_connection()
        if self._protocol == "marlin":
            return self.send_command("M400", timeout=timeout, _internal_motion=True)

        deadline = time.monotonic() + timeout
        responses: list[str] = []
        while time.monotonic() < deadline:
            transport.write_raw(b"?")
            self._append_log("TX", "?")
            query_deadline = min(deadline, time.monotonic() + 0.8)
            while time.monotonic() < query_deadline:
                line = transport.read_line(timeout=min(0.15, query_deadline - time.monotonic()))
                if not line:
                    continue
                responses.append(line)
                self._append_log("RX", line)
                lower = line.lower()
                if lower.startswith("<idle"):
                    return responses
                if lower.startswith("<alarm") or lower.startswith("alarm") or lower.startswith("error"):
                    raise MachineError(line)
            time.sleep(0.1)
        raise MachineError(f"Controller did not become idle within {timeout:g} seconds")

    def _wait_for_motion_complete(
        self,
        timeout: float = 120.0,
        *,
        expected_stop_epoch: int | None = None,
    ) -> list[str]:
        """Wait behind all previously accepted planner motion.

        GRBL's short positive ``G4`` dwell enters the planner synchronization
        path after preceding motion. This is more portable across GRBL-derived controllers than
        requiring a particular realtime ``?`` status-report shape. Marlin has
        a dedicated synchronization command.
        """

        command = "G4 P0.01" if self._protocol == "grbl" else "M400"
        return self.send_command(
            command,
            timeout=timeout,
            _internal_motion=True,
            _expected_stop_epoch=expected_stop_epoch,
        )

    @staticmethod
    def _parse_grbl_coordinate_state(
        modal_responses: list[str],
        offset_responses: list[str],
    ) -> dict[str, Any]:
        active_workspace: str | None = None
        for response in modal_responses:
            upper = response.strip().upper()
            if not upper.startswith("[GC:"):
                continue
            words = upper[4:-1].split() if upper.endswith("]") else upper[4:].split()
            active_workspace = next(
                (word for word in words if word in _GRBL_WORK_COORDINATE_CODES),
                None,
            )
            break

        offsets: dict[str, list[float]] = {}
        for response in offset_responses:
            match = _GRBL_OFFSET_PATTERN.match(response.strip())
            if match is None:
                continue
            offsets[match.group(1).upper()] = [
                float(match.group(2)),
                float(match.group(3)),
                float(match.group(4)),
            ]
        if active_workspace is None:
            raise MachineError("GRBL did not report an active G54-G59 work-coordinate system")
        if active_workspace not in offsets or "G92" not in offsets:
            raise MachineError(
                "GRBL did not report the active work offset and G92 offset in response to $#"
            )
        return {
            "active_workspace": active_workspace,
            "active_offset_mm": offsets[active_workspace],
            "g92_offset_mm": offsets["G92"],
        }

    def _read_grbl_coordinate_state(self) -> dict[str, Any]:
        modal = self.send_command(
            "$G",
            timeout=max(_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS, self.settings.read_timeout),
        )
        offsets = self.send_command(
            "$#",
            timeout=max(_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS, self.settings.read_timeout),
        )
        return self._parse_grbl_coordinate_state(modal, offsets)

    @staticmethod
    def _coordinate_state_difference(
        reference: dict[str, Any], current: dict[str, Any]
    ) -> str | None:
        if current["active_workspace"] != reference["active_workspace"]:
            return (
                f"active workspace changed from {reference['active_workspace']} "
                f"to {current['active_workspace']}"
            )
        for label, key in (
            (current["active_workspace"], "active_offset_mm"),
            ("G92", "g92_offset_mm"),
        ):
            before = reference[key]
            after = current[key]
            if any(
                abs(float(after[index]) - float(before[index]))
                > _GRBL_COORDINATE_EPSILON_MM
                for index in range(3)
            ):
                return f"{label} changed from {before} mm to {after} mm"
        return None

    def _verify_grbl_coordinate_state(self) -> dict[str, Any]:
        current = self._read_grbl_coordinate_state()
        reference = self._coordinate_state_reference
        if reference is None:
            raise SafetyError(
                "Home / park did not record a GRBL work-coordinate reference for this connection"
            )
        difference = self._coordinate_state_difference(reference, current)
        if difference is not None:
            self._coordinate_reference_ready = False
            self._coordinate_state_reference = None
            self._jog_position_mm = None
            raise SafetyError(
                "GRBL coordinate state changed after Home / park: "
                f"{difference}. The job was blocked; Home / park and realign the camera job."
            )
        return current

    @staticmethod
    def _coordinate_state_summary(state: dict[str, Any]) -> str:
        workspace = state["active_workspace"]
        active = ",".join(f"{float(value):g}" for value in state["active_offset_mm"])
        g92 = ",".join(f"{float(value):g}" for value in state["g92_offset_mm"])
        return f"{workspace}=[{active}] mm; G92=[{g92}] mm"

    @staticmethod
    def _require_zero_xy_coordinate_offsets(state: dict[str, Any]) -> None:
        nonzero: list[str] = []
        for label, key in (
            (str(state["active_workspace"]), "active_offset_mm"),
            ("G92", "g92_offset_mm"),
        ):
            values = [float(value) for value in state[key]][:2]
            if any(abs(value) > _GRBL_COORDINATE_EPSILON_MM for value in values):
                nonzero.append(
                    f"{label} XY=[{values[0]:g},{values[1]:g}] mm"
                )
        if nonzero:
            raise SafetyError(
                "Home / park requires zero GRBL XY work offsets so configured "
                "absolute positions match the physical machine: " + "; ".join(nonzero)
            )

    def prepare_photo_position(self) -> dict[str, Any]:
        """Home if configured, then park XY at the repeatable camera pose.

        This routine never emits a laser-enable command. It remains subject to
        the normal serial-hardware and ``machine.allow_motion`` gates.
        """
        operation_stop_epoch = self._operation_stop_epoch()
        with self._command_lock:
            return self._prepare_photo_position_locked(
                operation_stop_epoch=operation_stop_epoch,
                park_at_photo_position=True,
            )

    def prepare_job_start(self) -> dict[str, Any]:
        """Home once and establish coordinates without moving to the camera pose.

        This routine never emits a laser-enable command.  The validated program
        remains responsible for its initial laser-off travel from Home to the
        first toolpath position.
        """

        operation_stop_epoch = self._operation_stop_epoch()
        with self._command_lock:
            return self._prepare_photo_position_locked(
                operation_stop_epoch=operation_stop_epoch,
                park_at_photo_position=False,
            )

    def _prepare_photo_position_locked(
        self,
        *,
        operation_stop_epoch: int,
        park_at_photo_position: bool,
    ) -> dict[str, Any]:
        with self._stop_epoch_lock:
            if self._stop_epoch != operation_stop_epoch:
                raise MachineError("Home / park was cancelled by software STOP")
        self._require_safety_configuration()
        if self._job.running:
            raise MachineError("Cannot move to the photography position while a job is running")
        self._require_connection()
        if self.settings.backend == "serial" and not self.settings.home_before_photo:
            raise SafetyError(
                "Serial hardware requires machine.home_before_photo=true so Home / park "
                "can establish a repeatable absolute coordinate reference"
            )
        x = float(self.settings.photo_x)
        y = float(self.settings.photo_y)
        if park_at_photo_position and not self.settings.work_area.contains(
            x,
            y,
            self.laser_settings.boundary_margin_mm,
        ):
            raise SafetyError("Configured photography position lies outside the safe work area")

        transcript: list[dict[str, Any]] = []
        idle_responses: list[str] = []

        def require_not_stopped() -> None:
            with self._stop_epoch_lock:
                if self._stop_epoch != operation_stop_epoch:
                    raise MachineError("Home / park was cancelled by software STOP")

        def execute(command: str, timeout: float | None = None) -> None:
            acknowledgement_timeout = (
                max(_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS, self.settings.read_timeout)
                if timeout is None
                else timeout
            )
            responses = self.send_command(
                command,
                timeout=acknowledgement_timeout,
                _internal_motion=True,
                _expected_stop_epoch=operation_stop_epoch,
            )
            transcript.append({"command": command, "responses": responses})

        self._coordinate_reference_ready = self.settings.backend == "simulator"
        self._coordinate_state_reference = None
        self._jog_position_mm = None
        try:
            try:
                execute("M5")
            except MachineError as exc:
                # Standard GRBL wakes from $SLP/reset in alarm state and rejects
                # even M5 with error:9. Unlock only this exact pre-home case;
                # position remains invalid and the mandatory $H follows.
                if not (
                    self._protocol == "grbl"
                    and self.settings.home_before_photo
                    and "error:9" in str(exc).lower()
                ):
                    raise
                self._append_log(
                    "INFO",
                    "M5 was blocked by the post-sleep alarm; unlocking before mandatory homing",
                )
                execute("$X")
                execute("M5")
            if self.settings.home_before_photo:
                execute(
                    "$H" if self._protocol == "grbl" else "G28",
                    timeout=max(120.0, self.settings.read_timeout),
                )
                # A successful homing acknowledgement is the completion
                # barrier. Some GRBL-derived controllers do not emit standard
                # realtime <Idle...> reports after homing.
            coordinate_state = (
                self._read_grbl_coordinate_state()
                if self.settings.backend == "serial" and self._protocol == "grbl"
                else None
            )
            require_not_stopped()
            if coordinate_state is not None:
                # Work/G92 offsets affect G0 coordinates even immediately after
                # homing. Reject them before issuing the photography-position
                # move, then verify again after the move below.
                self._require_zero_xy_coordinate_offsets(coordinate_state)
            execute("G21")
            execute("G90")
            if park_at_photo_position:
                execute(
                    f"G0 X{x:.3f} Y{y:.3f} F{float(self.laser_settings.travel_feed_mm_min):.3f}",
                    timeout=max(
                        _PHOTO_COMMAND_ACK_TIMEOUT_SECONDS,
                        self.settings.read_timeout,
                    ),
                )
                idle_responses = self._wait_for_motion_complete(
                    timeout=120.0,
                    expected_stop_epoch=operation_stop_epoch,
                )
                parked_coordinate_state = (
                    self._read_grbl_coordinate_state()
                    if self.settings.backend == "serial" and self._protocol == "grbl"
                    else None
                )
                require_not_stopped()
                if parked_coordinate_state is not None:
                    self._require_zero_xy_coordinate_offsets(parked_coordinate_state)
                    coordinate_state = parked_coordinate_state
            if coordinate_state is not None:
                self._append_log(
                    "INFO",
                    ("Home / park" if park_at_photo_position else "Job-start Home")
                    + " GRBL coordinate reference: "
                    + self._coordinate_state_summary(coordinate_state),
                )
        except Exception:
            self._coordinate_reference_ready = False
            self._jog_position_mm = None
            raise
        with self._stop_epoch_lock:
            if self._stop_epoch != operation_stop_epoch:
                self._coordinate_reference_ready = False
                self._coordinate_state_reference = None
                self._jog_position_mm = None
                raise MachineError("Home / park was cancelled by software STOP")
            if self._controller_reconnect_required:
                self._coordinate_reference_ready = False
                self._coordinate_state_reference = None
                self._jog_position_mm = None
                raise MachineError(
                    "Controller command state is untrusted after software STOP; "
                    "disconnect and reconnect before Home / park"
                )
            self._coordinate_reference_ready = True
            self._coordinate_state_reference = coordinate_state
            self._jog_position_mm = (x, y) if park_at_photo_position else None
        return {
            "position": (
                {"x": x, "y": y, "z": self.settings.photo_z}
                if park_at_photo_position
                else None
            ),
            "homed": self.settings.home_before_photo,
            "parked": park_at_photo_position,
            "transcript": transcript,
            "idle_responses": idle_responses,
            "coordinate_state": coordinate_state,
            "warning": (
                "photo_position.z is recorded but is not moved automatically; set laser focus/material height manually."
                if park_at_photo_position and self.settings.photo_z is not None
                else None
            ),
        }

    def jog(self, dx_mm: float, dy_mm: float, feed_mm_min: float) -> dict[str, Any]:
        """Make one laser-off XY move from the last trusted jog pose.

        Home / park establishes the starting pose.  Incremental requests are
        converted to absolute moves so controller modal state cannot change
        their meaning. Jogging intentionally does not apply the configured work
        area because it is also the operator's tool for measuring that area.
        Any attempted jog move invalidates the cached pose until the planner
        confirms completion.
        """

        values = {
            "X jog distance": dx_mm,
            "Y jog distance": dy_mm,
            "Jog feed": feed_mm_min,
        }
        invalid_types = [
            label for label, value in values.items() if type(value) not in {int, float}
        ]
        if invalid_types:
            raise SafetyError(
                "Jog distances and feed must be finite numbers: "
                + ", ".join(invalid_types)
            )
        finite = {label: float(value) for label, value in values.items()}
        invalid = [label for label, value in finite.items() if not math.isfinite(value)]
        if invalid:
            raise SafetyError(
                "Jog distances and feed must be finite numbers: " + ", ".join(invalid)
            )
        dx = finite["X jog distance"]
        dy = finite["Y jog distance"]
        feed = finite["Jog feed"]
        if dx == 0.0 and dy == 0.0:
            raise SafetyError("Jog distance must move X or Y")
        if feed <= 0.0:
            raise SafetyError("Jog feed must be positive")

        operation_stop_epoch = self._operation_stop_epoch()
        with self._command_lock:
            with self._stop_epoch_lock:
                if self._stop_epoch != operation_stop_epoch:
                    raise MachineError("Jog was cancelled by software STOP")
            self._require_safety_configuration()
            if self.settings.allow_motion is not True:
                raise SafetyError("Jogging is disabled in machine.allow_motion")
            if feed > float(self.settings.max_travel_feed_mm_min):
                raise SafetyError(
                    f"Jog feed F{feed:g} exceeds the configured travel ceiling of "
                    f"{float(self.settings.max_travel_feed_mm_min):g} mm/min"
                )
            self._require_connection()
            if self._job.running:
                raise MachineError("Cannot jog while a controller job is running")
            if self.armed:
                raise SafetyError("Disarm laser control before jogging")
            if not self._coordinate_reference_ready or self._jog_position_mm is None:
                raise SafetyError(
                    "Home / park must complete before jogging so the current XY position is known"
                )
            if self.settings.backend == "serial" and self._protocol == "grbl":
                self._verify_grbl_coordinate_state()

            current_x, current_y = self._jog_position_mm
            target_x = round(current_x + dx, 3)
            target_y = round(current_y + dy, 3)
            if target_x == current_x and target_y == current_y:
                raise SafetyError("Jog distance is below the 0.001 mm controller resolution")

            transcript: list[dict[str, Any]] = []

            def execute(command: str, timeout: float | None = None) -> None:
                responses = self.send_command(
                    command,
                    timeout=(
                        max(_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS, self.settings.read_timeout)
                        if timeout is None
                        else timeout
                    ),
                    _internal_motion=True,
                    _expected_stop_epoch=operation_stop_epoch,
                )
                transcript.append({"command": command, "responses": responses})

            execute("M5")
            execute("G21")
            execute("G90")
            with self._stop_epoch_lock:
                if self._stop_epoch != operation_stop_epoch:
                    raise MachineError("Jog was cancelled by software STOP")
                # From this point the controller may accept motion.  Do not
                # expose the old position during the command/ACK window.
                self._jog_position_mm = None
            execute(f"G0 X{target_x:.3f} Y{target_y:.3f} F{feed:.3f}")
            idle_responses = self._wait_for_motion_complete(
                timeout=120.0,
                expected_stop_epoch=operation_stop_epoch,
            )
            with self._stop_epoch_lock:
                if self._stop_epoch != operation_stop_epoch:
                    raise MachineError("Jog was cancelled by software STOP")
                if self._controller_reconnect_required:
                    raise MachineError(
                        "Controller command state became untrusted during jogging; "
                        "disconnect and reconnect"
                    )
                self._jog_position_mm = (target_x, target_y)
            return {
                "position": {"x": target_x, "y": target_y},
                "delta": {"x": dx, "y": dy},
                "feed_mm_min": feed,
                "transcript": transcript,
                "idle_responses": idle_responses,
            }

    def _validate_stream_line(self, line: str) -> tuple[list[Any], set[int], set[int]]:
        if len(line) > 256:
            raise SafetyError("Single streamed G-code line exceeds 256 characters")
        try:
            words = parse_words(line, require_full_match=True)
        except ValueError as exc:
            raise SafetyError(str(exc)) from exc
        if not words:
            raise SafetyError("Executable G-code line contains no supported words")
        letters = {word.letter for word in words}
        unsupported = letters - _STREAM_LETTERS
        if unsupported:
            raise SafetyError(f"Unsupported G-code word(s): {', '.join(sorted(unsupported))}")
        if any(word.letter in {"G", "M"} and abs(word.value - round(word.value)) >= 1e-9 for word in words):
            raise SafetyError("G and M codes must be whole numbers")
        g_codes = self._codes(words, "G")
        m_codes = self._codes(words, "M")
        if len(g_codes) > 1 or len(m_codes) > 1 or (g_codes and m_codes):
            raise SafetyError("Each streamed line may contain only one G code or one M code")
        if not g_codes.issubset(_STREAM_G_CODES):
            raise SafetyError(f"Unsupported streamed G code: {sorted(g_codes - _STREAM_G_CODES)}")
        if not m_codes.issubset(_STREAM_M_CODES):
            raise SafetyError(f"Unsupported streamed M code: {sorted(m_codes - _STREAM_M_CODES)}")

        counts: dict[str, int] = {}
        values: dict[str, float] = {}
        for word in words:
            counts[word.letter] = counts.get(word.letter, 0) + 1
            values[word.letter] = word.value
        for letter in ("G", "M", "X", "Y", "F", "S"):
            if counts.get(letter, 0) > 1:
                raise SafetyError(f"Duplicate {letter} word on streamed line")
        if "F" in values and values["F"] <= 0:
            raise SafetyError("Feed rate must be positive")
        if "F" in values and not math.isfinite(values["F"]):
            raise SafetyError("Feed rate must be finite")
        if "S" in values and not 0 <= values["S"] <= self.laser_settings.power_max:
            raise SafetyError(f"S power must be between 0 and {self.laser_settings.power_max}")

        if g_codes in ({0}, {1}):
            if "X" not in values and "Y" not in values:
                raise SafetyError("G0/G1 line must contain X and/or Y")
            if "F" not in values:
                raise SafetyError("Every G0/G1 line must include an explicit F feed rate")
            maximum_feed = (
                self.settings.max_travel_feed_mm_min
                if g_codes == {0}
                else self.settings.max_work_feed_mm_min
            )
            if values["F"] > maximum_feed:
                motion = "travel" if g_codes == {0} else "work"
                raise SafetyError(
                    f"{motion.capitalize()} feed F{values['F']:g} exceeds the "
                    f"configured {motion} ceiling of {maximum_feed:g} mm/min"
                )
            allowed_motion_letters = {"G", "X", "Y", "F"}
            if g_codes == {1}:
                allowed_motion_letters.add("S")
            if any(word.letter not in allowed_motion_letters for word in words):
                raise SafetyError(
                    "Only X, Y, F, and bounded inline S on G1 are permitted "
                    "on streamed motion lines"
                )
        elif g_codes in ({21}, {90}):
            if len(words) != 1:
                raise SafetyError("G21 and G90 must be standalone lines")
        elif m_codes in ({3}, {4}):
            if counts.get("S", 0) != 1 or len(words) != 2:
                raise SafetyError("M3/M4 must include exactly one S value on the same line")
        elif m_codes == {5}:
            if len(words) != 1:
                raise SafetyError("M5 must be a standalone line")
        else:
            raise SafetyError("Unsupported streamed G-code line")
        return words, g_codes, m_codes

    def _configured_guarded_output_polygon(self) -> ConvexPolygon | None:
        configured = self.laser_settings.guarded_output_polygon_mm
        if configured is None:
            return None
        try:
            return normalize_convex_polygon(
                configured,
                label="laser.guarded_output_polygon_mm",
            )
        except ValueError as exc:
            raise SafetyError(str(exc)) from exc

    def _resolve_guarded_output_polygon(
        self,
        requested: tuple[tuple[float, float], ...] | None,
    ) -> ConvexPolygon | None:
        if requested is None:
            return None
        configured = self._configured_guarded_output_polygon()
        if configured is None:
            raise SafetyError(
                "A support-bound output polygon was requested, but none is configured"
            )
        try:
            normalized = normalize_convex_polygon(
                requested,
                label="guarded output polygon",
            )
        except ValueError as exc:
            raise SafetyError(str(exc)) from exc
        if normalized != configured:
            raise SafetyError(
                "The requested guarded output polygon does not match the configured authority"
            )
        return normalized

    def _analyze_program(
        self,
        text: str,
        guarded_output_polygon_mm: ConvexPolygon | None = None,
    ) -> tuple[list[str], bool]:
        self._require_safety_configuration()
        guarded_polygon = self._resolve_guarded_output_polygon(
            guarded_output_polygon_mm
        )
        lines = [strip_comment(line) for line in text.splitlines()]
        lines = [line for line in lines if line]
        if not lines:
            raise SafetyError("G-code program is empty")
        if len(lines) > 250_000:
            raise SafetyError("G-code program exceeds the 250,000-line safety limit")

        seen_mm = False
        seen_absolute = False
        seen_initial_m5 = False
        laser_on = False
        requires_laser_authorization = False
        position_established = False
        x: float | None = None
        y: float | None = None
        last_m_code: int | None = None
        last_line_is_m5 = False

        for index, line in enumerate(lines, start=1):
            words, g_codes, m_codes = self._validate_stream_line(line)
            last_line_is_m5 = m_codes == {5}
            values = {word.letter: word.value for word in words}
            try:
                self._check_line_safety(
                    line,
                    job_execution=True,
                    preflight=True,
                )
            except SafetyError as exc:
                raise SafetyError(f"Line {index}: {exc}") from exc

            if g_codes == {21}:
                seen_mm = True
            elif g_codes == {90}:
                seen_absolute = True
            elif g_codes in ({0}, {1}):
                if not seen_mm or not seen_absolute:
                    raise SafetyError(f"Line {index}: G21 and G90 must appear before motion")
                if not seen_initial_m5:
                    raise SafetyError(f"Line {index}: M5 must appear before the first motion")
                if "S" in values:
                    if g_codes != {1} or last_m_code not in {3, 4}:
                        raise SafetyError(
                            f"Line {index}: inline S is allowed only on G1 after M3/M4"
                        )
                    requires_laser_authorization = True
                    laser_on = values["S"] > 0
                if x is None or y is None:
                    if "X" not in values or "Y" not in values:
                        raise SafetyError(f"Line {index}: the first move must establish both X and Y")
                new_x = values.get("X", x)
                new_y = values.get("Y", y)
                assert new_x is not None and new_y is not None
                controller_allowed = (
                    convex_polygon_contains_normalized(
                        (new_x, new_y), guarded_polygon
                    )
                    if guarded_polygon is not None
                    else self.settings.work_area.contains(
                        new_x,
                        new_y,
                        self.laser_settings.boundary_margin_mm,
                    )
                )
                if not controller_allowed:
                    raise SafetyError(
                        f"Line {index}: G-code point X{new_x:.3f} Y{new_y:.3f} "
                        "lies outside the configured guarded output authority"
                    )
                spot_x = new_x + float(self.laser_settings.spot_offset_x_mm)
                spot_y = new_y + float(self.laser_settings.spot_offset_y_mm)
                spot_allowed = (
                    convex_polygon_contains_normalized(
                        (spot_x, spot_y), guarded_polygon
                    )
                    if guarded_polygon is not None
                    else self.settings.work_area.contains(
                        spot_x,
                        spot_y,
                        self.laser_settings.boundary_margin_mm,
                    )
                )
                if not spot_allowed:
                    raise SafetyError(
                        f"Line {index}: physical laser spot X{spot_x:.3f} "
                        f"Y{spot_y:.3f} lies outside the configured guarded output authority"
                    )
                if g_codes == {0} and laser_on:
                    raise SafetyError(f"Line {index}: rapid G0 motion is blocked while the laser is enabled")
                x, y = new_x, new_y
                position_established = True

            if m_codes in ({3}, {4}):
                if not seen_mm or not seen_absolute or not seen_initial_m5:
                    raise SafetyError(f"Line {index}: G21, G90, and an initial M5 are required before laser enable")
                if not position_established:
                    raise SafetyError(
                        f"Line {index}: an absolute XY move with the laser off "
                        "is required before laser enable"
                    )
                requires_laser_authorization = True
                laser_on = values["S"] > 0
                last_m_code = next(iter(m_codes))
            elif m_codes == {5}:
                laser_on = False
                seen_initial_m5 = True
                last_m_code = 5

        if not seen_mm or not seen_absolute:
            raise SafetyError("Program must explicitly set millimetres (G21) and absolute positioning (G90)")
        if not seen_initial_m5:
            raise SafetyError("Program must contain M5 before motion or laser commands")
        if laser_on or last_m_code != 5 or not last_line_is_m5:
            raise SafetyError("Program must end with a standalone M5 laser-off command")
        return lines, requires_laser_authorization

    def _require_safety_configuration(self) -> None:
        if type(self.settings.backend) is not str or self.settings.backend not in {
            "simulator",
            "serial",
        }:
            raise SafetyError("Machine backend must be exactly 'simulator' or 'serial'")
        if type(self.settings.protocol) is not str or self.settings.protocol not in {
            "auto",
            "grbl",
            "marlin",
        }:
            raise SafetyError("Machine protocol must be exactly auto, grbl, or marlin")

        boolean_values = {
            "machine.allow_motion": self.settings.allow_motion,
            "machine.home_before_photo": self.settings.home_before_photo,
            "machine.home_and_release_after_powered_job": (
                self.settings.home_and_release_after_powered_job
            ),
            "process.hardware_enabled": self.hardware_enabled,
            "process.laser_lockout": self.laser_lockout,
        }
        invalid_booleans = [
            label for label, value in boolean_values.items() if type(value) is not bool
        ]
        if invalid_booleans:
            raise SafetyError(
                "Machine safety gates must be exact booleans: "
                + ", ".join(invalid_booleans)
            )

        area = self.settings.work_area
        values = {
            "machine.work_area.x_min": area.x_min,
            "machine.work_area.x_max": area.x_max,
            "machine.work_area.y_min": area.y_min,
            "machine.work_area.y_max": area.y_max,
            "machine.max_travel_feed_mm_min": self.settings.max_travel_feed_mm_min,
            "machine.max_work_feed_mm_min": self.settings.max_work_feed_mm_min,
            "machine.read_timeout": self.settings.read_timeout,
            "machine.controller_startup_delay": self.settings.controller_startup_delay,
            "machine.photo_x": self.settings.photo_x,
            "machine.photo_y": self.settings.photo_y,
            "laser.boundary_margin_mm": self.laser_settings.boundary_margin_mm,
            "laser.spot_offset_x_mm": self.laser_settings.spot_offset_x_mm,
            "laser.spot_offset_y_mm": self.laser_settings.spot_offset_y_mm,
            "laser.travel_feed_mm_min": self.laser_settings.travel_feed_mm_min,
        }
        if self.settings.photo_z is not None:
            values["machine.photo_z"] = self.settings.photo_z
        invalid_numeric_types = [
            label for label, value in values.items() if type(value) not in {int, float}
        ]
        if invalid_numeric_types:
            raise SafetyError(
                "Machine safety settings must be finite numbers: "
                + ", ".join(invalid_numeric_types)
            )
        finite = {label: float(value) for label, value in values.items()}
        invalid = [label for label, value in finite.items() if not math.isfinite(value)]
        if invalid:
            raise SafetyError(
                "Machine safety settings must be finite numbers: " + ", ".join(invalid)
            )
        if (
            finite["machine.work_area.x_max"] <= finite["machine.work_area.x_min"]
            or finite["machine.work_area.y_max"] <= finite["machine.work_area.y_min"]
        ):
            raise SafetyError("Machine work-area bounds are invalid")
        if (
            finite["machine.max_travel_feed_mm_min"] <= 0
            or finite["machine.max_work_feed_mm_min"] <= 0
        ):
            raise SafetyError("Machine feed ceilings must be positive")
        if finite["machine.read_timeout"] <= 0:
            raise SafetyError("Machine read timeout must be positive")
        if finite["machine.controller_startup_delay"] < 0:
            raise SafetyError("Machine controller startup delay cannot be negative")
        arm_timeout = self.laser_settings.arm_timeout_seconds
        if type(arm_timeout) is not int or not 1 <= arm_timeout <= _MAX_ARM_TIMEOUT_SECONDS:
            raise SafetyError(
                "Laser arm timeout must be an integer from 1 through "
                f"{_MAX_ARM_TIMEOUT_SECONDS} seconds"
            )
        travel_feed = finite["laser.travel_feed_mm_min"]
        if travel_feed <= 0:
            raise SafetyError("Laser travel feed must be positive")
        if travel_feed > finite["machine.max_travel_feed_mm_min"]:
            raise SafetyError(
                "Laser travel feed exceeds the configured machine travel ceiling"
            )
        margin = finite["laser.boundary_margin_mm"]
        width = finite["machine.work_area.x_max"] - finite["machine.work_area.x_min"]
        height = finite["machine.work_area.y_max"] - finite["machine.work_area.y_min"]
        if margin < 0 or margin * 2 >= min(width, height):
            raise SafetyError("Laser boundary margin leaves no valid work area")
        if type(self.laser_settings.power_max) is not int or self.laser_settings.power_max <= 0:
            raise SafetyError("Laser power ceiling must be a positive integer")
        if type(self.settings.baudrate) is not int or self.settings.baudrate <= 0:
            raise SafetyError("Machine baud rate must be a positive integer")
        if (
            type(self.settings.grbl_step_idle_delay_ms) is not int
            or not 0 <= self.settings.grbl_step_idle_delay_ms < 255
        ):
            raise SafetyError("GRBL step idle delay must be an integer from 0 through 254")
        if type(self.settings.port) is not str or not self.settings.port.strip():
            raise SafetyError("Machine controller port must be a non-empty string")

    def validate_program(self, text: str) -> list[str]:
        return list(self.preflight_program(text).lines)

    def _program_safety_profile(
        self,
        guarded_output_polygon_mm: ConvexPolygon | None = None,
    ) -> tuple[Any, ...]:
        self._require_safety_configuration()
        area = self.settings.work_area
        return (
            self.settings.backend,
            self.settings.protocol,
            self.settings.allow_motion,
            self.hardware_enabled,
            self.laser_lockout,
            self.settings.home_before_photo,
            self.settings.home_and_release_after_powered_job,
            float(area.x_min),
            float(area.x_max),
            float(area.y_min),
            float(area.y_max),
            float(self.laser_settings.boundary_margin_mm),
            float(self.laser_settings.spot_offset_x_mm),
            float(self.laser_settings.spot_offset_y_mm),
            int(self.laser_settings.power_max),
            float(self.settings.max_travel_feed_mm_min),
            float(self.settings.max_work_feed_mm_min),
            float(self.laser_settings.travel_feed_mm_min),
            self.laser_settings.arm_timeout_seconds,
            float(self.settings.photo_x),
            float(self.settings.photo_y),
            None if self.settings.photo_z is None else float(self.settings.photo_z),
            self._configured_guarded_output_polygon(),
            guarded_output_polygon_mm,
        )

    def preflight_program(
        self,
        text: str,
        *,
        guarded_output_polygon_mm: tuple[tuple[float, float], ...] | None = None,
    ) -> ValidatedProgram:
        guarded_polygon = self._resolve_guarded_output_polygon(
            guarded_output_polygon_mm
        )
        lines, requires_laser_authorization = self._analyze_program(
            text,
            guarded_polygon,
        )
        if self.laser_lockout and requires_laser_authorization:
            raise SafetyError(
                "Program contains laser-enable commands while process laser lockout is active"
            )
        canonical = "\n".join(lines).encode("utf-8")
        return ValidatedProgram(
            lines=tuple(lines),
            digest=hashlib.sha256(canonical).hexdigest(),
            requires_laser_authorization=requires_laser_authorization,
            requires_motion=any(contains_motion(line) for line in lines),
            safety_profile=self._program_safety_profile(guarded_polygon),
            guarded_output_polygon_mm=guarded_polygon,
        )

    def _require_current_safety_profile(self, program: ValidatedProgram) -> None:
        self._require_validated_program_integrity(program)

    def _require_validated_program_integrity(self, program: ValidatedProgram) -> None:
        if type(program) is not ValidatedProgram:
            raise SafetyError("Program authorization requires an exact preflight result")
        if type(program.lines) is not tuple or any(
            type(line) is not str for line in program.lines
        ):
            raise SafetyError("Program authorization contains invalid line data")
        if type(program.digest) is not str:
            raise SafetyError("Program authorization contains an invalid digest")
        if (
            type(program.requires_laser_authorization) is not bool
            or type(program.requires_motion) is not bool
            or type(program.safety_profile) is not tuple
            or (
                program.guarded_output_polygon_mm is not None
                and type(program.guarded_output_polygon_mm) is not tuple
            )
        ):
            raise SafetyError("Program authorization contains invalid safety metadata")

        lines, requires_laser_authorization = self._analyze_program(
            "\n".join(program.lines),
            program.guarded_output_polygon_mm,
        )
        canonical_lines = tuple(lines)
        canonical_digest = hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()
        requires_motion = any(contains_motion(line) for line in lines)
        current_profile = self._program_safety_profile(
            program.guarded_output_polygon_mm
        )
        if (
            program.lines != canonical_lines
            or program.digest != canonical_digest
            or program.requires_laser_authorization is not requires_laser_authorization
            or program.requires_motion is not requires_motion
            or program.safety_profile != current_profile
        ):
            raise SafetyError(
                "Program lines, digest, flags, machine bounds, offsets, feed ceilings, "
                "or hardware gates changed after program preflight; validate the exact "
                "program again"
            )

    def start_validated_program(
        self,
        program: ValidatedProgram,
        name: str = "generated.gcode",
        *,
        _expected_stop_epoch: int | None = None,
    ) -> dict[str, Any]:
        # A STOP that arrives after this request begins must cancel this exact
        # start, even if it lands during controller-state verification.
        start_stop_epoch = (
            self._operation_stop_epoch()
            if _expected_stop_epoch is None
            else _expected_stop_epoch
        )
        with self._command_lock:
            try:
                self._require_current_safety_profile(program)
                return self._start_validated_program(
                    program,
                    name,
                    start_stop_epoch=start_stop_epoch,
                )
            except Exception:
                with self._stop_epoch_lock:
                    if self._stop_epoch == start_stop_epoch:
                        self._clear_arm_authorization()
                raise

    def start_job(self, text: str, name: str = "generated.gcode") -> dict[str, Any]:
        start_stop_epoch = self._operation_stop_epoch()
        try:
            program = self.preflight_program(text)
        except Exception:
            with self._stop_epoch_lock:
                if self._stop_epoch == start_stop_epoch:
                    self._clear_arm_authorization()
            raise
        return self.start_validated_program(
            program,
            name,
            _expected_stop_epoch=start_stop_epoch,
        )

    def _start_validated_program(
        self,
        program: ValidatedProgram,
        name: str,
        *,
        start_stop_epoch: int,
    ) -> dict[str, Any]:
        with self._lock:
            requires_laser_authorization = program.requires_laser_authorization
            requires_motion = program.requires_motion
            start_authorization_epoch: int | None = None
            with self._stop_epoch_lock:
                if self._stop_epoch != start_stop_epoch:
                    raise MachineError("Job start was cancelled by software STOP")
                if requires_laser_authorization:
                    start_authorization_epoch = self._authorization_epoch
                    if (
                        not self._connected
                        or time.monotonic() >= self._armed_until_monotonic
                        or self._armed_program_digest != program.digest
                    ):
                        raise SafetyError(
                            "This powered program must be armed immediately before "
                            "starting using its exact preflight result"
                        )
            if self._job.running:
                raise MachineError("A controller job is already running")
            self._require_connection()
            lines = list(program.lines)
            if (
                requires_motion
                and self.settings.backend == "serial"
                and not self._coordinate_reference_ready
            ):
                raise SafetyError(
                    "Home / park must complete after this controller connection or reset "
                    "before an absolute-motion job can start"
                )
            if (
                requires_motion
                and self.settings.backend == "serial"
                and self._protocol == "grbl"
            ):
                self._verify_grbl_coordinate_state()
            if (
                requires_laser_authorization
                and self.settings.backend == "serial"
                and self.settings.home_and_release_after_powered_job
            ):
                if not self.settings.home_before_photo:
                    raise SafetyError(
                        "Automatic post-job Home / park requires machine.home_before_photo=true"
                    )
            with self._stop_epoch_lock:
                if self._stop_epoch != start_stop_epoch:
                    raise MachineError("Job start was cancelled by software STOP")
                if requires_laser_authorization:
                    if self._authorization_epoch != start_authorization_epoch:
                        raise MachineError("Job start was cancelled by disarm")
                    if (
                        not self._connected
                        or time.monotonic() >= self._armed_until_monotonic
                        or self._armed_program_digest != program.digest
                    ):
                        raise SafetyError(
                            "This powered program must be armed immediately before "
                            "starting using its exact preflight result"
                        )
                self._job_laser_authorized = requires_laser_authorization
                # Every start consumes any temporary grant. A powered job keeps
                # its exact authorization only in this running-job state.
                self._clear_arm_authorization()
                if requires_motion:
                    self._jog_position_mm = None
                self._job = JobStatus(
                    running=True,
                    phase="streaming",
                    name=name[:160],
                    total_lines=len(lines),
                    completed_lines=0,
                    started_at=time.time(),
                    program_digest=program.digest,
                    powered=requires_laser_authorization,
                )
                self._job_stop.clear()
                self._job_thread = threading.Thread(
                    target=self._run_job,
                    args=(
                        lines,
                        requires_laser_authorization,
                        requires_motion,
                        program.digest,
                    ),
                    name="gcode-streamer",
                    daemon=True,
                )
                self._job_thread.start()
            return self._job.to_dict()

    def _execute_running_job_command(
        self,
        command: str,
        *,
        timeout: float | None = None,
    ) -> list[str]:
        """Execute an internal completion command while the job owns the transport."""

        self._write_running_job_line(command)
        try:
            return self._wait_for_ack(timeout or self.settings.read_timeout)
        except MachineError as exc:
            raise MachineError(f"Command {command!r} failed: {exc}") from exc

    def _write_running_job_line(self, command: str) -> None:
        """Check and transmit one job line atomically against software STOP."""

        with self._transport_write_lock:
            if self._job_stop.is_set():
                raise MachineError("Job stopped")
            self._check_line_safety(command, job_execution=True)
            try:
                transport = self._require_connection()
            except MachineError:
                if self._job_stop.is_set():
                    raise MachineError("Job stopped") from None
                raise
            transport.write_line(command)
            self._append_log("TX", command)

    def _finish_powered_job_home_park_and_release(self) -> None:
        """Home, park, and release a serial machine after a successful laser job."""

        self._require_safety_configuration()
        if not self.settings.home_before_photo:
            raise SafetyError(
                "Automatic post-job Home / park requires machine.home_before_photo=true"
            )
        x = float(self.settings.photo_x)
        y = float(self.settings.photo_y)
        travel_feed = float(self.laser_settings.travel_feed_mm_min)
        configured_idle_delay = self.settings.grbl_step_idle_delay_ms

        setup_timeout = max(_PHOTO_COMMAND_ACK_TIMEOUT_SECONDS, self.settings.read_timeout)
        positioning_error: Exception | None = None
        try:
            if not self.settings.work_area.contains(
                x,
                y,
                self.laser_settings.boundary_margin_mm,
            ):
                raise SafetyError(
                    "Configured photography position lies outside the safe work area"
                )
            # GRBL acknowledges planner acceptance before physical motion is
            # necessarily complete. Synchronize behind the final job move before
            # issuing the system-level homing command, which GRBL rejects in Run.
            self._execute_running_job_command(
                "G4 P0.01" if self._protocol == "grbl" else "M400",
                timeout=max(
                    _JOB_COMMAND_ACK_TIMEOUT_SECONDS,
                    self.settings.read_timeout,
                ),
            )
            with self._lock:
                self._job.phase = "homing"
            self._execute_running_job_command(
                "$H" if self._protocol == "grbl" else "G28",
                timeout=max(120.0, self.settings.read_timeout),
            )
            with self._lock:
                self._job.phase = "parking"
            self._execute_running_job_command("G21", timeout=setup_timeout)
            self._execute_running_job_command("G90", timeout=setup_timeout)
            self._execute_running_job_command(
                f"G0 X{x:.3f} Y{y:.3f} "
                f"F{travel_feed:.3f}",
                timeout=setup_timeout,
            )
            self._execute_running_job_command(
                "G4 P0.01" if self._protocol == "grbl" else "M400",
                timeout=max(120.0, self.settings.read_timeout),
            )
        except Exception as exc:
            positioning_error = exc

        release_error: Exception | None = None
        try:
            with self._lock:
                self._job.phase = "releasing"
            if self._protocol == "grbl":
                try:
                    responses = self._execute_running_job_command(
                        "$$",
                        timeout=setup_timeout,
                    )
                    restore_idle_delay = (
                        configured_idle_delay
                        if self._reported_grbl_step_idle_delay(responses) == 255
                        else None
                    )
                except Exception as settings_error:
                    # The release must still be attempted. Restoring the configured
                    # finite delay is safer than risking a persisted $1=255 hold.
                    restore_idle_delay = configured_idle_delay
                    self._append_log(
                        "INFO",
                        "Could not inspect GRBL $1 after the job; forcing the configured "
                        f"finite delay before release ({settings_error})",
                    )
                self._release_grbl_motors(
                    restore_idle_delay=restore_idle_delay,
                    job_execution=True,
                    context="post-job Home / park",
                )
            else:
                self._execute_running_job_command("M84", timeout=setup_timeout)
        except Exception as exc:
            release_error = exc

        # Released axes are convenient for access but no longer a trusted
        # absolute coordinate reference.
        self._coordinate_reference_ready = False
        self._coordinate_state_reference = None
        self._jog_position_mm = None
        if positioning_error is not None:
            if release_error is not None:
                positioning_error.add_note(
                    f"Post-job motor release also failed: {release_error}"
                )
            raise positioning_error
        if release_error is not None:
            raise release_error
        self._append_log(
            "INFO",
            "Powered job completed; machine homed, parked, and motors released",
        )

    def _run_job(
        self,
        lines: list[str],
        requires_laser_authorization: bool,
        requires_motion: bool,
        program_digest: str,
    ) -> None:
        error: str | None = None
        try:
            job_ack_timeout = max(
                _JOB_COMMAND_ACK_TIMEOUT_SECONDS,
                self.settings.read_timeout,
            )
            self._write_running_job_line("M5")
            self._wait_for_ack(job_ack_timeout)
            for index, line in enumerate(lines, start=1):
                self._write_running_job_line(line)
                # GRBL may delay an acknowledgement while its planner is full or
                # while a standalone laser-state command synchronizes queued
                # motion. A short interactive-command timeout can therefore turn
                # a healthy finishing move into a false job failure.
                self._wait_for_ack(job_ack_timeout)
                with self._lock:
                    self._job.completed_lines = index
            run_completion = (
                requires_laser_authorization
                and self.settings.backend == "serial"
                and self.settings.home_and_release_after_powered_job
            )
            if run_completion:
                with self._lock:
                    self._job.phase = "draining"
                self._append_log(
                    "INFO",
                    "Powered toolpath streamed; waiting for queued motion to finish before Home / park",
                )
            self._write_running_job_line("M5")
            self._wait_for_ack(job_ack_timeout)
            if run_completion:
                self._finish_powered_job_home_park_and_release()
            elif self.settings.backend == "serial" and requires_motion:
                # Controller acknowledgement proves planner acceptance, not
                # physical completion. Do not publish a successful terminal
                # job while accepted motion may still be running.
                with self._lock:
                    self._job.phase = "draining"
                self._execute_running_job_command(
                    "G4 P0.01" if self._protocol == "grbl" else "M400",
                    timeout=max(
                        _JOB_COMMAND_ACK_TIMEOUT_SECONDS,
                        self.settings.read_timeout,
                    ),
                )
            if self._job_stop.is_set():
                raise MachineError("Job stopped")
        except Exception as exc:
            error = str(exc)
            # After any failed streamed command, the controller's receive queue
            # and planner acknowledgement position are not provable. Keep the
            # simulator subject to the same reconnect boundary so it remains a
            # useful ordering oracle rather than hiding stale-ACK defects.
            self._mark_controller_command_state_untrusted()
            LOGGER.error("Controller job failed: %s", exc)
            self._append_log("ERROR", f"Controller job failed: {exc}")
            try:
                with self._transport_write_lock:
                    if self._transport is not None:
                        self._transport.write_line("M5")
                        self._append_log("TX", "M5 (job cleanup)")
            except Exception:
                pass
        finally:
            with self._lock:
                # Commit the terminal state atomically against STOP. If STOP
                # wins this lock first, this job cannot publish a success
                # receipt afterward; if completion wins, STOP linearizes after
                # the already-complete job.
                with self._stop_epoch_lock:
                    if error is None and self._job_stop.is_set():
                        error = "Job stopped"
                    self._job.finished_at = time.time()
                    self._job.error = error
                    self._job.phase = "failed" if error is not None else "complete"
                    if error is None:
                        self._last_successful_job = {
                            "name": self._job.name,
                            "program_digest": program_digest,
                            "powered": requires_laser_authorization,
                            "started_at": self._job.started_at,
                            "finished_at": self._job.finished_at,
                            "completed_lines": self._job.completed_lines,
                            "total_lines": self._job.total_lines,
                            "backend": self.settings.backend,
                            "protocol": self._protocol,
                            "hardware_enabled": self.hardware_enabled,
                        }
                    # Publish `running = False` last so status polling cannot observe
                    # a terminal job before its final result and error are available.
                    self._job.running = False
                    self._clear_arm_authorization()
                    self._job_laser_authorized = False

    def request_stop(self, emergency: bool = False) -> None:
        """Latch a stop and issue controller laser-off without waiting for workers."""

        with self._stop_epoch_lock:
            self._stop_epoch += 1
            self._authorization_epoch += 1
            self._job_stop.set()
            self._job_laser_authorized = False
            self._clear_arm_authorization()
            transport = self._transport
            stop_protocol = self._protocol
            if transport is not None:
                # STOP injects an unacknowledged M5 into the controller stream.
                # Even the simulator must reconnect before any later command,
                # otherwise that `ok` can counterfeit a homing acknowledgement
                # and hide exactly the ordering bugs simulation is meant to catch.
                self._controller_reconnect_required = True
                self._coordinate_reference_ready = False
                self._coordinate_state_reference = None
                self._jog_position_mm = None
        if transport is not None:
            if emergency and stop_protocol == "grbl":
                finished = threading.Event()
                failures: list[Exception] = []

                def realtime_stop() -> None:
                    try:
                        transport.write_raw(b"!\x18")
                    except Exception as exc:
                        failures.append(exc)
                    finally:
                        finished.set()

                try:
                    threading.Thread(
                        target=realtime_stop,
                        name="controller-realtime-stop",
                        daemon=True,
                    ).start()
                except Exception as exc:
                    self._append_log(
                        "ERROR",
                        f"GRBL realtime stop could not start: {exc}; continuing with M5",
                    )
                else:
                    if not finished.wait(_REALTIME_STOP_WRITE_DEADLINE_SECONDS):
                        self._append_log(
                            "ERROR",
                            "GRBL realtime stop write timed out; continuing with M5",
                        )
                    elif failures:
                        self._append_log(
                            "ERROR",
                            f"GRBL realtime stop failed: {failures[0]}",
                        )
                    else:
                        self._append_log("TX", "GRBL feed hold + soft reset")
            elif emergency and stop_protocol == "marlin":
                try:
                    with self._transport_write_lock:
                        transport.write_line("M112")
                    self._append_log("TX", "M112")
                except Exception as exc:
                    self._append_log("ERROR", f"Marlin emergency stop failed: {exc}")
            try:
                # Place M5 after an in-flight job write. A worker reaching the
                # same gate later sees the stop/auth latch and cannot transmit.
                with self._transport_write_lock:
                    transport.write_line("M5")
                    self._append_log("TX", "M5")
            except Exception as exc:
                self._append_log("ERROR", f"Laser-off request failed: {exc}")

    def stop_job(self, emergency: bool = False) -> None:
        self.request_stop(emergency=emergency)
        if (
            self._job_thread
            and self._job_thread.is_alive()
            and threading.current_thread() is not self._job_thread
        ):
            self._job_thread.join(timeout=1.5)

    def successful_job_receipt(
        self,
        program_digest: str,
        *,
        not_before: float,
    ) -> dict[str, Any] | None:
        """Return a completed exact-program receipt, never a prepared/running job."""

        if (
            type(program_digest) is not str
            or _PROGRAM_DIGEST_PATTERN.fullmatch(program_digest) is None
            or type(not_before) not in {int, float}
            or not math.isfinite(float(not_before))
        ):
            return None
        threshold = float(not_before)
        with self._lock:
            receipt = self._last_successful_job
            if (
                receipt is None
                or receipt.get("program_digest") != program_digest
                or float(receipt.get("finished_at") or 0.0) < threshold
            ):
                return None
            return dict(receipt)

    def status(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "connecting": self._connecting,
            "backend": self.settings.backend,
            "hardware_enabled": self.hardware_enabled,
            "laser_lockout": self.laser_lockout,
            "protocol": self._protocol,
            "port": self._active_port,
            "baudrate": self._active_baudrate,
            "allow_motion": self.settings.allow_motion,
            "coordinate_reference_ready": self._coordinate_reference_ready,
            "coordinate_state_reference": self._coordinate_state_reference,
            "jog_position_mm": (
                None
                if self._jog_position_mm is None
                else {"x": self._jog_position_mm[0], "y": self._jog_position_mm[1]}
            ),
            "jog_ready": bool(
                self.connected
                and self._coordinate_reference_ready
                and self._jog_position_mm is not None
                and not self._controller_reconnect_required
                and not self.armed
                and not self._job.running
            ),
            "max_travel_feed_mm_min": self.settings.max_travel_feed_mm_min,
            "controller_reconnect_required": self._controller_reconnect_required,
            "armed": self.armed,
            "armed_until": self._armed_until if self.armed else None,
            "arm_phrase": self.ARM_PHRASE,
            "job": self._job.to_dict(),
            "last_successful_job": (
                None
                if self._last_successful_job is None
                else dict(self._last_successful_job)
            ),
            "log": list(self._log)[-80:],
        }
