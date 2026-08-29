from __future__ import annotations

import socket
import threading
import uuid

import pytest

from laser_aligner.machine import pi_job_protocol as protocol_module
from laser_aligner.machine.pi_job_protocol import (
    LEGACY_PROTOCOL_VERSION,
    MAX_FRAME_PAYLOAD_BYTES,
    MAX_UPLOAD_CHUNK_BYTES,
    AuthenticatedChannel,
    PiJobProtocolError,
    authenticate_client,
    authenticate_server,
    decode_upload_chunk,
    encode_upload_chunk,
    validate_guarded_output_polygon,
    validate_job_id,
    validate_job_name,
    validate_job_size,
    validate_sha256,
)

TOKEN = "correct-horse-battery-staple-e3-machine-token"


class MemorySocket:
    def __init__(self, incoming: bytes = b"") -> None:
        self.incoming = bytearray(incoming)
        self.sent = bytearray()

    def sendall(self, payload: bytes) -> None:
        self.sent.extend(payload)

    def recv(self, length: int) -> bytes:
        if not self.incoming:
            return b""
        payload = bytes(self.incoming[:length])
        del self.incoming[:length]
        return payload


def _memory_channel(sock: MemorySocket) -> AuthenticatedChannel:
    return AuthenticatedChannel(
        sock,  # type: ignore[arg-type]
        send_key=b"s" * 32,
        receive_key=b"s" * 32,
        send_domain=b"frame\0",
        receive_domain=b"frame\0",
    )


def test_mutual_authentication_and_counted_frames_round_trip() -> None:
    client_socket, server_socket = socket.socketpair()
    client_socket.settimeout(2.0)
    server_socket.settimeout(2.0)
    failures: list[BaseException] = []

    def serve() -> None:
        try:
            channel = authenticate_server(server_socket, TOKEN)
            first = channel.receive_json()
            second = channel.receive_json()
            channel.send_json({"ok": True, "seen": [first, second]})
        except BaseException as exc:
            failures.append(exc)
        finally:
            server_socket.close()

    worker = threading.Thread(target=serve)
    worker.start()
    try:
        channel = authenticate_client(client_socket, TOKEN)
        channel.send_json({"action": "machine.status", "sequence": 1})
        channel.send_json({"action": "job.active", "sequence": 2})
        response = channel.receive_json()
    finally:
        client_socket.close()
        worker.join(timeout=2.0)

    assert not worker.is_alive()
    assert not failures
    assert response == {
        "ok": True,
        "seen": [
            {"action": "machine.status", "sequence": 1},
            {"action": "job.active", "sequence": 2},
        ],
    }


def test_crlf_authentication_lines_do_not_prefix_the_first_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client_socket, server_socket = socket.socketpair()
    failures: list[BaseException] = []

    def send_crlf(sock: socket.socket, line: str) -> None:
        sock.sendall(line.rstrip("\r\n").encode("ascii") + b"\r\n")

    monkeypatch.setattr(protocol_module, "_send_ascii_line", send_crlf)

    def serve() -> None:
        try:
            channel = authenticate_server(server_socket, TOKEN)
            channel.send_json({"ok": True})
        except BaseException as exc:
            failures.append(exc)
        finally:
            server_socket.close()

    worker = threading.Thread(target=serve)
    worker.start()
    try:
        channel = authenticate_client(client_socket, TOKEN)
        assert channel.receive_json() == {"ok": True}
    finally:
        client_socket.close()
        worker.join(timeout=2.0)
    assert not failures


def test_wrong_token_fails_mutual_authentication() -> None:
    client_socket, server_socket = socket.socketpair()
    client_socket.settimeout(2.0)
    server_socket.settimeout(2.0)
    failures: list[BaseException] = []

    def serve() -> None:
        try:
            authenticate_server(server_socket, TOKEN)
        except BaseException as exc:
            failures.append(exc)
        finally:
            server_socket.close()

    worker = threading.Thread(target=serve)
    worker.start()
    try:
        with pytest.raises(PiJobProtocolError, match="authentication failed"):
            authenticate_client(client_socket, "wrong-token-that-is-still-long-enough")
    finally:
        client_socket.close()
        worker.join(timeout=2.0)
    assert failures
    assert isinstance(failures[0], PiJobProtocolError)


def test_legacy_raw_bridge_is_explicitly_incompatible() -> None:
    client_socket, server_socket = socket.socketpair()
    try:
        server_socket.sendall(
            f"{LEGACY_PROTOCOL_VERSION} CHALLENGE {'00' * 32}\n".encode("ascii")
        )
        with pytest.raises(PiJobProtocolError, match="legacy E3BRIDGE/1 raw serial"):
            authenticate_client(client_socket, TOKEN)
    finally:
        client_socket.close()
        server_socket.close()


def test_frame_hmac_detects_payload_tampering() -> None:
    writer_socket = MemorySocket()
    _memory_channel(writer_socket).send_json({"ok": True})
    packet = bytearray(writer_socket.sent)
    packet[-33] ^= 1

    reader = _memory_channel(MemorySocket(bytes(packet)))
    with pytest.raises(PiJobProtocolError, match="authentication failed"):
        reader.receive_json()


def test_frame_counter_rejects_replay() -> None:
    writer_socket = MemorySocket()
    _memory_channel(writer_socket).send_json({"ok": True})
    packet = bytes(writer_socket.sent)
    reader = _memory_channel(MemorySocket(packet + packet))

    assert reader.receive_json() == {"ok": True}
    with pytest.raises(PiJobProtocolError, match="replayed or out of sequence"):
        reader.receive_json()


def test_frame_and_chunk_limits_are_bounded() -> None:
    channel = _memory_channel(MemorySocket())
    with pytest.raises(PiJobProtocolError, match="frame must contain"):
        channel.send_json({"payload": "x" * MAX_FRAME_PAYLOAD_BYTES})

    payload = b"z" * MAX_UPLOAD_CHUNK_BYTES
    encoded = encode_upload_chunk(payload)
    assert decode_upload_chunk(encoded) == payload
    with pytest.raises(PiJobProtocolError, match="upload chunk"):
        encode_upload_chunk(payload + b"x")
    with pytest.raises(PiJobProtocolError, match="canonical base64"):
        decode_upload_chunk(encoded[:-1] + "A")


@pytest.mark.parametrize(
    "value",
    [
        "../job",
        "{A8098C1A-F86E-11DA-BD1A-00112444BE1E}",
        "a8098c1a-f86e-11da-bd1a-00112444BE1E",
        "a8098c1af86e11dabd1a00112444be1e",
        7,
    ],
)
def test_job_id_requires_canonical_lowercase_uuid(value: object) -> None:
    with pytest.raises(PiJobProtocolError, match="canonical"):
        validate_job_id(value)
    generated = str(uuid.uuid4())
    assert validate_job_id(generated) == generated


@pytest.mark.parametrize(
    "value",
    ["", " outer", "outer ", "bad/name", "bad\\name", "line\nbreak", 3],
)
def test_job_name_rejects_ambiguous_or_path_like_values(value: object) -> None:
    with pytest.raises(PiJobProtocolError, match="job name"):
        validate_job_name(value)


def test_exact_scalar_and_polygon_validation() -> None:
    digest = "a" * 64
    assert validate_sha256(digest) == digest
    assert validate_job_size(1) == 1
    with pytest.raises(PiJobProtocolError):
        validate_job_size(True)
    with pytest.raises(PiJobProtocolError):
        validate_sha256(digest.upper())

    polygon = validate_guarded_output_polygon([[0, 0], [0, 2], [2, 2], [2, 0]])
    assert polygon == ((2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0))
    with pytest.raises(PiJobProtocolError, match="finite numbers"):
        validate_guarded_output_polygon([[0, 0], [1, True], [2, 0]])
    with pytest.raises(PiJobProtocolError, match="strictly convex"):
        validate_guarded_output_polygon([[0, 0], [1, 0], [2, 0]])
