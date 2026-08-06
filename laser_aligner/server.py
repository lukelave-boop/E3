from __future__ import annotations

import json
import logging
import mimetypes
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .app import AppContext
from .errors import LaserAlignerError

LOGGER = logging.getLogger(__name__)


class AppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], context: AppContext):
        self.context = context
        super().__init__(address, AppRequestHandler)


class AppRequestHandler(BaseHTTPRequestHandler):
    server: AppHTTPServer
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:
        LOGGER.info("%s - %s", self.address_string(), format % args)

    @property
    def context(self) -> AppContext:
        return self.server.context

    def _headers(self, content_type: str, content_length: int | None = None, cache: bool = False) -> None:
        self.send_header("Content-Type", content_type)
        if content_length is not None:
            self.send_header("Content-Length", str(content_length))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; img-src 'self' blob: data:; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'",
        )
        self.send_header("Cache-Control", "public, max-age=300" if cache else "no-store")

    def _send_bytes(
        self,
        data: bytes,
        content_type: str = "application/octet-stream",
        status: HTTPStatus = HTTPStatus.OK,
        cache: bool = False,
        disposition: str | None = None,
    ) -> None:
        self.send_response(status)
        self._headers(content_type, len(data), cache=cache)
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2, default=str).encode("utf-8")
        self._send_bytes(data, "application/json; charset=utf-8", status=status)

    def _error(self, status: HTTPStatus, message: str, details: str | None = None) -> None:
        payload: dict[str, Any] = {"ok": False, "error": message}
        if details:
            payload["details"] = details
        self._send_json(payload, status=status)

    def _read_json(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            return {}
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise ValueError("Invalid Content-Length") from exc
        if length < 0 or length > self.context.settings.app.max_request_bytes:
            raise ValueError("Request body exceeds configured limit")
        body = self.rfile.read(length)
        if not body:
            return {}
        payload = json.loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object")
        return payload

    def _is_local_client(self) -> bool:
        return self.client_address[0] in {"127.0.0.1", "::1"}

    def _check_remote_machine_control(self, path: str) -> bool:
        if path.startswith("/api/machine/") and not self._is_local_client():
            if not self.context.settings.app.allow_remote_control:
                self._error(
                    HTTPStatus.FORBIDDEN,
                    "Remote machine control is disabled. Use the computer locally or explicitly enable it in configuration.",
                )
                return False
        return True

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path in {"/", "/index.html"}:
                self._serve_static("index.html")
            elif path.startswith("/static/"):
                self._serve_static(path.removeprefix("/static/"))
            elif path == "/api/status":
                self._send_json({"ok": True, **self.context.status()})
            elif path == "/api/camera/frame.jpg":
                undistort = query.get("undistort", ["1"])[0] != "0"
                image = self.context.camera_frame(undistort=undistort)
                self._send_bytes(self.context.encode_jpeg(image), "image/jpeg")
            elif path == "/api/camera/stream.mjpg":
                self._serve_mjpeg()
            elif path == "/api/calibration/lens":
                self._send_json({"ok": True, **self.context.lens.status()})
            elif path == "/api/calibration/bed":
                self._send_json({"ok": True, **self.context.bed.status()})
            elif path == "/api/calibration/bed/frame.jpg":
                image = self.context.bed_reference()
                self._send_bytes(self.context.encode_jpeg(image, 96), "image/jpeg")
            elif path == "/api/workspace/frame.jpg":
                refresh = query.get("refresh", ["0"])[0] == "1"
                image = self.context.rectified_frame(refresh=refresh)
                self._send_bytes(self.context.encode_jpeg(image, 94), "image/jpeg")
            elif path == "/api/machine/status":
                self._send_json({"ok": True, **self.context.machine.status()})
            elif path.startswith("/api/generated/"):
                self._serve_generated(path.removeprefix("/api/generated/"))
            elif path == "/api/health":
                self._send_json({"ok": True, "status": "healthy"})
            else:
                self._error(HTTPStatus.NOT_FOUND, "Route not found")
        except BrokenPipeError:
            return
        except Exception as exc:
            self._handle_exception(exc)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        if not self._check_remote_machine_control(path):
            return
        try:
            payload = self._read_json()
            if path == "/api/camera/capture":
                capture = self.context.save_capture(
                    prefix=str(payload.get("prefix", "capture")),
                    undistort=bool(payload.get("undistort", True)),
                )
                self._send_json({"ok": True, "filename": capture.name})
            elif path == "/api/camera/controls/apply":
                result = self.context.camera.apply_configured_controls()
                self._send_json(
                    {"ok": True, "requested": result.requested, "applied": result.applied, "skipped": result.skipped}
                )
            elif path == "/api/camera/synthetic-scene":
                self.context.synthetic_scene(str(payload.get("scene", "bed")))
                self._send_json({"ok": True})
            elif path == "/api/calibration/lens/capture":
                image = self.context.camera_frame(undistort=False)
                self._send_json({"ok": True, **self.context.lens.capture(image)})
            elif path == "/api/calibration/lens/solve":
                model = self.context.lens.solve()
                self._send_json({"ok": True, "model": model.to_dict()})
            elif path == "/api/calibration/lens/clear":
                self.context.lens.clear(delete_images=bool(payload.get("delete_images", False)))
                self._send_json({"ok": True})
            elif path == "/api/calibration/bed/capture":
                self._send_json({"ok": True, **self.context.capture_bed_reference()})
            elif path == "/api/calibration/bed/point":
                index = self.context.add_bed_point(payload)
                self._send_json({"ok": True, "index": index, **self.context.bed.status()})
            elif path == "/api/calibration/bed/delete":
                self.context.bed.delete_point(int(payload["index"]))
                self._send_json({"ok": True, **self.context.bed.status()})
            elif path == "/api/calibration/bed/clear":
                self.context.bed.clear()
                self._send_json({"ok": True})
            elif path == "/api/calibration/bed/solve":
                self._send_json({"ok": True, "calibration": self.context.solve_bed()})
            elif path == "/api/calibration/bed/fiducials":
                self._send_json({"ok": True, **self.context.detect_fiducials()})
            elif path == "/api/calibration/bed/auto-detect":
                self._send_json({"ok": True, **self.context.detect_bed_cross_grid()})
            elif path == "/api/calibration/bed/auto-accept":
                self._send_json({"ok": True, **self.context.replace_bed_points(payload)})
            elif path == "/api/workspace/capture":
                image = self.context.rectified_frame(refresh=True)
                self._send_json({"ok": True, "width": image.shape[1], "height": image.shape[0]})
            elif path == "/api/vision/workpiece":
                self._send_json({"ok": True, **self.context.detect_workpiece()})
            elif path == "/api/design/analyze":
                self._send_json({"ok": True, **self.context.analyze_svg(str(payload["svg"]))})
            elif path == "/api/design/gcode":
                self._send_json({"ok": True, **self.context.generate_gcode(payload)})
            elif path == "/api/design/frame":
                self._send_json({"ok": True, **self.context.generate_frame(payload)})
            elif path == "/api/machine/connect":
                result = self.context.machine.connect(
                    port=payload.get("port"),
                    protocol=payload.get("protocol"),
                    baudrate=None if payload.get("baudrate") is None else int(payload["baudrate"]),
                )
                self._send_json({"ok": True, **result})
            elif path == "/api/machine/disconnect":
                self.context.machine.disconnect()
                self._send_json({"ok": True})
            elif path == "/api/machine/arm":
                until = self.context.machine.arm(str(payload.get("phrase", "")))
                self._send_json({"ok": True, "armed_until": until})
            elif path == "/api/machine/disarm":
                self.context.machine.disarm()
                self._send_json({"ok": True})
            elif path == "/api/machine/command":
                responses = self.context.machine.send_command(str(payload["command"]))
                self._send_json({"ok": True, "responses": responses})
            elif path == "/api/machine/photo-position":
                self._send_json({"ok": True, **self.context.machine.prepare_photo_position()})
            elif path == "/api/machine/run":
                result = self.context.machine.start_job(str(payload["gcode"]), str(payload.get("name", "job.gcode")))
                self._send_json({"ok": True, "job": result}, status=HTTPStatus.ACCEPTED)
            elif path == "/api/machine/stop":
                self.context.machine.stop_job(emergency=bool(payload.get("emergency", False)))
                self._send_json({"ok": True})
            else:
                self._error(HTTPStatus.NOT_FOUND, "Route not found")
        except Exception as exc:
            self._handle_exception(exc)

    def _serve_static(self, relative: str) -> None:
        root = Path(__file__).resolve().parent / "web"
        candidate = (root / relative).resolve()
        if root not in candidate.parents and candidate != root:
            self._error(HTTPStatus.FORBIDDEN, "Invalid static path")
            return
        if not candidate.is_file():
            self._error(HTTPStatus.NOT_FOUND, "Static file not found")
            return
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        self._send_bytes(candidate.read_bytes(), content_type, cache=relative != "index.html")

    def _serve_generated(self, filename: str) -> None:
        if Path(filename).name != filename:
            self._error(HTTPStatus.FORBIDDEN, "Invalid generated filename")
            return
        path = self.context.settings.app.data_dir / "generated" / filename
        if not path.is_file():
            self._error(HTTPStatus.NOT_FOUND, "Generated file not found")
            return
        self._send_bytes(
            path.read_bytes(),
            "text/plain; charset=utf-8",
            disposition=f'attachment; filename="{filename}"',
        )

    def _serve_mjpeg(self) -> None:
        self.send_response(HTTPStatus.OK)
        self._headers("multipart/x-mixed-replace; boundary=frame")
        self.end_headers()
        try:
            for chunk in self.context.camera.mjpeg(target_fps=10):
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            return

    def _handle_exception(self, exc: Exception) -> None:
        if isinstance(exc, (LaserAlignerError, ValueError, KeyError, json.JSONDecodeError)):
            LOGGER.warning("Request failed: %s", exc)
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        LOGGER.error("Unhandled request error: %s\n%s", exc, traceback.format_exc())
        self._error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal server error")
