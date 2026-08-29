"""Crash-safe persistent artifacts for Pi-owned controller jobs."""

from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Any

from ..errors import MachineError
from ..geometry.polygon import ConvexPolygon
from ..storage import atomic_write_json, strict_json_loads
from .pi_job_protocol import (
    MAX_JOB_BYTES,
    MAX_UPLOAD_CHUNK_BYTES,
    PiJobProtocolError,
    validate_guarded_output_polygon,
    validate_job_id,
    validate_job_name,
    validate_job_size,
    validate_sha256,
    validate_upload_offset,
)

JOB_RECORD_SCHEMA = 1
JOB_STATES = frozenset(
    {
        "receiving",
        "prepared",
        "starting",
        "running",
        "stopping",
        "complete",
        "failed",
        "stopped",
        "interrupted",
    }
)
ACTIVE_JOB_STATES = frozenset({"starting", "running", "stopping"})
TERMINAL_JOB_STATES = frozenset(
    {"complete", "failed", "stopped", "interrupted"}
)
MAX_METADATA_RECORDS = 8
MAX_TERMINAL_PROGRAMS = 2
DEFAULT_STALE_PART_SECONDS = 24 * 60 * 60

_PROTECTED_UPDATE_FIELDS = frozenset(
    {
        "schema",
        "job_id",
        "name",
        "state",
        "revision",
        "created_at",
        "updated_at",
        "expected_size",
        "expected_sha256",
        "received_size",
        "guarded_output_polygon_mm",
        "program_digest",
        "requires_laser_authorization",
        "requires_motion",
        "execution_policy_digest",
        "program_retained",
    }
)
_STATE_TRANSITIONS: dict[str, frozenset[str]] = {
    "receiving": frozenset({"prepared", "failed"}),
    "prepared": frozenset({"starting", "failed"}),
    "starting": frozenset(
        {"running", "stopping", "complete", "failed", "stopped", "interrupted"}
    ),
    "running": frozenset(
        {"stopping", "complete", "failed", "stopped", "interrupted"}
    ),
    "stopping": frozenset({"complete", "failed", "stopped", "interrupted"}),
    "complete": frozenset(),
    "failed": frozenset(),
    "stopped": frozenset(),
    "interrupted": frozenset(),
}
_MAX_STATE_DETAILS_BYTES = 64 * 1024
_COPY_CHUNK_BYTES = 1024 * 1024


class PiJobStoreError(MachineError):
    """A persistent Pi job record or artifact failed closed."""


@dataclass(frozen=True, slots=True)
class JobValidation:
    """Authoritative metadata derived by the Pi's local MachineService."""

    program_digest: str
    requires_laser_authorization: bool
    requires_motion: bool
    execution_policy_digest: str
    value: Any = None

    def __post_init__(self) -> None:
        try:
            validate_sha256(self.program_digest, label="program_digest")
            validate_sha256(
                self.execution_policy_digest,
                label="execution_policy_digest",
            )
        except PiJobProtocolError as exc:
            raise PiJobStoreError(str(exc)) from exc
        if type(self.requires_laser_authorization) is not bool:
            raise PiJobStoreError(
                "requires_laser_authorization must be an exact boolean"
            )
        if type(self.requires_motion) is not bool:
            raise PiJobStoreError("requires_motion must be an exact boolean")


@dataclass(frozen=True, slots=True)
class FinalizeResult:
    record: dict[str, Any]
    validation: JobValidation


JobValidator = Callable[[str, ConvexPolygon | None], JobValidation]


def _finite_time(value: object, label: str) -> float:
    if type(value) not in {int, float} or not math.isfinite(float(value)):
        raise PiJobStoreError(f"{label} must be a finite timestamp")
    return float(value)


def _fsync_parent_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


class PiJobStore:
    """Persistent bounded job records and immutable validated G-code."""

    def __init__(
        self,
        root: str | Path,
        *,
        clock: Callable[[], float] = time.time,
        stale_part_seconds: float = DEFAULT_STALE_PART_SECONDS,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.records_dir = self.root / "records"
        self.programs_dir = self.root / "programs"
        self._clock = clock
        if (
            type(stale_part_seconds) not in {int, float}
            or not math.isfinite(float(stale_part_seconds))
            or stale_part_seconds < 0
        ):
            raise ValueError("stale_part_seconds must be finite and non-negative")
        self.stale_part_seconds = float(stale_part_seconds)
        self._lock = RLock()
        self.records_dir.mkdir(parents=True, exist_ok=True)
        self.programs_dir.mkdir(parents=True, exist_ok=True)
        self.reconcile_boot()

    def _now(self) -> float:
        return _finite_time(self._clock(), "job-store clock")

    def _record_path(self, job_id: str) -> Path:
        return self.records_dir / f"{validate_job_id(job_id)}.json"

    def _journal_path(self, job_id: str) -> Path:
        return self.records_dir / f"{validate_job_id(job_id)}.finalize.json"

    def _part_path(self, job_id: str) -> Path:
        return self.programs_dir / f"{validate_job_id(job_id)}.part"

    def _program_path(self, job_id: str) -> Path:
        return self.programs_dir / f"{validate_job_id(job_id)}.gcode"

    @staticmethod
    def _public(record: Mapping[str, Any]) -> dict[str, Any]:
        return copy.deepcopy(dict(record))

    @staticmethod
    def _polygon_json(polygon: ConvexPolygon | None) -> list[list[float]] | None:
        if polygon is None:
            return None
        return [[float(x), float(y)] for x, y in polygon]

    def _validate_record(self, raw: object, *, expected_id: str) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise PiJobStoreError(f"Pi job record {expected_id} must be a JSON object")
        record = dict(raw)
        required = {
            "schema",
            "job_id",
            "name",
            "state",
            "revision",
            "created_at",
            "updated_at",
            "expected_size",
            "expected_sha256",
            "received_size",
            "guarded_output_polygon_mm",
            "program_digest",
            "requires_laser_authorization",
            "requires_motion",
            "powered",
            "execution_policy_digest",
            "program_retained",
        }
        missing = required - set(record)
        if missing:
            raise PiJobStoreError(
                f"Pi job record {expected_id} is missing fields: {', '.join(sorted(missing))}"
            )
        if record["schema"] != JOB_RECORD_SCHEMA or type(record["schema"]) is not int:
            raise PiJobStoreError(f"Pi job record {expected_id} has an unsupported schema")
        try:
            job_id = validate_job_id(record["job_id"])
            name = validate_job_name(record["name"])
            expected_size = validate_job_size(record["expected_size"])
            expected_sha256 = validate_sha256(
                record["expected_sha256"], label="expected_sha256"
            )
            polygon = validate_guarded_output_polygon(
                record["guarded_output_polygon_mm"]
            )
        except PiJobProtocolError as exc:
            raise PiJobStoreError(f"Pi job record {expected_id} is invalid: {exc}") from exc
        if job_id != expected_id:
            raise PiJobStoreError("Pi job record identity does not match its filename")
        state = record["state"]
        if type(state) is not str or state not in JOB_STATES:
            raise PiJobStoreError(f"Pi job record {expected_id} has an invalid state")
        if type(record["revision"]) is not int or record["revision"] < 1:
            raise PiJobStoreError(f"Pi job record {expected_id} has an invalid revision")
        _finite_time(record["created_at"], "created_at")
        _finite_time(record["updated_at"], "updated_at")
        received_size = record["received_size"]
        if (
            type(received_size) is not int
            or not 0 <= received_size <= expected_size
            or received_size > MAX_JOB_BYTES
        ):
            raise PiJobStoreError(
                f"Pi job record {expected_id} has an invalid received_size"
            )
        if type(record["program_retained"]) is not bool:
            raise PiJobStoreError(
                f"Pi job record {expected_id} has invalid retention metadata"
            )
        authority_values = (
            record["program_digest"],
            record["requires_laser_authorization"],
            record["requires_motion"],
            record["powered"],
            record["execution_policy_digest"],
        )
        authority_is_empty = all(value is None for value in authority_values)
        if state == "receiving":
            if not authority_is_empty:
                raise PiJobStoreError(
                    f"Receiving Pi job {expected_id} must not contain execution authority"
                )
            if record["program_retained"]:
                raise PiJobStoreError(
                    f"Receiving Pi job {expected_id} cannot retain a runnable program"
                )
        elif (
            not authority_is_empty
            or record["program_retained"]
            or state == "prepared"
            or state in ACTIVE_JOB_STATES
        ):
            try:
                validate_sha256(record["program_digest"], label="program_digest")
                validate_sha256(
                    record["execution_policy_digest"],
                    label="execution_policy_digest",
                )
            except PiJobProtocolError as exc:
                raise PiJobStoreError(
                    f"Pi job record {expected_id} has invalid execution metadata: {exc}"
                ) from exc
            if (
                type(record["requires_laser_authorization"]) is not bool
                or type(record["requires_motion"]) is not bool
                or type(record["powered"]) is not bool
                or record["powered"] is not record["requires_laser_authorization"]
            ):
                raise PiJobStoreError(
                    f"Pi job record {expected_id} has invalid execution flags"
                )
            if received_size != expected_size:
                raise PiJobStoreError(
                    f"Finalized Pi job {expected_id} has an incomplete byte count"
                )
        if (state == "prepared" or state in ACTIVE_JOB_STATES) and not record[
            "program_retained"
        ]:
            raise PiJobStoreError(
                f"Runnable Pi job {expected_id} must retain its validated program"
            )
        record["job_id"] = job_id
        record["name"] = name
        record["expected_size"] = expected_size
        record["expected_sha256"] = expected_sha256
        record["guarded_output_polygon_mm"] = self._polygon_json(polygon)
        return record

    def _read_record_path(self, path: Path) -> dict[str, Any]:
        expected_id = path.name[: -len(".json")]
        try:
            raw = strict_json_loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise PiJobStoreError(f"Could not read Pi job record {path.name}: {exc}") from exc
        return self._validate_record(raw, expected_id=expected_id)

    def _load_record(self, job_id: str) -> dict[str, Any]:
        canonical = validate_job_id(job_id)
        path = self._record_path(canonical)
        if not path.is_file():
            raise PiJobStoreError(f"Pi job {canonical} does not exist")
        return self._read_record_path(path)

    def _write_record(self, record: Mapping[str, Any]) -> dict[str, Any]:
        validated = self._validate_record(
            dict(record),
            expected_id=validate_job_id(record.get("job_id")),
        )
        atomic_write_json(self._record_path(validated["job_id"]), validated)
        return validated

    def _all_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        for path in sorted(self.records_dir.glob("*.json")):
            if path.name.endswith(".finalize.json"):
                continue
            records.append(self._read_record_path(path))
        return records

    @staticmethod
    def _record_sort_key(record: Mapping[str, Any]) -> tuple[float, str]:
        terminal_time = record.get("finished_at", record["updated_at"])
        if type(terminal_time) not in {int, float} or not math.isfinite(
            float(terminal_time)
        ):
            terminal_time = record["updated_at"]
        return float(terminal_time), str(record["job_id"])

    def _delete_artifacts(self, job_id: str, *, metadata: bool) -> None:
        self._part_path(job_id).unlink(missing_ok=True)
        self._program_path(job_id).unlink(missing_ok=True)
        self._journal_path(job_id).unlink(missing_ok=True)
        if metadata:
            self._record_path(job_id).unlink(missing_ok=True)

    def _apply_retention(self) -> None:
        records = self._all_records()
        terminal = sorted(
            (record for record in records if record["state"] in TERMINAL_JOB_STATES),
            key=self._record_sort_key,
            reverse=True,
        )
        terminal_with_program = [
            record
            for record in terminal
            if self._program_path(record["job_id"]).is_file()
        ]
        retained_program_ids = {
            record["job_id"]
            for record in terminal_with_program[:MAX_TERMINAL_PROGRAMS]
        }
        programs_changed = False
        for record in terminal:
            job_id = record["job_id"]
            should_retain = job_id in retained_program_ids and self._program_path(
                job_id
            ).is_file()
            if record["program_retained"] is not should_retain:
                record["program_retained"] = should_retain
                record["revision"] += 1
                record["updated_at"] = self._now()
                self._write_record(record)
            if not should_retain:
                program_path = self._program_path(job_id)
                if program_path.exists():
                    program_path.unlink(missing_ok=True)
                    programs_changed = True
        if programs_changed:
            _fsync_parent_directory(self.programs_dir)

        records = self._all_records()
        if len(records) <= MAX_METADATA_RECORDS:
            return
        removable = sorted(
            (record for record in records if record["state"] in TERMINAL_JOB_STATES),
            key=self._record_sort_key,
        )
        excess = len(records) - MAX_METADATA_RECORDS
        if len(removable) < excess:
            raise PiJobStoreError(
                "Pi job metadata capacity is exhausted by non-terminal jobs"
            )
        for record in removable[:excess]:
            self._delete_artifacts(record["job_id"], metadata=True)
        _fsync_parent_directory(self.records_dir)
        _fsync_parent_directory(self.programs_dir)

    def begin(
        self,
        job_id: str,
        *,
        name: str,
        expected_size: int,
        expected_sha256: str,
        guarded_output_polygon_mm: object = None,
    ) -> dict[str, Any]:
        try:
            canonical = validate_job_id(job_id)
            checked_name = validate_job_name(name)
            checked_size = validate_job_size(expected_size)
            checked_sha = validate_sha256(
                expected_sha256,
                label="expected_sha256",
            )
            polygon = validate_guarded_output_polygon(guarded_output_polygon_mm)
        except PiJobProtocolError as exc:
            raise PiJobStoreError(str(exc)) from exc
        with self._lock:
            path = self._record_path(canonical)
            if path.exists():
                record = self._load_record(canonical)
                identity = (
                    record["name"],
                    record["expected_size"],
                    record["expected_sha256"],
                    record["guarded_output_polygon_mm"],
                )
                requested = (
                    checked_name,
                    checked_size,
                    checked_sha,
                    self._polygon_json(polygon),
                )
                if identity != requested:
                    raise PiJobStoreError(
                        "Existing Pi job ID belongs to different upload metadata"
                    )
                if record["state"] == "receiving":
                    record = self._synchronize_receiving_size(record)
                return self._public(record)

            self._apply_retention()
            records = self._all_records()
            if len(records) >= MAX_METADATA_RECORDS:
                removable = sorted(
                    (
                        record
                        for record in records
                        if record["state"] in TERMINAL_JOB_STATES
                    ),
                    key=self._record_sort_key,
                )
                needed = len(records) - MAX_METADATA_RECORDS + 1
                if len(removable) < needed:
                    raise PiJobStoreError(
                        "Pi job metadata capacity is full; delete a non-active job first"
                    )
                for old_record in removable[:needed]:
                    self._delete_artifacts(old_record["job_id"], metadata=True)
                _fsync_parent_directory(self.records_dir)
                _fsync_parent_directory(self.programs_dir)
            part_path = self._part_path(canonical)
            if part_path.exists() or self._program_path(canonical).exists():
                raise PiJobStoreError(
                    "Unreferenced Pi job artifact blocks this job identifier"
                )
            try:
                with part_path.open("xb") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise PiJobStoreError(f"Could not begin Pi job upload: {exc}") from exc
            _fsync_parent_directory(self.programs_dir)
            now = self._now()
            record = {
                "schema": JOB_RECORD_SCHEMA,
                "job_id": canonical,
                "name": checked_name,
                "state": "receiving",
                "revision": 1,
                "created_at": now,
                "updated_at": now,
                "expected_size": checked_size,
                "expected_sha256": checked_sha,
                "received_size": 0,
                "guarded_output_polygon_mm": self._polygon_json(polygon),
                "program_digest": None,
                "requires_laser_authorization": None,
                "requires_motion": None,
                "powered": None,
                "execution_policy_digest": None,
                "program_retained": False,
            }
            try:
                written = self._write_record(record)
            except Exception:
                part_path.unlink(missing_ok=True)
                raise
            return self._public(written)

    def _synchronize_receiving_size(
        self, record: dict[str, Any]
    ) -> dict[str, Any]:
        if record["state"] != "receiving":
            return record
        part_path = self._part_path(record["job_id"])
        if not part_path.is_file():
            raise PiJobStoreError("Receiving Pi job is missing its partial program")
        actual = part_path.stat().st_size
        if actual < record["received_size"] or actual > record["expected_size"]:
            raise PiJobStoreError("Partial Pi job size contradicts its durable metadata")
        if actual != record["received_size"]:
            record["received_size"] = actual
            record["revision"] += 1
            record["updated_at"] = self._now()
            record = self._write_record(record)
        return record

    def append_chunk(self, job_id: str, *, offset: int, data: bytes) -> dict[str, Any]:
        try:
            canonical = validate_job_id(job_id)
        except PiJobProtocolError as exc:
            raise PiJobStoreError(str(exc)) from exc
        if type(data) is not bytes or not 1 <= len(data) <= MAX_UPLOAD_CHUNK_BYTES:
            raise PiJobStoreError(
                f"Pi job chunk must contain 1 through {MAX_UPLOAD_CHUNK_BYTES} bytes"
            )
        with self._lock:
            record = self._synchronize_receiving_size(self._load_record(canonical))
            if record["state"] != "receiving":
                raise PiJobStoreError("Only a receiving Pi job accepts upload chunks")
            try:
                checked_offset = validate_upload_offset(
                    offset,
                    maximum=record["expected_size"],
                )
            except PiJobProtocolError as exc:
                raise PiJobStoreError(str(exc)) from exc
            current = record["received_size"]
            end = checked_offset + len(data)
            if end > record["expected_size"] or end > MAX_JOB_BYTES:
                raise PiJobStoreError("Pi job chunk exceeds the declared job size")
            part_path = self._part_path(canonical)
            if checked_offset < current:
                if end > current:
                    raise PiJobStoreError(
                        f"Pi job upload offset must resume at {current}"
                    )
                with part_path.open("rb") as handle:
                    handle.seek(checked_offset)
                    existing = handle.read(len(data))
                if existing != data:
                    raise PiJobStoreError(
                        "Repeated Pi job chunk does not match durable upload bytes"
                    )
                return self._public(record)
            if checked_offset != current:
                raise PiJobStoreError(f"Pi job upload offset must resume at {current}")
            try:
                with part_path.open("r+b") as handle:
                    handle.seek(current)
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
            except OSError as exc:
                raise PiJobStoreError(f"Could not persist Pi job chunk: {exc}") from exc
            record["received_size"] = end
            record["revision"] += 1
            record["updated_at"] = self._now()
            return self._public(self._write_record(record))

    @staticmethod
    def _file_size_and_sha(path: Path) -> tuple[int, str]:
        digest = hashlib.sha256()
        size = 0
        try:
            with path.open("rb") as handle:
                while True:
                    chunk = handle.read(_COPY_CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > MAX_JOB_BYTES:
                        raise PiJobStoreError("Pi job artifact exceeds the 64 MiB limit")
                    digest.update(chunk)
        except OSError as exc:
            raise PiJobStoreError(f"Could not read Pi job artifact: {exc}") from exc
        return size, digest.hexdigest()

    @staticmethod
    def _read_program_text_path(path: Path) -> str:
        try:
            payload = path.read_bytes()
        except OSError as exc:
            raise PiJobStoreError(f"Could not read Pi job program: {exc}") from exc
        if not 1 <= len(payload) <= MAX_JOB_BYTES:
            raise PiJobStoreError("Pi job program has an invalid bounded size")
        try:
            return payload.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise PiJobStoreError("Pi job program must be valid UTF-8") from exc

    @staticmethod
    def _require_validation(value: object) -> JobValidation:
        if type(value) is not JobValidation:
            raise PiJobStoreError("Pi job validator must return an exact JobValidation")
        return value

    @staticmethod
    def _validation_fields(validation: JobValidation) -> dict[str, Any]:
        return {
            "program_digest": validation.program_digest,
            "requires_laser_authorization": validation.requires_laser_authorization,
            "requires_motion": validation.requires_motion,
            "powered": validation.requires_laser_authorization,
            "execution_policy_digest": validation.execution_policy_digest,
        }

    def _validate_artifact(
        self,
        record: Mapping[str, Any],
        path: Path,
        validator: JobValidator,
    ) -> tuple[str, JobValidation]:
        size, digest = self._file_size_and_sha(path)
        if size != record["expected_size"]:
            raise PiJobStoreError(
                f"Pi job upload is incomplete ({size}/{record['expected_size']} bytes)"
            )
        if digest != record["expected_sha256"]:
            raise PiJobStoreError("Pi job upload SHA-256 does not match its declaration")
        text = self._read_program_text_path(path)
        polygon = validate_guarded_output_polygon(
            record["guarded_output_polygon_mm"]
        )
        try:
            validation = validator(text, polygon)
        except Exception:
            raise
        return text, self._require_validation(validation)

    def _fail_receiving(self, record: dict[str, Any], message: str) -> None:
        self._part_path(record["job_id"]).unlink(missing_ok=True)
        record["state"] = "failed"
        record["error"] = str(message)[:4096]
        record["finished_at"] = self._now()
        record["updated_at"] = record["finished_at"]
        record["revision"] += 1
        self._write_record(record)
        self._apply_retention()

    def finalize(self, job_id: str, validator: JobValidator) -> FinalizeResult:
        try:
            canonical = validate_job_id(job_id)
        except PiJobProtocolError as exc:
            raise PiJobStoreError(str(exc)) from exc
        if not callable(validator):
            raise PiJobStoreError("Pi job finalize requires a validator callback")
        with self._lock:
            record = self._load_record(canonical)
            if record["state"] == "prepared":
                path = self._program_path(canonical)
                if not path.is_file():
                    raise PiJobStoreError("Prepared Pi job is missing its committed program")
                _, validation = self._validate_artifact(record, path, validator)
                if self._validation_fields(validation) != {
                    key: record[key] for key in self._validation_fields(validation)
                }:
                    raise PiJobStoreError(
                        "Prepared Pi job validation metadata changed after finalize"
                    )
                return FinalizeResult(self._public(record), validation)
            if record["state"] != "receiving":
                raise PiJobStoreError("Only a receiving or prepared Pi job can finalize")
            record = self._synchronize_receiving_size(record)
            part_path = self._part_path(canonical)
            try:
                _, validation = self._validate_artifact(record, part_path, validator)
            except Exception as exc:
                self._fail_receiving(record, str(exc))
                raise
            authority = self._validation_fields(validation)
            journal = {
                "schema": JOB_RECORD_SCHEMA,
                "job_id": canonical,
                "expected_size": record["expected_size"],
                "expected_sha256": record["expected_sha256"],
                "guarded_output_polygon_mm": record["guarded_output_polygon_mm"],
                **authority,
            }
            atomic_write_json(self._journal_path(canonical), journal)
            program_path = self._program_path(canonical)
            try:
                os.replace(part_path, program_path)
                _fsync_parent_directory(self.programs_dir)
            except OSError as exc:
                raise PiJobStoreError(f"Could not commit validated Pi job: {exc}") from exc
            record.update(authority)
            record["state"] = "prepared"
            record["program_retained"] = True
            record["received_size"] = record["expected_size"]
            record["revision"] += 1
            record["updated_at"] = self._now()
            record["prepared_at"] = record["updated_at"]
            written = self._write_record(record)
            self._journal_path(canonical).unlink(missing_ok=True)
            _fsync_parent_directory(self.records_dir)
            return FinalizeResult(self._public(written), validation)

    def get(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            return self._public(self._load_record(job_id))

    def list_records(self) -> list[dict[str, Any]]:
        with self._lock:
            records = sorted(
                self._all_records(),
                key=lambda record: (record["created_at"], record["job_id"]),
                reverse=True,
            )
            return [self._public(record) for record in records]

    def read_program_bytes(self, job_id: str) -> bytes:
        with self._lock:
            record = self._load_record(job_id)
            if record["state"] == "receiving" or not record["program_retained"]:
                raise PiJobStoreError("Pi job has no retained validated program")
            path = self._program_path(record["job_id"])
            size, digest = self._file_size_and_sha(path)
            if size != record["expected_size"] or digest != record["expected_sha256"]:
                raise PiJobStoreError("Retained Pi job program failed its integrity check")
            try:
                return path.read_bytes()
            except OSError as exc:
                raise PiJobStoreError(f"Could not read Pi job program: {exc}") from exc

    def read_program(self, job_id: str) -> str:
        payload = self.read_program_bytes(job_id)
        try:
            return payload.decode("utf-8", errors="strict")
        except UnicodeError as exc:
            raise PiJobStoreError("Retained Pi job program is not valid UTF-8") from exc

    @staticmethod
    def _validate_update_fields(fields: Mapping[str, Any]) -> dict[str, Any]:
        if any(
            type(key) is not str
            or not key
            or key.startswith("_")
            or key in _PROTECTED_UPDATE_FIELDS
            for key in fields
        ):
            raise PiJobStoreError("Pi job state update contains a protected field")
        try:
            encoded = json.dumps(
                dict(fields),
                allow_nan=False,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, RecursionError) as exc:
            raise PiJobStoreError(f"Pi job state update is not strict JSON: {exc}") from exc
        if len(encoded) > _MAX_STATE_DETAILS_BYTES:
            raise PiJobStoreError("Pi job state update exceeds the bounded metadata limit")
        return copy.deepcopy(dict(fields))

    def update_state(
        self,
        job_id: str,
        state: str,
        **fields: Any,
    ) -> dict[str, Any]:
        if type(state) is not str or state not in JOB_STATES:
            raise PiJobStoreError("Pi job state is invalid")
        checked_fields = self._validate_update_fields(fields)
        with self._lock:
            record = self._load_record(job_id)
            current = record["state"]
            if state != current and state not in _STATE_TRANSITIONS[current]:
                raise PiJobStoreError(
                    f"Pi job state transition {current!r} -> {state!r} is not allowed"
                )
            if state in ACTIVE_JOB_STATES and current not in ACTIVE_JOB_STATES:
                active = self.active()
                if active is not None and active["job_id"] != record["job_id"]:
                    raise PiJobStoreError("Another Pi job already owns execution")
            if "powered" in checked_fields:
                if (
                    type(checked_fields["powered"]) is not bool
                    or checked_fields["powered"] is not record["powered"]
                ):
                    raise PiJobStoreError(
                        "Pi job powered state cannot change finalized execution authority"
                    )
            record.update(checked_fields)
            if state != current:
                record["state"] = state
            record["revision"] += 1
            record["updated_at"] = self._now()
            if state in TERMINAL_JOB_STATES and "finished_at" not in record:
                record["finished_at"] = record["updated_at"]
            written = self._write_record(record)
            if state in TERMINAL_JOB_STATES:
                self._apply_retention()
                if not self._record_path(record["job_id"]).exists():
                    return self._public(written)
                written = self._load_record(record["job_id"])
            return self._public(written)

    def active(self) -> dict[str, Any] | None:
        with self._lock:
            active = [
                record
                for record in self._all_records()
                if record["state"] in ACTIVE_JOB_STATES
            ]
            if len(active) > 1:
                raise PiJobStoreError("Multiple persisted Pi jobs claim execution ownership")
            return None if not active else self._public(active[0])

    def delete(self, job_id: str) -> dict[str, Any]:
        with self._lock:
            record = self._load_record(job_id)
            if record["state"] in ACTIVE_JOB_STATES:
                raise PiJobStoreError("An active Pi job cannot be deleted")
            self._delete_artifacts(record["job_id"], metadata=True)
            _fsync_parent_directory(self.records_dir)
            _fsync_parent_directory(self.programs_dir)
            return self._public(record)

    def _read_journal(self, path: Path) -> dict[str, Any]:
        try:
            raw = strict_json_loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise PiJobStoreError(f"Could not read Pi finalize journal {path.name}: {exc}") from exc
        if not isinstance(raw, dict):
            raise PiJobStoreError("Pi finalize journal must be a JSON object")
        expected = {
            "schema",
            "job_id",
            "expected_size",
            "expected_sha256",
            "guarded_output_polygon_mm",
            "program_digest",
            "requires_laser_authorization",
            "requires_motion",
            "powered",
            "execution_policy_digest",
        }
        if set(raw) != expected or raw.get("schema") != JOB_RECORD_SCHEMA:
            raise PiJobStoreError("Pi finalize journal has an invalid schema")
        try:
            validate_job_id(raw["job_id"])
            validate_job_size(raw["expected_size"])
            validate_sha256(raw["expected_sha256"], label="expected_sha256")
            validate_guarded_output_polygon(raw["guarded_output_polygon_mm"])
            validate_sha256(raw["program_digest"], label="program_digest")
            validate_sha256(
                raw["execution_policy_digest"],
                label="execution_policy_digest",
            )
        except PiJobProtocolError as exc:
            raise PiJobStoreError(f"Pi finalize journal is invalid: {exc}") from exc
        if (
            type(raw["requires_laser_authorization"]) is not bool
            or type(raw["requires_motion"]) is not bool
            or type(raw["powered"]) is not bool
            or raw["powered"] is not raw["requires_laser_authorization"]
        ):
            raise PiJobStoreError("Pi finalize journal has invalid execution flags")
        return dict(raw)

    def _recover_journals(self) -> None:
        for journal_path in sorted(self.records_dir.glob("*.finalize.json")):
            journal = self._read_journal(journal_path)
            job_id = journal["job_id"]
            if journal_path != self._journal_path(job_id):
                raise PiJobStoreError("Pi finalize journal identity does not match its filename")
            record = self._load_record(job_id)
            if record["state"] == "prepared":
                journal_path.unlink(missing_ok=True)
                continue
            if record["state"] != "receiving":
                raise PiJobStoreError("Pi finalize journal does not belong to a receiving job")
            if (
                record["expected_size"] != journal["expected_size"]
                or record["expected_sha256"] != journal["expected_sha256"]
                or record["guarded_output_polygon_mm"]
                != journal["guarded_output_polygon_mm"]
            ):
                raise PiJobStoreError("Pi finalize journal contradicts its job record")
            program_path = self._program_path(job_id)
            part_path = self._part_path(job_id)
            source = program_path if program_path.is_file() else part_path
            if not source.is_file():
                raise PiJobStoreError("Pi finalize journal has no complete program artifact")
            size, digest = self._file_size_and_sha(source)
            if size != record["expected_size"] or digest != record["expected_sha256"]:
                raise PiJobStoreError("Pi finalize journal artifact failed integrity validation")
            if source == part_path:
                os.replace(part_path, program_path)
                _fsync_parent_directory(self.programs_dir)
            authority_keys = self._validation_fields(
                JobValidation(
                    program_digest=journal["program_digest"],
                    requires_laser_authorization=journal[
                        "requires_laser_authorization"
                    ],
                    requires_motion=journal["requires_motion"],
                    execution_policy_digest=journal["execution_policy_digest"],
                )
            )
            record.update(authority_keys)
            record["state"] = "prepared"
            record["received_size"] = record["expected_size"]
            record["program_retained"] = True
            record["revision"] += 1
            record["updated_at"] = self._now()
            record["prepared_at"] = record["updated_at"]
            self._write_record(record)
            journal_path.unlink(missing_ok=True)

    def reconcile_boot(self) -> list[dict[str, Any]]:
        """Fail interrupted execution closed and clean stale partial artifacts."""

        with self._lock:
            self._recover_journals()
            now = self._now()
            changed: list[dict[str, Any]] = []
            records = self._all_records()
            by_id = {record["job_id"]: record for record in records}

            for part_path in sorted(self.programs_dir.glob("*.part")):
                job_id = part_path.name[: -len(".part")]
                try:
                    canonical = validate_job_id(job_id)
                except PiJobProtocolError:
                    part_path.unlink(missing_ok=True)
                    continue
                record = by_id.get(canonical)
                stale = now - part_path.stat().st_mtime > self.stale_part_seconds
                if record is None or record["state"] != "receiving":
                    part_path.unlink(missing_ok=True)
                elif stale:
                    part_path.unlink(missing_ok=True)
                    record["state"] = "failed"
                    record["error"] = "Stale partial upload was discarded at node startup"
                    record["finished_at"] = now
                    record["updated_at"] = now
                    record["revision"] += 1
                    written = self._write_record(record)
                    by_id[canonical] = written
                    changed.append(self._public(written))

            for record in list(by_id.values()):
                job_id = record["job_id"]
                if record["state"] in ACTIVE_JOB_STATES:
                    record["state"] = "interrupted"
                    record["error"] = (
                        "Pi node restarted during execution; the job was not resumed"
                    )
                    record["finished_at"] = now
                    record["updated_at"] = now
                    record["revision"] += 1
                    written = self._write_record(record)
                    by_id[job_id] = written
                    changed.append(self._public(written))
                    continue
                if record["state"] == "receiving":
                    part_path = self._part_path(job_id)
                    if not part_path.is_file():
                        record["state"] = "failed"
                        record["error"] = (
                            "Partial upload was missing at node startup"
                        )
                        record["finished_at"] = now
                        record["updated_at"] = now
                        record["revision"] += 1
                        written = self._write_record(record)
                        by_id[job_id] = written
                        changed.append(self._public(written))
                    else:
                        synchronized = self._synchronize_receiving_size(record)
                        by_id[job_id] = synchronized
                elif record["program_retained"]:
                    program_path = self._program_path(job_id)
                    if not program_path.is_file():
                        raise PiJobStoreError(
                            f"Retained Pi job {job_id} is missing its program"
                        )
                    size, digest = self._file_size_and_sha(program_path)
                    if (
                        size != record["expected_size"]
                        or digest != record["expected_sha256"]
                    ):
                        raise PiJobStoreError(
                            f"Retained Pi job {job_id} failed boot integrity verification"
                        )

            for program_path in sorted(self.programs_dir.glob("*.gcode")):
                job_id = program_path.name[: -len(".gcode")]
                try:
                    canonical = validate_job_id(job_id)
                except PiJobProtocolError:
                    program_path.unlink(missing_ok=True)
                    continue
                record = by_id.get(canonical)
                if record is None or not record["program_retained"]:
                    program_path.unlink(missing_ok=True)

            self._apply_retention()
            return changed


__all__ = [
    "ACTIVE_JOB_STATES",
    "DEFAULT_STALE_PART_SECONDS",
    "FinalizeResult",
    "JOB_RECORD_SCHEMA",
    "JOB_STATES",
    "JobValidation",
    "MAX_METADATA_RECORDS",
    "MAX_TERMINAL_PROGRAMS",
    "PiJobStore",
    "PiJobStoreError",
    "TERMINAL_JOB_STATES",
]
