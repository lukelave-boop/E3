"""One receive owner for a validated primary GRBL session.

Wire replies have no transaction IDs. This owner prevents input already queued
or observed while idle from being attributed to a later command; it cannot
identify an arbitrarily delayed old ACK first arriving during another exchange.
An ambiguous exchange must therefore retire the entire session, never retry it.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ..errors import MachineError
from .controller_dialects import CommandResponseKind, ControllerDialect
from .transport import MachineTransport


class ControllerReceiver:
    """Serialize receive dispatch and transaction admission on one transport."""

    def __init__(
        self,
        transport: MachineTransport,
        dialect: ControllerDialect,
        *,
        on_failure: Callable[[str], None],
        on_idle: Callable[[str], None],
        owner_alive: Callable[[], bool],
    ) -> None:
        self._transport = transport
        self._dialect = dialect
        self._on_failure = on_failure
        self._on_idle = on_idle
        self._owner_alive = owner_alive
        self._ingress = threading.Lock()
        self._condition = threading.Condition()
        self._pending: deque[str] = deque()
        self._transaction: int | None = None
        self._fault: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._run, name="primary-controller-receiver", daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        # Never join while the service holds locks needed by a failure callback.
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    @property
    def has_transaction(self) -> bool:
        with self._condition:
            return self._transaction is not None

    @property
    def failure(self) -> str | None:
        with self._condition:
            return self._fault

    def raise_if_faulted(self) -> None:
        with self._condition:
            if self._fault is not None:
                raise MachineError(self._fault)
            if self._stop.is_set():
                raise MachineError("Controller receive session was retired")

    @contextmanager
    def authority(self) -> Iterator[None]:
        """Linearize a short authority grant with receive-fault latching."""
        with self._condition:
            self.raise_if_faulted()
            yield

    @contextmanager
    def terminal_evidence(self) -> Iterator[str | None]:
        """Publish a terminal job result atomically with observed RX failure."""
        with self._condition:
            yield self._fault

    def _latch_failure(self, detail: str) -> None:
        with self._condition:
            # STOP retires authority before issuing its own reset. Late bytes
            # or close exceptions from that retired generation are cleanup,
            # not a new initiating fault that can replace "Job stopped".
            if self._stop.is_set():
                return
            if self._fault is None:
                self._fault = detail
            self._condition.notify_all()

    def _check_transport(self) -> None:
        checker = getattr(self._transport, "raise_if_faulted", None)
        if callable(checker):
            checker()
        fault = getattr(self._transport, "fault", None)
        if fault:
            raise MachineError(f"Controller read failed: {fault}")
        if getattr(self._transport, "is_open", True) is False:
            raise MachineError("Controller transport closed unexpectedly")

    def _dispatch(self, response: str) -> None:
        if self._stop.is_set():
            return
        try:
            self._dispatch_frame(response)
        except MachineError as exc:
            # Fatal input never reaches a command consumer. Preserve the exact
            # offending frame before the service snapshots quarantine evidence.
            self._on_idle(response)
            self._latch_failure(str(exc))
            raise

    def _read_transport(self, timeout: float) -> str | None:
        # Called under ingress: publish a read fault before admission can run.
        try:
            self._check_transport()
            return self._transport.read_line(timeout=timeout)
        except Exception as exc:
            detail = f"Controller read failed: {exc}"
            self._latch_failure(detail)
            raise MachineError(detail) from exc

    def _dispatch_frame(self, response: str) -> None:
        kind = self._dialect.classify_command_response(response)
        if kind is CommandResponseKind.ALARM:
            raise MachineError(f"Controller alarm invalidated the session: {response}")
        if kind is CommandResponseKind.STARTUP:
            raise MachineError(f"Controller restarted during the session: {response}")
        if kind is CommandResponseKind.MALFORMED:
            raise MachineError(f"Controller returned a malformed response frame: {response!r}")
        if kind is CommandResponseKind.REALTIME_STATUS:
            state = response[1:-1].split("|", 1)[0].split(":", 1)[0].casefold()
            if state in {"alarm", "sleep", "door", "hold"}:
                raise MachineError(f"Controller state invalidated the session: {response}")
        with self._condition:
            if self._transaction is not None:
                if len(self._pending) >= 512:
                    raise MachineError("Controller receive transaction exceeded its bounded queue")
                self._pending.append(response)
                self._condition.notify_all()
                return
        if kind not in {
            CommandResponseKind.REALTIME_STATUS,
            CommandResponseKind.FIRMWARE_DIAGNOSTIC,
        }:
            raise MachineError(f"Controller returned an unowned response while idle: {response!r}")
        # No wire transaction does not imply physical idle: accepted planner
        # motion and delayed realtime reports can span command boundaries.
        self._on_idle(response)

    def begin(self, sequence: int, write: Callable[[], None]) -> None:
        """Drain idle input and admit/write an exchange under receive ownership."""
        try:
            with self._ingress:
                self.raise_if_faulted()
                with self._condition:
                    if self._transaction is not None:
                        raise MachineError("Controller receive transaction already has an owner")
                # Input queued before admission cannot acknowledge the new TX.
                # Bound even a stream of benign diagnostics at this boundary.
                for _ in range(512):
                    response = self._read_transport(timeout=0.0)
                    if not response:
                        break
                    self._dispatch(response)
                else:
                    raise MachineError("Controller idle input did not reach a bounded boundary")
                with self._condition:
                    self.raise_if_faulted()
                    self._transaction = sequence
                try:
                    write()
                except Exception as exc:
                    self._latch_failure(str(exc))
                    raise
        except Exception as exc:
            self._latch_failure(str(exc))
            # Caller handles exact-session quarantine; never callback while it
            # might hold the service's physical-write lock.
            raise

    def end(self, sequence: int | None) -> None:
        with self._ingress:
            with self._condition:
                if self._transaction != sequence:
                    return
                self._transaction = None
                remaining = tuple(self._pending)
                self._pending.clear()
                if self._fault is not None or self._stop.is_set():
                    return
            try:
                for response in remaining:
                    self._dispatch(response)
            except MachineError as exc:
                self._latch_failure(str(exc))
                raise

    def read_line(self, timeout: float) -> str | None:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            while True:
                self.raise_if_faulted()
                if self._pending:
                    return self._pending.popleft()
                remaining = deadline - time.monotonic()
                if remaining <= 0.0:
                    break
                self._condition.wait(remaining)
        # A condition timeout alone does not prove a wire boundary: the worker
        # may still be in its raw read. Serialize one nonblocking read before
        # declaring the receive queue quiet, including on coarse Windows timers.
        with self._ingress:
            self.raise_if_faulted()
            response = self._read_transport(timeout=0.0)
            if response:
                self._dispatch(response)
            with self._condition:
                self.raise_if_faulted()
                return self._pending.popleft() if self._pending else None

    def _run(self) -> None:
        while not self._stop.is_set() and self._owner_alive():
            try:
                with self._ingress:
                    if self._stop.is_set():
                        return
                    self.raise_if_faulted()
                    response = self._read_transport(timeout=0.02)
                    if response:
                        self._dispatch(response)
            except Exception as exc:
                if self._stop.is_set():
                    return
                self._latch_failure(str(exc))
                # Revocation/abort may acquire service and transport locks. It
                # must occur outside ingress/condition ownership.
                self._on_failure(str(exc))
                return
            # Yield between reads so command admission cannot starve behind a
            # fast test transport or continuous asynchronous telemetry.
            time.sleep(0)
