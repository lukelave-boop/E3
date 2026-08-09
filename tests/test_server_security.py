from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from http.client import HTTPConnection
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from laser_aligner.errors import MachineError
from laser_aligner.server import AppHTTPServer, AppRequestHandler, _json_boolean

_MUTATING_ROUTES = (
    "/api/camera/capture",
    "/api/camera/controls/apply",
    "/api/camera/synthetic-scene",
    "/api/calibration/lens/capture",
    "/api/calibration/lens/solve",
    "/api/calibration/lens/clear",
    "/api/calibration/bed/capture",
    "/api/calibration/bed/point",
    "/api/calibration/bed/delete",
    "/api/calibration/bed/clear",
    "/api/calibration/bed/solve",
    "/api/calibration/bed/fiducials",
    "/api/calibration/bed/auto-detect",
    "/api/calibration/bed/auto-accept",
    "/api/workspace/capture",
    "/api/vision/workpiece",
    "/api/design/analyze",
    "/api/design/gcode",
    "/api/machine/connect",
    "/api/machine/disconnect",
    "/api/machine/arm",
    "/api/machine/disarm",
    "/api/machine/command",
    "/api/machine/photo-position",
    "/api/machine/run",
    "/api/machine/stop",
)


@pytest.mark.parametrize("value", ["false", "true", 0, 1, None, [], {}])
def test_route_boolean_parser_rejects_truthy_and_falsy_non_booleans(value: Any) -> None:
    with pytest.raises(ValueError, match="flag must be a JSON boolean"):
        _json_boolean({"flag": value}, "flag", False)


def test_route_boolean_parser_accepts_only_json_booleans() -> None:
    assert _json_boolean({}, "flag", True) is True
    assert _json_boolean({"flag": False}, "flag", True) is False


class _SpyMachine:
    def __init__(self, mutations: list[tuple[str, Any]]) -> None:
        self._mutations = mutations

    def preflight_program(self, gcode: str) -> SimpleNamespace:
        self._mutations.append(("preflight_program", gcode))
        return SimpleNamespace(digest="validated-program")

    def operation_generation(self) -> int:
        return 0

    @contextmanager
    def operation_scope(self, generation: int):
        del generation
        yield

    def arm_program(self, phrase: str, program: SimpleNamespace) -> float:
        self._mutations.append(("arm_program", (phrase, program.digest)))
        return 1234.5

    def disarm(self) -> None:
        self._mutations.append(("disarm", None))


class _SpyContext:
    def __init__(self, data_dir: Path) -> None:
        self.settings = SimpleNamespace(
            app=SimpleNamespace(
                allow_remote_control=True,
                data_dir=data_dir,
                max_request_bytes=1_000_000,
            )
        )
        self.mutations: list[tuple[str, Any]] = []
        self.machine = _SpyMachine(self.mutations)

    def status(self) -> dict[str, Any]:
        return {"mode": "simulation"}

    def synthetic_scene(self, scene: str) -> None:
        self.mutations.append(("synthetic_scene", scene))


@pytest.fixture
def http_app(tmp_path: Path):
    context = _SpyContext(tmp_path)
    server = AppHTTPServer(("127.0.0.1", 0), context)  # type: ignore[arg-type]
    thread = threading.Thread(
        target=server.serve_forever,
        kwargs={"poll_interval": 0.01},
        daemon=True,
    )
    thread.start()
    try:
        yield server, context
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2.0)


def _authority(server: AppHTTPServer, host: str = "127.0.0.1") -> str:
    return f"{host}:{int(server.server_address[1])}"


def _request(
    server: AppHTTPServer,
    method: str,
    path: str,
    *,
    body: str | bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = HTTPConnection("127.0.0.1", int(server.server_address[1]), timeout=3.0)
    request_headers = {"Host": _authority(server), **(headers or {})}
    connection.request(method, path, body=body, headers=request_headers)
    response = connection.getresponse()
    payload = response.read()
    response_headers = {name.lower(): value for name, value in response.getheaders()}
    connection.close()
    return response.status, response_headers, payload


def _request_with_header_lines(
    server: AppHTTPServer,
    method: str,
    path: str,
    header_lines: list[tuple[str, str]],
    *,
    body: bytes = b"",
) -> tuple[int, dict[str, str], bytes]:
    connection = HTTPConnection("127.0.0.1", int(server.server_address[1]), timeout=3.0)
    connection.putrequest(method, path, skip_host=True)
    for name, value in header_lines:
        connection.putheader(name, value)
    if body:
        connection.putheader("Content-Length", str(len(body)))
    connection.endheaders(body)
    response = connection.getresponse()
    payload = response.read()
    response_headers = {name.lower(): value for name, value in response.getheaders()}
    connection.close()
    return response.status, response_headers, payload


def _token_from_index(server: AppHTTPServer, *, host: str = "127.0.0.1") -> str:
    authority = _authority(server, host)
    status, headers, payload = _request(
        server,
        "GET",
        "/",
        headers={"Host": authority},
    )
    assert status == 200
    assert headers["cache-control"] == "no-store"
    match = re.search(
        rb'<meta name="e3-request-token" content="([A-Za-z0-9_-]+)">',
        payload,
    )
    assert match is not None
    return match.group(1).decode("ascii")


def _authorized_headers(
    server: AppHTTPServer,
    token: str,
    *,
    host: str = "127.0.0.1",
) -> dict[str, str]:
    authority = _authority(server, host)
    return {
        "Host": authority,
        "Origin": f"http://{authority}",
        "Content-Type": "application/json; charset=utf-8",
        "X-E3-Request-Token": token,
    }


def test_app_shell_delivers_an_uncached_process_token_only_for_allowed_host(
    http_app,
) -> None:
    server, _context = http_app
    token = _token_from_index(server)
    assert len(token) >= 40

    status, headers, payload = _request(server, "GET", "/api/status")
    assert status == 200
    assert token.encode() not in payload
    assert "access-control-allow-origin" not in headers

    status, headers, payload = _request(
        server,
        "GET",
        "/",
        headers={"Host": f"attacker.example:{server.server_address[1]}"},
    )
    assert status == 421
    assert token.encode() not in payload
    assert "access-control-allow-origin" not in headers


def test_token_is_unpredictable_per_server_process(tmp_path: Path) -> None:
    context = _SpyContext(tmp_path)
    first = AppHTTPServer(("127.0.0.1", 0), context)  # type: ignore[arg-type]
    second = AppHTTPServer(("127.0.0.1", 0), context)  # type: ignore[arg-type]
    try:
        assert first._request_token != second._request_token
    finally:
        first.server_close()
        second.server_close()


def test_valid_same_origin_json_post_mutates_once(http_app) -> None:
    server, context = http_app
    token = _token_from_index(server)
    status, _headers, payload = _request(
        server,
        "POST",
        "/api/camera/synthetic-scene",
        body=json.dumps({"scene": "checkerboard"}),
        headers=_authorized_headers(server, token),
    )
    assert status == 200
    assert json.loads(payload)["ok"] is True
    assert context.mutations == [("synthetic_scene", "checkerboard")]


def test_localhost_authority_remains_usable(http_app) -> None:
    server, context = http_app
    token = _token_from_index(server, host="localhost")
    status, _headers, _payload = _request(
        server,
        "POST",
        "/api/camera/synthetic-scene",
        body="{}",
        headers=_authorized_headers(server, token, host="localhost"),
    )
    assert status == 200
    assert context.mutations == [("synthetic_scene", "bed")]


@pytest.mark.parametrize(
    ("header_changes", "expected_status"),
    [
        ({"X-E3-Request-Token": ""}, 403),
        ({"X-E3-Request-Token": "not-the-process-token"}, 403),
        ({"Origin": ""}, 403),
        ({"Origin": "https://attacker.example"}, 403),
        ({"Content-Type": "text/plain"}, 415),
        ({"Content-Type": "application/x-www-form-urlencoded"}, 415),
    ],
)
def test_rejected_post_has_zero_route_side_effects(
    http_app,
    header_changes: dict[str, str],
    expected_status: int,
) -> None:
    server, context = http_app
    token = _token_from_index(server)
    headers = _authorized_headers(server, token)
    headers.update(header_changes)
    status, _headers, _payload = _request(
        server,
        "POST",
        "/api/camera/synthetic-scene",
        body='{"scene":"checkerboard"}',
        headers=headers,
    )
    assert status == expected_status
    assert context.mutations == []


def test_dns_rebinding_host_is_rejected_before_route_dispatch(http_app) -> None:
    server, context = http_app
    token = _token_from_index(server)
    authority = f"attacker.example:{server.server_address[1]}"
    status, _headers, _payload = _request(
        server,
        "POST",
        "/api/camera/synthetic-scene",
        body='{"scene":"checkerboard"}',
        headers={
            "Host": authority,
            "Origin": f"http://{authority}",
            "Content-Type": "application/json",
            "X-E3-Request-Token": token,
        },
    )
    assert status == 421
    assert context.mutations == []


@pytest.mark.parametrize(
    ("header_name", "expected_status"),
    [("Host", 421), ("Origin", 403), ("Content-Type", 415)],
)
def test_duplicate_security_header_is_rejected_before_route_dispatch(
    http_app,
    header_name: str,
    expected_status: int,
) -> None:
    server, context = http_app
    token = _token_from_index(server)
    authority = _authority(server)
    headers = [
        ("Host", authority),
        ("Origin", f"http://{authority}"),
        ("Content-Type", "application/json"),
        ("X-E3-Request-Token", token),
    ]
    duplicate_value = {
        "Host": authority,
        "Origin": f"http://{authority}",
        "Content-Type": "application/json",
    }[header_name]
    headers.append((header_name, duplicate_value))
    status, _response_headers, _payload = _request_with_header_lines(
        server,
        "POST",
        "/api/camera/synthetic-scene",
        headers,
        body=b'\x7b"scene":"checkerboard"\x7d',
    )
    assert status == expected_status
    assert context.mutations == []


def test_missing_host_is_rejected_before_route_dispatch(http_app) -> None:
    server, context = http_app
    status, _headers, _payload = _request_with_header_lines(
        server,
        "POST",
        "/api/camera/synthetic-scene",
        [],
        body=b"{}",
    )
    assert status == 421
    assert context.mutations == []


def test_remote_client_is_rejected_even_when_legacy_remote_flag_is_true(
    http_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, context = http_app
    token = _token_from_index(server)
    monkeypatch.setattr(AppRequestHandler, "_is_local_client", lambda _self: False)
    status, _headers, _payload = _request(
        server,
        "POST",
        "/api/camera/synthetic-scene",
        body="{}",
        headers=_authorized_headers(server, token),
    )
    assert status == 403
    assert context.settings.app.allow_remote_control is True
    assert context.mutations == []


def test_remote_client_cannot_retrieve_the_browser_token(
    http_app,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server, context = http_app
    token = _token_from_index(server)
    monkeypatch.setattr(AppRequestHandler, "_is_local_client", lambda _self: False)
    status, _headers, payload = _request(server, "GET", "/")
    assert status == 403
    assert token.encode() not in payload
    assert context.mutations == []

    status, _headers, _payload = _request(server, "GET", "/api/health")
    assert status == 403


@pytest.mark.parametrize("path", _MUTATING_ROUTES)
def test_every_mutating_route_requires_the_process_token(http_app, path: str) -> None:
    server, context = http_app
    headers = _authorized_headers(server, "")
    status, _headers, _payload = _request(
        server,
        "POST",
        path,
        body="{}",
        headers=headers,
    )
    assert status == 403
    assert context.mutations == []


@pytest.mark.parametrize("path", _MUTATING_ROUTES)
def test_every_mutating_route_requires_json_media_type(http_app, path: str) -> None:
    server, context = http_app
    token = _token_from_index(server)
    headers = _authorized_headers(server, token)
    headers["Content-Type"] = "text/plain"
    status, _headers, _payload = _request(
        server,
        "POST",
        path,
        body="{}",
        headers=headers,
    )
    assert status == 415
    assert context.mutations == []


def test_invalid_json_is_rejected_without_route_side_effects(http_app) -> None:
    server, context = http_app
    token = _token_from_index(server)
    status, _headers, _payload = _request(
        server,
        "POST",
        "/api/camera/synthetic-scene",
        body="{",
        headers=_authorized_headers(server, token),
    )
    assert status == 400
    assert context.mutations == []


@pytest.mark.parametrize(
    "body",
    [
        '{"scene":"bed","scene":"checkerboard"}',
        '{"scene":NaN}',
        '{"scene":Infinity}',
        '{"scene":-Infinity}',
    ],
)
def test_ambiguous_or_nonstandard_json_is_rejected_without_side_effects(
    http_app,
    body: str,
) -> None:
    server, context = http_app
    token = _token_from_index(server)

    status, _headers, _payload = _request(
        server,
        "POST",
        "/api/camera/synthetic-scene",
        body=body,
        headers=_authorized_headers(server, token),
    )

    assert status == 400
    assert context.mutations == []


@pytest.mark.parametrize(
    "extra_headers",
    [
        [("Content-Length", "0"), ("Content-Length", "0")],
        [("Content-Length", "+0")],
        [("Content-Length", "0, 0")],
        [("Transfer-Encoding", "chunked")],
    ],
)
def test_ambiguous_or_unsupported_request_framing_is_rejected(
    http_app,
    extra_headers: list[tuple[str, str]],
) -> None:
    server, context = http_app
    token = _token_from_index(server)
    authority = _authority(server)
    status, _headers, _payload = _request_with_header_lines(
        server,
        "POST",
        "/api/camera/synthetic-scene",
        [
            ("Host", authority),
            ("Origin", f"http://{authority}"),
            ("Content-Type", "application/json"),
            ("X-E3-Request-Token", token),
            *extra_headers,
        ],
    )

    assert status == 400
    assert context.mutations == []


@pytest.mark.parametrize(
    "filename",
    [
        "notes.txt",
        ".hidden.gcode",
        'quoted%22name.gcode',
        "backslash%5Cname.gcode",
        f"{'a' * 190}.gcode",
    ],
)
def test_generated_download_rejects_non_application_filenames(
    http_app,
    filename: str,
) -> None:
    server, _context = http_app

    status, headers, payload = _request(
        server,
        "GET",
        f"/api/generated/{filename}",
    )

    assert status == 403
    assert "content-disposition" not in headers
    assert b"private" not in payload


def test_generated_download_serves_a_safe_gcode_filename(http_app) -> None:
    server, context = http_app
    generated = context.settings.app.data_dir / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    path = generated / "design-20260809-010203-00000001-abcdef.gcode"
    path.write_text("G21\nG90\nM5\n", encoding="utf-8")

    status, headers, payload = _request(
        server,
        "GET",
        f"/api/generated/{path.name}",
    )

    assert status == 200
    assert headers["content-disposition"] == f'attachment; filename="{path.name}"'
    assert payload == path.read_bytes()


def test_generated_download_does_not_follow_symlinks(http_app, tmp_path: Path) -> None:
    server, context = http_app
    generated = context.settings.app.data_dir / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    private = tmp_path / "private.txt"
    private.write_bytes(b"private controller data")
    link = generated / "published.gcode"
    try:
        link.symlink_to(private)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")

    status, headers, payload = _request(
        server,
        "GET",
        f"/api/generated/{link.name}",
    )

    assert status == 403
    assert "content-disposition" not in headers
    assert private.read_bytes() not in payload


def test_generated_download_rejects_non_regular_targets(http_app) -> None:
    server, context = http_app
    generated = context.settings.app.data_dir / "generated"
    generated.mkdir(parents=True, exist_ok=True)
    target = generated / "directory.gcode"
    target.mkdir()

    status, headers, _payload = _request(
        server,
        "GET",
        f"/api/generated/{target.name}",
    )

    assert status == 403
    assert "content-disposition" not in headers


def test_arm_requires_gcode_before_preflight_or_arming(http_app) -> None:
    server, context = http_app
    token = _token_from_index(server)
    status, _headers, payload = _request(
        server,
        "POST",
        "/api/machine/arm",
        body=json.dumps({"phrase": "ARM LASER"}),
        headers=_authorized_headers(server, token),
    )
    assert status == 400
    assert "G-code is required" in json.loads(payload)["error"]
    assert context.mutations == [("disarm", None)]


def test_arm_preflights_then_binds_authorization_to_that_program(http_app) -> None:
    server, context = http_app
    token = _token_from_index(server)
    gcode = "G21\nG90\nM5"
    status, _headers, payload = _request(
        server,
        "POST",
        "/api/machine/arm",
        body=json.dumps({"phrase": "ARM LASER", "gcode": gcode}),
        headers=_authorized_headers(server, token),
    )
    assert status == 200
    assert json.loads(payload) == {"ok": True, "armed_until": 1234.5}
    assert context.mutations == [
        ("preflight_program", gcode),
        ("arm_program", ("ARM LASER", "validated-program")),
    ]


def test_stale_arm_request_cannot_disarm_a_new_generation(http_app) -> None:
    server, context = http_app
    entered = threading.Event()
    release = threading.Event()

    class PausedArmMachine(_SpyMachine):
        def __init__(self, mutations: list[tuple[str, Any]]) -> None:
            super().__init__(mutations)
            self.generation = 0
            self.scope_generation = threading.local()
            self.fresh_grant = False

        def operation_generation(self) -> int:
            return self.generation

        @contextmanager
        def operation_scope(self, generation: int):
            self.scope_generation.value = generation
            try:
                yield
            finally:
                del self.scope_generation.value

        def preflight_program(self, gcode: str) -> SimpleNamespace:
            program = super().preflight_program(gcode)
            entered.set()
            assert release.wait(timeout=2.0)
            return program

        def arm_program(self, phrase: str, program: SimpleNamespace) -> float:
            del phrase, program
            if self.scope_generation.value != self.generation:
                raise MachineError("Arming was cancelled by software STOP")
            raise AssertionError("Stale request reached authorization")

        def disarm(self) -> None:
            self.fresh_grant = False
            super().disarm()

    machine = PausedArmMachine(context.mutations)
    context.machine = machine
    token = _token_from_index(server)
    result: list[tuple[int, dict[str, str], str]] = []

    def request_arm() -> None:
        result.append(
            _request(
                server,
                "POST",
                "/api/machine/arm",
                body=json.dumps(
                    {
                        "phrase": "ARM LASER",
                        "gcode": "G21\nG90\nM5",
                    }
                ),
                headers=_authorized_headers(server, token),
            )
        )

    worker = threading.Thread(target=request_arm, daemon=True)
    worker.start()
    assert entered.wait(timeout=1.0)
    machine.generation += 1
    machine.fresh_grant = True
    release.set()
    worker.join(timeout=2.0)

    assert result and result[0][0] != 200
    assert machine.fresh_grant
    assert ("disarm", None) not in context.mutations


def test_preflight_never_grants_cross_origin_access(http_app) -> None:
    server, context = http_app
    authority = _authority(server)
    status, headers, _payload = _request(
        server,
        "OPTIONS",
        "/api/machine/run",
        headers={
            "Origin": "https://attacker.example",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type,x-e3-request-token",
        },
    )
    assert status == 403
    assert "access-control-allow-origin" not in headers
    assert context.mutations == []

    status, headers, _payload = _request(
        server,
        "OPTIONS",
        "/api/machine/run",
        headers={
            "Origin": f"http://{authority}",
            "Access-Control-Request-Method": "POST",
        },
    )
    assert status == 204
    assert "access-control-allow-origin" not in headers


def test_frontend_reads_the_injected_token_and_sends_it_on_posts() -> None:
    web_root = Path(__file__).resolve().parents[1] / "laser_aligner" / "web"
    index = (web_root / "index.html").read_text(encoding="utf-8")
    script = (web_root / "app.js").read_text(encoding="utf-8")
    assert 'name="e3-request-token" content="__E3_REQUEST_TOKEN__"' in index
    assert 'meta[name="e3-request-token"]' in script
    assert "options.headers['X-E3-Request-Token'] = REQUEST_TOKEN" in script
    assert "credentials: 'same-origin'" in script
    assert "gcode: state.lastGcode" in script
    assert "Generate or load G-code before arming." in script
