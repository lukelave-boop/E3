from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..storage import (
    atomic_write_bytes_if_absent,
    atomic_write_json,
    strict_json_loads,
)

LOGGER = logging.getLogger(__name__)
FIXTURE_REACH_SCHEMA_VERSION = 1
FIXTURE_REACH_MIGRATION_SCHEMA_VERSION = 1
_FIXTURE_MODES = frozenset({"unclassified", "permanent", "movable"})
_LIMIT_KEYS = ("x_min", "x_max", "y_min", "y_max")
_SCOPE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _optional_finite(value: object, label: str) -> float | None:
    if value is None:
        return None
    if type(value) not in {int, float}:
        raise ValueError(f"{label} must be null or a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{label} must be null or a finite number")
    return number


def _scope_id(value: object) -> str:
    if type(value) is not str or _SCOPE_RE.fullmatch(value) is None:
        raise ValueError(
            "Fixture reach machine_id must use 1-80 lowercase letters, digits, "
            "dots, underscores, or hyphens"
        )
    return value


@dataclass(slots=True)
class FixtureReachEvidence:
    """Operator-recorded fixed-fixture classification and safe carriage limits.

    The limits are diagnostic evidence only. They never alter controller
    settings, machine.work_area, G-code, arming, or laser-output authority.
    """

    fixture_mode: str = "unclassified"
    x_min_mm: float | None = None
    x_max_mm: float | None = None
    y_min_mm: float | None = None
    y_max_mm: float | None = None
    observations: dict[str, dict[str, Any]] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)
    schema_version: int = FIXTURE_REACH_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or (
            self.schema_version != FIXTURE_REACH_SCHEMA_VERSION
        ):
            raise ValueError(
                "Fixture reach evidence schema_version is unsupported"
            )
        if type(self.fixture_mode) is not str or self.fixture_mode not in _FIXTURE_MODES:
            raise ValueError(
                "fixture_mode must be unclassified, permanent, or movable"
            )
        self.x_min_mm = _optional_finite(self.x_min_mm, "x_min_mm")
        self.x_max_mm = _optional_finite(self.x_max_mm, "x_max_mm")
        self.y_min_mm = _optional_finite(self.y_min_mm, "y_min_mm")
        self.y_max_mm = _optional_finite(self.y_max_mm, "y_max_mm")
        if (
            self.x_min_mm is not None
            and self.x_max_mm is not None
            and self.x_max_mm <= self.x_min_mm
        ):
            raise ValueError("x_max_mm must be greater than x_min_mm")
        if (
            self.y_min_mm is not None
            and self.y_max_mm is not None
            and self.y_max_mm <= self.y_min_mm
        ):
            raise ValueError("y_max_mm must be greater than y_min_mm")
        if not isinstance(self.observations, dict):
            raise ValueError("observations must be a JSON object")
        normalized: dict[str, dict[str, Any]] = {}
        for key, value in self.observations.items():
            if key not in _LIMIT_KEYS or not isinstance(value, Mapping):
                raise ValueError("observations contain an invalid limit entry")
            normalized[key] = copy.deepcopy(dict(value))
        self.observations = normalized
        if type(self.updated_at) not in {int, float} or not math.isfinite(
            float(self.updated_at)
        ):
            raise ValueError("updated_at must be a finite number")
        self.updated_at = float(self.updated_at)

    @property
    def complete(self) -> bool:
        return all(
            value is not None
            for value in (
                self.x_min_mm,
                self.x_max_mm,
                self.y_min_mm,
                self.y_max_mm,
            )
        )

    @property
    def safe_travel_area_mm(self) -> tuple[float, float, float, float] | None:
        if not self.complete:
            return None
        assert self.x_min_mm is not None
        assert self.x_max_mm is not None
        assert self.y_min_mm is not None
        assert self.y_max_mm is not None
        return (
            self.x_min_mm,
            self.x_max_mm,
            self.y_min_mm,
            self.y_max_mm,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "fixture_mode": self.fixture_mode,
            "safe_travel_limits_mm": {
                "x_min": self.x_min_mm,
                "x_max": self.x_max_mm,
                "y_min": self.y_min_mm,
                "y_max": self.y_max_mm,
            },
            "observations": copy.deepcopy(self.observations),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any] | None) -> FixtureReachEvidence:
        if raw is None:
            return cls()
        if not isinstance(raw, Mapping):
            raise ValueError("Fixture reach evidence must be a JSON object")
        limits_value = raw.get("safe_travel_limits_mm")
        if limits_value is None:
            limits: Mapping[str, Any] = {}
        elif not isinstance(limits_value, Mapping):
            raise ValueError("safe_travel_limits_mm must be a JSON object")
        else:
            limits = limits_value
        unknown_limits = sorted(
            str(key) for key in limits if key not in _LIMIT_KEYS
        )
        if unknown_limits:
            raise ValueError(
                "safe_travel_limits_mm contains unexpected key(s): "
                + ", ".join(unknown_limits)
            )
        observations_value = raw.get("observations")
        if observations_value is None:
            observations: Mapping[str, Any] = {}
        elif not isinstance(observations_value, Mapping):
            raise ValueError("observations must be a JSON object")
        else:
            observations = observations_value
        return cls(
            schema_version=raw.get(
                "schema_version", FIXTURE_REACH_SCHEMA_VERSION
            ),
            fixture_mode=raw.get("fixture_mode", "unclassified"),
            x_min_mm=limits.get("x_min"),
            x_max_mm=limits.get("x_max"),
            y_min_mm=limits.get("y_min"),
            y_max_mm=limits.get("y_max"),
            observations=copy.deepcopy(dict(observations)),
            updated_at=raw.get("updated_at", time.time()),
        )


class FixtureReachStore:
    """Persist diagnostic fixture reach evidence in one stable machine scope."""

    def __init__(
        self,
        data_dir: Path,
        *,
        machine_id: str,
        migrate_legacy: bool = False,
    ) -> None:
        self.machine_id = _scope_id(machine_id)
        self.data_dir = Path(data_dir)
        self.path = (
            self.data_dir
            / "machine_state"
            / self.machine_id
            / "fixture_reach.json"
        )
        self.legacy_path = self.data_dir / "fixture_reach.json"
        self.migration_path = (
            self.data_dir
            / "machine_state"
            / ".fixture_reach_legacy_claim.json"
        )
        self._load_error: str | None = None
        self._migration_error: str | None = None
        if type(migrate_legacy) is not bool:
            raise TypeError("migrate_legacy must be an exact boolean")
        if migrate_legacy and not self.path.exists():
            self._migrate_legacy()
        self._evidence = self._load()

    @property
    def evidence(self) -> FixtureReachEvidence:
        return self._evidence

    @property
    def load_error(self) -> str | None:
        return self._load_error or self._migration_error

    @property
    def migration_error(self) -> str | None:
        return self._migration_error

    def _load(self) -> FixtureReachEvidence:
        if not self.path.exists():
            return FixtureReachEvidence()
        try:
            raw = strict_json_loads(self.path.read_text(encoding="utf-8"))
            return FixtureReachEvidence.from_dict(raw)
        except (
            KeyError,
            OSError,
            OverflowError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            self._load_error = f"Saved fixture reach evidence is invalid: {exc}"
            LOGGER.warning("Ignoring invalid fixture reach evidence: %s", exc)
            return FixtureReachEvidence()

    def _read_migration_claim(self) -> tuple[str, str]:
        raw = strict_json_loads(self.migration_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("migration metadata must be a JSON object")
        allowed = {"schema_version", "claimed_machine_id", "legacy_sha256"}
        unknown = sorted(str(key) for key in raw if key not in allowed)
        if unknown:
            raise ValueError(
                f"unknown migration metadata key(s): {', '.join(unknown)}"
            )
        if type(raw.get("schema_version")) is not int or (
            raw.get("schema_version")
            != FIXTURE_REACH_MIGRATION_SCHEMA_VERSION
        ):
            raise ValueError("migration metadata schema_version is unsupported")
        claimed_machine_id = _scope_id(raw.get("claimed_machine_id"))
        digest = raw.get("legacy_sha256")
        if type(digest) is not str or _SHA256_RE.fullmatch(digest) is None:
            raise ValueError("migration metadata legacy_sha256 is invalid")
        return claimed_machine_id, digest

    def _migration_failure(self, message: str, exc: Exception) -> None:
        self._migration_error = f"{message}: {exc}"
        LOGGER.warning("%s: %s", message, exc)

    def _migrate_legacy(self) -> None:
        if not self.legacy_path.exists():
            return
        try:
            legacy_bytes = self.legacy_path.read_bytes()
            legacy_raw = strict_json_loads(legacy_bytes.decode("utf-8"))
            FixtureReachEvidence.from_dict(legacy_raw)
        except (
            KeyError,
            OSError,
            OverflowError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            self._migration_failure(
                "Legacy fixture reach evidence is invalid; it was not claimed",
                exc,
            )
            return

        digest = hashlib.sha256(legacy_bytes).hexdigest()
        if not self.migration_path.exists():
            claim = {
                "schema_version": FIXTURE_REACH_MIGRATION_SCHEMA_VERSION,
                "claimed_machine_id": self.machine_id,
                "legacy_sha256": digest,
            }
            encoded = (
                json.dumps(claim, indent=2, sort_keys=True, allow_nan=False)
                + "\n"
            ).encode("utf-8")
            try:
                atomic_write_bytes_if_absent(self.migration_path, encoded)
            except OSError as exc:
                self._migration_failure(
                    "Legacy fixture reach migration claim could not be recorded",
                    exc,
                )
                return

        try:
            claimed_machine_id, claimed_digest = self._read_migration_claim()
        except (
            OSError,
            RecursionError,
            TypeError,
            UnicodeError,
            ValueError,
        ) as exc:
            self._migration_failure(
                "Legacy fixture reach migration metadata is invalid",
                exc,
            )
            return
        if claimed_machine_id != self.machine_id:
            return
        if claimed_digest != digest:
            self._migration_error = (
                "Legacy fixture reach evidence changed after this machine claimed it; "
                "the changed file was not copied"
            )
            LOGGER.warning(self._migration_error)
            return
        try:
            atomic_write_bytes_if_absent(self.path, legacy_bytes)
        except OSError as exc:
            self._migration_failure(
                "Legacy fixture reach evidence could not be copied",
                exc,
            )

    def save(self, evidence: FixtureReachEvidence) -> FixtureReachEvidence:
        canonical = FixtureReachEvidence.from_dict(evidence.to_dict())
        atomic_write_json(self.path, canonical.to_dict())
        self._evidence = canonical
        self._load_error = None
        self._migration_error = None
        return canonical

    def set_fixture_mode(self, mode: str) -> FixtureReachEvidence:
        payload = self._evidence.to_dict()
        payload["fixture_mode"] = mode
        payload["updated_at"] = time.time()
        return self.save(FixtureReachEvidence.from_dict(payload))

    def set_safe_travel_area(
        self,
        *,
        x_min_mm: float,
        x_max_mm: float,
        y_min_mm: float,
        y_max_mm: float,
        source: str,
        machine_port: str,
        protocol: str,
    ) -> FixtureReachEvidence:
        now = time.time()
        observations = copy.deepcopy(self._evidence.observations)
        for key, value in (
            ("x_min", x_min_mm),
            ("x_max", x_max_mm),
            ("y_min", y_min_mm),
            ("y_max", y_max_mm),
        ):
            observations[key] = {
                "value_mm": float(value),
                "source": str(source),
                "recorded_at": now,
                "machine_port": str(machine_port),
                "protocol": str(protocol),
            }
        return self.save(
            FixtureReachEvidence(
                fixture_mode=self._evidence.fixture_mode,
                x_min_mm=x_min_mm,
                x_max_mm=x_max_mm,
                y_min_mm=y_min_mm,
                y_max_mm=y_max_mm,
                observations=observations,
                updated_at=now,
            )
        )

    def record_limit(
        self,
        key: str,
        *,
        value_mm: float,
        position_mm: tuple[float, float],
        machine_port: str,
        protocol: str,
    ) -> FixtureReachEvidence:
        if key not in _LIMIT_KEYS:
            raise ValueError("Fixture reach limit key is invalid")
        value = _optional_finite(value_mm, key)
        assert value is not None
        x, y = position_mm
        if not all(math.isfinite(float(item)) for item in (x, y)):
            raise ValueError("Trusted machine position must be finite")
        payload = self._evidence.to_dict()
        limits = dict(payload["safe_travel_limits_mm"])
        limits[key] = value
        payload["safe_travel_limits_mm"] = limits
        observations = dict(payload.get("observations") or {})
        observations[key] = {
            "value_mm": value,
            "source": "trusted_jog_position",
            "recorded_at": time.time(),
            "observed_position_mm": [float(x), float(y)],
            "machine_port": str(machine_port),
            "protocol": str(protocol),
        }
        payload["observations"] = observations
        payload["updated_at"] = time.time()
        return self.save(FixtureReachEvidence.from_dict(payload))

    def clear_limits(self) -> FixtureReachEvidence:
        return self.save(
            FixtureReachEvidence(
                fixture_mode=self._evidence.fixture_mode,
                updated_at=time.time(),
            )
        )


__all__ = [
    "FIXTURE_REACH_MIGRATION_SCHEMA_VERSION",
    "FIXTURE_REACH_SCHEMA_VERSION",
    "FixtureReachEvidence",
    "FixtureReachStore",
]
