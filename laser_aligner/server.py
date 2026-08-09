from __future__ import annotations

import ipaddress
import json
import logging
import mimetypes
import os
import re
import secrets
import stat
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .app import AppContext
from .errors import LaserAlignerError
from .storage import strict_json_loads

LOGGER = logging.getLogger(__name__)
_REQUEST_TOKEN_HEADER = "X-E3-Request-Token"
_REQUEST_TOKEN_PLACEHOLDER = "__E3_REQUEST_TOKEN__"
_GENERATED_FILENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,180}\.gcode")


def _json_boolean(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if type(value) is not bool:
        raise ValueError(f"{key} must be a JSON boolean")
    return value


def _http_authority(host: str, port: int) -> str:
    normalized = host.strip().lower()
    if ":" in normalized and not normalized.startswith("["):
        normalized = f"[{normalized}]"
    return normalized if port == 80 else f"{normalized}:{port}"


def _loopback_authorities(bind_host: str, port: int) -> frozenset[str]:
    hosts = {"127.0.0.1", "localhost", "::1"}
    candidate = bind_host.strip().strip("[]").split("%", 1)[0]
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        if candidate.lower() == "localhost":
            hosts.add("localhost")
    else:
        if address.is_loopback:
            hosts.add(address.compressed)
    authorities = {_http_authority(host, port) for host in hosts}
    if port == 80:
        authorities.update(f"{authority}:80" for authority in tuple(authorities))
    return frozenset(authorities)


class AppHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], context: AppContext):
        self.context = context
        self._request_token = secrets.token_urlsafe(32)
        super().__init__(address, AppRequestHandler)
        self.allowed_authorities = _loopback_authorities(
            address[0],
            int(self.server_address[1]),
        )


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
        if self.headers.get_all("Transfer-Encoding", failobj=[]):
            self.close_connection = True
            raise ValueError("Transfer-Encoding is not supported")
        lengths = self.headers.get_all("Content-Length", failobj=[])
        if len(lengths) > 1:
            self.close_connection = True
            raise ValueError("Multiple Content-Length headers are not allowed")
        if not lengths:
            return {}
        raw_length = lengths[0].strip()
        if re.fullmatch(r"[0-9]+", raw_length) is None:
            self.close_connection = True
            raise ValueError("Invalid Content-Length")
        length = int(raw_length)
        if length < 0 or length > self.context.settings.app.max_request_bytes:
            self.close_connection = True
            raise ValueError("Request body exceeds configured limit")
        body = self.rfile.read(length)
        if not body:
            return {}
        payload = strict_json_loads(body.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON request body must be an object")
        return payload

    def _is_local_client(self) -> bool:
        address = self.client_address[0].split("%", 1)[0]
        try:
            return ipaddress.ip_address(address).is_loopback
        except ValueError:
            return False

    def _reject_request(self, status: HTTPStatus, message: str) -> None:
        # Rejected POST bodies are intentionally not consumed. Close this HTTP/1.1
        # connection so those bytes cannot be parsed as a subsequent request.
        self.close_connection = True
        self._error(status, message)

    def _validated_authority(self) -> str | None:
        values = self.headers.get_all("Host", failobj=[])
        if len(values) != 1:
            self._reject_request(HTTPStatus.MISDIRECTED_REQUEST, "Request host is not allowed")
            return None
        authority = values[0].strip().lower()
        if authority not in self.server.allowed_authorities:
            self._reject_request(HTTPStatus.MISDIRECTED_REQUEST, "Request host is not allowed")
            return None
        if not self._is_local_client():
            self._reject_request(
                HTTPStatus.FORBIDDEN,
                "Browser access is restricted to this computer",
            )
            return None
        return authority

    def _authorize_mutating_request(self, authority: str) -> bool:
        origins = self.headers.get_all("Origin", failobj=[])
        expected_origin = f"http://{authority}"
        if len(origins) != 1 or origins[0].strip().lower() != expected_origin:
            self._reject_request(HTTPStatus.FORBIDDEN, "Request origin is not allowed")
            return False

        content_types = self.headers.get_all("Content-Type", failobj=[])
        if len(content_types) != 1 or self.headers.get_content_type().lower() != "application/json":
            self._reject_request(
                HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                "State-changing requests require application/json",
            )
            return False

        tokens = self.headers.get_all(_REQUEST_TOKEN_HEADER, failobj=[])
        if len(tokens) != 1 or not secrets.compare_digest(
            tokens[0].strip(),
            self.server._request_token,
        ):
            self._reject_request(HTTPStatus.FORBIDDEN, "Request token is missing or invalid")
            return False
        return True

    def do_OPTIONS(self) -> None:
        authority = self._validated_authority()
        if authority is None:
            return
        origins = self.headers.get_all("Origin", failobj=[])
        if len(origins) != 1 or origins[0].strip().lower() != f"http://{authority}":
            self._reject_request(HTTPStatus.FORBIDDEN, "Request origin is not allowed")
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Allow", "GET, POST, OPTIONS")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self) -> None:
        if self._validated_authority() is None:
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)
        try:
            if path in {"/", "/index.html"}:
                self._serve_index()
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
        authority = self._validated_authority()
        if authority is None or not self._authorize_mutating_request(authority):
            return
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        try:
            payload = self._read_json()
            if path == "/api/camera/capture":
                capture = self.context.save_capture(
                    prefix=str(payload.get("prefix", "capture")),
                    undistort=_json_boolean(payload, "undistort", True),
                )
                self._send_json({"ok": True, "filename": capture.name})
            elif path == "/api/camera/controls/apply":
                result = self.context.camera.apply_configured_controls()
                self._send_json(
                    {
                        "ok": True,
                        "requested": result.requested,
                        "applied": result.applied,
                        "verified": result.verified,
                        "skipped": result.skipped,
                    }
                )
            elif path == "/api/camera/synthetic-scene":
                self.context.synthetic_scene(str(payload.get("scene", "bed")))
                self._send_json({"ok": True})
            elif path == "/api/calibration/lens/capture":
                self._send_json(
                    {"ok": True, **self.context.capture_lens_calibration()}
                )
            elif path == "/api/calibration/lens/solve":
                model = self.context.solve_lens_calibration()
                self._send_json({"ok": True, "model": model.to_dict()})
            elif path == "/api/calibration/lens/clear":
                self.context.clear_lens_calibration(
                    delete_images=_json_boolean(payload, "delete_images", False)
                )
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
                image = self.context.rectified_frame(
                    refresh=True,
                    precision=True,
                    persist=True,
                )
                self._send_json({"ok": True, "width": image.shape[1], "height": image.shape[0]})
            elif path == "/api/vision/workpiece":
                self._send_json({"ok": True, **self.context.detect_workpiece()})
            elif path == "/api/design/analyze":
                self._send_json({"ok": True, **self.context.analyze_svg(str(payload["svg"]))})
            elif path == "/api/design/gcode":
                self._send_json({"ok": True, **self.context.generate_gcode(payload)})
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
                generation = self.context.machine.operation_generation()
                try:
                    with self.context.machine.operation_scope(generation):
                        gcode = str(payload.get("gcode", ""))
                        if not gcode.strip():
                            raise ValueError(
                                "G-code is required before laser control can be armed"
                            )
                        program = self.context.machine.preflight_program(gcode)
                        until = self.context.machine.arm_program(
                            str(payload.get("phrase", "")),
                            program,
                        )
                except Exception:
                    # A STOP after this request began already revoked its grant.
                    # Do not let the stale request disarm a newer connection's
                    # independently authorized program.
                    if self.context.machine.operation_generation() == generation:
                        self.context.machine.disarm()
                    raise
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
                self.context.machine.stop_job(
                    emergency=_json_boolean(payload, "emergency", False)
                )
                self._send_json({"ok": True})
            else:
                self._error(HTTPStatus.NOT_FOUND, "Route not found")
        except Exception as exc:
            self._handle_exception(exc)

    def _serve_index(self) -> None:
        path = Path(__file__).resolve().parent / "web" / "index.html"
        template = path.read_text(encoding="utf-8")
        if _REQUEST_TOKEN_PLACEHOLDER not in template:
            raise RuntimeError("Browser app shell is missing its request-token placeholder")
        data = template.replace(
            _REQUEST_TOKEN_PLACEHOLDER,
            self.server._request_token,
            1,
        ).encode("utf-8")
        self._send_bytes(data, "text/html; charset=utf-8")

    def _serve_static(self, relative: str) -> None:
        if relative == "index.html":
            self._serve_index()
            return
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
        if _GENERATED_FILENAME_RE.fullmatch(filename) is None:
            self._error(HTTPStatus.FORBIDDEN, "Invalid generated filename")
            return
        root = (self.context.settings.app.data_dir / "generated").resolve()
        path = root / filename
        if path.is_symlink():
            self._error(HTTPStatus.FORBIDDEN, "Invalid generated file target")
            return
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(path, flags)
        except FileNotFoundError:
            self._error(HTTPStatus.NOT_FOUND, "Generated file not found")
            return
        except OSError:
            self._error(HTTPStatus.FORBIDDEN, "Invalid generated file target")
            return
        try:
            target = os.fstat(descriptor)
            if not stat.S_ISREG(target.st_mode):
                self._error(HTTPStatus.FORBIDDEN, "Invalid generated file target")
                return
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                data = handle.read()
        finally:
            os.close(descriptor)
        self._send_bytes(
            data,
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
