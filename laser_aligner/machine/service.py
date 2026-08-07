from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from ..config import LaserSettings, MachineSettings
from ..errors import MachineError, SafetyError
from ..gcode.preview import contains_motion, parse_words, strip_comment
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
_GRBL_COORDINATE_EPSILON_MM = 0.001
_GRBL_WORK_COORDINATE_CODES = {f"G{number}" for number in range(54, 60)}
_GRBL_OFFSET_PATTERN = re.compile(
    r"^\[(G5[4-9]|G92):\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)),\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+)),\s*"
    r"([-+]?(?:\d+(?:\.\d*)?|\.\d+))\]$",
    re.IGNORECASE,
)


@dataclass(slots=True)
class JobStatus:
    running: bool = False
    name: str = ""
    total_lines: int = 0
    completed_lines: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        elapsed = None
        if self.started_at is not None:
            end = self.finished_at or time.time()
            elapsed = max(0.0, end - self.started_at)
        return {
            "running": self.running,
            "name": self.name,
            "total_lines": self.total_lines,
            "completed_lines": self.completed_lines,
            "progress": 0.0 if self.total_lines == 0 else self.completed_lines / self.total_lines,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "elapsed_seconds": elapsed,
            "error": self.error,
        }


class MachineService:
    ARM_PHRASE = "ENABLE LASER CONTROL"

    def __init__(
        self,
        settings: MachineSettings,
        laser_settings: LaserSettings,
        hardware_enabled: bool = False,
    ):
        self.settings = settings
        self.laser_settings = laser_settings
        self.hardware_enabled = hardware_enabled
        self._transport: MachineTransport | None = None
        self._protocol = "simulator" if settings.backend == "simulator" else settings.protocol
        self._active_port = settings.port
        self._active_baudrate = settings.baudrate
        self._connected = False
        self._coordinate_reference_ready = settings.backend == "simulator"
        self._coordinate_state_reference: dict[str, Any] | None = None
        self._armed_until = 0.0
        self._lock = threading.RLock()
        self._job = JobStatus()
        self._job_thread: threading.Thread | None = None
        self._job_stop = threading.Event()
        self._job_laser_authorized = False
        self._log: deque[str] = deque(maxlen=200)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def armed(self) -> bool:
        return self._connected and time.time() < self._armed_until

    def _append_log(self, direction: str, line: str) -> None:
        entry = f"{time.strftime('%H:%M:%S')} {direction} {line}"
        self._log.append(entry)
        LOGGER.debug("machine %s %s", direction, line)

    def connect(self, port: str | None = None, protocol: str | None = None, baudrate: int | None = None) -> dict[str, Any]:
        with self._lock:
            if self._connected:
                return self.status()
            if self.settings.backend == "serial" and not self.hardware_enabled:
                raise SafetyError(
                    "Serial hardware is disabled for this process. Start with --hardware after reviewing the configuration."
                )
            selected = (protocol or self.settings.protocol).lower()
            if selected not in {"auto", "grbl", "marlin"}:
                raise MachineError("Protocol must be auto, grbl, or marlin")
            active_port = port or self.settings.port
            active_baudrate = baudrate or self.settings.baudrate
            if self.settings.backend == "simulator":
                transport: MachineTransport = SimulatedTransport()
            else:
                transport = create_serial_transport(active_port, active_baudrate)
            transport.open()
            try:
                self._transport = transport
                self._connected = True
                self._active_port = "simulator" if self.settings.backend == "simulator" else active_port
                self._active_baudrate = active_baudrate
                self._coordinate_reference_ready = self.settings.backend == "simulator"
                self._coordinate_state_reference = None
                self._armed_until = 0.0
                if self.settings.backend == "simulator":
                    self._protocol = "grbl"
                elif selected == "auto":
                    self._protocol = self._identify_protocol()
                else:
                    self._protocol = selected
            except Exception:
                transport.close()
                self._transport = None
                self._connected = False
                self._coordinate_reference_ready = False
                self._armed_until = 0.0
                raise
            self._append_log("INFO", f"connected using {self._protocol}")
            return self.status()

    def _identify_protocol(self) -> str:
        assert self._transport is not None
        time.sleep(max(0.0, self.settings.controller_startup_delay))
        startup = self._transport.drain()
        for line in startup:
            self._append_log("RX", line)
        joined = "\n".join(startup).lower()
        if "grbl" in joined:
            return "grbl"
        self._transport.write_line("$I")
        self._append_log("TX", "$I")
        responses = self._collect_for(1.0)
        if any("grbl" in line.lower() or "[ver:" in line.lower() for line in responses):
            return "grbl"
        self._transport.write_line("M115")
        self._append_log("TX", "M115")
        responses = self._collect_for(1.5)
        if any("firmware_name" in line.lower() or "marlin" in line.lower() for line in responses):
            return "marlin"
        raise MachineError(
            "Controller protocol could not be identified. Set machine.protocol explicitly after running tools/controller_probe.py."
        )

    def _collect_for(self, duration: float) -> list[str]:
        assert self._transport is not None
        deadline = time.monotonic() + duration
        lines: list[str] = []
        while time.monotonic() < deadline:
            line = self._transport.read_line(timeout=min(0.1, max(0.0, deadline - time.monotonic())))
            if line:
                lines.append(line)
                self._append_log("RX", line)
        return lines

    def disconnect(self) -> None:
        self.stop_job(emergency=False)
        with self._lock:
            if self._transport is not None:
                try:
                    self._transport.write_line("M5")
                except Exception:
                    pass
                self._transport.close()
            self._transport = None
            self._connected = False
            self._coordinate_reference_ready = False
            self._coordinate_state_reference = None
            self._armed_until = 0.0
            self._job_laser_authorized = False

    def arm(self, phrase: str) -> float:
        if phrase.strip() != self.ARM_PHRASE:
            raise SafetyError("Arming phrase did not match")
        if not self._connected:
            raise MachineError("Controller is not connected")
        if self.settings.backend == "serial" and not self._coordinate_reference_ready:
            raise SafetyError(
                "Home / park must complete after this controller connection or reset "
                "before laser control can be armed"
            )
        if self._job.running:
            raise MachineError("Cannot change arming state while a job is running")
        self._armed_until = time.time() + self.laser_settings.arm_timeout_seconds
        self._append_log("INFO", "laser control armed temporarily")
        return self._armed_until

    def disarm(self) -> None:
        self._armed_until = 0.0
        self._job_laser_authorized = False
        if self._job.running:
            self.stop_job(emergency=False)
            return
        if self._transport is not None:
            try:
                self._transport.write_line("M5")
                self._append_log("TX", "M5")
            except Exception:
                pass

    def _require_connection(self) -> MachineTransport:
        if not self._connected or self._transport is None:
            raise MachineError("Controller is not connected")
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

    def _check_line_safety(self, line: str, *, job_execution: bool = False) -> None:
        cleaned = strip_comment(line)
        if not cleaned:
            return
        words = parse_words(cleaned)
        m_codes = self._codes(words, "M")
        g_codes = self._codes(words, "G")
        powers = [word.value for word in words if word.letter == "S"]
        requests_laser = bool(m_codes.intersection({3, 4}) or any(value > 0 for value in powers))
        laser_authorized = self.armed or (job_execution and self._job_laser_authorized)
        if requests_laser and not laser_authorized:
            raise SafetyError("Laser-enable or positive-power command blocked because control is not armed")
        is_homing = cleaned.upper() == "$H" or 28 in g_codes
        if (contains_motion(cleaned) or is_homing) and not (
            self.settings.allow_motion or self.settings.backend == "simulator"
        ):
            raise SafetyError("Motion commands are disabled in machine.allow_motion")
        if self.settings.backend == "serial" and not self.hardware_enabled:
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
    ) -> list[str]:
        if len(line) > 256:
            raise MachineError("Single controller command exceeds 256 characters")
        if self._job.running:
            raise MachineError("Manual commands are disabled while a job is running")
        cleaned = strip_comment(line).upper() if _internal_motion else self._validate_manual_command(line)
        if not cleaned:
            raise MachineError("Controller command is empty")
        self._check_line_safety(cleaned)
        transport = self._require_connection()
        transport.write_line(cleaned)
        self._append_log("TX", cleaned)
        acknowledgement_timeout = timeout or self.settings.read_timeout
        try:
            return self._wait_for_ack(acknowledgement_timeout)
        except MachineError as exc:
            raise MachineError(f"Command {cleaned!r} failed: {exc}") from exc

    def _wait_for_ack(self, timeout: float) -> list[str]:
        transport = self._require_connection()
        deadline = time.monotonic() + timeout
        responses: list[str] = []
        while time.monotonic() < deadline:
            response = transport.read_line(timeout=min(0.2, max(0.0, deadline - time.monotonic())))
            if not response:
                continue
            responses.append(response)
            self._append_log("RX", response)
            lower = response.lower()
            if lower == "ok" or lower.startswith("ok "):
                return responses
            if lower.startswith("error") or lower.startswith("alarm"):
                raise MachineError(response)
            if lower.startswith("busy") or response.startswith("<") or response.startswith("["):
                continue
        raise MachineError(f"Controller did not acknowledge command within {timeout:g} seconds")

    def query_identity(self) -> list[str]:
        command = "$I" if self._protocol == "grbl" else "M115"
        return self.send_command(command, timeout=3.0)

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

    def _wait_for_motion_complete(self, timeout: float = 120.0) -> list[str]:
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

    def prepare_photo_position(self) -> dict[str, Any]:
        """Home if configured, then park XY at the repeatable camera pose.

        This routine never emits a laser-enable command. It remains subject to
        the normal serial-hardware and ``machine.allow_motion`` gates.
        """
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
        if not self.settings.work_area.contains(x, y, self.laser_settings.boundary_margin_mm):
            raise SafetyError("Configured photography position lies outside the safe work area")

        transcript: list[dict[str, Any]] = []

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
            )
            transcript.append({"command": command, "responses": responses})

        self._coordinate_reference_ready = self.settings.backend == "simulator"
        self._coordinate_state_reference = None
        try:
            execute("M5")
            if self.settings.home_before_photo:
                execute(
                    "$H" if self._protocol == "grbl" else "G28",
                    timeout=max(120.0, self.settings.read_timeout),
                )
                # A successful homing acknowledgement is the completion
                # barrier. Some GRBL-derived controllers do not emit standard
                # realtime <Idle...> reports after homing.
            execute("G21")
            execute("G90")
            execute(
                f"G0 X{x:.3f} Y{y:.3f} F{float(self.laser_settings.travel_feed_mm_min):.3f}",
                timeout=max(
                    _PHOTO_COMMAND_ACK_TIMEOUT_SECONDS,
                    self.settings.read_timeout,
                ),
            )
            idle_responses = self._wait_for_motion_complete(timeout=120.0)
            coordinate_state = (
                self._read_grbl_coordinate_state()
                if self.settings.backend == "serial" and self._protocol == "grbl"
                else None
            )
            if coordinate_state is not None:
                self._append_log(
                    "INFO",
                    "Home / park GRBL coordinate reference: "
                    + self._coordinate_state_summary(coordinate_state),
                )
        except Exception:
            self._coordinate_reference_ready = False
            raise
        self._coordinate_reference_ready = True
        self._coordinate_state_reference = coordinate_state
        return {
            "position": {"x": x, "y": y, "z": self.settings.photo_z},
            "homed": self.settings.home_before_photo,
            "transcript": transcript,
            "idle_responses": idle_responses,
            "coordinate_state": coordinate_state,
            "warning": (
                "photo_position.z is recorded but is not moved automatically; set laser focus/material height manually."
                if self.settings.photo_z is not None
                else None
            ),
        }

    def _validate_stream_line(self, line: str) -> tuple[list[Any], set[int], set[int]]:
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
        if "S" in values and not 0 <= values["S"] <= self.laser_settings.power_max:
            raise SafetyError(f"S power must be between 0 and {self.laser_settings.power_max}")

        if g_codes in ({0}, {1}):
            if "X" not in values and "Y" not in values:
                raise SafetyError("G0/G1 line must contain X and/or Y")
            if any(word.letter not in {"G", "X", "Y", "F"} for word in words):
                raise SafetyError("Only X, Y, and F are permitted on streamed G0/G1 lines")
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

    def _analyze_program(self, text: str) -> tuple[list[str], bool]:
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
        x: float | None = None
        y: float | None = None
        last_m_code: int | None = None
        last_line_is_m5 = False

        for index, line in enumerate(lines, start=1):
            words, g_codes, m_codes = self._validate_stream_line(line)
            last_line_is_m5 = m_codes == {5}
            values = {word.letter: word.value for word in words}
            try:
                self._check_line_safety(line, job_execution=True)
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
                if x is None or y is None:
                    if "X" not in values or "Y" not in values:
                        raise SafetyError(f"Line {index}: the first move must establish both X and Y")
                new_x = values.get("X", x)
                new_y = values.get("Y", y)
                assert new_x is not None and new_y is not None
                if not self.settings.work_area.contains(
                    new_x, new_y, self.laser_settings.boundary_margin_mm
                ):
                    raise SafetyError(
                        f"Line {index}: G-code point X{new_x:.3f} Y{new_y:.3f} lies outside the configured safe work area"
                    )
                if g_codes == {0} and laser_on:
                    raise SafetyError(f"Line {index}: rapid G0 motion is blocked while the laser is enabled")
                x, y = new_x, new_y

            if m_codes in ({3}, {4}):
                if not seen_mm or not seen_absolute or not seen_initial_m5:
                    raise SafetyError(f"Line {index}: G21, G90, and an initial M5 are required before laser enable")
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

    def validate_program(self, text: str) -> list[str]:
        lines, _ = self._analyze_program(text)
        return lines

    def start_job(self, text: str, name: str = "generated.gcode") -> dict[str, Any]:
        with self._lock:
            if self._job.running:
                raise MachineError("A controller job is already running")
            self._require_connection()
            lines, requires_laser_authorization = self._analyze_program(text)
            requires_motion = any(contains_motion(line) for line in lines)
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
            if requires_laser_authorization and not self.armed:
                raise SafetyError("This program contains laser-enable commands and must be armed immediately before starting")
            self._job_laser_authorized = requires_laser_authorization
            self._job = JobStatus(
                running=True,
                name=name[:160],
                total_lines=len(lines),
                completed_lines=0,
                started_at=time.time(),
            )
            self._job_stop.clear()
            self._job_thread = threading.Thread(
                target=self._run_job,
                args=(lines,),
                name="gcode-streamer",
                daemon=True,
            )
            self._job_thread.start()
            return self._job.to_dict()

    def _run_job(self, lines: list[str]) -> None:
        error: str | None = None
        try:
            transport = self._require_connection()
            transport.write_line("M5")
            self._append_log("TX", "M5")
            self._wait_for_ack(self.settings.read_timeout)
            for index, line in enumerate(lines, start=1):
                if self._job_stop.is_set():
                    raise MachineError("Job stopped")
                self._check_line_safety(line)
                transport.write_line(line)
                self._append_log("TX", line)
                self._wait_for_ack(self.settings.read_timeout)
                with self._lock:
                    self._job.completed_lines = index
            transport.write_line("M5")
            self._append_log("TX", "M5")
            self._wait_for_ack(self.settings.read_timeout)
        except Exception as exc:
            error = str(exc)
            if self.settings.backend == "serial":
                self._coordinate_reference_ready = False
                self._coordinate_state_reference = None
            LOGGER.error("Controller job failed: %s", exc)
            try:
                if self._transport is not None:
                    self._transport.write_line("M5")
            except Exception:
                pass
        finally:
            with self._lock:
                self._job.running = False
                self._job.finished_at = time.time()
                self._job.error = error
                self._armed_until = 0.0
                self._job_laser_authorized = False

    def stop_job(self, emergency: bool = False) -> None:
        self._job_stop.set()
        transport = self._transport
        if transport is not None:
            try:
                if emergency and self._protocol == "grbl":
                    transport.write_raw(b"!\x18")
                    self._append_log("TX", "GRBL feed hold + soft reset")
                    if self.settings.backend == "serial":
                        self._coordinate_reference_ready = False
                        self._coordinate_state_reference = None
                elif emergency and self._protocol == "marlin":
                    transport.write_line("M112")
                    self._append_log("TX", "M112")
                    if self.settings.backend == "serial":
                        self._coordinate_reference_ready = False
                        self._coordinate_state_reference = None
                transport.write_line("M5")
                self._append_log("TX", "M5")
            except Exception:
                pass
        if self._job_thread and self._job_thread.is_alive() and threading.current_thread() is not self._job_thread:
            self._job_thread.join(timeout=1.5)
        self._armed_until = 0.0
        if not (self._job_thread and self._job_thread.is_alive()):
            self._job_laser_authorized = False

    def status(self) -> dict[str, Any]:
        return {
            "connected": self._connected,
            "backend": self.settings.backend,
            "hardware_enabled": self.hardware_enabled,
            "protocol": self._protocol,
            "port": self._active_port,
            "baudrate": self._active_baudrate,
            "allow_motion": self.settings.allow_motion,
            "coordinate_reference_ready": self._coordinate_reference_ready,
            "coordinate_state_reference": self._coordinate_state_reference,
            "armed": self.armed,
            "armed_until": self._armed_until if self.armed else None,
            "arm_phrase": self.ARM_PHRASE,
            "job": self._job.to_dict(),
            "log": list(self._log)[-80:],
        }
