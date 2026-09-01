from __future__ import annotations

import errno
import math
import select
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import asdict, replace
from typing import Any
from urllib.parse import urlsplit

import cv2
import numpy as np

from ..config import CameraSettings, PrecisionCaptureSettings
from ..errors import CameraError
from ..imaging import probe_encoded_image_dimensions
from .controls import ControlResult, validate_control_request
from .remote_protocol import (
    authenticate_camera_client,
    camera_token_from_environment,
    receive_packet,
    send_packet,
)
from .service import CameraService, CameraStatus, FrameBurst

_REMOTE_CAMERA_SCHEME = "e3camera"
_DEFAULT_CAMERA_PORT = 8766
_CONNECT_TIMEOUT_SECONDS = 5.0
_STATUS_PROBE_CONNECT_TIMEOUT_SECONDS = 0.75
_STATUS_PROBE_INITIAL_DELAY_SECONDS = 2.0
_STATUS_PROBE_HEALTHY_DELAY_SECONDS = 2.0
_STATUS_PROBE_REACHABLE_OFFLINE_DELAY_SECONDS = 5.0
_STATUS_PROBE_MAX_DELAY_SECONDS = 30.0
_TRANSFER_MARGIN_SECONDS = 30.0
_STILL_TRANSFER_QUALITY = 95
_MONITOR_FPS = frozenset({5, 10, 15})
_MONITOR_SIZES = frozenset({(1280, 720), (1920, 1080)})
_MAX_MONITOR_JPEG_BYTES = 4 * 1024 * 1024
_CONNECT_CANCEL_POLL_SECONDS = 0.05
_NETWORK_CANCELLED_MESSAGE = "Remote camera request was cancelled during shutdown"
_CONNECT_IN_PROGRESS_ERRORS = frozenset(
    {
        errno.EINPROGRESS,
        errno.EWOULDBLOCK,
        errno.EALREADY,
        errno.EINTR,
        getattr(errno, "WSAEINPROGRESS", 10036),
        getattr(errno, "WSAEWOULDBLOCK", 10035),
        getattr(errno, "WSAEALREADY", 10037),
    }
)


def _status_probe_delay(failure_count: int) -> float:
    count = max(1, int(failure_count))
    delay = _STATUS_PROBE_INITIAL_DELAY_SECONDS * (2 ** (count - 1))
    return min(_STATUS_PROBE_MAX_DELAY_SECONDS, delay)


def is_remote_camera_uri(value: str) -> bool:
    return isinstance(value, str) and value.lower().startswith(
        f"{_REMOTE_CAMERA_SCHEME}://"
    )


def _parse_remote_camera_uri(value: str) -> tuple[str, int]:
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except (AttributeError, ValueError) as exc:
        raise CameraError(f"Invalid remote camera address: {exc}") from exc
    if parsed.scheme.lower() != _REMOTE_CAMERA_SCHEME:
        raise CameraError("Remote camera address must use e3camera://")
    if parsed.username is not None or parsed.password is not None:
        raise CameraError("Remote camera credentials must not be embedded in the address")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise CameraError("Remote camera address may contain only a host and optional port")
    host = parsed.hostname
    if not host:
        raise CameraError("Remote camera address must include a host")
    if port is None:
        port = _DEFAULT_CAMERA_PORT
    if not 1 <= port <= 65535:
        raise CameraError("Remote camera port must be between 1 and 65535")
    return host, port


def _control_from_dict(raw: object) -> ControlResult:
    if not isinstance(raw, dict):
        raise CameraError("Remote camera returned invalid control diagnostics")
    fields = {
        "requested": raw.get("requested", {}),
        "applied": raw.get("applied", {}),
        "skipped": raw.get("skipped", {}),
        "verified": raw.get("verified", {}),
        "satisfied": raw.get("satisfied", {}),
        "critical_unverified": raw.get("critical_unverified", {}),
    }
    if any(not isinstance(value, dict) for value in fields.values()):
        raise CameraError("Remote camera returned invalid control diagnostics")
    return ControlResult(**fields)


def _decode_frame(blob: bytes) -> np.ndarray:
    if not blob:
        raise CameraError("Remote camera returned an empty frame")
    encoded = np.frombuffer(blob, dtype=np.uint8)
    frame = cv2.imdecode(encoded, cv2.IMREAD_COLOR)
    if frame is None or frame.size == 0 or frame.dtype != np.uint8:
        raise CameraError("Remote camera returned an invalid encoded frame")
    return frame


def _validated_monitor_payload(
    header: dict[str, Any],
    blobs: list[bytes],
    *,
    requested_width: int,
    requested_height: int,
    requested_fps: int,
    received_monotonic: float,
) -> dict[str, Any]:
    if len(blobs) != 1 or len(blobs[0]) > _MAX_MONITOR_JPEG_BYTES:
        raise CameraError("Remote monitor returned an invalid bounded frame")
    jpeg = blobs[0]
    raw_width = header.get("width", requested_width)
    raw_height = header.get("height", requested_height)
    if (
        type(raw_width) is not int
        or type(raw_height) is not int
        or (raw_width, raw_height) not in _MONITOR_SIZES
    ):
        raise CameraError("Remote monitor returned an invalid frame resolution")
    source_mode = header.get("source_mode", "transcoded")
    actual_size = (raw_width, raw_height)
    allowed_fallback = (
        (requested_width, requested_height) == (1920, 1080)
        and actual_size == (1280, 720)
        and source_mode == "transcoded"
    )
    if actual_size != (requested_width, requested_height) and not allowed_fallback:
        raise CameraError("Remote monitor frame resolution did not match its profile")
    try:
        jpeg_size = probe_encoded_image_dimensions(
            jpeg,
            source="remote monitor frame",
        )
    except ValueError as exc:
        raise CameraError(str(exc)) from exc
    if jpeg_size != actual_size:
        raise CameraError("Remote monitor JPEG dimensions did not match its metadata")
    return {
        "jpeg": jpeg,
        "sequence": int(header.get("sequence", 0)),
        "width": raw_width,
        "height": raw_height,
        "jpeg_bytes": len(jpeg),
        "source_mode": source_mode,
        "source_width": int(header.get("source_width", requested_width)),
        "source_height": int(header.get("source_height", requested_height)),
        "monitor_fps": int(header.get("monitor_fps", requested_fps)),
        "frame_age_seconds": header.get("frame_age_seconds"),
        "capture_fps": header.get("capture_fps"),
        "negotiated_fps": header.get("negotiated_fps"),
        "received_monotonic": received_monotonic,
    }


class RemoteCameraService(CameraService):
    """Portable CameraService facade backed by the Pi-side E3 camera bridge."""

    def __init__(self, settings: CameraSettings):
        # Retain CameraService type compatibility for AppContext helpers such as
        # sharpness scoring, while overriding every operation that would touch a
        # local VideoCapture.
        super().__init__(settings)
        self.settings = settings
        self._host, self._port = _parse_remote_camera_uri(settings.device)
        self._mjpeg_generation = 0
        self._status_lock = threading.RLock()
        self._status_cache = self._offline_status()
        self._status_probe_stop = threading.Event()
        self._status_probe_thread: threading.Thread | None = None
        self._network_lock = threading.RLock()
        self._network_generation = 0
        self._shutdown_event = threading.Event()
        self._terminal_shutdown = False
        self._active_sockets: dict[int, socket.socket] = {}

    def _offline_status(self, error: str | None = None) -> CameraStatus:
        return CameraStatus(
            connected=False,
            device=self.settings.device,
            width=0,
            height=0,
            fps=0.0,
            frames_read=0,
            last_error=error,
        )

    def _set_cached_status(self, status: CameraStatus) -> None:
        with self._status_lock:
            self._status_cache = replace(status)

    def _fetch_status(
        self,
        *,
        connect_timeout: float = _STATUS_PROBE_CONNECT_TIMEOUT_SECONDS,
    ) -> CameraStatus:
        header, blobs = self._request(
            "status",
            timeout=1.0,
            connect_timeout=connect_timeout,
        )
        if blobs:
            raise CameraError("Remote camera status contained unexpected frame data")
        raw = header.get("status")
        if not isinstance(raw, dict):
            raise CameraError("Remote camera returned invalid status data")
        status_fields = dict(raw)
        if "synthetic" in status_fields:
            # Legacy physical Pi nodes reported this retired field as an exact
            # false boolean. Accept only that historical wire shape; every
            # other value remains invalid and cannot restore simulation.
            if status_fields["synthetic"] is not False:
                raise CameraError(
                    "Remote camera returned invalid legacy synthetic status data"
                )
            status_fields.pop("synthetic")
        status = CameraStatus(**status_fields)
        self._set_cached_status(status)
        return status

    def _ensure_status_probe(self) -> None:
        with self._network_lock:
            if self._shutdown_event.is_set():
                return
            with self._status_lock:
                thread = self._status_probe_thread
                if thread is not None and thread.is_alive():
                    return
                self._status_probe_stop.clear()
                thread = threading.Thread(
                    target=self._status_probe_loop,
                    name="remote-camera-status",
                    daemon=True,
                )
                self._status_probe_thread = thread
        thread.start()

    def _stop_status_probe(self, *, deadline: float | None = None) -> None:
        self._status_probe_stop.set()
        with self._status_lock:
            thread = self._status_probe_thread
        join_seconds = 1.0
        if deadline is not None:
            join_seconds = min(join_seconds, max(0.0, deadline - time.monotonic()))
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
            and join_seconds > 0
        ):
            thread.join(timeout=join_seconds)
        with self._status_lock:
            if self._status_probe_thread is thread and (
                thread is None or not thread.is_alive()
            ):
                self._status_probe_thread = None

    def _status_probe_loop(self) -> None:
        failures = 0
        delay = _STATUS_PROBE_INITIAL_DELAY_SECONDS
        while not self._status_probe_stop.wait(delay):
            try:
                status = self._fetch_status()
            except CameraError as exc:
                failures += 1
                self._set_cached_status(self._offline_status(str(exc)))
                delay = _status_probe_delay(failures)
                continue

            failures = 0
            delay = (
                _STATUS_PROBE_HEALTHY_DELAY_SECONDS
                if status.connected
                else _STATUS_PROBE_REACHABLE_OFFLINE_DELAY_SECONDS
            )

    def _rearm_network_requests(self) -> None:
        """Begin a new local client generation after an explicit start/restart."""

        with self._network_lock:
            if self._terminal_shutdown:
                raise CameraError(_NETWORK_CANCELLED_MESSAGE)
            if self._shutdown_event.is_set():
                self._network_generation += 1
                self._shutdown_event.clear()

    def _begin_network_request(self) -> int:
        with self._network_lock:
            if self._shutdown_event.is_set():
                raise CameraError(_NETWORK_CANCELLED_MESSAGE)
            return self._network_generation

    def _request_was_cancelled(self, generation: int) -> bool:
        with self._network_lock:
            return (
                self._shutdown_event.is_set()
                or generation != self._network_generation
            )

    def _raise_if_request_cancelled(self, generation: int) -> None:
        if self._request_was_cancelled(generation):
            raise CameraError(_NETWORK_CANCELLED_MESSAGE)

    def _register_socket(self, sock: socket.socket, generation: int) -> None:
        with self._network_lock:
            if (
                self._shutdown_event.is_set()
                or generation != self._network_generation
            ):
                self._close_socket(sock, interrupt=True)
                raise CameraError(_NETWORK_CANCELLED_MESSAGE)
            self._active_sockets[id(sock)] = sock

    @staticmethod
    def _close_socket(sock: socket.socket, *, interrupt: bool) -> None:
        if interrupt:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except (OSError, ValueError):
                pass
        try:
            sock.close()
        except (OSError, ValueError):
            pass

    def _release_socket(self, sock: socket.socket) -> None:
        with self._network_lock:
            if self._active_sockets.get(id(sock)) is sock:
                self._active_sockets.pop(id(sock), None)
        self._close_socket(sock, interrupt=False)

    def cancel_pending_requests(self, *, terminal: bool = False) -> None:
        """Revoke this client generation and interrupt all active network I/O."""

        self._status_probe_stop.set()
        with self._network_lock:
            if terminal:
                self._terminal_shutdown = True
            first_cancel = not self._shutdown_event.is_set()
            self._shutdown_event.set()
            if first_cancel:
                self._network_generation += 1
                self._mjpeg_generation += 1
            sockets = tuple(self._active_sockets.values())
            self._active_sockets.clear()
        for sock in sockets:
            self._close_socket(sock, interrupt=True)

    def _resolve_addresses(
        self,
        *,
        generation: int,
        timeout_seconds: float,
    ) -> tuple[tuple[Any, ...], ...]:
        """Resolve on a daemon so shutdown can release the requesting worker."""

        resolved: list[tuple[Any, ...]] = []
        failures: list[BaseException] = []
        finished = threading.Event()

        def resolve() -> None:
            try:
                resolved.extend(
                    socket.getaddrinfo(
                        self._host,
                        self._port,
                        type=socket.SOCK_STREAM,
                    )
                )
            except BaseException as exc:  # pragma: no cover - platform resolver
                failures.append(exc)
            finally:
                finished.set()

        threading.Thread(
            target=resolve,
            name="remote-camera-resolver",
            daemon=True,
        ).start()
        deadline = time.monotonic() + timeout_seconds
        while not finished.is_set():
            self._raise_if_request_cancelled(generation)
            remaining = deadline - time.monotonic()
            if remaining <= 0.0:
                raise TimeoutError("remote camera address resolution timed out")
            finished.wait(min(_CONNECT_CANCEL_POLL_SECONDS, remaining))
        self._raise_if_request_cancelled(generation)
        if failures:
            failure = failures[0]
            if isinstance(failure, OSError):
                raise failure
            raise CameraError(
                f"Could not resolve remote camera at {self._host}:{self._port}: "
                f"{failure}"
            ) from failure
        if not resolved:
            raise OSError("Remote camera address did not resolve")
        return tuple(resolved)

    def _connect_socket(
        self,
        *,
        generation: int,
        timeout_seconds: float,
    ) -> socket.socket:
        self._raise_if_request_cancelled(generation)
        addresses = self._resolve_addresses(
            generation=generation,
            timeout_seconds=timeout_seconds,
        )

        last_error: OSError | None = None
        for family, sock_type, protocol, _canonical_name, address in addresses:
            self._raise_if_request_cancelled(generation)
            sock = socket.socket(family, sock_type, protocol)
            try:
                # Registration happens before connect so shutdown can close a
                # socket in every connect/send/receive race window.
                self._register_socket(sock, generation)
                sock.setblocking(False)
                result = sock.connect_ex(address)
                if result not in {0, errno.EISCONN}:
                    if result not in _CONNECT_IN_PROGRESS_ERRORS:
                        raise OSError(result, f"socket connect failed ({result})")
                    deadline = time.monotonic() + timeout_seconds
                    while True:
                        self._raise_if_request_cancelled(generation)
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise TimeoutError("remote camera connect timed out")
                        _readable, writable, exceptional = select.select(
                            [],
                            [sock],
                            [sock],
                            min(_CONNECT_CANCEL_POLL_SECONDS, remaining),
                        )
                        if not writable and not exceptional:
                            continue
                        error_code = sock.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                        if error_code:
                            raise OSError(
                                error_code,
                                f"socket connect failed ({error_code})",
                            )
                        break
                self._raise_if_request_cancelled(generation)
                return sock
            except CameraError:
                self._release_socket(sock)
                raise
            except (OSError, ValueError) as exc:
                self._release_socket(sock)
                if self._request_was_cancelled(generation):
                    raise CameraError(_NETWORK_CANCELLED_MESSAGE) from exc
                last_error = exc if isinstance(exc, OSError) else OSError(str(exc))

        if last_error is not None:
            raise last_error
        raise OSError("Could not create a remote camera socket")

    def _request(
        self,
        action: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float = _CONNECT_TIMEOUT_SECONDS,
        connect_timeout: float | None = None,
    ) -> tuple[dict[str, Any], tuple[bytes, ...]]:
        generation = self._begin_network_request()
        connect_timeout_seconds = (
            _CONNECT_TIMEOUT_SECONDS
            if connect_timeout is None
            else max(0.05, float(connect_timeout))
        )
        token = camera_token_from_environment()
        request = {"action": action}
        if payload:
            request.update(payload)
        sock: socket.socket | None = None
        try:
            sock = self._connect_socket(
                generation=generation,
                timeout_seconds=connect_timeout_seconds,
            )
            sock.settimeout(max(connect_timeout_seconds, float(timeout)))
            authenticate_camera_client(sock, token)
            self._raise_if_request_cancelled(generation)
            send_packet(sock, request)
            self._raise_if_request_cancelled(generation)
            header, blobs = receive_packet(sock)
            self._raise_if_request_cancelled(generation)
        except CameraError as exc:
            if self._request_was_cancelled(generation):
                cancelled = CameraError(_NETWORK_CANCELLED_MESSAGE)
                self._set_cached_status(self._offline_status(str(cancelled)))
                raise cancelled from exc
            self._set_cached_status(self._offline_status(str(exc)))
            raise
        except (OSError, ValueError) as exc:
            if self._request_was_cancelled(generation):
                cancelled = CameraError(_NETWORK_CANCELLED_MESSAGE)
                self._set_cached_status(self._offline_status(str(cancelled)))
                raise cancelled from exc
            message = (
                f"Could not communicate with remote camera at "
                f"{self._host}:{self._port}: {exc}"
            )
            self._set_cached_status(self._offline_status(message))
            raise CameraError(message) from exc
        finally:
            if sock is not None:
                self._release_socket(sock)
        if header.get("ok") is not True:
            error = header.get("error")
            detail = error if isinstance(error, str) and error else "remote camera request failed"
            raise CameraError(detail)
        return header, blobs

    def _verify_remote_profile(self) -> None:
        header, blobs = self._request("profile")
        if blobs:
            raise CameraError("Remote camera profile contained unexpected frame data")
        profile = header.get("profile")
        if not isinstance(profile, dict):
            raise CameraError("Remote camera returned invalid profile data")
        expected = {
            "width": int(self.settings.width),
            "height": int(self.settings.height),
            "fps": int(self.settings.fps),
            "fourcc": str(self.settings.fourcc),
            "warmup_frames": int(self.settings.warmup_frames),
            "controls": dict(self.settings.controls),
        }
        changed = [key for key, value in expected.items() if profile.get(key) != value]
        if changed:
            raise CameraError(
                "Remote Pi camera profile does not match the desktop profile: "
                + ", ".join(changed)
            )

    def start(self) -> None:
        self._rearm_network_requests()
        try:
            self._verify_remote_profile()
            self._request("start")
            self._fetch_status(connect_timeout=1.0)
            self._mjpeg_generation += 1
        finally:
            # Status monitoring is independent of the GUI. If the Pi is away,
            # failed probes back off rather than blocking Qt polling.
            self._ensure_status_probe()

    def stop(self, *, deadline: float | None = None) -> None:
        """Release this desktop client's camera state without stopping the Pi camera."""
        self.cancel_pending_requests()
        self._stop_status_probe(deadline=deadline)
        self._set_cached_status(self._offline_status())

    def restart(self) -> None:
        self._rearm_network_requests()
        try:
            self._request("restart")
            self._fetch_status(connect_timeout=1.0)
            self._mjpeg_generation += 1
        finally:
            self._ensure_status_probe()

    def snapshot(self) -> np.ndarray:
        _, blobs = self._request(
            "snapshot",
            {"quality": _STILL_TRANSFER_QUALITY},
            timeout=10.0,
        )
        if len(blobs) != 1:
            raise CameraError("Remote camera snapshot did not contain exactly one frame")
        return _decode_frame(blobs[0])

    def frame_sequence(self) -> int:
        header, blobs = self._request("frame_sequence")
        if blobs:
            raise CameraError("Remote camera frame-sequence response contained unexpected data")
        sequence = header.get("sequence")
        if type(sequence) is not int or sequence < 0:
            raise CameraError("Remote camera returned an invalid frame sequence")
        return sequence

    def ensure_burst_current(self, burst: FrameBurst) -> None:
        generation = burst.camera_generation
        if generation is None:
            return
        header, _ = self._request("generation")
        current = header.get("generation")
        if type(current) is not int or current != generation:
            raise CameraError("Camera stopped or restarted after this frame burst")

    def snapshot_after(self, sequence: int, timeout: float = 6.0) -> np.ndarray:
        if type(sequence) is not int or sequence < 0:
            raise CameraError("Fresh-frame sequence must be a non-negative integer")
        if type(timeout) is bool:
            raise CameraError("Fresh-frame timeout must be a positive finite number")
        try:
            timeout_seconds = float(timeout)
        except (TypeError, ValueError) as exc:
            raise CameraError("Fresh-frame timeout must be a positive finite number") from exc
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise CameraError("Fresh-frame timeout must be a positive finite number")
        _, blobs = self._request(
            "snapshot_after",
            {
                "sequence": sequence,
                "timeout": timeout_seconds,
                "quality": _STILL_TRANSFER_QUALITY,
            },
            timeout=timeout_seconds + _TRANSFER_MARGIN_SECONDS,
        )
        if len(blobs) != 1:
            raise CameraError("Remote fresh-frame response did not contain exactly one frame")
        return _decode_frame(blobs[0])

    def capture_burst(
        self,
        settings: PrecisionCaptureSettings | None = None,
        *,
        reapply_controls: bool = True,
        score_frames: bool = True,
    ) -> FrameBurst:
        profile = settings or self.settings.precision_capture
        CameraService._validate_capture_profile(profile)
        header, blobs = self._request(
            "capture_burst",
            {
                "profile": asdict(profile),
                "reapply_controls": bool(reapply_controls),
                "quality": _STILL_TRANSFER_QUALITY,
            },
            timeout=float(profile.timeout_seconds) + _TRANSFER_MARGIN_SECONDS,
        )
        metadata = header.get("burst")
        if not isinstance(metadata, dict):
            raise CameraError("Remote camera returned invalid burst metadata")
        if len(blobs) != int(profile.sample_frames):
            raise CameraError(
                "Remote camera burst frame count did not match the requested sample count"
            )
        frames = tuple(_decode_frame(blob) for blob in blobs)
        sharpness = (
            tuple(CameraService._sharpness_score(frame) for frame in frames)
            if score_frames
            else ()
        )
        sequences_raw = metadata.get("sequence_numbers")
        if not isinstance(sequences_raw, list) or any(
            type(value) is not int for value in sequences_raw
        ):
            raise CameraError("Remote camera returned invalid burst sequence numbers")
        controls = _control_from_dict(metadata.get("controls"))
        return FrameBurst(
            frames=frames,
            sequence_numbers=tuple(sequences_raw),
            discarded_frames=int(metadata.get("discarded_frames", 0)),
            settle_seconds=float(metadata.get("settle_seconds", 0.0)),
            elapsed_seconds=float(metadata.get("elapsed_seconds", 0.0)),
            sharpness_scores=sharpness,
            controls=controls,
            timeout_seconds=(
                None
                if metadata.get("timeout_seconds") is None
                else float(metadata["timeout_seconds"])
            ),
            observed_fps=(
                None
                if metadata.get("observed_fps") is None
                else float(metadata["observed_fps"])
            ),
            negotiated_fps=(
                None
                if metadata.get("negotiated_fps") is None
                else float(metadata["negotiated_fps"])
            ),
            sequence_gaps=int(metadata.get("sequence_gaps", 0)),
            camera_generation=(
                None
                if metadata.get("camera_generation") is None
                else int(metadata["camera_generation"])
            ),
        )

    def jpeg(self, quality: int | None = None) -> bytes:
        raw_quality = self.settings.jpeg_quality if quality is None else quality
        if type(raw_quality) is not int:
            raise CameraError("JPEG quality must be an integer")
        encode_quality = max(1, min(100, raw_quality))
        _, blobs = self._request(
            "jpeg",
            {"quality": encode_quality},
            timeout=10.0,
        )
        if len(blobs) != 1:
            raise CameraError("Remote JPEG response did not contain exactly one frame")
        return blobs[0]

    def mjpeg(self, target_fps: float = 10.0) -> Iterator[bytes]:
        try:
            fps = float(target_fps)
        except (TypeError, ValueError) as exc:
            raise CameraError("MJPEG target FPS must be a positive finite number") from exc
        if type(target_fps) is bool or not math.isfinite(fps) or fps <= 0:
            raise CameraError("MJPEG target FPS must be a positive finite number")
        generation = self._mjpeg_generation
        delay = 1.0 / max(1.0, fps)
        while (
            generation == self._mjpeg_generation
            and not self._shutdown_event.is_set()
        ):
            try:
                jpeg = self.jpeg(quality=min(self.settings.jpeg_quality, 85))
            except CameraError:
                if self._shutdown_event.wait(0.2):
                    return
                continue
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(jpeg)).encode()
                + b"\r\n\r\n"
                + jpeg
                + b"\r\n"
            )
            if self._shutdown_event.wait(delay):
                return

    def monitor_jpeg_frames(
        self,
        *,
        fps: int = 10,
        width: int = 1920,
        height: int = 1080,
        quality: int = 78,
        stop_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield bounded monitor JPEGs without eagerly decoding their pixels."""

        if type(fps) is not int or fps not in _MONITOR_FPS:
            raise CameraError("Monitor FPS must be 5, 10, or 15")
        if type(width) is not int or type(height) is not int or (width, height) not in _MONITOR_SIZES:
            raise CameraError("Monitor resolution must be 1280x720 or 1920x1080")
        if type(quality) is not int or not 70 <= quality <= 85:
            raise CameraError("Monitor JPEG quality must be between 70 and 85")
        stopping = stop_event or threading.Event()
        token = camera_token_from_environment()
        established = False
        generation = self._begin_network_request()
        while not stopping.is_set() and not self._request_was_cancelled(generation):
            sock: socket.socket | None = None
            try:
                sock = self._connect_socket(
                    generation=generation,
                    timeout_seconds=_CONNECT_TIMEOUT_SECONDS,
                )
                sock.settimeout(2.0)
                authenticate_camera_client(sock, token)
                self._raise_if_request_cancelled(generation)
                send_packet(
                    sock,
                    {
                        "action": "monitor_stream",
                        "fps": fps,
                        "width": width,
                        "height": height,
                        "quality": quality,
                    },
                )
                self._raise_if_request_cancelled(generation)
                header, blobs = receive_packet(sock)
                self._raise_if_request_cancelled(generation)
                if header.get("ok") is not True or blobs:
                    raise CameraError(str(header.get("error") or "Monitor start failed"))
                established = True
                while (
                    not stopping.is_set()
                    and not self._request_was_cancelled(generation)
                ):
                    header, blobs = receive_packet(sock)
                    self._raise_if_request_cancelled(generation)
                    received_monotonic = time.monotonic()
                    if header.get("ok") is not True:
                        raise CameraError(str(header.get("error") or "Monitor stream failed"))
                    yield _validated_monitor_payload(
                        header,
                        blobs,
                        requested_width=width,
                        requested_height=height,
                        requested_fps=fps,
                        received_monotonic=received_monotonic,
                    )
            except (CameraError, OSError, ValueError) as exc:
                if self._request_was_cancelled(generation):
                    return
                if not established:
                    if isinstance(exc, CameraError):
                        raise
                    raise CameraError(
                        f"Could not communicate with remote camera at "
                        f"{self._host}:{self._port}: {exc}"
                    ) from exc
                if stopping.wait(0.25):
                    return
            finally:
                if sock is not None:
                    self._release_socket(sock)

    def monitor_frames(
        self,
        *,
        fps: int = 10,
        width: int = 1920,
        height: int = 1080,
        quality: int = 78,
        stop_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield decoded raw monitor frames, preserving the existing API."""

        for encoded in self.monitor_jpeg_frames(
            fps=fps,
            width=width,
            height=height,
            quality=quality,
            stop_event=stop_event,
        ):
            payload = dict(encoded)
            jpeg = payload.pop("jpeg")
            frame = _decode_frame(jpeg)
            if frame.shape[:2] != (payload["height"], payload["width"]):
                raise CameraError(
                    "Remote monitor decoded frame resolution did not match its metadata"
                )
            payload["image"] = frame
            yield payload

    def status(self) -> CameraStatus:
        # Called from desktop status polling, including the Qt GUI thread.
        # Never perform network I/O here.
        with self._status_lock:
            return replace(self._status_cache)

    def apply_controls(
        self,
        requested: Mapping[str, int | bool],
        *,
        timeout_seconds: float = 5.0,
    ) -> ControlResult:
        try:
            normalized = validate_control_request(requested)
        except ValueError as exc:
            raise CameraError(str(exc)) from exc
        if type(timeout_seconds) is bool:
            raise CameraError("Camera-control timeout must be a positive finite number")
        try:
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise CameraError("Camera-control timeout must be a positive finite number") from exc
        if not math.isfinite(timeout) or timeout <= 0:
            raise CameraError("Camera-control timeout must be a positive finite number")
        header, blobs = self._request(
            "apply_controls",
            {"controls": normalized, "timeout": timeout},
            timeout=timeout + _TRANSFER_MARGIN_SECONDS,
        )
        if blobs:
            raise CameraError("Remote camera-control response contained unexpected frame data")
        return _control_from_dict(header.get("controls"))

    def apply_controls_and_snapshot(
        self,
        requested: Mapping[str, int | bool],
        *,
        settle_seconds: float = 0.35,
        timeout_seconds: float = 2.0,
    ) -> tuple[ControlResult, np.ndarray]:
        try:
            normalized = validate_control_request(requested)
        except ValueError as exc:
            raise CameraError(str(exc)) from exc
        try:
            settle = float(settle_seconds)
            timeout = float(timeout_seconds)
        except (TypeError, ValueError) as exc:
            raise CameraError("Camera-control settle and timeout values must be finite") from exc
        if (
            type(settle_seconds) is bool
            or type(timeout_seconds) is bool
            or not math.isfinite(settle)
            or not math.isfinite(timeout)
            or settle < 0
            or timeout <= 0
        ):
            raise CameraError("Camera-control settle and timeout values must be finite")
        header, blobs = self._request(
            "apply_controls_and_snapshot",
            {
                "controls": normalized,
                "settle": settle,
                "timeout": timeout,
                "quality": _STILL_TRANSFER_QUALITY,
            },
            timeout=timeout + _TRANSFER_MARGIN_SECONDS,
        )
        if len(blobs) != 1:
            raise CameraError("Remote camera-control response did not contain one frame")
        return _control_from_dict(header.get("controls")), _decode_frame(blobs[0])

    def apply_configured_controls(self) -> ControlResult:
        return self.apply_controls(self.settings.controls)
