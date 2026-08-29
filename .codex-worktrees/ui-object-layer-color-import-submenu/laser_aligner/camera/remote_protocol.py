from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import socket
import struct
from collections.abc import Iterable
from typing import Any

from ..errors import CameraError

_CAMERA_VERSION = "E3CAMERA/1"
_TOKEN_ENV = "E3_BRIDGE_TOKEN"
_MIN_TOKEN_LENGTH = 24
_MAX_AUTH_LINE_BYTES = 1024
_MAX_JSON_BYTES = 1_000_000
_MAX_BLOB_BYTES = 16 * 1024 * 1024
_MAX_TOTAL_BLOB_BYTES = 256 * 1024 * 1024


def camera_token_from_environment() -> str:
    token = os.environ.get(_TOKEN_ENV, "")
    if len(token) < _MIN_TOKEN_LENGTH:
        raise CameraError(
            f"{_TOKEN_ENV} must be set to a secret of at least {_MIN_TOKEN_LENGTH} characters"
        )
    return token


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    data = bytearray()
    while len(data) < length:
        chunk = sock.recv(length - len(data))
        if not chunk:
            raise CameraError("Remote camera connection closed unexpectedly")
        data.extend(chunk)
    return bytes(data)


def _read_ascii_line(sock: socket.socket) -> str:
    data = bytearray()
    while True:
        chunk = sock.recv(1)
        if not chunk:
            raise CameraError("Remote camera connection closed during authentication")
        if chunk in {b"\r", b"\n"}:
            if data:
                try:
                    return data.decode("ascii", errors="strict")
                except UnicodeError as exc:
                    raise CameraError("Remote camera authentication was not ASCII") from exc
            continue
        data.extend(chunk)
        if len(data) > _MAX_AUTH_LINE_BYTES:
            raise CameraError("Remote camera authentication line is too long")


def _send_ascii_line(sock: socket.socket, line: str) -> None:
    sock.sendall(line.rstrip("\r\n").encode("ascii") + b"\n")


def authenticate_camera_client(sock: socket.socket, token: str) -> None:
    first = _read_ascii_line(sock).split()
    if len(first) == 2 and first == [_CAMERA_VERSION, "BUSY"]:
        raise CameraError("Remote camera service is busy")
    if len(first) != 3 or first[:2] != [_CAMERA_VERSION, "CHALLENGE"]:
        raise CameraError("Remote endpoint is not an E3 camera service")
    try:
        challenge = bytes.fromhex(first[2])
    except ValueError as exc:
        raise CameraError("Remote camera sent an invalid authentication challenge") from exc
    if len(challenge) != 32:
        raise CameraError("Remote camera sent an invalid authentication challenge")
    digest = hmac.new(token.encode("utf-8"), challenge, hashlib.sha256).hexdigest()
    _send_ascii_line(sock, f"{_CAMERA_VERSION} AUTH {digest}")
    result = _read_ascii_line(sock).split()
    if len(result) >= 2 and result[:2] == [_CAMERA_VERSION, "ERROR"]:
        reason = " ".join(result[2:]) or "authentication failed"
        raise CameraError(f"Remote camera rejected the connection: {reason}")
    if result != [_CAMERA_VERSION, "READY"]:
        raise CameraError("Remote camera did not complete authentication")


def authenticate_camera_server(sock: socket.socket, token: str) -> bool:
    challenge = secrets.token_bytes(32)
    _send_ascii_line(sock, f"{_CAMERA_VERSION} CHALLENGE {challenge.hex()}")
    try:
        response = _read_ascii_line(sock).split()
    except CameraError:
        return False
    if len(response) != 3 or response[:2] != [_CAMERA_VERSION, "AUTH"]:
        _send_ascii_line(sock, f"{_CAMERA_VERSION} ERROR authentication_failed")
        return False
    expected = hmac.new(token.encode("utf-8"), challenge, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(response[2], expected):
        _send_ascii_line(sock, f"{_CAMERA_VERSION} ERROR authentication_failed")
        return False
    _send_ascii_line(sock, f"{_CAMERA_VERSION} READY")
    return True


def send_packet(
    sock: socket.socket,
    header: dict[str, Any],
    blobs: Iterable[bytes] = (),
) -> None:
    payloads = tuple(bytes(blob) for blob in blobs)
    lengths = [len(blob) for blob in payloads]
    if any(length > _MAX_BLOB_BYTES for length in lengths):
        raise CameraError("Remote camera blob exceeds the per-frame transfer limit")
    if sum(lengths) > _MAX_TOTAL_BLOB_BYTES:
        raise CameraError("Remote camera response exceeds the aggregate transfer limit")
    document = dict(header)
    document["blob_lengths"] = lengths
    try:
        encoded = json.dumps(
            document,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CameraError(f"Could not serialize remote camera packet: {exc}") from exc
    if len(encoded) > _MAX_JSON_BYTES:
        raise CameraError("Remote camera packet header exceeds the transfer limit")
    sock.sendall(struct.pack("!I", len(encoded)))
    sock.sendall(encoded)
    for blob in payloads:
        sock.sendall(blob)


def receive_packet(sock: socket.socket) -> tuple[dict[str, Any], tuple[bytes, ...]]:
    header_length = struct.unpack("!I", _recv_exact(sock, 4))[0]
    if not 1 <= header_length <= _MAX_JSON_BYTES:
        raise CameraError("Remote camera packet header has an invalid length")
    try:
        header = json.loads(_recv_exact(sock, header_length).decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise CameraError("Remote camera packet header is invalid JSON") from exc
    if not isinstance(header, dict):
        raise CameraError("Remote camera packet header must be a JSON object")
    lengths = header.pop("blob_lengths", None)
    if not isinstance(lengths, list) or any(
        type(length) is not int or not 0 <= length <= _MAX_BLOB_BYTES
        for length in lengths
    ):
        raise CameraError("Remote camera packet contains invalid blob lengths")
    if sum(lengths) > _MAX_TOTAL_BLOB_BYTES:
        raise CameraError("Remote camera packet exceeds the aggregate transfer limit")
    blobs = tuple(_recv_exact(sock, length) for length in lengths)
    return header, blobs
