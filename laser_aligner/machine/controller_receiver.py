"""One receive owner for a validated primary GRBL session.

Wire replies have no transaction IDs. This owner prevents input already queued
or observed while idle from being attributed to a later command; it cannot
identify an arbitrarily delayed old ACK first arriving during another exchange.
An ambiguous exchange must therefore retire the entire session, never retry it.
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from ..errors import MachineError
from .controller_dialects import CommandResponseKind, ControllerDialect
from .io_diagnostics import IOProgress, thread_snapshot
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
        on_realtime: Callable[[str], bool] | None = None,
    ) -> None:
        self._transport = transport
        self._dialect = dialect
        self._on_failure = on_failure
        self._on_idle = on_idle
        self._owner_alive = owner_alive
        self._on_realtime = on_realtime
        self._ingress = threading.Lock()
        self._condition = threading.Condition()
        self._ingress_waiters = 0
        self._pending: deque[str] = deque()
        self._transaction: int | None = None
        self._stream: int | None = None
        self._stream_pending: deque[tuple[int, int]] = deque()
        self._stream_ready: deque[tuple[int, int, str]] = deque()
        self._stream_bytes = 0
        self._stream_last_sequence = 0
        self._fault: str | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._progress = IOProgress()

    def diagnostic_snapshot(self) -> dict:
        result = {
            "worker": self._progress.snapshot(),
            "thread": thread_snapshot(self._thread),
            "stop_requested": self._stop.is_set(),
        }
        if not self._condition.acquire(blocking=False):
            result["ownership"] = {"snapshot_unavailable": "condition_busy"}
            return result
        try:
            result["ownership"] = {
                "transaction": self._transaction, "pending_lines": len(self._pending),
                "ingress_waiters": self._ingress_waiters, "fault": self._fault,
                "stream": self._stream,
                "stream_pending": len(self._stream_pending),
                "stream_ready": len(self._stream_ready),
                "stream_bytes": self._stream_bytes,
            }
        finally:
            self._condition.release()
        return result

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
            return self._transaction is not None or self._stream is not None

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
            expected_suspend = self._on_realtime(response) if self._on_realtime else False
            state = response[1:-1].split("|", 1)[0].split(":", 1)[0].casefold()
            if state in {"alarm", "sleep", "hold"} or (state == "door" and not expected_suspend):
                raise MachineError(f"Controller state invalidated the session: {response}")
        with self._condition:
            if self._stream is not None:
                if kind is CommandResponseKind.ACKNOWLEDGEMENT:
                    if not self._stream_pending:
                        raise MachineError("Controller returned an unowned streaming acknowledgement")
                    sequence, byte_count = self._stream_pending.popleft()
                    # Retain credit until the stream consumer observes this ACK.
                    self._stream_ready.append((sequence, byte_count, response))
                    return
                if kind is CommandResponseKind.ERROR:
                    raise MachineError(f"Controller rejected a streaming command: {response}")
                if kind is CommandResponseKind.PAYLOAD:
                    raise MachineError(f"Controller returned unexpected streaming payload: {response!r}")
                self._on_idle(response)
                return
            if self._transaction is not None:
                if kind is CommandResponseKind.REALTIME_STATUS and expected_suspend:
                    # Pause has already observed and validated this Door frame.
                    # Its ordinary reply consumer is suspended: keep telemetry
                    # in diagnostics without filling the bounded ACK/payload
                    # queue. Resume status and every command reply stay queued.
                    self._on_idle(response)
                    return
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
            with self._consumer_ingress():
                self.raise_if_faulted()
                with self._condition:
                    if self._transaction is not None or self._stream is not None:
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
        with self._consumer_ingress():
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

    def begin_stream(self, token: int) -> None:
        """Transfer receive pumping to one bounded G1 stream owner.

        The caller must keep motion, output and STOP authorization at every
        physical write. This API only owns response ordering and byte credit.
        Untagged GRBL ACKs cannot reveal a duplicate while other replies are
        pending; excess ACKs and faults observed at admission/fences are fatal.
        """
        try:
            with self._consumer_ingress():
                self.raise_if_faulted()
                if type(token) is not int or token < 1:
                    raise MachineError("Controller stream token must be a positive integer")
                with self._condition:
                    if self._transaction is not None or self._stream is not None:
                        raise MachineError("Controller receive transaction already has an owner")
                self._drain_stream_input()
                with self._condition:
                    self.raise_if_faulted()
                    self._stream = token
                    self._stream_last_sequence = 0
                    self._condition.notify_all()
        except Exception as exc:
            self._latch_failure(str(exc))
            raise

    @contextmanager
    def _stream_ingress(self, token: int) -> Iterator[None]:
        # The background receiver yields for the complete stream. Each API
        # call owns ingress without holding the service physical-write lock
        # across a blocking read. Failure callbacks remain outside this gate.
        try:
            with self._consumer_ingress():
                self.raise_if_faulted()
                with self._condition:
                    if type(token) is not int or self._stream != token:
                        raise MachineError("Controller receive stream ownership changed")
                yield
        except Exception as exc:
            self._latch_failure(str(exc))
            raise

    def _drain_stream_input(self) -> bool:
        """Classify queued bytes; report status traffic that resets quiet time."""
        status_received = False
        for _ in range(512):
            self.raise_if_faulted()
            response = self._read_transport(timeout=0.0)
            if not response:
                return status_received
            self._dispatch(response)
            status_received |= (
                self._dialect.classify_command_response(response)
                is CommandResponseKind.REALTIME_STATUS
            )
        raise MachineError("Controller stream input did not reach a bounded boundary")

    def poll_suspended(self, timeout: float = 0.02) -> None:
        """Dispatch input without consuming a transaction or streaming ACK."""
        with self._consumer_ingress():
            self.raise_if_faulted()
            response = self._read_transport(timeout=timeout)
            if response:
                self._dispatch(response)

    def stream_write(
        self, token: int, sequence: int, byte_count: int,
        write: Callable[[], None], *, capacity: int = 127,
    ) -> bool:
        """Admit one complete line only when its exact wire bytes fit.

        ``byte_count`` includes the line terminator. The caller binds it to the
        exact bytes written. A failed or partial write retires this ownership;
        no reservation can be reused or retried after that failure.
        """
        with self._stream_ingress(token):
            if type(capacity) is not int or not 1 <= capacity <= 127:
                raise MachineError("Controller stream capacity must be between 1 and 127 bytes")
            if type(byte_count) is not int or not 1 <= byte_count <= capacity:
                raise MachineError("Controller stream line exceeds its byte capacity")
            if type(sequence) is not int or sequence <= self._stream_last_sequence:
                raise MachineError("Controller stream sequence must increase")
            self._drain_stream_input()
            with self._condition:
                self.raise_if_faulted()
                if (self._stream_bytes + byte_count > capacity
                        or len(self._stream_pending) + len(self._stream_ready) >= 16):
                    return False
                self._stream_pending.append((sequence, byte_count))
                self._stream_bytes += byte_count
                self._stream_last_sequence = sequence
            write()
            self.raise_if_faulted()
            return True

    def stream_read_ack(self, token: int, timeout: float) -> tuple[int, str] | None:
        """Read and consume one FIFO ACK, releasing only its line's credit."""
        with self._stream_ingress(token):
            if not math.isfinite(timeout) or timeout < 0:
                raise MachineError("Controller stream read timeout must be finite and nonnegative")
            deadline = time.monotonic() + timeout
            while True:
                self._drain_stream_input()
                with self._condition:
                    self.raise_if_faulted()
                    if self._stream_ready:
                        sequence, byte_count, response = self._stream_ready.popleft()
                        self._stream_bytes -= byte_count
                        return sequence, response
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None
                response = self._read_transport(timeout=min(0.02, remaining))
                if response:
                    self._dispatch(response)

    def finish_stream(self, token: int, *, quiet_interval: float, timeout: float) -> None:
        """Fence an ACK-drained stream, then return RX to the idle worker.

        ACK drain is not physical motion completion. The caller must still
        execute its normal M5/motion barriers before any dependent operation.
        """
        with self._stream_ingress(token):
            if (not math.isfinite(quiet_interval) or quiet_interval < 0
                    or not math.isfinite(timeout) or timeout < quiet_interval):
                raise MachineError("Controller stream fence requires a bounded quiet interval")
            self._drain_stream_input()
            with self._condition:
                if self._stream_pending or self._stream_ready or self._stream_bytes:
                    raise MachineError("Controller stream fence requires all acknowledgements consumed")
            deadline = time.monotonic() + timeout
            quiet_started = time.monotonic()
            while True:
                self.raise_if_faulted()
                now = time.monotonic()
                quiet_remaining = quiet_interval - (now - quiet_started)
                if quiet_remaining <= 0:
                    # Check the actual queue once more while admission remains
                    # excluded; elapsed thread time alone proves no boundary.
                    if not self._drain_stream_input():
                        break
                    quiet_started = time.monotonic()
                    continue
                remaining = deadline - now
                if remaining <= 0:
                    raise MachineError("Controller stream ended without a quiet boundary")
                response = self._read_transport(timeout=min(0.02, quiet_remaining, remaining))
                if response:
                    self._dispatch(response)
                    if self._dialect.classify_command_response(response) is CommandResponseKind.REALTIME_STATUS:
                        quiet_started = time.monotonic()
            with self._condition:
                self.raise_if_faulted()
                self._stream = None
                self._condition.notify_all()

    def read_line(self, timeout: float) -> str | None:
        deadline = time.monotonic() + max(0.0, timeout)
        with self._condition:
            if self._stream is not None:
                raise MachineError("Controller receive stream has exclusive ownership")
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
        with self._consumer_ingress():
            self.raise_if_faulted()
            response = self._read_transport(timeout=0.0)
            if response:
                self._dispatch(response)
            with self._condition:
                self.raise_if_faulted()
                return self._pending.popleft() if self._pending else None

    @contextmanager
    def _consumer_ingress(self) -> Iterator[None]:
        # Python locks do not promise FIFO acquisition. A polling reader must
        # yield the next turn to queued admission/terminal-boundary work rather
        # than repeatedly reacquire ingress for another blocking read.
        with self._condition:
            self._ingress_waiters += 1
        try:
            with self._ingress:
                yield
        finally:
            with self._condition:
                self._ingress_waiters -= 1
                self._condition.notify_all()

    def _run(self) -> None:
        while not self._stop.is_set() and self._owner_alive():
            try:
                self._progress.mark("condition_wait")
                with self._condition:
                    while (self._ingress_waiters or self._stream is not None) and not self._stop.is_set() and self._fault is None:
                        self._progress.mark("consumer_priority_wait")
                        self._condition.wait()
                self._progress.mark("ingress_wait")
                with self._ingress:
                    if self._stop.is_set():
                        return
                    self.raise_if_faulted()
                    with self._condition:
                        if self._stream is not None:
                            continue
                    self._progress.mark("transport_read_wait")
                    response = self._read_transport(timeout=0.02)
                    self._progress.mark("transport_line" if response else "transport_empty")
                    if response:
                        self._progress.mark("dispatch")
                        self._dispatch(response)
            except Exception as exc:
                if self._stop.is_set():
                    return
                self._latch_failure(str(exc))
                # Revocation/abort may acquire service and transport locks. It
                # must occur outside ingress/condition ownership.
                self._on_failure(str(exc))
                return
            # Avoid a busy loop when a transport returns immediately. Pending
            # consumer priority is enforced explicitly above, not by sleep(0).
            time.sleep(0)
