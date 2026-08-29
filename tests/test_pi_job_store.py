from __future__ import annotations

import hashlib
import json
import os
import uuid
from pathlib import Path

import pytest

from laser_aligner.machine import pi_job_store as store_module
from laser_aligner.machine.pi_job_protocol import (
    MAX_JOB_BYTES,
    MAX_UPLOAD_CHUNK_BYTES,
)
from laser_aligner.machine.pi_job_store import (
    MAX_METADATA_RECORDS,
    MAX_TERMINAL_PROGRAMS,
    JobValidation,
    PiJobStore,
    PiJobStoreError,
)


def _job_id(number: int) -> str:
    return str(uuid.UUID(int=number))


def _sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _validator(text: str, polygon: object) -> JobValidation:
    payload = text.encode("utf-8")
    return JobValidation(
        program_digest=_sha(payload),
        requires_laser_authorization=False,
        requires_motion=False,
        execution_policy_digest="e" * 64,
        value={"text": text, "polygon": polygon},
    )


def _begin(
    store: PiJobStore,
    job_id: str,
    payload: bytes,
    *,
    name: str = "fixture.gcode",
    polygon: object = None,
) -> dict[str, object]:
    return store.begin(
        job_id,
        name=name,
        expected_size=len(payload),
        expected_sha256=_sha(payload),
        guarded_output_polygon_mm=polygon,
    )


def _upload(
    store: PiJobStore,
    job_id: str,
    payload: bytes,
    *,
    polygon: object = None,
) -> dict[str, object]:
    _begin(store, job_id, payload, polygon=polygon)
    midpoint = max(1, len(payload) // 2)
    store.append_chunk(job_id, offset=0, data=payload[:midpoint])
    if midpoint < len(payload):
        store.append_chunk(job_id, offset=midpoint, data=payload[midpoint:])
    return store.finalize(job_id, validator=_validator).record


def test_sequential_upload_commit_and_idempotent_finalize(tmp_path: Path) -> None:
    store = PiJobStore(tmp_path / "jobs")
    job_id = _job_id(1)
    payload = b"G21\nG90\nM5\n"
    polygon = [[0, 0], [0, 2], [2, 2], [2, 0]]

    initial = _begin(store, job_id, payload, polygon=polygon)
    assert initial["state"] == "receiving"
    assert initial["received_size"] == 0
    with pytest.raises(PiJobStoreError, match="no retained validated program"):
        store.read_program(job_id)

    first = payload[:5]
    uploaded = store.append_chunk(job_id, offset=0, data=first)
    assert uploaded["received_size"] == len(first)
    duplicate = store.append_chunk(job_id, offset=0, data=first)
    assert duplicate["received_size"] == len(first)
    store.append_chunk(job_id, offset=len(first), data=payload[len(first) :])

    result = store.finalize(job_id, validator=_validator)
    assert result.record["state"] == "prepared"
    assert result.record["received_size"] == len(payload)
    assert result.record["program_digest"] == _sha(payload)
    assert result.record["requires_laser_authorization"] is False
    assert result.record["requires_motion"] is False
    assert result.record["powered"] is False
    assert result.record["execution_policy_digest"] == "e" * 64
    assert result.record["guarded_output_polygon_mm"] == [
        [2.0, 0.0],
        [2.0, 2.0],
        [0.0, 2.0],
        [0.0, 0.0],
    ]
    assert result.validation.value == {
        "text": payload.decode(),
        "polygon": ((2.0, 0.0), (2.0, 2.0), (0.0, 2.0), (0.0, 0.0)),
    }
    assert store.read_program(job_id) == payload.decode()
    assert store.read_program_bytes(job_id) == payload
    assert not (store.programs_dir / f"{job_id}.part").exists()
    assert (store.programs_dir / f"{job_id}.gcode").read_bytes() == payload

    again = store.finalize(job_id, validator=_validator)
    assert again.record == result.record
    assert again.validation.value == result.validation.value


def test_begin_is_idempotent_only_for_identical_metadata(tmp_path: Path) -> None:
    store = PiJobStore(tmp_path / "jobs")
    job_id = _job_id(2)
    payload = b"M5\n"
    original = _begin(store, job_id, payload)

    assert _begin(store, job_id, payload) == original
    with pytest.raises(PiJobStoreError, match="different upload metadata"):
        store.begin(
            job_id,
            name="other.gcode",
            expected_size=len(payload),
            expected_sha256=_sha(payload),
        )


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"job_id": "../escape"}, "canonical UUID"),
        ({"job_id": 7}, "canonical UUID"),
        ({"name": True}, "job name"),
        ({"name": "path/name.gcode"}, "path separators"),
        ({"expected_size": True}, "job size"),
        ({"expected_size": MAX_JOB_BYTES + 1}, "job size"),
        ({"expected_sha256": "A" * 64}, "lowercase SHA-256"),
        (
            {"guarded_output_polygon_mm": [[0, 0], [1, True], [2, 0]]},
            "finite numbers",
        ),
        (
            {"guarded_output_polygon_mm": [[0, 0], [1, 0], [2, 0]]},
            "strictly convex",
        ),
    ],
)
def test_begin_rejects_malformed_exact_types_and_path_values(
    tmp_path: Path,
    overrides: dict[str, object],
    match: str,
) -> None:
    store = PiJobStore(tmp_path / "jobs")
    arguments: dict[str, object] = {
        "job_id": _job_id(3),
        "name": "safe.gcode",
        "expected_size": 1,
        "expected_sha256": _sha(b"x"),
        "guarded_output_polygon_mm": None,
    }
    arguments.update(overrides)
    with pytest.raises(PiJobStoreError, match=match):
        store.begin(**arguments)  # type: ignore[arg-type]


def test_chunk_requires_exact_bytes_and_sequential_resume_offset(tmp_path: Path) -> None:
    store = PiJobStore(tmp_path / "jobs")
    job_id = _job_id(4)
    payload = b"abcdefgh"
    _begin(store, job_id, payload)

    store.append_chunk(job_id, offset=0, data=b"abcd")
    with pytest.raises(PiJobStoreError, match="resume at 4"):
        store.append_chunk(job_id, offset=6, data=b"gh")
    with pytest.raises(PiJobStoreError, match="does not match"):
        store.append_chunk(job_id, offset=0, data=b"ABCD")
    with pytest.raises(PiJobStoreError, match="resume at 4"):
        store.append_chunk(job_id, offset=2, data=b"cdef")
    with pytest.raises(PiJobStoreError, match="chunk must contain"):
        store.append_chunk(job_id, offset=4, data=b"")
    with pytest.raises(PiJobStoreError, match="chunk must contain"):
        store.append_chunk(
            job_id,
            offset=4,
            data=b"x" * (MAX_UPLOAD_CHUNK_BYTES + 1),
        )
    with pytest.raises(PiJobStoreError, match="chunk must contain"):
        store.append_chunk(job_id, offset=4, data=bytearray(b"efgh"))  # type: ignore[arg-type]

    finished = store.append_chunk(job_id, offset=4, data=b"efgh")
    assert finished["received_size"] == len(payload)


def test_partial_finalize_fails_closed_and_is_never_runnable(tmp_path: Path) -> None:
    store = PiJobStore(tmp_path / "jobs")
    job_id = _job_id(5)
    payload = b"G21\nG90\nM5\n"
    _begin(store, job_id, payload)
    store.append_chunk(job_id, offset=0, data=payload[:-1])

    with pytest.raises(PiJobStoreError, match="incomplete"):
        store.finalize(job_id, validator=_validator)
    record = store.get(job_id)
    assert record["state"] == "failed"
    assert record["program_retained"] is False
    assert not (store.programs_dir / f"{job_id}.part").exists()
    assert not (store.programs_dir / f"{job_id}.gcode").exists()
    with pytest.raises(PiJobStoreError, match="no retained validated program"):
        store.read_program(job_id)


@pytest.mark.parametrize("failure", ["sha", "utf8", "validator"])
def test_finalize_integrity_or_validation_failure_never_commits(
    tmp_path: Path,
    failure: str,
) -> None:
    store = PiJobStore(tmp_path / failure)
    job_id = _job_id({"sha": 6, "utf8": 7, "validator": 8}[failure])
    payload = b"M5\n" if failure != "utf8" else b"\xff"
    declared_sha = "0" * 64 if failure == "sha" else _sha(payload)
    store.begin(
        job_id,
        name="unsafe.gcode",
        expected_size=len(payload),
        expected_sha256=declared_sha,
    )
    store.append_chunk(job_id, offset=0, data=payload)

    def reject(_text: str, _polygon: object) -> JobValidation:
        raise ValueError("validator rejected fixture")

    validator = reject if failure == "validator" else _validator
    with pytest.raises((PiJobStoreError, ValueError)):
        store.finalize(job_id, validator=validator)
    assert store.get(job_id)["state"] == "failed"
    assert not (store.programs_dir / f"{job_id}.gcode").exists()
    assert not (store.programs_dir / f"{job_id}.part").exists()


def test_state_machine_enforces_single_owner_and_immutable_authority(
    tmp_path: Path,
) -> None:
    store = PiJobStore(tmp_path / "jobs")
    first = _job_id(9)
    second = _job_id(10)
    _upload(store, first, b"M5\n; first")
    _upload(store, second, b"M5\n; second")

    started = store.update_state(first, "starting", phase="starting")
    assert store.active() == started
    with pytest.raises(PiJobStoreError, match="already owns execution"):
        store.update_state(second, "starting")
    with pytest.raises(PiJobStoreError, match="protected field"):
        store.update_state(first, "running", program_digest="f" * 64)
    with pytest.raises(PiJobStoreError, match="execution authority"):
        store.update_state(first, "running", powered=True)

    running = store.update_state(first, "running", phase="streaming", powered=False)
    assert running["powered"] is False
    stopped = store.update_state(first, "stopping", phase="stopping")
    assert stopped["state"] == "stopping"
    terminal = store.update_state(first, "stopped", result={"ok": False})
    assert terminal["state"] == "stopped"
    assert store.active() is None
    with pytest.raises(PiJobStoreError, match="not allowed"):
        store.update_state(first, "running")


@pytest.mark.parametrize("active_state", ["starting", "running", "stopping"])
def test_boot_reconciliation_marks_execution_interrupted_without_resume(
    tmp_path: Path,
    active_state: str,
) -> None:
    root = tmp_path / active_state
    store = PiJobStore(root)
    job_id = _job_id({"starting": 11, "running": 12, "stopping": 13}[active_state])
    payload = b"G21\nG90\nM5\n"
    _upload(store, job_id, payload)
    store.update_state(job_id, "starting")
    if active_state in {"running", "stopping"}:
        store.update_state(job_id, "running")
    if active_state == "stopping":
        store.update_state(job_id, "stopping")

    restarted = PiJobStore(root)
    record = restarted.get(job_id)
    assert record["state"] == "interrupted"
    assert "not resumed" in record["error"]
    assert restarted.active() is None
    assert restarted.read_program_bytes(job_id) == payload


def test_partial_upload_resumes_after_restart_and_stale_partial_is_discarded(
    tmp_path: Path,
) -> None:
    root = tmp_path / "jobs"
    job_id = _job_id(14)
    payload = b"abcdefgh"
    store = PiJobStore(root)
    _begin(store, job_id, payload)
    store.append_chunk(job_id, offset=0, data=payload[:4])

    resumed = PiJobStore(root)
    assert resumed.begin(
        job_id,
        name="fixture.gcode",
        expected_size=len(payload),
        expected_sha256=_sha(payload),
    )["received_size"] == 4
    resumed.append_chunk(job_id, offset=4, data=payload[4:])
    assert resumed.finalize(job_id, validator=_validator).record["state"] == "prepared"

    stale_id = _job_id(15)
    _begin(resumed, stale_id, payload)
    part_path = resumed.programs_dir / f"{stale_id}.part"
    os.utime(part_path, (1, 1))
    reconciled = PiJobStore(root, stale_part_seconds=1)
    assert reconciled.get(stale_id)["state"] == "failed"
    assert not part_path.exists()


def test_finalize_journal_recovers_atomic_commit_after_replace_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "jobs"
    store = PiJobStore(root)
    job_id = _job_id(16)
    payload = b"G21\nG90\nM5\n"
    _begin(store, job_id, payload)
    store.append_chunk(job_id, offset=0, data=payload)

    original_replace = store_module.os.replace
    interrupted = False

    def replace_once(source: object, destination: object) -> None:
        nonlocal interrupted
        if not interrupted and str(source).endswith(".part"):
            interrupted = True
            raise OSError("simulated loss between journal and artifact rename")
        original_replace(source, destination)

    monkeypatch.setattr(store_module.os, "replace", replace_once)
    with pytest.raises(PiJobStoreError, match="Could not commit"):
        store.finalize(job_id, validator=_validator)
    assert (store.records_dir / f"{job_id}.finalize.json").is_file()

    recovered = PiJobStore(root)
    assert recovered.get(job_id)["state"] == "prepared"
    assert recovered.read_program_bytes(job_id) == payload
    assert not (recovered.records_dir / f"{job_id}.finalize.json").exists()


def test_retention_keeps_latest_eight_records_and_two_terminal_programs(
    tmp_path: Path,
) -> None:
    store = PiJobStore(tmp_path / "jobs")
    job_ids: list[str] = []
    for number in range(100, 100 + MAX_METADATA_RECORDS + 2):
        job_id = _job_id(number)
        job_ids.append(job_id)
        payload = f"M5\n; fixture {number}".encode()
        _upload(store, job_id, payload)
        store.update_state(job_id, "failed", error=f"fixture {number}")

    records = store.list_records()
    assert len(records) == MAX_METADATA_RECORDS
    assert {record["job_id"] for record in records} == set(job_ids[-8:])
    retained = [record for record in records if record["program_retained"]]
    assert {record["job_id"] for record in retained} == set(
        job_ids[-MAX_TERMINAL_PROGRAMS:]
    )
    assert {
        path.stem for path in store.programs_dir.glob("*.gcode")
    } == set(job_ids[-MAX_TERMINAL_PROGRAMS:])
    with pytest.raises(PiJobStoreError, match="does not exist"):
        store.get(job_ids[0])
    with pytest.raises(PiJobStoreError, match="no retained validated program"):
        store.read_program_bytes(job_ids[-3])


def test_failed_partial_does_not_consume_a_terminal_program_retention_slot(
    tmp_path: Path,
) -> None:
    store = PiJobStore(tmp_path / "jobs")
    committed: list[str] = []
    for number in range(400, 403):
        job_id = _job_id(number)
        committed.append(job_id)
        payload = f"M5\n; retained {number}".encode()
        _upload(store, job_id, payload)
        store.update_state(job_id, "failed", error="fixture terminal")

    partial_id = _job_id(403)
    partial_payload = b"incomplete"
    _begin(store, partial_id, partial_payload)
    store.append_chunk(partial_id, offset=0, data=partial_payload[:-1])
    with pytest.raises(PiJobStoreError, match="incomplete"):
        store.finalize(partial_id, validator=_validator)

    retained = {
        record["job_id"]
        for record in store.list_records()
        if record["program_retained"]
    }
    assert retained == set(committed[-MAX_TERMINAL_PROGRAMS:])


def test_metadata_capacity_never_evicts_nonterminal_jobs(tmp_path: Path) -> None:
    store = PiJobStore(tmp_path / "jobs")
    for number in range(200, 200 + MAX_METADATA_RECORDS):
        payload = f"partial-{number}".encode()
        _begin(store, _job_id(number), payload)

    payload = b"ninth"
    with pytest.raises(PiJobStoreError, match="capacity is full"):
        _begin(store, _job_id(999), payload)
    assert len(store.list_records()) == MAX_METADATA_RECORDS


def test_duplicate_key_record_json_fails_closed_on_boot(tmp_path: Path) -> None:
    root = tmp_path / "jobs"
    records = root / "records"
    programs = root / "programs"
    records.mkdir(parents=True)
    programs.mkdir()
    job_id = _job_id(300)
    malformed = f'{{"job_id":"{job_id}","job_id":"{job_id}"}}'
    (records / f"{job_id}.json").write_text(malformed, encoding="utf-8")

    with pytest.raises(PiJobStoreError, match="duplicate key"):
        PiJobStore(root)


def test_persisted_record_is_strict_json_and_no_partial_state_file_remains(
    tmp_path: Path,
) -> None:
    store = PiJobStore(tmp_path / "jobs")
    job_id = _job_id(301)
    payload = b"M5\n"
    _begin(store, job_id, payload)
    record_path = store.records_dir / f"{job_id}.json"

    persisted = json.loads(record_path.read_text(encoding="utf-8"))
    assert persisted == store.get(job_id)
    assert list(store.records_dir.glob("*.tmp")) == []
