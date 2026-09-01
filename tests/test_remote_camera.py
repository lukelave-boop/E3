from __future__ import annotations

import errno
import socket
import threading
import time
from collections.abc import Mapping
from dataclasses import asdict
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from laser_aligner.app import AppContext
from laser_aligner.camera import bridge as camera_bridge
from laser_aligner.camera import remote as camera_remote
from laser_aligner.camera.bridge import CameraBridgeServer
from laser_aligner.camera.controls import ControlResult
from laser_aligner.camera.remote import (
    RemoteCameraService,
    _status_probe_delay,
)
from laser_aligner.camera.remote_protocol import (
    authenticate_camera_server,
    receive_packet,
)
from laser_aligner.camera.service import CameraStatus, CompressedCameraFrame, FrameBurst
from laser_aligner.config import CameraSettings, PrecisionCaptureSettings
from laser_aligner.errors import CameraError


class FakeCamera:
    def __init__(self) -> None:
        self.settings = CameraSettings(width=64, height=48, fps=15)
        self.started = False
        self.sequence = 10
        self.generation = 3
        self.frame = np.zeros((48, 64, 3), dtype=np.uint8)
        cv2.circle(self.frame, (32, 24), 10, (255, 255, 255), -1)
        self.controls = ControlResult({}, {}, {})
        self.snapshot_after_requests: list[int] = []

    def _current_generation(self) -> int:
        return self.generation

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False
        self.generation += 1

    def restart(self) -> None:
        self.started = True
        self.generation += 1

    def status(self) -> CameraStatus:
        return CameraStatus(
            connected=self.started,
            device="/dev/video-test",
            width=64 if self.started else 0,
            height=48 if self.started else 0,
            fps=15.0,
            frames_read=self.sequence,
            last_error=None,
            frame_age_seconds=0.01,
            negotiated_fps=30.0,
        )

    def frame_sequence(self) -> int:
        return self.sequence

    def snapshot(self) -> np.ndarray:
        return self.frame.copy()

    def snapshot_after(self, sequence: int, timeout: float = 6.0) -> np.ndarray:
        del timeout
        if not self.started:
            raise CameraError("Camera is not connected")
        self.snapshot_after_requests.append(sequence)
        self.sequence = max(self.sequence + 5, sequence + 1)
        return self.frame.copy()

    def jpeg(self, quality: int = 90) -> bytes:
        ok, encoded = cv2.imencode(
            ".jpg",
            self.frame,
            [cv2.IMWRITE_JPEG_QUALITY, quality],
        )
        assert ok
        return encoded.tobytes()

    def apply_controls(
        self,
        controls: Mapping[str, int | bool],
        timeout_seconds: float = 5.0,
    ) -> ControlResult:
        del timeout_seconds
        return ControlResult(
            dict(controls),
            dict(controls),
            {},
            verified={key: int(value) for key, value in controls.items()},
        )

    def apply_controls_and_snapshot(
        self,
        controls: Mapping[str, int | bool],
        settle_seconds: float = 0.35,
        timeout_seconds: float = 2.0,
    ) -> tuple[ControlResult, np.ndarray]:
        del settle_seconds
        return self.apply_controls(controls, timeout_seconds), self.frame.copy()

    def capture_burst(
        self,
        profile: PrecisionCaptureSettings,
        reapply_controls: bool = True,
        score_frames: bool = False,
    ) -> FrameBurst:
        del reapply_controls, score_frames
        frames: list[np.ndarray] = []
        sequences: list[int] = []
        for _ in range(profile.sample_frames):
            self.sequence += 1
            frames.append(self.frame.copy())
            sequences.append(self.sequence)
        return FrameBurst(
            frames=tuple(frames),
            sequence_numbers=tuple(sequences),
            discarded_frames=profile.discard_frames,
            settle_seconds=profile.settle_seconds,
            elapsed_seconds=0.1,
            sharpness_scores=(),
            controls=self.controls,
            timeout_seconds=profile.timeout_seconds,
            observed_fps=15.0,
            negotiated_fps=15.0,
            camera_generation=self.generation,
        )


def free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return int(port)


class BlockingCameraRequestServer:
    """Authenticate camera requests, then intentionally never answer them."""

    def __init__(self, token: str, *, expected_requests: int = 1) -> None:
        self.token = token
        self.expected_requests = expected_requests
        self.requests: list[dict[str, object]] = []
        self._lock = threading.Lock()
        self._requests_ready = threading.Event()
        self._clients_closed = threading.Event()
        self._stop = threading.Event()
        self._clients: list[socket.socket] = []
        self._handlers: list[threading.Thread] = []
        self._closed_clients = 0
        self._listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._listener.bind(("127.0.0.1", 0))
        self.port = int(self._listener.getsockname()[1])
        self._listener.listen()
        self._listener.settimeout(0.05)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _address = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._lock:
                self._clients.append(client)
            handler = threading.Thread(
                target=self._handle_client,
                args=(client,),
                daemon=True,
            )
            self._handlers.append(handler)
            handler.start()

    def _handle_client(self, client: socket.socket) -> None:
        client_closed = False
        try:
            if not authenticate_camera_server(client, self.token):
                return
            header, blobs = receive_packet(client)
            assert blobs == ()
            with self._lock:
                self.requests.append(header)
                if len(self.requests) >= self.expected_requests:
                    self._requests_ready.set()
            client.settimeout(0.05)
            while not self._stop.is_set():
                try:
                    if not client.recv(1):
                        client_closed = True
                        return
                except TimeoutError:
                    continue
        except (CameraError, OSError):
            client_closed = not self._stop.is_set()
        finally:
            if client_closed:
                with self._lock:
                    self._closed_clients += 1
                    if self._closed_clients >= self.expected_requests:
                        self._clients_closed.set()
            try:
                client.close()
            except OSError:
                pass

    def wait_for_requests(self, timeout: float = 1.0) -> bool:
        return self._requests_ready.wait(timeout)

    def wait_for_client_closes(self, timeout: float = 1.0) -> bool:
        return self._clients_closed.wait(timeout)

    def close(self) -> None:
        self._stop.set()
        try:
            self._listener.close()
        except OSError:
            pass
        with self._lock:
            clients = tuple(self._clients)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                client.close()
            except OSError:
                pass
        self._thread.join(timeout=1.0)
        for handler in self._handlers:
            handler.join(timeout=1.0)


def _run_remote_operation(remote: RemoteCameraService, operation: str) -> object:
    if operation == "snapshot_after":
        return remote.snapshot_after(0, timeout=36.0)
    if operation == "capture_burst":
        return remote.capture_burst(
            PrecisionCaptureSettings(
                settle_seconds=0.0,
                discard_frames=0,
                sample_frames=1,
                timeout_seconds=36.0,
                minimum_valid_frames=1,
                consensus_frames=1,
            )
        )
    if operation == "apply_controls_and_snapshot":
        return remote.apply_controls_and_snapshot(
            {"focus_absolute": 40},
            settle_seconds=0.0,
            timeout_seconds=36.0,
        )
    raise AssertionError(f"Unknown test operation: {operation}")


@pytest.mark.parametrize(
    "operation",
    ("snapshot_after", "capture_burst", "apply_controls_and_snapshot"),
)
def test_remote_camera_stop_interrupts_blocked_operation(
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    token = "camera-cancel-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    server = BlockingCameraRequestServer(token)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{server.port}")
    )
    errors: list[Exception] = []

    def run() -> None:
        try:
            _run_remote_operation(remote, operation)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert server.wait_for_requests()
        started = time.monotonic()
        remote.stop(deadline=started + 1.0)
        worker.join(timeout=1.0)
        elapsed = time.monotonic() - started

        assert not worker.is_alive()
        assert elapsed < 1.0
        assert len(errors) == 1
        assert isinstance(errors[0], CameraError)
        assert "cancelled during shutdown" in str(errors[0])
        assert server.wait_for_client_closes()
        assert server.requests[0]["action"] == operation
        if operation == "snapshot_after":
            assert server.requests[0]["timeout"] == 36.0
        elif operation == "capture_burst":
            profile = server.requests[0]["profile"]
            assert isinstance(profile, dict)
            assert profile["timeout_seconds"] == 36.0
        else:
            assert server.requests[0]["timeout"] == 36.0

        # Shutdown is idempotent even after the worker owns no socket.
        remote.stop(deadline=time.monotonic())
    finally:
        remote.cancel_pending_requests()
        worker.join(timeout=1.0)
        server.close()


def test_combined_blocked_camera_and_unreachable_machine_share_exit_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "combined-shutdown-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    server = BlockingCameraRequestServer(token)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{server.port}")
    )
    errors: list[Exception] = []

    class UnreachableMachine:
        def __init__(self) -> None:
            self.deadlines: list[float] = []

        def shutdown(self, *, deadline: float) -> None:
            self.deadlines.append(deadline)
            # Model the remote facade consuming its complete shutdown-only RPC
            # allowance before giving up and detaching.
            time.sleep(min(0.15, max(0.0, deadline - time.monotonic())))

    def run_camera_request() -> None:
        try:
            remote.snapshot_after(0, timeout=36.0)
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_camera_request)
    worker.start()
    machine = UnreachableMachine()
    context = AppContext.__new__(AppContext)
    context.machine = machine
    context.camera = remote
    try:
        assert server.wait_for_requests()
        started = time.monotonic()
        deadline = started + 4.0

        context.stop(deadline=deadline)
        worker.join(timeout=1.0)

        assert time.monotonic() - started < 1.0
        assert machine.deadlines == [deadline]
        assert not worker.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], CameraError)
        assert "cancelled during shutdown" in str(errors[0])
        assert server.wait_for_client_closes()
    finally:
        remote.cancel_pending_requests()
        worker.join(timeout=1.0)
        server.close()


def test_cancel_pending_requests_interrupts_all_concurrent_camera_sockets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "camera-concurrent-cancel-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    operations = (
        "snapshot_after",
        "capture_burst",
        "apply_controls_and_snapshot",
    )
    server = BlockingCameraRequestServer(token, expected_requests=len(operations))
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{server.port}")
    )
    errors: list[Exception] = []

    def run(operation: str) -> None:
        try:
            _run_remote_operation(remote, operation)
        except Exception as exc:
            errors.append(exc)

    workers = [threading.Thread(target=run, args=(operation,)) for operation in operations]
    for worker in workers:
        worker.start()
    try:
        assert server.wait_for_requests()
        started = time.monotonic()
        remote.cancel_pending_requests()
        for worker in workers:
            worker.join(timeout=1.0)

        assert time.monotonic() - started < 1.0
        assert all(not worker.is_alive() for worker in workers)
        assert len(errors) == len(operations)
        assert all(
            isinstance(exc, CameraError) and "cancelled during shutdown" in str(exc)
            for exc in errors
        )
        assert server.wait_for_client_closes()
        assert {request["action"] for request in server.requests} == set(operations)
    finally:
        remote.stop(deadline=time.monotonic() + 1.0)
        for worker in workers:
            worker.join(timeout=1.0)
        server.close()


def test_cancel_before_socket_creation_fails_without_opening_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "E3_BRIDGE_TOKEN",
        "camera-preconnect-cancel-token-with-plenty-entropy",
    )
    remote = RemoteCameraService(
        CameraSettings(device="e3camera://127.0.0.1:65534")
    )
    remote.cancel_pending_requests()
    monkeypatch.setattr(
        camera_remote.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: pytest.fail("cancelled request resolved an address"),
    )

    with pytest.raises(CameraError, match="cancelled during shutdown"):
        remote.snapshot_after(0, timeout=36.0)

    remote.stop(deadline=time.monotonic())


def test_cancel_during_address_resolution_releases_request_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "E3_BRIDGE_TOKEN",
        "camera-resolution-cancel-token-with-plenty-entropy",
    )
    remote = RemoteCameraService(
        CameraSettings(device="e3camera://blocked-camera.test:65534")
    )
    entered = threading.Event()
    release = threading.Event()
    errors: list[Exception] = []

    def blocked_resolution(*_args, **_kwargs):
        entered.set()
        release.wait(2.0)
        return []

    def request() -> None:
        try:
            remote.snapshot_after(0, timeout=36.0)
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(camera_remote.socket, "getaddrinfo", blocked_resolution)
    worker = threading.Thread(target=request)
    worker.start()
    assert entered.wait(1.0)
    started = time.monotonic()
    try:
        remote.cancel_pending_requests(terminal=True)
        worker.join(timeout=1.0)

        assert time.monotonic() - started < 1.0
        assert not worker.is_alive()
        assert len(errors) == 1
        assert isinstance(errors[0], CameraError)
        assert "cancelled during shutdown" in str(errors[0])
    finally:
        release.set()


def test_terminal_shutdown_cannot_be_rearmed_by_an_entered_restart_worker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "E3_BRIDGE_TOKEN",
        "camera-terminal-restart-token-with-plenty-entropy",
    )
    remote = RemoteCameraService(
        CameraSettings(device="e3camera://127.0.0.1:65534")
    )
    entered_rearm = threading.Event()
    resume_rearm = threading.Event()
    original_rearm = remote._rearm_network_requests
    errors: list[Exception] = []

    def delayed_rearm() -> None:
        entered_rearm.set()
        assert resume_rearm.wait(1.0)
        original_rearm()

    def restart() -> None:
        try:
            remote.restart()
        except Exception as exc:
            errors.append(exc)

    monkeypatch.setattr(remote, "_rearm_network_requests", delayed_rearm)
    monkeypatch.setattr(
        camera_remote.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: pytest.fail(
            "terminally cancelled restart opened a network request"
        ),
    )
    worker = threading.Thread(target=restart)
    worker.start()
    assert entered_rearm.wait(1.0)

    remote.cancel_pending_requests(terminal=True)
    resume_rearm.set()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], CameraError)
    assert "cancelled during shutdown" in str(errors[0])
    with pytest.raises(CameraError, match="cancelled during shutdown"):
        remote.start()


def test_cancel_immediately_after_socket_creation_closes_unregistered_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "E3_BRIDGE_TOKEN",
        "camera-registration-race-token-with-plenty-entropy",
    )
    entered_registration = threading.Event()
    finish_registration = threading.Event()

    class CreatedSocket:
        def __init__(self, *_args, **_kwargs) -> None:
            self.closed = 0

        def shutdown(self, _how: int) -> None:
            return

        def close(self) -> None:
            self.closed += 1
            if self.closed > 1:
                raise OSError("socket was already closed")

    created = CreatedSocket()
    monkeypatch.setattr(
        camera_remote.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 1))],
    )
    monkeypatch.setattr(camera_remote.socket, "socket", lambda *_args: created)
    remote = RemoteCameraService(
        CameraSettings(device="e3camera://127.0.0.1:65534")
    )
    original_register = remote._register_socket

    def register_after_cancel(sock: socket.socket, generation: int) -> None:
        entered_registration.set()
        assert finish_registration.wait(1.0)
        original_register(sock, generation)

    monkeypatch.setattr(remote, "_register_socket", register_after_cancel)
    errors: list[Exception] = []

    def run() -> None:
        try:
            remote.snapshot()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert entered_registration.wait(1.0)
    remote.cancel_pending_requests()
    finish_registration.set()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], CameraError)
    assert "cancelled during shutdown" in str(errors[0])
    assert created.closed >= 1


def test_cancel_during_connect_interrupts_socket_and_hides_double_close_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "E3_BRIDGE_TOKEN",
        "camera-connect-race-token-with-plenty-entropy",
    )
    connect_started = threading.Event()
    socket_closed = threading.Event()

    class ConnectingSocket:
        def __init__(self, *_args, **_kwargs) -> None:
            self.close_calls = 0

        def setblocking(self, _enabled: bool) -> None:
            return

        def connect_ex(self, _address: object) -> int:
            connect_started.set()
            return errno.EWOULDBLOCK

        def shutdown(self, _how: int) -> None:
            socket_closed.set()

        def close(self) -> None:
            self.close_calls += 1
            if self.close_calls > 1:
                raise OSError("socket was already closed")

    connecting = ConnectingSocket()
    monkeypatch.setattr(
        camera_remote.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 1))],
    )
    monkeypatch.setattr(camera_remote.socket, "socket", lambda *_args: connecting)

    def blocked_select(*_args, **_kwargs):
        assert socket_closed.wait(1.0)
        raise ValueError("closed socket")

    monkeypatch.setattr(camera_remote.select, "select", blocked_select)
    remote = RemoteCameraService(
        CameraSettings(device="e3camera://127.0.0.1:65534")
    )
    errors: list[Exception] = []

    def run() -> None:
        try:
            remote.snapshot()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert connect_started.wait(1.0)
    remote.cancel_pending_requests()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], CameraError)
    assert "cancelled during shutdown" in str(errors[0])
    assert connecting.close_calls >= 1


def test_cancel_during_send_interrupts_socket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "E3_BRIDGE_TOKEN",
        "camera-send-race-token-with-plenty-entropy",
    )
    send_started = threading.Event()
    socket_closed = threading.Event()

    class SendingSocket:
        def __init__(self, *_args, **_kwargs) -> None:
            self.close_calls = 0

        def setblocking(self, _enabled: bool) -> None:
            return

        def connect_ex(self, _address: object) -> int:
            return 0

        def settimeout(self, _timeout: float) -> None:
            return

        def sendall(self, _payload: bytes) -> None:
            send_started.set()
            assert socket_closed.wait(1.0)
            raise OSError("socket closed during send")

        def shutdown(self, _how: int) -> None:
            socket_closed.set()

        def close(self) -> None:
            self.close_calls += 1
            if self.close_calls > 1:
                raise OSError("socket was already closed")

    sending = SendingSocket()
    monkeypatch.setattr(
        camera_remote.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 1))],
    )
    monkeypatch.setattr(camera_remote.socket, "socket", lambda *_args: sending)
    monkeypatch.setattr(
        camera_remote,
        "authenticate_camera_client",
        lambda *_args, **_kwargs: None,
    )
    remote = RemoteCameraService(
        CameraSettings(device="e3camera://127.0.0.1:65534")
    )
    errors: list[Exception] = []

    def run() -> None:
        try:
            remote.snapshot()
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    assert send_started.wait(1.0)
    remote.cancel_pending_requests()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert len(errors) == 1
    assert isinstance(errors[0], CameraError)
    assert "cancelled during shutdown" in str(errors[0])
    assert sending.close_calls >= 1


def test_remote_camera_round_trip(monkeypatch) -> None:
    token = "camera-bridge-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    settings = CameraSettings(
        device=f"e3camera://127.0.0.1:{port}",
        width=64,
        height=48,
        fps=15,
    )
    remote = RemoteCameraService(settings)
    try:
        remote.start()
        status = remote.status()
        assert status.connected and status.device == "/dev/video-test"
        frame = remote.snapshot()
        assert frame.shape == (48, 64, 3)
        profile = PrecisionCaptureSettings(
            sample_frames=3,
            discard_frames=1,
            minimum_valid_frames=1,
            consensus_frames=1,
        )
        burst = remote.capture_burst(profile)
        assert len(burst.frames) == 3
        assert len(burst.sharpness_scores) == 3
        remote.ensure_burst_current(burst)
        result = remote.apply_controls({"focus_absolute": 40})
        assert result.verified["focus_absolute"] == 40
        remote.stop(deadline=time.monotonic() + 1.0)
        with pytest.raises(CameraError, match="cancelled during shutdown"):
            remote.snapshot()
        assert camera.started is True
        remote.start()
        assert remote.snapshot().shape == (48, 64, 3)
    finally:
        remote.stop()
        # Desktop teardown releases only the client; the Pi owns the physical camera.
        assert camera.started is True
        server.stop()
        thread.join(timeout=1)
        assert camera.started is False


def test_remote_camera_rejects_mismatched_capture_profile(monkeypatch) -> None:
    token = "camera-bridge-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    assert server._listener is not None
    settings = CameraSettings(
        device=f"e3camera://127.0.0.1:{port}",
        width=1920,
        height=1080,
        fps=15,
    )
    remote = RemoteCameraService(settings)
    try:
        from laser_aligner.errors import CameraError

        with pytest.raises(CameraError, match="does not match"):
            remote.start()
    finally:
        remote.stop()
        server.stop()
        thread.join(timeout=1)


def test_persistent_raw_monitor_authenticates_frames_and_drops_stale_sequences(
    monkeypatch,
) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    stop = threading.Event()
    try:
        stream = remote.monitor_frames(fps=15, stop_event=stop)
        frames = [next(stream) for _ in range(3)]
        assert all(item["image"].shape == (1080, 1920, 3) for item in frames)
        sequences = [item["sequence"] for item in frames]
        assert sequences == sorted(sequences)
        assert all(
            b - a >= 5 for a, b in zip(sequences, sequences[1:], strict=False)
        )
        assert len(camera.snapshot_after_requests) == 3
        # The persistent stream authenticates once rather than creating one
        # request connection per delivered frame.
        assert server._slots._value == 7
        stop.set()
        stream.close()
        deadline = time.time() + 1
        while server._monitor_slots._value != 2 and time.time() < deadline:
            time.sleep(0.01)
        assert server._monitor_slots._value == 2
        assert server._slots._value == 8
    finally:
        stop.set()
        server.stop()
        thread.join(timeout=1)


def test_raw_monitor_enforces_its_stricter_per_frame_bound(monkeypatch) -> None:
    monkeypatch.setattr(
        camera_bridge,
        "_encode_frame",
        lambda _frame, _quality: b"x" * (4 * 1024 * 1024 + 1),
    )
    with pytest.raises(CameraError, match="bounded frame limit"):
        camera_bridge._encode_monitor_frame(np.zeros((1, 1, 3), dtype=np.uint8), 78)


def test_direct_monitor_forwards_source_bytes_without_resize_or_encode(monkeypatch) -> None:
    camera = FakeCamera()
    camera.settings.width = 1920
    camera.settings.height = 1080
    source_jpeg = b"\xff\xd8exact-camera-packet\xff\xd9"

    def direct(sequence: int, *, timeout: float) -> CompressedCameraFrame:
        del sequence, timeout
        return CompressedCameraFrame(
            jpeg=source_jpeg,
            frame=camera.frame,
            sequence=44,
            generation=camera.generation,
            captured_monotonic=time.monotonic(),
            width=1920,
            height=1080,
        )

    camera.direct_mjpeg_after = direct  # type: ignore[attr-defined]
    monkeypatch.setattr(camera_bridge.cv2, "resize", lambda *_a, **_k: pytest.fail("resize called"))
    monkeypatch.setattr(camera_bridge, "_encode_monitor_frame", lambda *_a, **_k: pytest.fail("encode called"))

    metadata, jpeg = camera_bridge._monitor_frame(
        camera, sequence=10, width=1920, height=1080, quality=78, timeout=1.0
    )

    assert jpeg is source_jpeg
    assert metadata["source_mode"] == "direct_mjpeg"
    assert metadata["sequence"] == 44
    assert metadata["jpeg_bytes"] == len(source_jpeg)
    assert metadata["capture_fps"] == 15.0
    assert metadata["negotiated_fps"] == 30.0


def test_non_native_monitor_resolution_uses_transcoded_fallback() -> None:
    camera = FakeCamera()
    camera.settings.width = 1920
    camera.settings.height = 1080
    camera.started = True
    direct_calls: list[int] = []

    def direct(sequence: int, *, timeout: float) -> CompressedCameraFrame:
        del timeout
        direct_calls.append(sequence)
        raise AssertionError("non-native request must not try direct passthrough")

    camera.direct_mjpeg_after = direct  # type: ignore[attr-defined]
    metadata, jpeg = camera_bridge._monitor_frame(
        camera, sequence=10, width=1280, height=720, quality=78, timeout=1.0
    )

    assert direct_calls == []
    assert metadata["source_mode"] == "transcoded"
    assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")


def test_native_monitor_request_uses_720p_transcoded_fallback() -> None:
    camera = FakeCamera()
    camera.settings.width = 1920
    camera.settings.height = 1080
    camera.frame = np.zeros((1080, 1920, 3), dtype=np.uint8)
    camera.started = True

    metadata, jpeg = camera_bridge._monitor_frame(
        camera, sequence=10, width=1920, height=1080, quality=78, timeout=1.0
    )

    assert metadata["source_mode"] == "transcoded"
    assert (metadata["width"], metadata["height"]) == (1280, 720)
    assert jpeg.startswith(b"\xff\xd8") and jpeg.endswith(b"\xff\xd9")


def test_remote_monitor_timestamps_receipt_before_jpeg_decode(monkeypatch) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    decode_started = False
    original_decode = camera_remote._decode_frame

    def decode_after_receipt(jpeg: bytes) -> np.ndarray:
        nonlocal decode_started
        decode_started = True
        return original_decode(jpeg)

    def monotonic() -> float:
        return 20.0 if decode_started else 10.0

    monkeypatch.setattr(camera_remote, "time", SimpleNamespace(monotonic=monotonic))
    monkeypatch.setattr(camera_remote, "_decode_frame", decode_after_receipt)
    stop = threading.Event()
    stream = remote.monitor_frames(fps=10, stop_event=stop)
    try:
        payload = next(stream)
        assert payload["received_monotonic"] == 10.0
        assert payload["capture_fps"] == 15.0
        assert payload["negotiated_fps"] == 30.0
    finally:
        stop.set()
        stream.close()
        server.stop()
        thread.join(timeout=1)


def test_encoded_monitor_path_does_not_eagerly_decode_with_opencv(monkeypatch) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    monkeypatch.setattr(
        camera_remote,
        "_decode_frame",
        lambda _jpeg: pytest.fail("encoded monitor eagerly decoded with OpenCV"),
    )
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    stop = threading.Event()
    stream = remote.monitor_jpeg_frames(fps=15, stop_event=stop)
    try:
        payload = next(stream)
        assert payload["jpeg"].startswith(b"\xff\xd8")
        assert (payload["width"], payload["height"]) == (1920, 1080)
        assert payload["jpeg_bytes"] == len(payload["jpeg"])
        assert payload["received_monotonic"] is not None
    finally:
        stop.set()
        stream.close()
        server.stop()
        thread.join(timeout=1)


def test_encoded_monitor_payload_rejects_bounds_and_dimension_mismatch() -> None:
    with pytest.raises(CameraError, match="bounded"):
        camera_remote._validated_monitor_payload(
            {"width": 1920, "height": 1080},
            [b"x" * (4 * 1024 * 1024 + 1)],
            requested_width=1920,
            requested_height=1080,
            requested_fps=15,
            received_monotonic=1.0,
        )

    ok, encoded = cv2.imencode(
        ".jpg", np.zeros((720, 1280, 3), dtype=np.uint8)
    )
    assert ok
    with pytest.raises(CameraError, match="JPEG dimensions"):
        camera_remote._validated_monitor_payload(
            {"width": 1920, "height": 1080, "source_mode": "direct_mjpeg"},
            [encoded.tobytes()],
            requested_width=1920,
            requested_height=1080,
            requested_fps=15,
            received_monotonic=1.0,
        )


def test_encoded_monitor_accepts_reported_720p_transcoded_fallback() -> None:
    ok, encoded = cv2.imencode(
        ".jpg", np.zeros((720, 1280, 3), dtype=np.uint8)
    )
    assert ok
    payload = camera_remote._validated_monitor_payload(
        {"width": 1280, "height": 720, "source_mode": "transcoded"},
        [encoded.tobytes()],
        requested_width=1920,
        requested_height=1080,
        requested_fps=15,
        received_monotonic=1.0,
    )

    assert (payload["width"], payload["height"]) == (1280, 720)
    assert payload["source_mode"] == "transcoded"


def test_raw_monitor_rejects_bad_authentication_and_invalid_profiles(monkeypatch) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    try:
        monkeypatch.setenv("E3_BRIDGE_TOKEN", "wrong-monitor-token-still-long-enough")
        with pytest.raises(Exception, match="rejected|closed"):
            next(remote.monitor_frames())
        monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
        with pytest.raises(Exception, match="FPS"):
            next(remote.monitor_frames(fps=12))
        with pytest.raises(Exception, match="resolution"):
            next(remote.monitor_frames(width=640, height=480))
    finally:
        server.stop()
        thread.join(timeout=1)


def test_precision_capture_remains_authoritative_during_raw_monitor(monkeypatch) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    stop = threading.Event()
    stream = remote.monitor_frames(fps=10, stop_event=stop)
    try:
        assert next(stream)["image"].shape == (1080, 1920, 3)
        burst = remote.capture_burst(
            PrecisionCaptureSettings(
                sample_frames=2,
                discard_frames=0,
                minimum_valid_frames=1,
                consensus_frames=1,
            )
        )
        assert len(burst.frames) == 2
        assert next(stream)["image"].shape == (1080, 1920, 3)
        assert camera.controls.requested == {}
    finally:
        stop.set()
        stream.close()
        server.stop()
        thread.join(timeout=1)


def test_raw_monitor_recovers_after_camera_stop_and_restart(monkeypatch) -> None:
    token = "camera-monitor-token-with-plenty-entropy"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    camera = FakeCamera()
    camera.started = True
    port = free_port()
    server = CameraBridgeServer(camera, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    deadline = time.time() + 1
    while server._listener is None and time.time() < deadline:
        time.sleep(0.01)
    remote = RemoteCameraService(
        CameraSettings(device=f"e3camera://127.0.0.1:{port}")
    )
    stop = threading.Event()
    stream = remote.monitor_frames(fps=15, stop_event=stop)
    try:
        first = next(stream)
        camera.stop()
        restart = threading.Timer(0.4, camera.restart)
        restart.start()
        recovered = next(stream)
        restart.join(timeout=1)
        assert recovered["sequence"] > first["sequence"]
        assert camera.started
    finally:
        stop.set()
        stream.close()
        server.stop()
        thread.join(timeout=1)


def _current_remote_status_payload() -> dict[str, object]:
    return {
        "connected": True,
        "device": "/dev/v4l/by-id/camera",
        "width": 1920,
        "height": 1080,
        "fps": 14.8,
        "frames_read": 321,
        "last_error": None,
        "frame_age_seconds": 0.025,
        "controls_verified": {"focus_absolute": 40},
        "controls_satisfied": {"focus_absolute": "exact"},
        "controls_critical_unverified": {},
        "negotiated_fps": 30.0,
        "operation": "idle",
        "monitor_source_mode": "direct_mjpeg",
    }


def _fetch_supplied_remote_status(
    monkeypatch: pytest.MonkeyPatch,
    raw: dict[str, object],
) -> CameraStatus:
    remote = RemoteCameraService(
        CameraSettings(device="e3camera://127.0.0.1:65534")
    )
    monkeypatch.setattr(
        remote,
        "_request",
        lambda *_args, **_kwargs: ({"status": raw}, ()),
    )
    return remote._fetch_status()


def test_remote_status_accepts_legacy_synthetic_false_without_mutating_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = _current_remote_status_payload()
    raw = {**current, "synthetic": False}
    original = dict(raw)

    status = _fetch_supplied_remote_status(monkeypatch, raw)

    assert asdict(status) == current
    assert raw == original
    assert raw["synthetic"] is False


@pytest.mark.parametrize(
    "synthetic",
    (True, 0, None, "false"),
    ids=("boolean-true", "integer-zero", "none", "string-false"),
)
def test_remote_status_rejects_nonlegacy_synthetic_values(
    monkeypatch: pytest.MonkeyPatch,
    synthetic: object,
) -> None:
    raw = {**_current_remote_status_payload(), "synthetic": synthetic}
    original = dict(raw)

    with pytest.raises(CameraError, match="invalid legacy synthetic status"):
        _fetch_supplied_remote_status(monkeypatch, raw)

    assert raw == original


def test_remote_status_accepts_current_payload_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = _current_remote_status_payload()
    original = dict(raw)

    status = _fetch_supplied_remote_status(monkeypatch, raw)

    assert asdict(status) == original
    assert raw == original


def test_remote_status_is_cached_and_does_not_touch_network(monkeypatch) -> None:
    remote = RemoteCameraService(
        CameraSettings(device="e3camera://127.0.0.1:65534")
    )

    monkeypatch.setattr(
        remote,
        "_request",
        lambda *_args, **_kwargs: pytest.fail(
            "status() attempted synchronous network I/O"
        ),
    )

    status = remote.status()

    assert not status.connected
    assert status.device == "e3camera://127.0.0.1:65534"
    assert status.frames_read == 0


def test_remote_status_probe_uses_bounded_exponential_backoff() -> None:
    assert [_status_probe_delay(value) for value in range(1, 8)] == [
        2.0,
        4.0,
        8.0,
        16.0,
        30.0,
        30.0,
        30.0,
    ]
