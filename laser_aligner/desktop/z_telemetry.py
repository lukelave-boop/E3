"""Observational Ender step-counter readout; never motion authority."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from typing import Any

LIVE_Z_FRESH_SECONDS = 1.5


def _finite(value: object) -> float | None:
    try:
        return float(value) if type(value) in {int, float} and math.isfinite(value) else None
    except OverflowError:
        return None


class EnderZTelemetry:
    """Age received samples locally, independently of typed control results."""

    def __init__(self) -> None:
        self._sample: dict[str, Any] | None = None
        self._sample_at: float | None = None
        self._key: tuple[int, int] | None = None
        self._generation: int | None = None
        self._reset_at: float | None = None
        self._usable = False
        self._supported = False

    def clear(self) -> None:
        self._sample = None
        self._sample_at = None
        self._key = None

    def update(self, status: Mapping[str, Any]) -> None:
        now = time.monotonic()
        self._usable = bool(
            status.get("connected") is True
            and status.get("status_stale") is not True
            and not status.get("status_refresh_error")
            and status.get("controller_state") in {
                "READY_MOTION", "READY_HOME_REQUIRED", "JOB_RUNNING",
            }
        )
        raw = status.get("ender_z_telemetry")
        self._supported = isinstance(raw, Mapping) and raw.get("supported") is True
        if not isinstance(raw, Mapping) or any(
            type(raw.get(key)) is not bool for key in ("supported", "valid", "known", "homing", "moving")
        ):
            self.clear()
            return
        generation = raw.get("controller_generation")
        if type(generation) is not int or generation < 0:
            self.clear()
            return
        if self._generation is not None and self._generation != generation:
            self._reset_at = now
            self.clear()
        self._generation = generation
        age, z, sequence = _finite(raw.get("age_seconds")), _finite(raw.get("z_mm")), raw.get("sequence")
        if (
            not self._usable or not raw["supported"] or not raw["valid"]
            or age is None or not 0 <= age <= LIVE_Z_FRESH_SECONDS or z is None
            or type(sequence) is not int or not 0 <= sequence <= 0xFFFFFFFF
        ):
            self.clear()
            return
        key = (generation, sequence)
        sample_at = now - age
        if key == self._key and self._sample_at is not None:
            # Repeated cache snapshots do not make the same counter sample new.
            sample_at = min(sample_at, self._sample_at)
        self._sample = dict(raw, z_mm=z)
        self._key, self._sample_at = key, sample_at

    def readback_current(self, received_at: float | None) -> bool:
        return received_at is not None and (self._reset_at is None or received_at >= self._reset_at)

    def readout(self, *, active: bool, readback_at: float | None) -> tuple[str, str] | None:
        """Return label overrides, or None to retain a fresh idle typed readback."""
        readback_current = self.readback_current(readback_at)
        sample = self._sample
        fresh = bool(
            self._usable and sample is not None and self._sample_at is not None
            and 0 <= time.monotonic() - self._sample_at <= LIVE_Z_FRESH_SECONDS
        )
        if fresh:
            assert sample is not None and self._sample_at is not None
            if (readback_current and not active
                    and (not (sample["moving"] or sample["homing"]) or readback_at >= self._sample_at)):
                return None
            if sample["homing"]:
                qualifier = "live · homing / unreferenced"
            elif not sample["known"]:
                qualifier = "live · unreferenced"
            else:
                qualifier = "live"
            return (
                f"Z {sample['z_mm']:.3f} mm · {qualifier}",
                "Live controller step counter · unreferenced coordinate, not a border height."
                if sample["homing"] or not sample["known"] else
                "Live controller step counter · not an encoder measurement.",
            )
        if self._usable and readback_current and not active:
            return None
        if active or self._supported or self._reset_at is not None:
            return (
                "Z — mm",
                "Live Z unavailable · waiting for a fresh controller sample."
                if self._supported else "Live Z unavailable · no live telemetry from this controller.",
            )
        return None
