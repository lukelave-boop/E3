from __future__ import annotations

import hashlib
import hmac
import socket
import threading
import time

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import MachineError
from laser_aligner.machine.controller_dialects import GRBL_DIALECT
from laser_aligner.machine.network_transport import (
    NetworkSerialTransport,
    _authenticate,
    parse_bridge_uri,
)
from laser_aligner.machine.service import MachineService


def test_parse_bridge_uri_defaults_port_and_rejects_embedded_credentials() -> None:
    target = parse_bridge_uri("e3bridge://e3-laser.local")
    assert target.host == "e3-laser.local"
    assert target.port == 8765

    with pytest.raises(MachineError, match="must not be embedded"):
        parse_bridge_uri("e3bridge://name:secret@e3-laser.local")


def test_authentication_uses_challenge_response_and_verifies_baudrate() -> None:
    client, server = socket.socketpair()
    token = "correct-horse-battery-staple-bridge-token"
    challenge = bytes(range(32))
    failure: list[BaseException] = []

    def serve() -> None:
        try:
            server.sendall(f"E3BRIDGE/1 CHALLENGE {challenge.hex()}\n".encode("ascii"))
            auth = b""
            while not auth.endswith(b"\n"):
                auth += server.recv(1024)
            fields = auth.decode("ascii").split()
            assert fields[:2] == ["E3BRIDGE/1", "AUTH"]
            expected = hmac.new(token.encode(), challenge, hashlib.sha256).hexdigest()
            assert fields[2] == expected
            server.sendall(b"E3BRIDGE/1 READY 115200\n")
        except BaseException as exc:
            failure.append(exc)
        finally:
            server.close()

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        _authenticate(client, token, 115200)
    finally:
        client.close()
        thread.join(timeout=1)
    assert not failure


def test_transport_exchanges_controller_lines_after_handshake(monkeypatch: pytest.MonkeyPatch) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    token = "bridge-token-with-enough-randomness-123"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    observed: list[bytes] = []

    def serve() -> None:
        conn, _ = listener.accept()
        with conn:
            challenge = b"x" * 32
            conn.sendall(f"E3BRIDGE/1 CHALLENGE {challenge.hex()}\n".encode("ascii"))
            auth = b""
            while not auth.endswith(b"\n"):
                auth += conn.recv(1024)
            digest = hmac.new(token.encode(), challenge, hashlib.sha256).hexdigest()
            assert auth.decode("ascii").strip() == f"E3BRIDGE/1 AUTH {digest}"
            conn.sendall(b"E3BRIDGE/1 READY 115200\n")
            observed.append(conn.recv(1024))
            conn.sendall(b"[VER:1.1h]\r\nok\r\n")
        listener.close()

    thread = threading.Thread(target=serve)
    thread.start()
    transport = NetworkSerialTransport(f"e3bridge://127.0.0.1:{port}", 115200)
    transport.open()
    try:
        transport.write_line("$I")
        assert transport.read_line(1.0) == "[VER:1.1h]"
        assert transport.read_line(1.0) == "ok"
    finally:
        transport.close()
        thread.join(timeout=1)
    assert observed == [b"$I\n"]


def test_machine_realtime_sampling_uses_existing_e3bridge_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    token = "bridge-token-with-enough-randomness-123"
    monkeypatch.setenv("E3_BRIDGE_TOKEN", token)
    observed: list[bytes] = []

    def serve() -> None:
        conn, _ = listener.accept()
        with conn:
            challenge = b"r" * 32
            conn.sendall(f"E3BRIDGE/1 CHALLENGE {challenge.hex()}\n".encode("ascii"))
            auth = b""
            while not auth.endswith(b"\n"):
                auth += conn.recv(1024)
            digest = hmac.new(token.encode(), challenge, hashlib.sha256).hexdigest()
            assert auth.decode("ascii").strip() == f"E3BRIDGE/1 AUTH {digest}"
            conn.sendall(b"E3BRIDGE/1 READY 115200\n")
            observed.append(conn.recv(1024))
            conn.sendall(b"<Idle|MPos:15,195,0|WPos:15,195,0|WCO:0,0,0>\r\n")
            time.sleep(0.1)
        listener.close()

    thread = threading.Thread(target=serve)
    thread.start()
    transport = NetworkSerialTransport(f"e3bridge://127.0.0.1:{port}", 115200)
    transport.open()
    machine = MachineService(
        MachineSettings(backend="serial", protocol="grbl"),
        LaserSettings(),
        hardware_enabled=True,
    )
    machine._transport = transport
    machine._connected = True
    transport.test_only_allow_legacy_input_synchronization = True
    machine._dialect = GRBL_DIALECT
    machine._protocol = "grbl"
    try:
        snapshot = machine.sample_realtime_position(timeout=1.0)
    finally:
        transport.close()
        thread.join(timeout=1)

    assert observed == [b"?"]
    assert snapshot["mpos_mm"][:2] == [15.0, 195.0]


def test_busy_bridge_is_reported_before_authentication() -> None:
    client, server = socket.socketpair()

    def serve() -> None:
        try:
            server.sendall(b"E3BRIDGE/1 BUSY\n")
        finally:
            server.close()

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        with pytest.raises(MachineError, match="already in use"):
            _authenticate(client, "x" * 32, 115200)
    finally:
        client.close()
        thread.join(timeout=1)


def test_transport_requires_secret_before_opening_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("E3_BRIDGE_TOKEN", raising=False)
    transport = NetworkSerialTransport("e3bridge://127.0.0.1:1", 115200)
    with pytest.raises(MachineError, match="E3_BRIDGE_TOKEN"):
        transport.open()
