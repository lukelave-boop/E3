from __future__ import annotations

import math
import socket
import threading
import time
from collections.abc import Iterator, Mapping
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

import cv2
import numpy as np

from ..config import CameraSettings, PrecisionCaptureSettings
from ..errors import CameraError
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
_TRANSFER_MARGIN_SECONDS = 30.0
_STILL_TRANSFER_QUALITY = 95
_MONITOR_FPS = frozenset({5, 10, 15})
_MONITOR_SIZES = frozenset({(1280, 720), (1920, 1080)})
_MAX_MONITOR_JPEG_BYTES = 4 * 1024 * 1024


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

    def _request(
        self,
        action: str,
        payload: Mapping[str, Any] | None = None,
        *,
        timeout: float = _CONNECT_TIMEOUT_SECONDS,
    ) -> tuple[dict[str, Any], tuple[bytes, ...]]:
        token = camera_token_from_environment()
        request = {"action": action}
        if payload:
            request.update(payload)
        try:
            sock = socket.create_connection(
                (self._host, self._port),
                timeout=_CONNECT_TIMEOUT_SECONDS,
            )
            with sock:
                sock.settimeout(max(_CONNECT_TIMEOUT_SECONDS, float(timeout)))
                authenticate_camera_client(sock, token)
                send_packet(sock, request)
                header, blobs = receive_packet(sock)
        except CameraError:
            raise
        except (OSError, ValueError) as exc:
            raise CameraError(
                f"Could not communicate with remote camera at {self._host}:{self._port}: {exc}"
            ) from exc
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
        self._verify_remote_profile()
        self._request("start")
        self._mjpeg_generation += 1

    def stop(self) -> None:
        try:
            self._request("stop")
        except CameraError:
            # Teardown must remain best-effort when the Pi or Wi-Fi has already
            # disappeared. The remote camera itself cannot produce laser output.
            pass
        finally:
            self._mjpeg_generation += 1

    def restart(self) -> None:
        self._request("restart")
        self._mjpeg_generation += 1

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
        while generation == self._mjpeg_generation:
            try:
                jpeg = self.jpeg(quality=min(self.settings.jpeg_quality, 85))
            except CameraError:
                time.sleep(0.2)
                continue
            yield (
                b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: "
                + str(len(jpeg)).encode()
                + b"\r\n\r\n"
                + jpeg
                + b"\r\n"
            )
            time.sleep(delay)

    def monitor_frames(
        self,
        *,
        fps: int = 10,
        width: int = 1920,
        height: int = 1080,
        quality: int = 78,
        stop_event: threading.Event | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield raw frames over one authenticated socket, reconnecting after loss."""

        if type(fps) is not int or fps not in _MONITOR_FPS:
            raise CameraError("Monitor FPS must be 5, 10, or 15")
        if type(width) is not int or type(height) is not int or (width, height) not in _MONITOR_SIZES:
            raise CameraError("Monitor resolution must be 1280x720 or 1920x1080")
        if type(quality) is not int or not 70 <= quality <= 85:
            raise CameraError("Monitor JPEG quality must be between 70 and 85")
        stopping = stop_event or threading.Event()
        token = camera_token_from_environment()
        established = False
        while not stopping.is_set():
            try:
                sock = socket.create_connection((self._host, self._port), timeout=_CONNECT_TIMEOUT_SECONDS)
                with sock:
                    sock.settimeout(2.0)
                    authenticate_camera_client(sock, token)
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
                    header, blobs = receive_packet(sock)
                    if header.get("ok") is not True or blobs:
                        raise CameraError(str(header.get("error") or "Monitor start failed"))
                    established = True
                    while not stopping.is_set():
                        header, blobs = receive_packet(sock)
                        if header.get("ok") is not True:
                            raise CameraError(str(header.get("error") or "Monitor stream failed"))
                        if len(blobs) != 1 or len(blobs[0]) > _MAX_MONITOR_JPEG_BYTES:
                            raise CameraError("Remote monitor returned an invalid bounded frame")
                        frame = _decode_frame(blobs[0])
                        if frame.shape[:2] != (height, width):
                            raise CameraError("Remote monitor frame resolution did not match its profile")
                        yield {
                            "image": frame,
                            "sequence": int(header.get("sequence", 0)),
                            "width": width,
                            "height": height,
                            "jpeg_bytes": len(blobs[0]),
                            "source_mode": header.get("source_mode", "transcoded"),
                            "source_width": int(header.get("source_width", width)),
                            "source_height": int(header.get("source_height", height)),
                            "monitor_fps": int(header.get("monitor_fps", fps)),
                            "frame_age_seconds": header.get("frame_age_seconds"),
                            "received_monotonic": time.monotonic(),
                        }
            except CameraError:
                if not established:
                    raise
                if stopping.wait(0.25):
                    return

    def status(self) -> CameraStatus:
        try:
            header, blobs = self._request("status")
            if blobs:
                raise CameraError("Remote camera status contained unexpected frame data")
            raw = header.get("status")
            if not isinstance(raw, dict):
                raise CameraError("Remote camera returned invalid status data")
            return CameraStatus(**raw)
        except CameraError as exc:
            return CameraStatus(
                connected=False,
                device=self.settings.device,
                width=0,
                height=0,
                fps=0.0,
                frames_read=0,
                last_error=str(exc),
            )

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
