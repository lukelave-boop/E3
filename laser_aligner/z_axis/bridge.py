from __future__ import annotations

import logging
import select
import socket
import threading
from typing import Any

from ..errors import MachineError
from .remote_protocol import authenticate_z_server, receive_packet, send_packet
from .service import ZAxisHardwareService

LOGGER = logging.getLogger(__name__)
_DEFAULT_PORT = 8767
_HANDSHAKE_TIMEOUT_SECONDS = 5.0


class ZAxisBridgeServer:
    """Authenticated high-level RPC boundary around the sole Pi Z service."""

    def __init__(
        self,
        service: ZAxisHardwareService,
        *,
        host: str,
        port: int = _DEFAULT_PORT,
        token: str,
    ) -> None:
        self.service = service
        self.host = host
        self.port = port
        self.token = token
        self._client_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._active_client: socket.socket | None = None
        self._stop = threading.Event()
        self._listener: socket.socket | None = None

    def stop(self) -> None:
        self._stop.set()
        listener = self._listener
        if listener is not None:
            try:
                listener.close()
            except OSError:
                pass
        with self._state_lock:
            client = self._active_client
        if client is not None:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.service.request_stop()

    def _dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        action = request.get("action")
        if not isinstance(action, str):
            raise MachineError("S1 Pro Z request is missing an action")
        if action == "connect":
            self.service.connect()
            return {"ok": True, "status": self.service.status()}
        if action == "status":
            return {"ok": True, "status": self.service.status()}
        if action == "test_probe":
            result = self.service.test_probe()
        elif action == "prepare_home":
            result = self.service.prepare_home(
                confirmed_unknown=request.get("confirmed_unknown"),
                effective_max_mm=request.get("effective_max_mm"),
            )
        elif action == "complete_home":
            result = self.service.complete_home(
                request.get("token"),
                reference_mode=request.get("reference_mode"),
                surface_height_mm=request.get("surface_height_mm"),
                effective_max_mm=request.get("effective_max_mm"),
            )
        elif action == "abort_home":
            reason = request.get("reason")
            if not isinstance(reason, str) or not reason:
                raise MachineError("Z homing abort requires a reason")
            self.service.abort_home(request.get("token"), reason)
            result = {"aborted": True}
        elif action == "move_absolute":
            result = self.service.move_absolute(
                request.get("target_z_mm"),
                effective_max_mm=request.get("effective_max_mm"),
            )
        else:
            raise MachineError("Unsupported S1 Pro Z request action")
        return {"ok": True, "result": result, "status": self.service.status()}

    def _watch_disconnect(self, conn: socket.socket, stopped: threading.Event) -> None:
        def stop_active_operation() -> None:
            if self.service.status().get("operation", "idle") != "idle":
                self.service.request_stop()

        while not stopped.wait(0.1):
            try:
                readable, _, _ = select.select([conn], [], [], 0.0)
                if not readable:
                    continue
                if conn.recv(1, socket.MSG_PEEK) == b"":
                    stop_active_operation()
                    return
            except (OSError, ValueError):
                stop_active_operation()
                return

    def _handle_client(self, conn: socket.socket, address: tuple[object, ...]) -> None:
        acquired = False
        watchdog_stop = threading.Event()
        watchdog: threading.Thread | None = None
        with conn:
            try:
                conn.settimeout(_HANDSHAKE_TIMEOUT_SECONDS)
                if not authenticate_z_server(conn, self.token):
                    return
                acquired = self._client_lock.acquire(blocking=False)
                if not acquired:
                    # Consume the client's first bounded request before closing
                    # so TCP does not reset the explicit BUSY response because
                    # unread request bytes remain in the receive buffer.
                    receive_packet(conn)
                    send_packet(conn, {"ok": False, "error": "S1 Pro Z service is busy"})
                    return
                with self._state_lock:
                    self._active_client = conn
                self.service.invalidate_position("New E3 Z session requires homing")
                conn.settimeout(None)
                watchdog = threading.Thread(
                    target=self._watch_disconnect,
                    args=(conn, watchdog_stop),
                    name="e3-z-client-watchdog",
                    daemon=True,
                )
                watchdog.start()
                while not self._stop.is_set():
                    request = receive_packet(conn)
                    try:
                        response = self._dispatch(request)
                    except Exception as exc:
                        response = {
                            "ok": False,
                            "error": str(exc) or "S1 Pro Z request failed",
                            "status": self.service.status(),
                        }
                    send_packet(conn, response)
            except Exception as exc:
                LOGGER.info("S1 Pro Z client %s disconnected: %s", address[0], exc)
            finally:
                watchdog_stop.set()
                if watchdog is not None and watchdog.is_alive():
                    watchdog.join(timeout=0.5)
                if acquired:
                    self.service.client_disconnected()
                    with self._state_lock:
                        if self._active_client is conn:
                            self._active_client = None
                    self._client_lock.release()

    def serve_forever(self) -> None:
        if not 1 <= self.port <= 65535:
            raise MachineError("S1 Pro Z bridge port must be between 1 and 65535")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(4)
        listener.settimeout(0.5)
        self._listener = listener
        LOGGER.info("S1 Pro Z bridge listening on %s:%d", self.host, self.port)
        try:
            while not self._stop.is_set():
                try:
                    conn, address = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stop.is_set():
                        break
                    raise
                threading.Thread(
                    target=self._handle_client,
                    args=(conn, address),
                    name="e3-z-client",
                    daemon=True,
                ).start()
        finally:
            self._listener = None
            try:
                listener.close()
            except OSError:
                pass
            self.service.close()
