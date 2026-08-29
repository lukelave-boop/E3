from __future__ import annotations

import math
import socket
import threading
from typing import Any
from urllib.parse import urlsplit

from ..config import ZAxisSettings
from ..errors import MachineError
from .model import ZReferenceMode, ZState
from .remote_protocol import (
    authenticate_z_client,
    receive_packet,
    send_packet,
    z_token_from_environment,
)

_SCHEME = "e3z"
_DEFAULT_PORT = 8767
_CONNECT_TIMEOUT_SECONDS = 5.0
_STATUS_INTERVAL_SECONDS = 1.0


def is_z_axis_uri(value: str) -> bool:
    return isinstance(value, str) and value.lower().startswith(f"{_SCHEME}://")


def parse_z_axis_uri(value: str) -> tuple[str, int]:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (AttributeError, ValueError) as exc:
        raise MachineError(f"Invalid S1 Pro Z endpoint: {exc}") from exc
    if parsed.scheme.lower() != _SCHEME:
        raise MachineError("Desktop S1 Pro Z endpoint must use e3z://")
    if parsed.username is not None or parsed.password is not None:
        raise MachineError("S1 Pro Z credentials must not be embedded in the endpoint")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise MachineError("S1 Pro Z endpoint may contain only a host and optional port")
    host = parsed.hostname
    if not host:
        raise MachineError("S1 Pro Z endpoint must include a host")
    if port is None:
        port = _DEFAULT_PORT
    if not 1 <= port <= 65535:
        raise MachineError("S1 Pro Z endpoint port must be between 1 and 65535")
    return host, port


class RemoteZAxisService:
    """Persistent desktop facade for the Pi-owned Z/CR Touch service."""

    def __init__(self, settings: ZAxisSettings) -> None:
        self.settings = settings
        self._host, self._port = (
            parse_z_axis_uri(settings.endpoint)
            if settings.enabled
            else ("", _DEFAULT_PORT)
        )
        self._socket: socket.socket | None = None
        self._request_lock = threading.RLock()
        self._status_lock = threading.RLock()
        self._status = self._offline_status()
        self._probe_stop = threading.Event()
        self._probe_thread: threading.Thread | None = None

    def _offline_status(self, error: str | None = None) -> dict[str, Any]:
        return {
            "enabled": bool(self.settings.enabled),
            "connected": False,
            "state": ZState.FAULT.value if error else ZState.UNKNOWN.value,
            "z_known": False,
            "current_z_mm": None,
            "effective_safe_max_mm": float(self.settings.safe_max_mm),
            "safe_max_mm": float(self.settings.safe_max_mm),
            "reference_mode": self.settings.reference_mode,
            "work_surface_height_mm": self.settings.work_surface_height_mm,
            "serial_device": None,
            "operation": "fault" if error else "idle",
            "last_error": error,
            "focus_calibrated": (
                self.settings.laser_focus_offset_from_probe_mm is not None
            ),
        }

    def _cache_status(self, value: object) -> None:
        if not isinstance(value, dict):
            raise MachineError("Remote S1 Pro Z service returned invalid status")
        required = {"connected", "state", "z_known", "effective_safe_max_mm"}
        if not required.issubset(value):
            raise MachineError("Remote S1 Pro Z service returned incomplete status")
        if value["state"] not in {state.value for state in ZState}:
            raise MachineError("Remote S1 Pro Z service returned an invalid state")
        maximum = value["effective_safe_max_mm"]
        if type(maximum) not in {int, float} or not math.isfinite(float(maximum)):
            raise MachineError("Remote S1 Pro Z service returned an invalid safety ceiling")
        with self._status_lock:
            self._status = dict(value)

    def status(self) -> dict[str, Any]:
        with self._status_lock:
            return dict(self._status)

    def connect(self) -> dict[str, Any]:
        if not self.settings.enabled:
            raise MachineError("S1 Pro Z / CR Touch support is disabled for this machine")
        with self._request_lock:
            if self._socket is not None:
                return self.status()
            sock = socket.create_connection(
                (self._host, self._port),
                timeout=_CONNECT_TIMEOUT_SECONDS,
            )
            try:
                sock.settimeout(_CONNECT_TIMEOUT_SECONDS)
                authenticate_z_client(sock, z_token_from_environment())
                sock.settimeout(max(5.0, float(self.settings.read_timeout)))
                self._socket = sock
                response = self._request_locked("connect")
            except Exception:
                self._socket = None
                sock.close()
                raise
        self._cache_status(response.get("status"))
        self._ensure_probe_thread()
        return self.status()

    def ensure_connected(self) -> None:
        if self._socket is None:
            self.connect()
            return
        if self.status().get("connected") is True:
            return
        with self._request_lock:
            response = self._request_locked("connect")
            self._cache_status(response.get("status"))

    def close(self) -> None:
        self._probe_stop.set()
        thread = self._probe_thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=1.0)
        with self._request_lock:
            sock = self._socket
            self._socket = None
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                sock.close()
        with self._status_lock:
            self._status = self._offline_status()

    def request_stop(self) -> None:
        """Closing the owned RPC session wakes the Pi disconnect watchdog."""

        if not self.settings.enabled:
            return
        # Deliberately bypass the ordinary request lock: a homing request may
        # be blocked waiting for G28. Closing its socket from the STOP caller
        # wakes both that request and the Pi-side disconnect watchdog.
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        with self._status_lock:
            self._status = self._offline_status("S1 Pro Z operation interrupted by STOP")

    def _request_locked(self, action: str, **payload: Any) -> dict[str, Any]:
        sock = self._socket
        if sock is None:
            raise MachineError("Remote S1 Pro Z service is disconnected")
        send_packet(sock, {"action": action, **payload})
        response = receive_packet(sock)
        if response.get("ok") is not True:
            status = response.get("status")
            if isinstance(status, dict):
                self._cache_status(status)
            raise MachineError(str(response.get("error") or "S1 Pro Z request failed"))
        return response

    def _request(self, action: str, *, timeout: float | None = None, **payload: Any) -> dict[str, Any]:
        self.ensure_connected()
        with self._request_lock:
            sock = self._socket
            if sock is None:
                raise MachineError("Remote S1 Pro Z service is disconnected")
            previous = sock.gettimeout()
            try:
                if timeout is not None:
                    sock.settimeout(float(timeout))
                response = self._request_locked(action, **payload)
                if "status" in response:
                    self._cache_status(response["status"])
                return response
            except Exception as exc:
                try:
                    sock.close()
                except OSError:
                    pass
                self._socket = None
                with self._status_lock:
                    self._status = self._offline_status(str(exc))
                raise
            finally:
                if self._socket is sock:
                    sock.settimeout(previous)

    def _result(self, response: dict[str, Any]) -> dict[str, Any]:
        result = response.get("result")
        if not isinstance(result, dict):
            raise MachineError("Remote S1 Pro Z service returned an invalid result")
        return result

    def test_probe(self) -> dict[str, Any]:
        return self._result(self._request("test_probe"))

    def prepare_home(self, *, confirmed_unknown: bool, effective_max_mm: float) -> dict[str, Any]:
        return self._result(
            self._request(
                "prepare_home",
                timeout=max(45.0, float(self.settings.read_timeout)),
                confirmed_unknown=confirmed_unknown,
                effective_max_mm=effective_max_mm,
            )
        )

    def complete_home(
        self,
        token: str,
        *,
        reference_mode: ZReferenceMode | str,
        surface_height_mm: float | None,
        effective_max_mm: float,
    ) -> dict[str, Any]:
        return self._result(
            self._request(
                "complete_home",
                timeout=float(self.settings.homing_timeout) + 15.0,
                token=token,
                reference_mode=ZReferenceMode(reference_mode).value,
                surface_height_mm=surface_height_mm,
                effective_max_mm=effective_max_mm,
            )
        )

    def abort_home(self, token: str, reason: str) -> None:
        try:
            self._request("abort_home", token=token, reason=reason)
        except Exception:
            self.request_stop()

    def move_absolute(self, target_z_mm: float, *, effective_max_mm: float) -> dict[str, Any]:
        return self._result(
            self._request(
                "move_absolute",
                timeout=45.0,
                target_z_mm=target_z_mm,
                effective_max_mm=effective_max_mm,
            )
        )

    def _ensure_probe_thread(self) -> None:
        thread = self._probe_thread
        if thread is not None and thread.is_alive():
            return
        self._probe_stop.clear()
        thread = threading.Thread(
            target=self._probe_loop,
            name="remote-s1pro-z-status",
            daemon=True,
        )
        self._probe_thread = thread
        thread.start()

    def _probe_loop(self) -> None:
        while not self._probe_stop.wait(_STATUS_INTERVAL_SECONDS):
            if not self._request_lock.acquire(blocking=False):
                continue
            try:
                if self._socket is None:
                    return
                response = self._request_locked("status")
                self._cache_status(response.get("status"))
            except Exception as exc:
                sock = self._socket
                self._socket = None
                if sock is not None:
                    try:
                        sock.close()
                    except OSError:
                        pass
                with self._status_lock:
                    self._status = self._offline_status(str(exc))
                return
            finally:
                self._request_lock.release()
