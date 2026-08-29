from __future__ import annotations

import argparse
import logging
import math
import socket
import threading
import time
from dataclasses import asdict
from typing import Any

import cv2
import numpy as np

from ..config import PrecisionCaptureSettings, load_settings
from ..errors import CameraError
from .remote_protocol import (
    authenticate_camera_server,
    camera_token_from_environment,
    receive_packet,
    send_packet,
)
from .service import CameraService, FrameBurst

LOGGER = logging.getLogger(__name__)
_DEFAULT_PORT = 8766
_MAX_CLIENTS = 8
_HANDSHAKE_TIMEOUT_SECONDS = 5.0
_STILL_TRANSFER_QUALITY = 95
_MONITOR_FPS = frozenset({5, 10, 15})
_MONITOR_SIZES = frozenset({(1280, 720), (1920, 1080)})
_MONITOR_QUALITY_MIN = 70
_MONITOR_QUALITY_MAX = 85
_MAX_MONITOR_JPEG_BYTES = 4 * 1024 * 1024
_MAX_MONITOR_CLIENTS = 2


def _encode_frame(frame: np.ndarray, quality: int) -> bytes:
    if type(quality) is not int or not 1 <= quality <= 100:
        raise CameraError("Remote camera transfer quality must be an integer from 1 to 100")
    ok, encoded = cv2.imencode(
        ".jpg",
        frame,
        [cv2.IMWRITE_JPEG_QUALITY, quality],
    )
    if not ok:
        raise CameraError("Could not encode remote camera frame")
    return encoded.tobytes()


def _encode_monitor_frame(frame: np.ndarray, quality: int) -> bytes:
    jpeg = _encode_frame(frame, quality)
    if len(jpeg) > _MAX_MONITOR_JPEG_BYTES:
        raise CameraError("Monitor JPEG exceeds the bounded frame limit")
    return jpeg


def _monitor_frame(
    camera: CameraService,
    *,
    sequence: int,
    width: int,
    height: int,
    quality: int,
    timeout: float,
) -> tuple[dict[str, Any], bytes]:
    source_mode = "transcoded"
    source_width = 0
    source_height = 0
    captured_monotonic = time.monotonic()
    direct = getattr(camera, "direct_mjpeg_after", None)
    native_size = width == int(camera.settings.width) and height == int(
        camera.settings.height
    )
    if callable(direct) and native_size:
        try:
            compressed = direct(sequence, timeout=timeout)
        except CameraError:
            compressed = None
        if compressed is not None:
            jpeg = compressed.jpeg
            sequence = compressed.sequence
            source_width = compressed.width
            source_height = compressed.height
            captured_monotonic = compressed.captured_monotonic
            source_mode = "direct_mjpeg"
    if source_mode == "transcoded":
        if native_size:
            width, height = 1280, 720
        frame = camera.snapshot_after(sequence, timeout=timeout)
        sequence = camera.frame_sequence()
        source_height, source_width = frame.shape[:2]
        status = camera.status()
        status_age = status.frame_age_seconds
        captured_monotonic = time.monotonic() - max(0.0, float(status_age or 0.0))
        if source_width != width or source_height != height:
            frame = cv2.resize(frame, (width, height), interpolation=cv2.INTER_AREA)
        jpeg = _encode_monitor_frame(frame, quality)
    else:
        status = camera.status()
    if len(jpeg) > _MAX_MONITOR_JPEG_BYTES:
        raise CameraError("Monitor JPEG exceeds the bounded frame limit")
    return (
        {
            "ok": True,
            "sequence": sequence,
            "captured_monotonic": captured_monotonic,
            "width": width,
            "height": height,
            "source_mode": source_mode,
            "source_width": source_width,
            "source_height": source_height,
            "jpeg_bytes": len(jpeg),
            "frame_age_seconds": max(0.0, time.monotonic() - captured_monotonic),
            "capture_fps": status.fps,
            "negotiated_fps": status.negotiated_fps,
        },
        jpeg,
    )


def _control_dict(result: Any) -> dict[str, Any]:
    return {
        "requested": dict(result.requested),
        "applied": dict(result.applied),
        "skipped": dict(result.skipped),
        "verified": dict(result.verified),
        "satisfied": dict(result.satisfied),
        "critical_unverified": dict(result.critical_unverified),
    }


def _burst_dict(burst: FrameBurst) -> dict[str, Any]:
    return {
        "sequence_numbers": list(burst.sequence_numbers),
        "discarded_frames": burst.discarded_frames,
        "settle_seconds": burst.settle_seconds,
        "elapsed_seconds": burst.elapsed_seconds,
        "controls": _control_dict(burst.controls),
        "timeout_seconds": burst.timeout_seconds,
        "observed_fps": burst.observed_fps,
        "negotiated_fps": burst.negotiated_fps,
        "sequence_gaps": burst.sequence_gaps,
        "camera_generation": burst.camera_generation,
    }


def _finite_number(raw: object, label: str, *, positive: bool = False) -> float:
    if type(raw) not in {int, float}:
        raise CameraError(f"{label} must be a finite number")
    value = float(raw)
    if not math.isfinite(value) or (positive and value <= 0):
        raise CameraError(f"{label} must be a finite number")
    return value


def _quality(request: dict[str, Any], default: int = _STILL_TRANSFER_QUALITY) -> int:
    raw = request.get("quality", default)
    if type(raw) is not int or not 1 <= raw <= 100:
        raise CameraError("Remote camera transfer quality must be an integer from 1 to 100")
    return raw


class CameraBridgeServer:
    def __init__(
        self,
        camera: CameraService,
        *,
        host: str,
        port: int,
        token: str,
    ):
        self.camera = camera
        self.host = host
        self.port = port
        self.token = token
        self._slots = threading.BoundedSemaphore(_MAX_CLIENTS)
        self._monitor_slots = threading.BoundedSemaphore(_MAX_MONITOR_CLIENTS)
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
        try:
            self.camera.stop()
        except Exception:
            LOGGER.exception("Could not stop camera during bridge shutdown")

    def _dispatch(
        self,
        request: dict[str, Any],
    ) -> tuple[dict[str, Any], tuple[bytes, ...]]:
        action = request.get("action")
        if not isinstance(action, str):
            raise CameraError("Remote camera request is missing an action")
        if action == "start":
            self.camera.start()
            return {"ok": True}, ()
        if action == "stop":
            # Legacy desktop clients used "stop" as client teardown. The physical
            # camera is owned by the Pi hardware node, so disconnecting one client
            # must never turn off the shared camera for every other client.
            return {"ok": True}, ()
        if action == "restart":
            self.camera.restart()
            return {"ok": True}, ()
        if action == "status":
            return {"ok": True, "status": asdict(self.camera.status())}, ()
        if action == "profile":
            settings = self.camera.settings
            return {
                "ok": True,
                "profile": {
                    "width": int(settings.width),
                    "height": int(settings.height),
                    "fps": int(settings.fps),
                    "fourcc": str(settings.fourcc),
                    "warmup_frames": int(settings.warmup_frames),
                    "controls": dict(settings.controls),
                },
            }, ()
        if action == "generation":
            return {"ok": True, "generation": self.camera._current_generation()}, ()
        if action == "frame_sequence":
            return {"ok": True, "sequence": self.camera.frame_sequence()}, ()
        if action == "snapshot":
            frame = self.camera.snapshot()
            return {"ok": True}, (_encode_frame(frame, _quality(request)),)
        if action == "snapshot_after":
            sequence = request.get("sequence")
            if type(sequence) is not int or sequence < 0:
                raise CameraError("Fresh-frame sequence must be a non-negative integer")
            timeout = _finite_number(request.get("timeout"), "Fresh-frame timeout", positive=True)
            frame = self.camera.snapshot_after(sequence, timeout=timeout)
            return {"ok": True}, (_encode_frame(frame, _quality(request)),)
        if action == "jpeg":
            return {"ok": True}, (self.camera.jpeg(quality=_quality(request)),)
        if action == "apply_controls":
            controls = request.get("controls")
            if not isinstance(controls, dict):
                raise CameraError("Camera controls must be a mapping")
            timeout = _finite_number(request.get("timeout"), "Camera-control timeout", positive=True)
            result = self.camera.apply_controls(controls, timeout_seconds=timeout)
            return {"ok": True, "controls": _control_dict(result)}, ()
        if action == "apply_controls_and_snapshot":
            controls = request.get("controls")
            if not isinstance(controls, dict):
                raise CameraError("Camera controls must be a mapping")
            settle = _finite_number(request.get("settle"), "Camera-control settle")
            timeout = _finite_number(request.get("timeout"), "Camera-control timeout", positive=True)
            if settle < 0:
                raise CameraError("Camera-control settle must be non-negative")
            result, frame = self.camera.apply_controls_and_snapshot(
                controls,
                settle_seconds=settle,
                timeout_seconds=timeout,
            )
            return {"ok": True, "controls": _control_dict(result)}, (
                _encode_frame(frame, _quality(request)),
            )
        if action == "capture_burst":
            profile_raw = request.get("profile")
            if not isinstance(profile_raw, dict):
                raise CameraError("Remote precision capture requires a profile")
            try:
                profile = PrecisionCaptureSettings(**profile_raw)
            except TypeError as exc:
                raise CameraError(f"Remote precision-capture profile is invalid: {exc}") from exc
            reapply = request.get("reapply_controls", True)
            if type(reapply) is not bool:
                raise CameraError("reapply_controls must be a JSON boolean")
            CameraService._validate_capture_profile(profile)
            burst = self.camera.capture_burst(
                profile,
                reapply_controls=reapply,
                score_frames=False,
            )
            quality = _quality(request)
            blobs = tuple(_encode_frame(frame, quality) for frame in burst.frames)
            return {"ok": True, "burst": _burst_dict(burst)}, blobs
        raise CameraError(f"Unsupported remote camera action: {action}")

    def _handle(self, conn: socket.socket, address: tuple[object, ...]) -> None:
        acquired = self._slots.acquire(blocking=False)
        with conn:
            if not acquired:
                try:
                    conn.sendall(b"E3CAMERA/1 BUSY\n")
                except OSError:
                    pass
                return
            try:
                conn.settimeout(_HANDSHAKE_TIMEOUT_SECONDS)
                if not authenticate_camera_server(conn, self.token):
                    LOGGER.warning("Rejected remote camera client from %s", address[0])
                    return
                conn.settimeout(120.0)
                request, request_blobs = receive_packet(conn)
                if request_blobs:
                    raise CameraError("Remote camera requests may not contain binary payloads")
                if request.get("action") == "monitor_stream":
                    self._monitor_stream(conn, request)
                    return
                try:
                    header, blobs = self._dispatch(request)
                except Exception as exc:
                    LOGGER.warning("Remote camera action failed: %s", exc)
                    send_packet(conn, {"ok": False, "error": str(exc)})
                    return
                send_packet(conn, header, blobs)
            except Exception as exc:
                LOGGER.warning("Remote camera client session ended: %s", exc)
            finally:
                self._slots.release()

    def _monitor_stream(self, conn: socket.socket, request: dict[str, Any]) -> None:
        if not self._monitor_slots.acquire(blocking=False):
            send_packet(conn, {"ok": False, "error": "Remote camera monitor limit reached"})
            return
        try:
            fps = request.get("fps", 10)
            width = request.get("width", 1280)
            height = request.get("height", 720)
            quality = request.get("quality", 78)
            if type(fps) is not int or fps not in _MONITOR_FPS:
                raise CameraError("Monitor FPS must be 5, 10, or 15")
            if type(width) is not int or type(height) is not int or (width, height) not in _MONITOR_SIZES:
                raise CameraError("Monitor resolution must be 1280x720 or 1920x1080")
            if type(quality) is not int or not _MONITOR_QUALITY_MIN <= quality <= _MONITOR_QUALITY_MAX:
                raise CameraError("Monitor JPEG quality must be between 70 and 85")
            # Keep the kernel backlog small. If send blocks, the next iteration
            # samples the newest camera sequence instead of accumulating frames.
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 512 * 1024)
            conn.settimeout(3.0)
            send_packet(
                conn,
                {"ok": True, "stream": "raw", "fps": fps, "width": width, "height": height},
            )
            sequence = max(0, self.camera.frame_sequence() - 1)
            interval = 1.0 / float(fps)
            next_frame = time.monotonic()
            while not self._stop.is_set():
                now = time.monotonic()
                if now < next_frame and self._stop.wait(next_frame - now):
                    break
                next_frame = max(next_frame + interval, time.monotonic())
                metadata, jpeg = _monitor_frame(
                    self.camera,
                    sequence=sequence,
                    width=width,
                    height=height,
                    quality=quality,
                    timeout=max(1.0, 3.0 * interval),
                )
                sequence = int(metadata["sequence"])
                metadata["monitor_fps"] = fps
                send_packet(
                    conn,
                    metadata,
                    (jpeg,),
                )
        except Exception as exc:
            try:
                send_packet(conn, {"ok": False, "error": str(exc)})
            except Exception:
                pass
        finally:
            self._monitor_slots.release()

    def serve_forever(self) -> None:
        if not 1 <= self.port <= 65535:
            raise CameraError("Remote camera port must be between 1 and 65535")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.host, self.port))
        listener.listen(_MAX_CLIENTS)
        listener.settimeout(0.5)
        self._listener = listener
        LOGGER.info("E3 camera bridge listening on %s:%d", self.host, self.port)
        if self.camera.settings.autostart:
            try:
                self.camera.start()
                LOGGER.info("E3 camera bridge started the node-owned camera")
            except Exception as exc:
                # Keep the bridge reachable so an operator can retry/restart the
                # camera remotely even if USB capture was unavailable at node boot.
                LOGGER.warning("E3 camera did not start at node startup: %s", exc)
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
                    target=self._handle,
                    args=(conn, address),
                    name="e3-camera-client",
                    daemon=True,
                ).start()
        finally:
            self._listener = None
            try:
                listener.close()
            except OSError:
                pass


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authenticated E3 camera bridge")
    parser.add_argument("--config", required=True, help="Pi-local E3 JSON configuration")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address; use 0.0.0.0 only on a trusted/firewalled machine network",
    )
    parser.add_argument("--port", type=int, default=_DEFAULT_PORT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(args.config)
    token = camera_token_from_environment()
    camera = CameraService(settings.camera)
    server = CameraBridgeServer(camera, host=args.host, port=args.port, token=token)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping E3 camera bridge")
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
