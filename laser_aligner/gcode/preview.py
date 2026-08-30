from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass

from ..air_assist import AIR_ASSIST_DIRECTIVE_PREFIX

# G-code words may be separated by spaces ("G1 X10") or packed together
# ("G1X10"). The parser intentionally handles both forms because many
# controllers and CAM programs emit compact lines.
_WORD_RE = re.compile(r"([A-Za-z])\s*([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)")
_SPOT_OFFSET_RE = re.compile(
    r"^\s*;\s*Laser spot offset .*?X\s*"
    r"([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)\s+Y\s*"
    r"([-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?)",
    re.IGNORECASE,
)


@dataclass(slots=True, frozen=True)
class GcodeWord:
    letter: str
    value: float


@dataclass(slots=True)
class GcodeSegment:
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    rapid: bool
    laser_on: bool
    power: float


def strip_comment(line: str) -> str:
    line = line.split(";", 1)[0]
    while "(" in line and ")" in line:
        start = line.find("(")
        end = line.find(")", start)
        if end < 0:
            break
        line = line[:start] + line[end + 1 :]
    return line.strip()


def parse_spot_offset_comment(line: str) -> tuple[float, float] | None:
    """Return the physical laser-spot offset recorded in a generated program."""

    match = _SPOT_OFFSET_RE.match(line)
    if match is None:
        return None
    return float(match.group(1)), float(match.group(2))


def parse_e3_metadata_comment(line: str) -> tuple[str, dict[str, object]] | None:
    """Parse one controller-ignored E3 metadata comment."""

    stripped = line.strip()
    if not stripped.startswith("; @E3_"):
        return None
    marker, separator, payload = stripped[2:].partition(" ")
    if not separator or not marker.startswith("@E3_") or not payload:
        return None
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return marker[4:].lower(), parsed


def parse_words(line: str, *, require_full_match: bool = False) -> list[GcodeWord]:
    """Parse numeric G-code words from a single line.

    When ``require_full_match`` is true, any non-whitespace text that is not a
    numeric word is rejected. This is used for streamed jobs so unmodelled
    controller features cannot bypass the coordinate and laser checks.
    """
    cleaned = strip_comment(line)
    words: list[GcodeWord] = []
    cursor = 0
    for match in _WORD_RE.finditer(cleaned):
        if require_full_match and cleaned[cursor : match.start()].strip():
            raise ValueError(f"Unsupported G-code syntax near: {cleaned[cursor:match.start()].strip()}")
        value = float(match.group(2))
        if not math.isfinite(value):
            raise ValueError("G-code values must be finite")
        words.append(GcodeWord(match.group(1).upper(), value))
        cursor = match.end()
    if require_full_match and cleaned[cursor:].strip():
        raise ValueError(f"Unsupported G-code syntax near: {cleaned[cursor:].strip()}")
    return words


def scan_word_state(
    line: str,
    *,
    comments_stripped: bool = False,
) -> tuple[set[int], set[int], dict[str, float]]:
    """Scan numeric words without allocating one ``GcodeWord`` per match.

    The scanner intentionally shares ``_WORD_RE`` and the same numeric,
    finiteness, uppercase-letter, and exact-integer G/M semantics as
    ``parse_words``. Callers that already stripped comments may set
    ``comments_stripped`` to avoid repeating that work.
    """

    cleaned = line if comments_stripped else strip_comment(line)
    g_codes: set[int] = set()
    m_codes: set[int] = set()
    values: dict[str, float] = {}
    for match in _WORD_RE.finditer(cleaned):
        value = float(match.group(2))
        if not math.isfinite(value):
            raise ValueError("G-code values must be finite")
        letter = match.group(1).upper()
        values[letter] = value
        if letter not in {"G", "M"}:
            continue
        rounded = round(value)
        if abs(value - rounded) >= 1e-9:
            continue
        code = int(rounded)
        if letter == "G":
            g_codes.add(code)
        else:
            m_codes.add(code)
    return g_codes, m_codes, values


def word_values(line: str, letter: str) -> list[float]:
    target = letter.upper()
    return [word.value for word in parse_words(line) if word.letter == target]


def exact_codes(line: str, letter: str) -> set[int]:
    codes: set[int] = set()
    for value in word_values(line, letter):
        rounded = round(value)
        if abs(value - rounded) < 1e-9:
            codes.add(int(rounded))
    return codes


def contains_positive_laser_command(line: str) -> bool:
    words = parse_words(line)
    m_codes = {int(round(word.value)) for word in words if word.letter == "M" and abs(word.value - round(word.value)) < 1e-9}
    powers = [word.value for word in words if word.letter == "S"]
    return bool(m_codes.intersection({3, 4}) or any(value > 0 for value in powers))


def contains_motion(line: str) -> bool:
    return bool(exact_codes(line, "G").intersection({0, 1, 2, 3}))


def parse_gcode_segments(text: str) -> list[GcodeSegment]:
    """Parse the conservative G0/G1 subset used by generated programs."""
    x = y = 0.0
    absolute = True
    laser_on = False
    power = 0.0
    spot_offset_x = spot_offset_y = 0.0
    segments: list[GcodeSegment] = []
    for raw_line in text.splitlines():
        if raw_line.strip().startswith(AIR_ASSIST_DIRECTIVE_PREFIX):
            continue
        metadata = parse_e3_metadata_comment(raw_line)
        if metadata is not None and metadata[0] == "job" and not segments:
            try:
                start_x = float(metadata[1]["start_x"])
                start_y = float(metadata[1]["start_y"])
            except (KeyError, TypeError, ValueError):
                pass
            else:
                if math.isfinite(start_x) and math.isfinite(start_y):
                    x, y = start_x, start_y
            continue
        spot_offset = parse_spot_offset_comment(raw_line)
        if spot_offset is not None:
            spot_offset_x, spot_offset_y = spot_offset
            continue
        line = strip_comment(raw_line)
        if not line:
            continue
        words = parse_words(line)
        g_codes = {
            int(round(word.value))
            for word in words
            if word.letter == "G" and abs(word.value - round(word.value)) < 1e-9
        }
        m_codes = {
            int(round(word.value))
            for word in words
            if word.letter == "M" and abs(word.value - round(word.value)) < 1e-9
        }
        values: dict[str, float] = {}
        for word in words:
            values[word.letter] = word.value

        if 90 in g_codes:
            absolute = True
        if 91 in g_codes:
            absolute = False
        if 5 in m_codes:
            laser_on = False
            power = 0.0
        if "S" in values:
            power = values["S"]
        if m_codes.intersection({3, 4}):
            laser_on = power > 0

        movement = 0 if 0 in g_codes else 1 if 1 in g_codes else None
        if movement is not None:
            target_x = values.get("X", 0.0)
            target_y = values.get("Y", 0.0)
            new_x = target_x if absolute and "X" in values else x + target_x if "X" in values else x
            new_y = target_y if absolute and "Y" in values else y + target_y if "Y" in values else y
            if new_x != x or new_y != y:
                segments.append(
                    GcodeSegment(
                        start_x=x + spot_offset_x,
                        start_y=y + spot_offset_y,
                        end_x=new_x + spot_offset_x,
                        end_y=new_y + spot_offset_y,
                        rapid=movement == 0,
                        laser_on=laser_on,
                        power=power,
                    )
                )
            x, y = new_x, new_y
    return segments
