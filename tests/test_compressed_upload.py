from __future__ import annotations

import base64
import hashlib
import random
import uuid
import zlib

import pytest

from laser_aligner.machine.pi_job_protocol import (
    ACTION_JOB_BEGIN,
    ACTION_JOB_CHUNK,
    ACTION_JOB_CHUNK_COMPRESSED,
    ACTION_JOB_FINALIZE,
    MAX_COMPRESSED_CHUNK_BYTES,
    MAX_EXPANDED_CHUNK_BYTES,
    MAX_UPLOAD_CHUNK_BYTES,
    PiJobProtocolError,
    decode_compressed_upload_chunk,
    decode_upload_chunk,
    encode_upload_piece,
)
from laser_aligner.machine.pi_job_service import canonical_program_bytes
from tests.test_pi_machine_server import _binding, _rpc
from tests.test_pi_machine_server import server_harness as server_harness


@pytest.mark.parametrize("compressible", [False, True])
@pytest.mark.parametrize("supported", [False, True])
def test_upload_pieces_preserve_exact_bytes_and_bounded_offsets(compressible, supported):
    size = MAX_EXPANDED_CHUNK_BYTES * 2 + 13
    data = (b"G1 X12.34 Y56.78 F6000 S200\n" * size)[:size] if compressible else random.Random(4).randbytes(size)
    offset, recovered, requests = 0, bytearray(), 0
    while offset < len(data):
        action, encoded, count = encode_upload_piece(data[offset:offset + MAX_EXPANDED_CHUNK_BYTES], compressed=supported)
        assert 0 < count <= MAX_EXPANDED_CHUNK_BYTES
        if action == ACTION_JOB_CHUNK_COMPRESSED:
            assert supported
            assert len(base64.b64decode(encoded)) <= MAX_COMPRESSED_CHUNK_BYTES
            decoded = decode_compressed_upload_chunk(encoded)
        else:
            assert action == ACTION_JOB_CHUNK
            decoded = decode_upload_chunk(encoded)
            assert len(decoded) <= MAX_UPLOAD_CHUNK_BYTES
        assert len(decoded) == count
        recovered.extend(decoded)
        offset += count
        requests += 1
    assert bytes(recovered) == data
    if supported and compressible:
        assert requests == 3


@pytest.mark.parametrize("packed", [
    b"", b"invalid", zlib.compress(b""), zlib.compress(b"safe")[:-1],
    zlib.compress(b"safe") + b"trailing", zlib.compress(b"safe") + zlib.compress(b"second"),
    zlib.compress(b"x" * (MAX_EXPANDED_CHUNK_BYTES + 1)),
    b"x" * (MAX_COMPRESSED_CHUNK_BYTES + 1),
], ids=["empty", "invalid", "empty-expanded", "truncated", "trailing", "concatenated", "expansion-limit", "input-limit"])
def test_invalid_compressed_streams_reject(packed):
    with pytest.raises(PiJobProtocolError):
        decode_compressed_upload_chunk(base64.b64encode(packed).decode("ascii"))


@pytest.mark.parametrize("value", [None, True, 12, " ", "!!!!", "YQ=", "YR=="])
def test_compressed_base64_must_be_canonical_and_bounded(value):
    with pytest.raises(PiJobProtocolError):
        decode_compressed_upload_chunk(value)


def test_compressed_upload_is_inert_durable_and_validated_before_ready(server_harness):
    harness = server_harness
    text = "G21\nG90\nM5\nG0 X1 Y1 F100\nM4 S0\n" + "G1 X2 Y2 F100 S1\n" * 8000 + "M5\n"
    program = harness.machine.preflight_program(text)
    data = canonical_program_bytes(program)
    before = list(harness.transport.commands)
    job_id = str(uuid.uuid4())
    assert _rpc(harness, ACTION_JOB_BEGIN, job_id=job_id, name="compressed.gcode",
                expected_size=len(data), expected_sha256=hashlib.sha256(data).hexdigest())["ok"]
    action, encoded, count = encode_upload_piece(data, compressed=True)
    assert action == ACTION_JOB_CHUNK_COMPRESSED and count == len(data)
    fields = dict(job_id=job_id, offset=0, data_b64=encoded)
    response = _rpc(harness, action, **fields)
    assert response["ok"] and response["job"]["received_size"] == len(data)
    assert _rpc(harness, action, **fields)["ok"]  # Exact retransmission is idempotent.
    assert not _rpc(harness, action, **{**fields, "offset": len(data)})["ok"]
    altered = base64.b64encode(zlib.compress(data.replace(b"X2", b"X3"))).decode("ascii")
    assert not _rpc(harness, action, **{**fields, "data_b64": altered})["ok"]
    result = _rpc(harness, ACTION_JOB_FINALIZE, job_id=job_id, **_binding(program))
    assert result["ok"] and result["ready"]
    assert result["job"].get("ownership_accepted") is not True
    assert harness.transport.commands == before
    assert not harness.machine.status()["armed"]


def test_compressed_unsafe_program_still_rejects_finalization(server_harness):
    data = b"G21\nG90\nM5\n" + b"G1 X999 Y999 F100 S100\n" * 5000 + b"M5\n"
    job_id, digest = str(uuid.uuid4()), hashlib.sha256(data).hexdigest()
    assert _rpc(server_harness, ACTION_JOB_BEGIN, job_id=job_id, name="unsafe.gcode",
                expected_size=len(data), expected_sha256=digest)["ok"]
    action, encoded, count = encode_upload_piece(data, compressed=True)
    assert count == len(data)
    assert _rpc(server_harness, action, job_id=job_id, offset=0, data_b64=encoded)["ok"]
    safe = server_harness.machine.preflight_program("G21\nG90\nM5\nM5")
    result = _rpc(server_harness, ACTION_JOB_FINALIZE, job_id=job_id,
                  **{**_binding(safe), "program_digest": digest})
    assert not result["ok"]
    assert not server_harness.machine.status()["job"]["running"]
