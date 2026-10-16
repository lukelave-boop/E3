"""Lossless wire encoding and conservative limits for guarded GRBL G1 runs.

This module grants no motion or laser authority. Callers must validate the
original immutable program and keep one compactor inside one contiguous,
exclusively owned G1 stream. All other commands are stream barriers.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

_LINE_LIMIT = 256
_CANONICAL_LINEAR_MOVE = re.compile(
    r"G1 X(-?[0-9]+(?:\.[0-9]+)?) Y(-?[0-9]+(?:\.[0-9]+)?) "
    r"F([0-9]+(?:\.[0-9]+)?)(?: S([0-9]+(?:\.[0-9]+)?))?",
    re.ASCII,
)
_OPTIONS = re.compile(r"\[OPT:[A-Za-z0-9]*,([0-9]{1,6}),([0-9]{1,6})\]", re.ASCII)


def _number(value: str) -> str:
    """Remove only decimal padding, without binary or decimal rounding."""

    negative = value.startswith("-")
    unsigned = value[1:] if negative else value
    whole, dot, fraction = unsigned.partition(".")
    whole = whole.lstrip("0") or "0"
    fraction = fraction.rstrip("0")
    result = whole + (dot + fraction if fraction else "")
    return "-" + result if negative and result != "0" else result


def _linear_values(command: str) -> tuple[str, str, str, str | None] | None:
    if type(command) is not str or len(command) > _LINE_LIMIT:
        return None
    match = _CANONICAL_LINEAR_MOVE.fullmatch(command)
    if match is None:
        return None
    x, y, feed, power = match.groups()
    feed = _number(feed)
    if feed == "0":
        return None
    return _number(x), _number(y), feed, None if power is None else _number(power)


def canonical_linear_move(command: str) -> bool:
    """Whether an already guarded line has the exact supported G1 spelling.

    Eligibility is deliberately narrower than the normal program parser. It is
    not a replacement for work-area, feed, power, or temporary-arming checks.
    """

    return _linear_values(command) is not None


class GrblStreamCompactor:
    """Preserve exact decimal words while omitting redundant modal G1 words.

    ``encode`` advances state once per original line. The caller must retain its
    returned wire line while waiting for capacity, and abandon/reset this object
    after any failed write. Reset before every non-G1 command or stream boundary.
    No command is dropped, including coincident moves and changes to S0.
    """

    def __init__(self) -> None:
        self._values: tuple[str, str, str, str | None] | None = None

    def reset(self) -> None:
        self._values = None

    def encode(self, command: str) -> str:
        values = _linear_values(command)
        if values is None:
            raise ValueError("GRBL stream compaction requires canonical G1 X Y F [S]")
        x, y, feed, power = values
        previous = self._values
        if previous is None:
            words = ["G1", "X" + x, "Y" + y, "F" + feed]
        else:
            words = []
            if x != previous[0]:
                words.append("X" + x)
            if y != previous[1]:
                words.append("Y" + y)
            # Retain an axis word even when both endpoints are identical.
            if not words:
                words.append("X" + x)
            if feed != previous[2]:
                words.append("F" + feed)
        if power is not None and (previous is None or power != previous[3]):
            words.append("S" + power)
        self._values = (x, y, feed, power if power is not None else previous[3] if previous else None)
        return "".join(words)


@dataclass(frozen=True, slots=True)
class GrblStreamWindow:
    """Bounded outstanding wire bytes (including newlines) and command count."""

    rx_bytes: int | None
    max_pending: int


def parse_stream_window(firmware_identity: Sequence[str]) -> GrblStreamWindow:
    """Use only an unambiguous strict OPT report from the existing handshake.

    A ring-buffer slot is reserved, even if a firmware reports usable capacity.
    The host never permits more than 127 bytes or 16 unacknowledged lines, and
    also respects a smaller advertised planner count. Missing, duplicate, or
    malformed capacity evidence retains one outstanding command. This function
    sends no probes and does not infer capacity from realtime Bf telemetry.
    """

    unknown = GrblStreamWindow(rx_bytes=None, max_pending=1)
    options = [line for line in firmware_identity if type(line) is str and line.startswith("[OPT")]
    if len(options) != 1 or len(options[0]) > _LINE_LIMIT:
        return unknown
    match = _OPTIONS.fullmatch(options[0])
    if match is None:
        return unknown
    planner, receive = (int(value) for value in match.groups())
    if planner < 1 or receive < 2:
        return unknown
    return GrblStreamWindow(rx_bytes=min(receive - 1, 127), max_pending=min(planner, 16))
