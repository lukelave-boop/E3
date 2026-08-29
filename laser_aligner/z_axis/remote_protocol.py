from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import struct
from typing import Any

from ..errors import MachineError

_VERSION = "E3Z/1"
_TOKEN_ENV = "E3_BRIDGE_TOKEN"
_MIN_TOKEN_LENGTH = 24
_MAX_AUTH_LINE_BYTES = 1024
_MAX_PACKET_BYTES = 1_000_000


def z_token_from_environment() -> str:
    token = os.environ.get(_TOKEN_ENV, "")
    if len(token) < _MIN_TOKEN_LENGTH:
        raise MachineError(
            f"{_TOKEN_ENV} must be set to a secret of at least {_MIN_TOKEN_LENGTH} characters"
        )
    return token


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise MachineError("Remote S1 Pro Z connection closed unexpectedly")
        data.extend(chunk)
    return bytes(data)


def _read_ascii_line(sock: socket.socket) -> str:
    data = bytearray()
    while True:
        chunk = sock.recv(1)
        if not chunk:
            raise MachineError("Remote S1 Pro Z connection closed during authentication")
        if chunk in {b"\r", b"\n"}:
            if data:
                try:
                    return data.decode("ascii", errors="strict")
                except UnicodeError as exc:
                    raise MachineError("Remote S1 Pro Z authentication was not ASCII") from exc
            continue
        data.extend(chunk)
        if len(data) > _MAX_AUTH_LINE_BYTES:
            raise MachineError("Remote S1 Pro Z authentication line is too long")


def _send_ascii_line(sock: socket.socket, line: str) -> None:
    sock.sendall(line.rstrip("\r\n").encode("ascii") + b"\n")


def authenticate_z_client(sock: socket.socket, token: str) -> None:
    first = _read_ascii_line(sock).split()
    if len(first) != 3 or first[:2] != [_VERSION, "CHALLENGE"]:
        raise MachineError("Remote endpoint is not an E3 S1 Pro Z service")
    try:
        challenge = bytes.fromhex(first[2])
    except ValueError as exc:
        raise MachineError("Remote S1 Pro Z service sent an invalid challenge") from exc
    if len(challenge) != 32:
        raise MachineError("Remote S1 Pro Z service sent an invalid challenge")
    digest = hmac.new(token.encode("utf-8"), challenge, hashlib.sha256).hexdigest()
    _send_ascii_line(sock, f"{_VERSION} AUTH {digest}")
    result = _read_ascii_line(sock).split()
    if result != [_VERSION, "READY"]:
        raise MachineError("Remote S1 Pro Z service rejected authentication")


def authenticate_z_server(sock: socket.socket, token: str) -> bool:
    challenge = secrets.token_bytes(32)
    _send_ascii_line(sock, f"{_VERSION} CHALLENGE {challenge.hex()}")
    try:
        response = _read_ascii_line(sock).split()
    except MachineError:
        return False
    if len(response) != 3 or response[:2] != [_VERSION, "AUTH"]:
        _send_ascii_line(sock, f"{_VERSION} ERROR")
        return False
    expected = hmac.new(token.encode("utf-8"), challenge, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(response[2], expected):
        _send_ascii_line(sock, f"{_VERSION} ERROR")
        return False
    _send_ascii_line(sock, f"{_VERSION} READY")
    return True


def send_packet(sock: socket.socket, payload: dict[str, Any]) -> None:
    try:
        encoded = json.dumps(
            payload,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise MachineError(f"Could not serialize S1 Pro Z packet: {exc}") from exc
    if not 1 <= len(encoded) <= _MAX_PACKET_BYTES:
        raise MachineError("S1 Pro Z packet exceeds the bounded transfer limit")
    sock.sendall(struct.pack("!I", len(encoded)) + encoded)


def receive_packet(sock: socket.socket) -> dict[str, Any]:
    length = struct.unpack("!I", _recv_exact(sock, 4))[0]
    if not 1 <= length <= _MAX_PACKET_BYTES:
        raise MachineError("Remote S1 Pro Z packet has an invalid length")
    try:
        payload = json.loads(_recv_exact(sock, length).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MachineError("Remote S1 Pro Z packet is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise MachineError("Remote S1 Pro Z packet must be a JSON object")
    return payload
