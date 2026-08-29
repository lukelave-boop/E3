from __future__ import annotations

import socket
import threading
import time
from typing import Any

import pytest

from laser_aligner.config import ZAxisSettings
from laser_aligner.errors import MachineError
from laser_aligner.z_axis.bridge import ZAxisBridgeServer
from laser_aligner.z_axis.remote import RemoteZAxisService


class StubZService:
    def __init__(self) -> None:
        self.connected = False
        self.probe_calls = 0
        self.stop_calls = 0
        self.operation = "idle"
        self.block_probe = False
        self.probe_active = threading.Event()
        self.stop_requested = threading.Event()

    def status(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "connected": self.connected,
            "state": "UNKNOWN",
            "z_known": False,
            "current_z_mm": None,
            "effective_safe_max_mm": 80.0,
            "operation": self.operation,
        }

    def connect(self) -> dict[str, Any]:
        self.connected = True
        return self.status()

    def invalidate_position(self, _reason: str, *, fault: bool = False) -> None:
        del fault

    def client_disconnected(self) -> None:
        pass

    def request_stop(self) -> None:
        self.stop_calls += 1
        self.stop_requested.set()

    def close(self) -> None:
        self.connected = False

    def test_probe(self) -> dict[str, Any]:
        self.probe_calls += 1
        if self.block_probe:
            self.operation = "probe_test"
            self.probe_active.set()
            if not self.stop_requested.wait(2.0):
                raise MachineError("Test probe did not receive disconnect STOP")
            self.operation = "fault"
            raise MachineError("Test probe interrupted")
        return {"passed": True, "z_min": "open"}


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _wait_for_listener(port: int) -> None:
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                return
        except OSError:
            time.sleep(0.01)
    raise AssertionError("Z bridge did not start")


def test_bridge_keeps_one_authenticated_z_client_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "z-bridge-test-token-with-32-characters"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    port = _free_port()
    stub = StubZService()
    server = ZAxisBridgeServer(stub, host="127.0.0.1", port=port, token=token)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _wait_for_listener(port)
    settings = ZAxisSettings(
        enabled=True,
        endpoint=f"e3z://127.0.0.1:{port}",
        startup_delay=0.0,
    )
    first = RemoteZAxisService(settings)
    second = RemoteZAxisService(settings)
    try:
        first.connect()
        assert first.test_probe()["passed"] is True
        assert first.test_probe()["passed"] is True
        assert stub.probe_calls == 2
        with pytest.raises(MachineError, match="busy"):
            second.connect()
        second.close()
        first.close()
        time.sleep(0.2)
        assert stub.stop_calls == 0
    finally:
        second.close()
        first.close()
        server.stop()
        thread.join(timeout=2.0)
    assert not thread.is_alive()


def test_bridge_disconnect_watchdog_stops_an_active_z_operation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "z-bridge-test-token-with-32-characters"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    port = _free_port()
    stub = StubZService()
    stub.block_probe = True
    server = ZAxisBridgeServer(stub, host="127.0.0.1", port=port, token=token)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    _wait_for_listener(port)
    client = RemoteZAxisService(
        ZAxisSettings(
            enabled=True,
            endpoint=f"e3z://127.0.0.1:{port}",
            startup_delay=0.0,
        )
    )
    request_errors: list[Exception] = []

    def run_probe() -> None:
        try:
            client.test_probe()
        except Exception as exc:
            request_errors.append(exc)

    try:
        client.connect()
        request_thread = threading.Thread(target=run_probe, daemon=True)
        request_thread.start()
        assert stub.probe_active.wait(1.0)

        client.request_stop()

        request_thread.join(timeout=2.0)
        assert not request_thread.is_alive()
        assert stub.stop_requested.wait(1.0)
        assert stub.stop_calls == 1
        assert request_errors
    finally:
        client.close()
        server.stop()
        server_thread.join(timeout=2.0)
    assert not server_thread.is_alive()
