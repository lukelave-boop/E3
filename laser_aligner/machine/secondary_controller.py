"""Pi-local ownership for the secondary Creality/Marlin controller.

The secondary controller has exactly one reader and writer.  Typed clients
share :class:`CrealityControllerOwner`, which keeps a command and its bounded
acknowledgement exchange under one lock.  This module is deliberately isolated
from the desktop and imports the POSIX serial implementation only when a real
session is opened.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from ..air_assist import AirAssistCommands, AirAssistMode, AirAssistTarget
from ..errors import MachineError

_FAN_ON_COMMAND = "M106 S255"
_FAN_OFF_COMMAND = "M106 S0"
_MAX_COMMAND_CHARACTERS = 160
_MAX_RESPONSE_DIAGNOSTIC_CHARACTERS = 160


class _SerialTransport(Protocol):
    def open(self) -> None: ...

    def close(self) -> None: ...

    def write_line(self, line: str) -> None: ...

    def read_line(self, timeout: float = 1.0) -> str | None: ...

    def drain(self) -> list[str]: ...

    def raise_if_faulted(self) -> None: ...


SerialFactory = Callable[[str, int], _SerialTransport]
WriteGuardFactory = Callable[[], AbstractContextManager[None]]


class SecondaryControllerError(MachineError):
    """The Pi-local secondary controller session cannot be trusted."""


@dataclass(frozen=True, slots=True)
class CrealityControllerSession:
    """Immutable serial identity and timeout policy for one owned session."""

    port: str
    baudrate: int
    startup_delay_seconds: float
    read_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class SecondaryMarlinFanStatus:
    """Immutable diagnostic snapshot; it is not a safety-rated indication."""

    ready: bool
    enabled: bool | None
    fault: str | None
    port: str
    baudrate: int
    mapping_digest: str


def _default_serial_factory(path: str, baudrate: int) -> _SerialTransport:
    if os.name != "posix":
        raise MachineError(
            "The secondary Creality controller requires a POSIX Raspberry Pi host"
        )
    from .serial_posix import PosixSerial

    return PosixSerial(path, baudrate)


def _bounded_detail(value: object) -> str:
    detail = str(value).strip().replace("\r", " ").replace("\n", " ")
    if not detail:
        detail = value.__class__.__name__
    return detail[:_MAX_RESPONSE_DIAGNOSTIC_CHARACTERS]


class CrealityControllerOwner:
    """Own one persistent Creality serial transport and its sole response reader."""

    def __init__(
        self,
        port: str,
        baudrate: int,
        *,
        serial_factory: SerialFactory = _default_serial_factory,
        sleep: Callable[[float], None] = time.sleep,
        startup_delay_seconds: float = 2.0,
        read_timeout_seconds: float = 1.0,
    ) -> None:
        if type(port) is not str or not port or port != port.strip():
            raise ValueError("Secondary controller port must be non-empty without whitespace")
        if type(baudrate) is not int or baudrate <= 0:
            raise ValueError("Secondary controller baudrate must be a positive integer")
        if not callable(serial_factory):
            raise TypeError("serial_factory must be callable")
        if not callable(sleep):
            raise TypeError("sleep must be callable")
        if (
            type(startup_delay_seconds) not in {int, float}
            or not math.isfinite(float(startup_delay_seconds))
            or float(startup_delay_seconds) < 0.0
        ):
            raise ValueError("Secondary controller startup delay must be finite and non-negative")
        if (
            type(read_timeout_seconds) not in {int, float}
            or not math.isfinite(float(read_timeout_seconds))
            or float(read_timeout_seconds) <= 0.0
        ):
            raise ValueError("Secondary controller read timeout must be positive and finite")

        self._session = CrealityControllerSession(
            port=port,
            baudrate=baudrate,
            startup_delay_seconds=float(startup_delay_seconds),
            read_timeout_seconds=float(read_timeout_seconds),
        )
        self._serial_factory = serial_factory
        self._sleep = sleep
        self._lock = RLock()
        self._transport: _SerialTransport | None = None
        self._trusted = False
        self._fault: str | None = None
        self._secondary_fan_binding: AirAssistCommands | None = None
        self._secondary_fan_enabled: bool | None = None

    @property
    def session(self) -> CrealityControllerSession:
        return self._session

    @property
    def ready(self) -> bool:
        with self._lock:
            self._refresh_transport_fault_locked()
            return self._trusted and self._transport is not None

    @property
    def fault(self) -> str | None:
        with self._lock:
            self._refresh_transport_fault_locked()
            return self._fault

    def raise_if_faulted(self) -> None:
        with self._lock:
            self._refresh_transport_fault_locked()
            if self._fault is not None:
                raise SecondaryControllerError(self._fault)

    def _refresh_transport_fault_locked(self) -> None:
        transport = self._transport
        if not self._trusted or transport is None:
            return
        checker = getattr(transport, "raise_if_faulted", None)
        if not callable(checker):
            return
        try:
            checker()
        except Exception as exc:
            self._fail_locked(exc)

    def _close_transport_locked(self) -> None:
        transport = self._transport
        self._transport = None
        self._trusted = False
        self._secondary_fan_enabled = None
        if transport is not None:
            try:
                transport.close()
            except Exception:
                pass

    def _fail_locked(self, detail: object) -> SecondaryControllerError:
        self._close_transport_locked()
        self._fault = (
            "Secondary Creality controller session failed: "
            f"{_bounded_detail(detail)}"
        )
        return SecondaryControllerError(self._fault)

    def _open_locked(self) -> _SerialTransport:
        if self._trusted and self._transport is not None:
            return self._transport
        self._close_transport_locked()
        transport: _SerialTransport | None = None
        try:
            transport = self._serial_factory(
                self.session.port,
                self.session.baudrate,
            )
            self._transport = transport
            transport.open()
            self._sleep(self.session.startup_delay_seconds)
            # Startup chatter predates the next command and can never acknowledge it.
            transport.drain()
        except Exception as exc:
            if transport is not None:
                self._transport = transport
            raise self._fail_locked(exc) from exc
        self._trusted = True
        self._fault = None
        return transport

    @staticmethod
    def _validate_typed_command(command: str) -> str:
        if (
            type(command) is not str
            or not command
            or command != command.strip()
            or len(command) > _MAX_COMMAND_CHARACTERS
            or "\r" in command
            or "\n" in command
        ):
            raise ValueError("Secondary controller command must be one bounded line")
        try:
            command.encode("ascii", errors="strict")
        except UnicodeError as exc:
            raise ValueError("Secondary controller command must be ASCII") from exc
        return command

    def _execute_acknowledged(
        self,
        command: str,
        *,
        write_guard: WriteGuardFactory | None = None,
        allow_open: bool = True,
    ) -> None:
        """Write one typed command and consume its positive bounded ``ok`` reply.

        The owner lock covers the complete exchange.  An optional cross-controller
        guard covers only the physical write, so primary STOP never waits for a
        secondary acknowledgement timeout.  Only typed controller clients in
        this module may use this private exchange boundary.
        """

        command = self._validate_typed_command(command)
        if type(allow_open) is not bool:
            raise TypeError("allow_open must be an exact boolean")
        if write_guard is not None and not callable(write_guard):
            raise TypeError("write_guard must be callable")

        with self._lock:
            self._refresh_transport_fault_locked()
            if self._transport is None or not self._trusted:
                if not allow_open:
                    detail = self._fault or "controller has not completed initialization"
                    raise SecondaryControllerError(detail)
                transport = self._open_locked()
            else:
                transport = self._transport
            guard = write_guard() if write_guard is not None else nullcontext()
            write_started = False
            try:
                with guard:
                    write_started = True
                    transport.write_line(command)
            except Exception as exc:
                # A STOP gate may reject before the controller write.  In that
                # case the serial session remains trustworthy and cleanup can
                # use it immediately.  Once a write begins, every failure must
                # close because an acknowledgement may now be outstanding.
                if not write_started:
                    raise
                raise self._fail_locked(exc) from exc
            try:
                deadline = time.monotonic() + self.session.read_timeout_seconds
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0.0:
                        raise SecondaryControllerError(
                            "timed out waiting for an acknowledged ok response"
                        )
                    response = transport.read_line(timeout=remaining)
                    if response is None:
                        continue
                    normalized = response.strip().casefold()
                    if normalized == "ok" or normalized.startswith("ok "):
                        self._fault = None
                        return
                    if (
                        normalized.startswith("error")
                        or normalized.startswith("alarm")
                        or normalized.startswith("!!")
                        or normalized.startswith("resend")
                        or "unknown command" in normalized
                    ):
                        raise SecondaryControllerError(
                            f"controller rejected the command: {_bounded_detail(response)}"
                        )
                    # Bounded startup/informational chatter is not an acknowledgement.
            except Exception as exc:
                raise self._fail_locked(exc) from exc

    def _bind_secondary_fan(self, binding: AirAssistCommands) -> None:
        """Register one shared FAN2 state cache for this physical owner."""

        with self._lock:
            existing = self._secondary_fan_binding
            if existing is not None and existing != binding:
                raise ValueError(
                    "One Creality owner cannot bind multiple secondary fan mappings"
                )
            self._secondary_fan_binding = binding

    def close(self) -> None:
        with self._lock:
            self._close_transport_locked()


class SecondaryMarlinFanController:
    """Typed exact-command client for the configured secondary FAN2 output."""

    def __init__(
        self,
        owner: CrealityControllerOwner,
        binding: AirAssistCommands,
    ) -> None:
        if not isinstance(owner, CrealityControllerOwner):
            raise TypeError("owner must be a CrealityControllerOwner")
        if not isinstance(binding, AirAssistCommands):
            raise TypeError("binding must be immutable AirAssistCommands")
        if (
            binding.mode is not AirAssistMode.SECONDARY_MARLIN_FAN
            or binding.target is not AirAssistTarget.PI_SECONDARY
            or binding.protocol != "marlin"
            or binding.fan_index is not None
            or binding.on_commands != (_FAN_ON_COMMAND,)
            or binding.off_commands != (_FAN_OFF_COMMAND,)
            or binding.port != owner.session.port
            or binding.baudrate != owner.session.baudrate
        ):
            raise ValueError(
                "Secondary fan binding must match the owned exact M106 FAN2 session"
            )
        self._owner = owner
        self._binding = binding
        owner._bind_secondary_fan(binding)

    @property
    def binding(self) -> AirAssistCommands:
        return self._binding

    @property
    def ready(self) -> bool:
        return self._owner.ready

    @property
    def fault(self) -> str | None:
        return self._owner.fault

    @property
    def status(self) -> SecondaryMarlinFanStatus:
        with self._owner._lock:
            self._owner._refresh_transport_fault_locked()
            ready = (
                self._owner._trusted
                and self._owner._transport is not None
            )
            enabled = (
                self._owner._secondary_fan_enabled
                if ready
                else None
            )
            return SecondaryMarlinFanStatus(
                ready=ready,
                enabled=enabled,
                fault=self._owner._fault,
                port=self._owner.session.port,
                baudrate=self._owner.session.baudrate,
                mapping_digest=self._binding.mapping_digest,
            )

    def raise_if_faulted(self) -> None:
        self._owner.raise_if_faulted()

    def _force_off(self) -> None:
        self._owner._execute_acknowledged(_FAN_OFF_COMMAND, allow_open=True)
        self._owner._secondary_fan_enabled = False

    def initialize_off(self) -> None:
        """Open the persistent session and require an acknowledged OFF."""

        with self._owner._lock:
            self._force_off()

    def ensure_off(self) -> None:
        """Force a fresh acknowledged OFF, even when OFF was previously known."""

        with self._owner._lock:
            self._force_off()

    def set_enabled(
        self,
        enabled: bool,
        *,
        mapping_digest: str,
        write_guard: WriteGuardFactory | None = None,
    ) -> None:
        if type(enabled) is not bool:
            raise TypeError("Secondary fan enabled state must be an exact boolean")
        if mapping_digest != self._binding.mapping_digest:
            raise SecondaryControllerError(
                "Secondary fan mapping digest does not match the immutable binding"
            )
        with self._owner._lock:
            self._owner.raise_if_faulted()
            if not self._owner.ready:
                raise SecondaryControllerError(
                    "Secondary fan controller has not completed acknowledged initialization"
                )
            if self._owner._secondary_fan_enabled is enabled:
                return
            command = _FAN_ON_COMMAND if enabled else _FAN_OFF_COMMAND
            try:
                self._owner._execute_acknowledged(
                    command,
                    write_guard=write_guard,
                    allow_open=False,
                )
            except Exception:
                self._owner._secondary_fan_enabled = None
                raise
            self._owner._secondary_fan_enabled = enabled

    def best_effort_off(self) -> bool:
        """Attempt OFF and at most one reopen without propagating cleanup failure."""

        with self._owner._lock:
            attempted_current_session = self._owner.ready
            if attempted_current_session:
                try:
                    self._owner._execute_acknowledged(
                        _FAN_OFF_COMMAND,
                        allow_open=False,
                    )
                    self._owner._secondary_fan_enabled = False
                    return True
                except Exception:
                    self._owner._secondary_fan_enabled = None
            # A failed current exchange closed the session.  Cleanup may reopen
            # once; an already-closed session receives this one attempt directly.
            try:
                self._owner._execute_acknowledged(
                    _FAN_OFF_COMMAND,
                    allow_open=True,
                )
                self._owner._secondary_fan_enabled = False
                return True
            except Exception:
                self._owner._secondary_fan_enabled = None
                return False

    def close(self) -> None:
        with self._owner._lock:
            self.best_effort_off()
            self._owner.close()
            self._owner._secondary_fan_enabled = None


__all__ = [
    "CrealityControllerOwner",
    "CrealityControllerSession",
    "SecondaryControllerError",
    "SecondaryMarlinFanController",
    "SecondaryMarlinFanStatus",
    "SerialFactory",
    "WriteGuardFactory",
]
