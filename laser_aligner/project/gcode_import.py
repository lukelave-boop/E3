"""Safe, Qt-neutral import of foreign laser G-code into E3 project geometry.

This module does *not* execute or preserve a foreign controller program.  It
interprets a deliberately bounded 2-D subset and translates powered XY motion
into ordinary E3 ``SceneObject`` and output-disabled ``OperationLayer`` records.
The normal E3 toolpath generator and guarded execution path remain the only way
an imported design can later reach a controller.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .model import DEFAULT_LAYER_COLORS, LayerMode, OperationLayer, SceneObject

if TYPE_CHECKING:
    from .import_manifest import ImportScanManifest

GCODE_FILE_DIALOG_FILTER = (
    "G-code Files (*.gc *.gcode *.nc *.tap *.GC *.GCODE *.NC *.TAP)"
)
SUPPORTED_GCODE_SUFFIXES = {".gc", ".gcode", ".nc", ".tap"}
MAX_GCODE_FILE_BYTES = 32 * 1024 * 1024
MAX_GCODE_LINES = 500_000
MAX_GCODE_POWERED_MOVES = 250_000
MAX_GCODE_POLYLINE_POINTS = 1_000_000
ARC_CHORD_TOLERANCE_MM = 0.05
ARC_MAX_STEP_DEG = 5.0

_NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_WORD_RE = re.compile(rf"([A-Za-z])\s*({_NUMBER_PATTERN})")
_S_SCALE_COMMENT_RE = re.compile(
    rf"(?:s\s*[-_ ]?value\s*max|max(?:imum)?\s+s|s\s*max|\$30)\s*[:=]\s*({_NUMBER_PATTERN})",
    re.IGNORECASE,
)


class GcodeImportError(ValueError):
    """Raised when foreign G-code cannot be translated without unsafe guessing."""


@dataclass(slots=True)
class GcodeImportResult:
    """Native E3 layers and vector objects produced by one G-code import."""

    layers: list[OperationLayer]
    objects: list[SceneObject]
    warnings: list[str] = field(default_factory=list)
    source_name: str = ""
    line_count: int = 0
    powered_move_count: int = 0
    travel_move_count: int = 0
    power_scale: float = 1000.0


@dataclass(slots=True)
class _Move:
    points: list[tuple[float, float]]
    feed_mm_min: float
    power_s: float
    laser_mode: int


@dataclass(slots=True)
class _ParserState:
    x_mm: float = 0.0
    y_mm: float = 0.0
    absolute: bool = True
    units_to_mm: float = 1.0
    motion_mode: int | None = None
    laser_mode: int | None = None
    power_s: float = 0.0
    feed_mm_min: float | None = None
    plane: int = 17
    line_count: int = 0
    powered_move_count: int = 0
    travel_move_count: int = 0
    point_count: int = 0


@dataclass(slots=True)
class _LayerGeometry:
    feed_mm_min: float
    power_s: float
    laser_mode: int
    polylines: list[list[tuple[float, float]]] = field(default_factory=list)


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GcodeImportError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise GcodeImportError(f"{label} must be a finite number")
    return number


def _integer_code(value: float, label: str) -> int:
    rounded = round(value)
    if abs(value - rounded) > 1e-9:
        raise GcodeImportError(f"Unsupported non-integer {label} code {value:g}")
    return int(rounded)


def _strip_comments(line: str) -> tuple[str, str]:
    """Return controller text plus comments retained for S-scale hints."""

    comments: list[str] = []
    semicolon = line.find(";")
    if semicolon >= 0:
        comments.append(line[semicolon + 1 :])
        line = line[:semicolon]

    output: list[str] = []
    index = 0
    while index < len(line):
        if line[index] != "(":
            output.append(line[index])
            index += 1
            continue
        end = line.find(")", index + 1)
        if end < 0:
            raise GcodeImportError("Unterminated parenthesized G-code comment")
        comments.append(line[index + 1 : end])
        index = end + 1
    return "".join(output).strip(), " ".join(comments)


def _parse_words(line: str) -> list[tuple[str, float]]:
    words: list[tuple[str, float]] = []
    cursor = 0
    for match in _WORD_RE.finditer(line):
        gap = line[cursor : match.start()].strip()
        if gap:
            raise GcodeImportError(f"Unsupported G-code syntax near {gap!r}")
        words.append((match.group(1).upper(), _finite(match.group(2), "G-code value")))
        cursor = match.end()
    remainder = line[cursor:].strip()
    if remainder:
        raise GcodeImportError(f"Unsupported G-code syntax near {remainder!r}")
    return words


def _normalize_sweep(start: float, end: float, *, clockwise: bool) -> float:
    sweep = end - start
    if clockwise:
        while sweep >= 0.0:
            sweep -= math.tau
    else:
        while sweep <= 0.0:
            sweep += math.tau
    return sweep


def _sample_center_arc(
    start: tuple[float, float],
    end: tuple[float, float],
    center: tuple[float, float],
    *,
    clockwise: bool,
) -> list[tuple[float, float]]:
    radius_start = math.hypot(start[0] - center[0], start[1] - center[1])
    radius_end = math.hypot(end[0] - center[0], end[1] - center[1])
    if radius_start <= 1e-12:
        raise GcodeImportError("Arc center coincides with its start point")
    tolerance = max(0.01, radius_start * 1e-4)
    if abs(radius_start - radius_end) > tolerance:
        raise GcodeImportError(
            "G2/G3 I/J arc has inconsistent start/end radii; refusing to distort it"
        )

    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    if math.hypot(end[0] - start[0], end[1] - start[1]) <= 1e-10:
        sweep = -math.tau if clockwise else math.tau
    else:
        end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
        sweep = _normalize_sweep(start_angle, end_angle, clockwise=clockwise)

    max_step = math.radians(ARC_MAX_STEP_DEG)
    if radius_start > ARC_CHORD_TOLERANCE_MM:
        chord_step = 2.0 * math.acos(
            max(-1.0, min(1.0, 1.0 - ARC_CHORD_TOLERANCE_MM / radius_start))
        )
        if chord_step > 1e-9:
            max_step = min(max_step, chord_step)
    count = max(1, int(math.ceil(abs(sweep) / max_step)))
    points = [start]
    for index in range(1, count):
        angle = start_angle + sweep * index / count
        points.append(
            (
                center[0] + radius_start * math.cos(angle),
                center[1] + radius_start * math.sin(angle),
            )
        )
    points.append(end)
    return points


def _radius_arc_center(
    start: tuple[float, float],
    end: tuple[float, float],
    radius_word: float,
    *,
    clockwise: bool,
) -> tuple[float, float]:
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    chord = math.hypot(dx, dy)
    radius = abs(radius_word)
    if chord <= 1e-12:
        raise GcodeImportError("A full-circle R arc is ambiguous; use I/J center offsets")
    if radius <= 0.0 or chord > 2.0 * radius + 1e-9:
        raise GcodeImportError("G2/G3 R arc radius is too small for its endpoints")

    midpoint = ((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
    half_chord = chord / 2.0
    height = math.sqrt(max(0.0, radius * radius - half_chord * half_chord))
    perpendicular = (-dy / chord, dx / chord)
    candidates = (
        (
            midpoint[0] + perpendicular[0] * height,
            midpoint[1] + perpendicular[1] * height,
        ),
        (
            midpoint[0] - perpendicular[0] * height,
            midpoint[1] - perpendicular[1] * height,
        ),
    )
    want_major = radius_word < 0.0
    for center in candidates:
        start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
        end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
        sweep = _normalize_sweep(start_angle, end_angle, clockwise=clockwise)
        is_major = abs(sweep) > math.pi + 1e-9
        if is_major == want_major:
            return center
    return candidates[0]


def _motion_points(
    state: _ParserState,
    motion: int,
    values: dict[str, float],
) -> tuple[list[tuple[float, float]], tuple[float, float]]:
    factor = state.units_to_mm
    start = (state.x_mm, state.y_mm)

    if state.absolute:
        end_x = values["X"] * factor if "X" in values else state.x_mm
        end_y = values["Y"] * factor if "Y" in values else state.y_mm
    else:
        end_x = state.x_mm + values.get("X", 0.0) * factor
        end_y = state.y_mm + values.get("Y", 0.0) * factor
    end = (end_x, end_y)

    if motion in {0, 1}:
        return [start, end], end

    if state.plane != 17:
        raise GcodeImportError("Only XY-plane (G17) G2/G3 arcs can be imported")
    has_ij = "I" in values or "J" in values
    has_r = "R" in values
    if has_ij and has_r:
        raise GcodeImportError("G2/G3 cannot use both I/J and R in the E3 importer")
    if not has_ij and not has_r:
        raise GcodeImportError("G2/G3 requires I/J center offsets or an R radius")
    clockwise = motion == 2
    if has_ij:
        center = (
            start[0] + values.get("I", 0.0) * factor,
            start[1] + values.get("J", 0.0) * factor,
        )
    else:
        center = _radius_arc_center(
            start,
            end,
            values["R"] * factor,
            clockwise=clockwise,
        )
    return _sample_center_arc(start, end, center, clockwise=clockwise), end


def _power_scale_hint(comment: str) -> float | None:
    match = _S_SCALE_COMMENT_RE.search(comment)
    if match is None:
        return None
    value = _finite(match.group(1), "commented S-value maximum")
    if value <= 0.0:
        return None
    return value


def _infer_power_scale(maximum_s: float) -> float:
    if maximum_s <= 1.0:
        return 1.0
    if maximum_s <= 100.0:
        return 100.0
    if maximum_s <= 255.0:
        return 255.0
    if maximum_s <= 1000.0:
        return 1000.0
    return maximum_s


def _layer_key(move: _Move) -> tuple[float, float, int]:
    return (round(move.feed_mm_min, 6), round(move.power_s, 6), move.laser_mode)


def _append_move(geometry: _LayerGeometry, move: _Move) -> None:
    if len(move.points) < 2:
        return
    if geometry.polylines and math.dist(geometry.polylines[-1][-1], move.points[0]) <= 1e-8:
        geometry.polylines[-1].extend(move.points[1:])
    else:
        geometry.polylines.append(list(move.points))


def _recenter_objects(objects: Iterable[SceneObject], center: tuple[float, float]) -> None:
    items = list(objects)
    if not items:
        return
    bounds = items[0].bounds()
    for item in items[1:]:
        bounds = bounds.union(item.bounds())
    dx = float(center[0]) - bounds.center[0]
    dy = float(center[1]) - bounds.center[1]
    for item in items:
        item.transform = item.transform.copy(
            x_mm=item.transform.x_mm + dx,
            y_mm=item.transform.y_mm + dy,
        )



@dataclass(slots=True)
class _GcodeScanState:
    x_mm: float = 0.0
    y_mm: float = 0.0
    absolute: bool = True
    units_to_mm: float = 1.0
    motion_mode: int | None = None
    laser_mode: int | None = None
    power_s: float = 0.0
    feed_mm_min: float | None = None
    plane: int = 17
    line_count: int = 0
    powered_move_count: int = 0
    travel_move_count: int = 0


def _scan_gcode_endpoint(
    state: _GcodeScanState,
    values: dict[str, float],
) -> tuple[float, float]:
    factor = state.units_to_mm
    if state.absolute:
        end_x = values["X"] * factor if "X" in values else state.x_mm
        end_y = values["Y"] * factor if "Y" in values else state.y_mm
    else:
        end_x = state.x_mm + values.get("X", 0.0) * factor
        end_y = state.y_mm + values.get("Y", 0.0) * factor
    return end_x, end_y


def scan_gcode_project(
    text: str,
    *,
    source_name: str = "untitled.gcode",
    source_suffix: str | None = None,
    source_size_bytes: int | None = None,
    max_file_bytes: int = MAX_GCODE_FILE_BYTES,
) -> ImportScanManifest:
    """Return bounded, non-vectorizing facts before strict G-code translation."""

    from .import_manifest import (
        GCODE_IMPORTER_SPEC,
        ImportLayerManifest,
        ImportScanManifest,
    )

    suffix = (
        Path(source_name).suffix.casefold()
        if source_suffix is None
        else str(source_suffix).casefold()
    )
    if not suffix:
        suffix = ".gcode"

    if isinstance(text, str):
        encoded_size = len(text.encode("utf-8"))
    else:
        encoded_size = 0

    size = encoded_size if source_size_bytes is None else int(source_size_bytes)
    limit = int(max_file_bytes)
    if limit < 1:
        raise ValueError("max_file_bytes must be positive")

    base = {
        "importer_id": GCODE_IMPORTER_SPEC.importer_id,
        "source_name": source_name,
        "source_suffix": suffix,
        "source_size_bytes": max(0, size),
        "capabilities": GCODE_IMPORTER_SPEC.capabilities,
    }

    if suffix not in SUPPORTED_GCODE_SUFFIXES:
        return ImportScanManifest(
            **base,
            errors=(
                "G-code import accepts .gc, .gcode, .nc, and .tap files",
            ),
        )
    if not isinstance(text, str):
        return ImportScanManifest(
            **base,
            errors=("G-code input must be text",),
        )
    if size < 0:
        return ImportScanManifest(
            **base,
            errors=("G-code source size must not be negative",),
        )
    if size > limit or encoded_size > limit:
        measured = max(size, encoded_size)
        return ImportScanManifest(
            **base,
            errors=(
                f"G-code file exceeds the {limit:,}-byte import limit "
                f"({measured:,} bytes)",
            ),
        )

    lines = text.splitlines()
    if len(lines) > MAX_GCODE_LINES:
        return ImportScanManifest(
            **base,
            source_facts=(f"{len(lines):,} source lines",),
            errors=(
                f"G-code exceeds the {MAX_GCODE_LINES:,}-line import limit",
            ),
        )

    state = _GcodeScanState()
    warnings: list[str] = []
    warning_set: set[str] = set()
    approximations: list[str] = []
    approximation_set: set[str] = set()
    unsupported_features: list[str] = []
    unsupported_set: set[str] = set()
    errors: list[str] = []
    error_set: set[str] = set()
    power_scale_hint: float | None = None
    maximum_power_s = 0.0
    operation_counts: dict[tuple[float, float, int], int] = {}
    operation_order: list[tuple[float, float, int]] = []
    saw_mm = False
    saw_inches = False
    saw_absolute = False
    saw_relative = False
    saw_arc = False

    def add_unique(target: list[str], seen: set[str], message: str) -> None:
        if message not in seen:
            seen.add(message)
            target.append(message)

    def warn(message: str) -> None:
        add_unique(warnings, warning_set, message)

    def approximate(message: str) -> None:
        add_unique(approximations, approximation_set, message)

    def unsupported(message: str) -> None:
        add_unique(unsupported_features, unsupported_set, message)

    def error(message: str) -> None:
        add_unique(errors, error_set, message)

    for line_number, raw_line in enumerate(lines, start=1):
        state.line_count += 1
        try:
            code, comment = _strip_comments(raw_line)
        except GcodeImportError as exc:
            error(f"Line {line_number}: {exc}")
            continue

        try:
            hint = _power_scale_hint(comment)
        except GcodeImportError as exc:
            error(f"Line {line_number}: {exc}")
            continue
        if hint is not None:
            if power_scale_hint is not None and abs(power_scale_hint - hint) > 1e-9:
                error("G-code contains conflicting S-value maximum comments")
            else:
                power_scale_hint = hint

        if not code or code == "%":
            continue
        if code.startswith("/"):
            unsupported(
                f"Line {line_number}: optional block-delete syntax is unsupported"
            )
            continue
        if "*" in code:
            unsupported(f"Line {line_number}: checksummed G-code is unsupported")
            continue

        try:
            words = _parse_words(code)
        except GcodeImportError as exc:
            error(f"Line {line_number}: {exc}")
            continue
        if not words:
            continue

        values: dict[str, float] = {}
        g_codes: list[int] = []
        m_codes: list[int] = []
        line_blocked = False
        for letter, value in words:
            if letter == "G":
                try:
                    g_codes.append(_integer_code(value, "G"))
                except GcodeImportError as exc:
                    error(f"Line {line_number}: {exc}")
                    line_blocked = True
            elif letter == "M":
                try:
                    m_codes.append(_integer_code(value, "M"))
                except GcodeImportError as exc:
                    error(f"Line {line_number}: {exc}")
                    line_blocked = True
            elif letter in {"N", "T"}:
                if letter == "T":
                    warn(
                        "Tool-selection words will be ignored; "
                        "E3 imports only 2-D laser geometry"
                    )
            elif letter in {"X", "Y", "I", "J", "R", "F", "S"}:
                values[letter] = value
            elif letter in {"Z", "A", "B", "C", "U", "V", "W"}:
                unsupported(
                    f"Line {line_number}: {letter}-axis motion cannot be represented "
                    "by E3's 2-D importer"
                )
                line_blocked = True
            else:
                unsupported(
                    f"Line {line_number}: unsupported G-code word {letter}"
                )
                line_blocked = True

        if line_blocked:
            continue

        for code_value in g_codes:
            if code_value in {0, 1, 2, 3}:
                state.motion_mode = code_value
                if code_value in {2, 3}:
                    saw_arc = True
            elif code_value == 17:
                state.plane = 17
            elif code_value in {18, 19}:
                state.plane = code_value
            elif code_value == 20:
                state.units_to_mm = 25.4
                saw_inches = True
            elif code_value == 21:
                state.units_to_mm = 1.0
                saw_mm = True
            elif code_value == 90:
                state.absolute = True
                saw_absolute = True
            elif code_value == 91:
                state.absolute = False
                saw_relative = True
            elif code_value in {40, 94}:
                pass
            elif code_value == 4:
                approximate("G4 dwell commands will be omitted from imported geometry")
            elif 54 <= code_value <= 59:
                approximate(
                    "G54-G59 work-coordinate selection will not be retained; "
                    "the imported design will be recentered in E3"
                )
            else:
                unsupported(
                    f"Line {line_number}: G{code_value} can change geometry or "
                    "machine state and is unsupported"
                )
                line_blocked = True

        for code_value in m_codes:
            if code_value in {3, 4}:
                state.laser_mode = code_value
            elif code_value == 5:
                state.laser_mode = None
            elif code_value in {2, 30}:
                pass
            elif code_value in {7, 8, 9}:
                approximate(
                    "Coolant/air-assist M-codes will not be mapped automatically; "
                    "review air assist in E3"
                )
            else:
                unsupported(
                    f"Line {line_number}: unsupported M{code_value} command"
                )
                line_blocked = True

        if line_blocked:
            continue

        if "F" in values:
            feed = values["F"] * state.units_to_mm
            if not math.isfinite(feed) or feed <= 0.0:
                error(f"Line {line_number}: feed rate F must be positive")
                continue
            state.feed_mm_min = feed

        if "S" in values:
            if values["S"] < 0.0:
                error(f"Line {line_number}: negative laser power S is unsupported")
                continue
            state.power_s = values["S"]

        has_xy = "X" in values or "Y" in values
        has_arc_geometry = any(letter in values for letter in ("I", "J", "R"))
        explicit_motion = any(code_value in {0, 1, 2, 3} for code_value in g_codes)
        if not has_xy and not (explicit_motion and has_arc_geometry):
            continue

        if state.motion_mode is None:
            error(
                f"Line {line_number}: XY coordinates appear before a "
                "G0/G1/G2/G3 motion mode"
            )
            continue

        motion = state.motion_mode
        start = (state.x_mm, state.y_mm)
        end = _scan_gcode_endpoint(state, values)

        if motion in {2, 3}:
            saw_arc = True
            if state.plane != 17:
                unsupported(
                    f"Line {line_number}: only XY-plane (G17) G2/G3 arcs can be imported"
                )
                continue
            has_ij = "I" in values or "J" in values
            has_r = "R" in values
            if has_ij and has_r:
                error(
                    f"Line {line_number}: G2/G3 cannot use both I/J and R "
                    "in the E3 importer"
                )
                continue
            if not has_ij and not has_r:
                error(
                    f"Line {line_number}: G2/G3 requires I/J center offsets "
                    "or an R radius"
                )
                continue
            if has_r and math.dist(start, end) <= 1e-12:
                error(
                    f"Line {line_number}: a full-circle R arc is ambiguous; "
                    "use I/J center offsets"
                )
                continue
            approximate(
                "G2/G3 arcs will be sampled into bounded vector polylines "
                "during strict import"
            )

        moved = (
            motion in {2, 3}
            or math.dist(start, end) > 1e-12
        )
        state.x_mm, state.y_mm = end
        if not moved:
            continue

        powered = (
            motion != 0
            and state.laser_mode in {3, 4}
            and state.power_s > 0.0
        )
        if not powered:
            state.travel_move_count += 1
            continue

        if state.feed_mm_min is None:
            error(
                f"Line {line_number}: powered motion has no positive modal F feed rate"
            )
            continue

        state.powered_move_count += 1
        if state.powered_move_count > MAX_GCODE_POWERED_MOVES:
            error(
                f"G-code exceeds the {MAX_GCODE_POWERED_MOVES:,}-powered-move "
                "import limit"
            )
            continue

        maximum_power_s = max(maximum_power_s, state.power_s)
        key = (
            round(state.feed_mm_min, 6),
            round(state.power_s, 6),
            int(state.laser_mode),
        )
        if key not in operation_counts:
            operation_counts[key] = 0
            operation_order.append(key)
        operation_counts[key] += 1

    if not operation_order and not errors and not unsupported_features:
        error("G-code contains no powered 2-D laser motion that E3 can import")

    power_scale: float | None = None
    if operation_order:
        if power_scale_hint is not None:
            power_scale = power_scale_hint
            if maximum_power_s > power_scale + 1e-9:
                error(
                    f"Powered S value {maximum_power_s:g} exceeds the file's "
                    f"stated S maximum {power_scale:g}"
                )
            else:
                warn(
                    "Power percentages will be reconstructed from the file's "
                    f"stated S maximum of {power_scale:g}"
                )
        else:
            power_scale = _infer_power_scale(maximum_power_s)
            approximate(
                "No authoritative S-value maximum was found; E3 inferred "
                f"an S scale of {power_scale:g}. Imported output remains disabled "
                "until every power value is reviewed."
            )

    layers = tuple(
        ImportLayerManifest(
            source_key=(
                f"feed:{feed_mm_min:g}|power:{power_s:g}|laser:M{laser_mode}"
            ),
            name=(
                f"{feed_mm_min:g} mm/min · S{power_s:g} · M{laser_mode}"
            )[:80],
            mode_hint="line",
            object_count=1,
        )
        for feed_mm_min, power_s, laser_mode in operation_order
    )

    coordinate_facts: list[str] = []
    if saw_mm and saw_inches:
        coordinate_facts.append(
            "Program switches between millimetres (G21) and inches (G20); "
            "strict import converts all coordinates to millimetres"
        )
    elif saw_inches:
        coordinate_facts.append(
            "Program uses inch units (G20); strict import converts coordinates "
            "and feed rates to millimetres"
        )
    else:
        coordinate_facts.append(
            "Program is interpreted in millimetres unless a G20 inch-mode "
            "command is encountered"
        )

    if saw_absolute and saw_relative:
        coordinate_facts.append(
            "Program uses both absolute (G90) and relative (G91) positioning"
        )
    elif saw_relative:
        coordinate_facts.append("Program uses relative positioning (G91)")
    elif saw_absolute:
        coordinate_facts.append("Program uses absolute positioning (G90)")

    if saw_arc:
        coordinate_facts.append(
            "Only XY-plane G17 arcs are accepted; strict import resolves arc "
            "geometry before E3 recenters the design"
        )
    else:
        coordinate_facts.append(
            "Imported geometry is recentered in E3 rather than retaining the "
            "foreign controller work-coordinate origin"
        )

    source_facts = [
        f"{state.line_count:,} source lines",
        f"{state.powered_move_count:,} powered moves",
        f"{state.travel_move_count:,} travel/unpowered moves",
        f"{len(operation_order):,} reconstructed operation combinations",
    ]
    if power_scale is not None:
        source_facts.append(f"Power scale for review: {power_scale:g}")

    approximate(
        "Foreign travel moves, controller setup, dwell timing, and program-end "
        "commands are not retained; E3 will regenerate its own guarded program "
        "from the imported vectors."
    )

    return ImportScanManifest(
        **base,
        layers=layers,
        source_facts=tuple(source_facts),
        coordinate_facts=tuple(coordinate_facts),
        warnings=tuple(warnings),
        approximations=tuple(approximations),
        unsupported_features=tuple(unsupported_features),
        errors=tuple(errors),
    )


def scan_gcode_file(
    path: str | Path,
    *,
    max_file_bytes: int = MAX_GCODE_FILE_BYTES,
) -> ImportScanManifest:
    """Read one G-code file once and return a bounded pre-parse manifest."""

    from .import_manifest import GCODE_IMPORTER_SPEC, ImportScanManifest

    source = Path(path)
    suffix = source.suffix.casefold() or ".gcode"
    limit = int(max_file_bytes)
    if limit < 1:
        raise ValueError("max_file_bytes must be positive")

    try:
        size = source.stat().st_size
    except OSError as exc:
        return ImportScanManifest(
            importer_id=GCODE_IMPORTER_SPEC.importer_id,
            source_name=source.name or "untitled.gcode",
            source_suffix=suffix,
            source_size_bytes=0,
            capabilities=GCODE_IMPORTER_SPEC.capabilities,
            errors=(f"Could not read G-code file metadata: {exc}",),
        )

    if suffix not in SUPPORTED_GCODE_SUFFIXES:
        return ImportScanManifest(
            importer_id=GCODE_IMPORTER_SPEC.importer_id,
            source_name=source.name,
            source_suffix=suffix,
            source_size_bytes=max(0, size),
            capabilities=GCODE_IMPORTER_SPEC.capabilities,
            errors=(
                "G-code import accepts .gc, .gcode, .nc, and .tap files",
            ),
        )

    if size > limit:
        return ImportScanManifest(
            importer_id=GCODE_IMPORTER_SPEC.importer_id,
            source_name=source.name,
            source_suffix=suffix,
            source_size_bytes=size,
            capabilities=GCODE_IMPORTER_SPEC.capabilities,
            errors=(
                f"G-code file exceeds the {limit:,}-byte import limit",
            ),
        )

    try:
        payload = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        return ImportScanManifest(
            importer_id=GCODE_IMPORTER_SPEC.importer_id,
            source_name=source.name,
            source_suffix=suffix,
            source_size_bytes=size,
            capabilities=GCODE_IMPORTER_SPEC.capabilities,
            errors=("G-code file is not valid UTF-8/ASCII text",),
        )
    except OSError as exc:
        return ImportScanManifest(
            importer_id=GCODE_IMPORTER_SPEC.importer_id,
            source_name=source.name,
            source_suffix=suffix,
            source_size_bytes=size,
            capabilities=GCODE_IMPORTER_SPEC.capabilities,
            errors=(f"Could not read G-code file: {exc}",),
        )

    return scan_gcode_project(
        payload,
        source_name=source.name,
        source_suffix=suffix,
        source_size_bytes=size,
        max_file_bytes=limit,
    )


def _raise_for_blocked_gcode_manifest(manifest: ImportScanManifest) -> None:
    if manifest.errors:
        raise GcodeImportError(manifest.errors[0])
    if manifest.unsupported_features:
        raise GcodeImportError(manifest.unsupported_features[0])


def parse_gcode_project(
    text: str,
    *,
    source_name: str = "",
    center: tuple[float, float] = (0.0, 0.0),
) -> GcodeImportResult:
    """Translate a bounded 2-D laser G-code program into native E3 project data."""

    if not isinstance(text, str):
        raise GcodeImportError("G-code input must be text")
    state = _ParserState()
    moves: list[_Move] = []
    warnings: list[str] = []
    warning_set: set[str] = set()
    power_scale_hint: float | None = None

    def warn(message: str) -> None:
        if message not in warning_set:
            warning_set.add(message)
            warnings.append(message)

    lines = text.splitlines()
    if len(lines) > MAX_GCODE_LINES:
        raise GcodeImportError(
            f"G-code exceeds the {MAX_GCODE_LINES:,}-line import limit"
        )

    for line_number, raw_line in enumerate(lines, start=1):
        state.line_count += 1
        code, comment = _strip_comments(raw_line)
        hint = _power_scale_hint(comment)
        if hint is not None:
            if power_scale_hint is not None and abs(power_scale_hint - hint) > 1e-9:
                raise GcodeImportError(
                    "G-code contains conflicting S-value maximum comments"
                )
            power_scale_hint = hint
        if not code or code == "%":
            continue
        if code.startswith("/"):
            raise GcodeImportError(
                f"Line {line_number}: optional block-delete syntax is unsupported"
            )
        if "*" in code:
            raise GcodeImportError(
                f"Line {line_number}: checksummed G-code is unsupported"
            )
        try:
            words = _parse_words(code)
        except GcodeImportError as exc:
            raise GcodeImportError(f"Line {line_number}: {exc}") from exc
        if not words:
            continue

        values: dict[str, float] = {}
        g_codes: list[int] = []
        m_codes: list[int] = []
        for letter, value in words:
            if letter == "G":
                g_codes.append(_integer_code(value, "G"))
            elif letter == "M":
                m_codes.append(_integer_code(value, "M"))
            elif letter in {"N", "T"}:
                if letter == "T":
                    warn("Tool-selection words were ignored; E3 imports only 2-D laser geometry")
                continue
            elif letter in {"X", "Y", "I", "J", "R", "F", "S"}:
                values[letter] = value
            elif letter in {"Z", "A", "B", "C", "U", "V", "W"}:
                raise GcodeImportError(
                    f"Line {line_number}: {letter}-axis motion cannot be represented by E3's 2-D importer"
                )
            else:
                raise GcodeImportError(
                    f"Line {line_number}: unsupported G-code word {letter}"
                )

        for code_value in g_codes:
            if code_value in {0, 1, 2, 3}:
                state.motion_mode = code_value
            elif code_value == 17:
                state.plane = 17
            elif code_value in {18, 19}:
                state.plane = code_value
            elif code_value == 20:
                state.units_to_mm = 25.4
            elif code_value == 21:
                state.units_to_mm = 1.0
            elif code_value == 90:
                state.absolute = True
            elif code_value == 91:
                state.absolute = False
            elif code_value in {40, 94}:
                # G40 only cancels cutter-radius compensation; importing the
                # programmed XY path is therefore safe. G41/G42 remain rejected.
                pass
            elif code_value == 4:
                warn("G4 dwell commands were omitted from imported geometry")
            elif 54 <= code_value <= 59:
                warn(
                    "G54-G59 work-coordinate selection was not retained; the imported design is recentered in E3"
                )
            else:
                raise GcodeImportError(
                    f"Line {line_number}: G{code_value} can change geometry or machine state and is unsupported"
                )

        for code_value in m_codes:
            if code_value in {3, 4}:
                state.laser_mode = code_value
            elif code_value == 5:
                state.laser_mode = None
            elif code_value in {2, 30}:
                pass
            elif code_value in {7, 8, 9}:
                warn(
                    "Coolant/air-assist M-codes were not mapped automatically; review air assist in E3"
                )
            else:
                raise GcodeImportError(
                    f"Line {line_number}: unsupported M{code_value} command"
                )

        if "F" in values:
            feed = values["F"] * state.units_to_mm
            if not math.isfinite(feed) or feed <= 0.0:
                raise GcodeImportError(
                    f"Line {line_number}: feed rate F must be positive"
                )
            state.feed_mm_min = feed
        if "S" in values:
            if values["S"] < 0.0:
                raise GcodeImportError(
                    f"Line {line_number}: negative laser power S is unsupported"
                )
            state.power_s = values["S"]

        has_xy = "X" in values or "Y" in values
        has_arc_geometry = any(letter in values for letter in ("I", "J", "R"))
        explicit_motion = any(code_value in {0, 1, 2, 3} for code_value in g_codes)
        if not has_xy and not (explicit_motion and has_arc_geometry):
            continue
        if state.motion_mode is None:
            raise GcodeImportError(
                f"Line {line_number}: XY coordinates appear before a G0/G1/G2/G3 motion mode"
            )

        motion = state.motion_mode
        try:
            points, end = _motion_points(state, motion, values)
        except GcodeImportError as exc:
            raise GcodeImportError(f"Line {line_number}: {exc}") from exc
        state.x_mm, state.y_mm = end
        if len(points) < 2 or all(math.dist(points[0], point) <= 1e-12 for point in points[1:]):
            continue

        powered = motion != 0 and state.laser_mode in {3, 4} and state.power_s > 0.0
        if not powered:
            state.travel_move_count += 1
            continue
        if state.feed_mm_min is None:
            raise GcodeImportError(
                f"Line {line_number}: powered motion has no positive modal F feed rate"
            )
        state.powered_move_count += 1
        if state.powered_move_count > MAX_GCODE_POWERED_MOVES:
            raise GcodeImportError(
                f"G-code exceeds the {MAX_GCODE_POWERED_MOVES:,}-powered-move import limit"
            )
        state.point_count += len(points)
        if state.point_count > MAX_GCODE_POLYLINE_POINTS:
            raise GcodeImportError(
                "G-code arc sampling produces too many vector points "
                f"(limit {MAX_GCODE_POLYLINE_POINTS:,})"
            )
        moves.append(
            _Move(
                points=points,
                feed_mm_min=state.feed_mm_min,
                power_s=state.power_s,
                laser_mode=state.laser_mode,
            )
        )

    if not moves:
        raise GcodeImportError(
            "G-code contains no powered 2-D laser motion that E3 can import"
        )

    maximum_s = max(move.power_s for move in moves)
    if power_scale_hint is not None:
        power_scale = power_scale_hint
        if maximum_s > power_scale + 1e-9:
            raise GcodeImportError(
                f"Powered S value {maximum_s:g} exceeds the file's stated S maximum {power_scale:g}"
            )
        warn(
            f"Power percentages were reconstructed from the file's stated S maximum of {power_scale:g}"
        )
    else:
        power_scale = _infer_power_scale(maximum_s)
        warn(
            f"No authoritative S-value maximum was found; E3 inferred an S scale of {power_scale:g}. "
            "Imported output remains disabled until every power value is reviewed."
        )

    geometry_by_key: dict[tuple[float, float, int], _LayerGeometry] = {}
    key_order: list[tuple[float, float, int]] = []
    for move in moves:
        key = _layer_key(move)
        geometry = geometry_by_key.get(key)
        if geometry is None:
            geometry = _LayerGeometry(
                feed_mm_min=move.feed_mm_min,
                power_s=move.power_s,
                laser_mode=move.laser_mode,
            )
            geometry_by_key[key] = geometry
            key_order.append(key)
        _append_move(geometry, move)

    layers: list[OperationLayer] = []
    objects: list[SceneObject] = []
    source_stem = Path(source_name).stem if source_name else "G-code"
    for index, key in enumerate(key_order):
        geometry = geometry_by_key[key]
        power_percent = max(0.0, min(100.0, geometry.power_s / power_scale * 100.0))
        layer = OperationLayer(
            name=(
                f"G-code · {geometry.feed_mm_min:g} mm/min · "
                f"{power_percent:.1f}% (S{geometry.power_s:g})"
            )[:80],
            color=DEFAULT_LAYER_COLORS[index % len(DEFAULT_LAYER_COLORS)],
            mode=LayerMode.LINE,
            speed_mm_min=geometry.feed_mm_min,
            power_percent=power_percent,
            passes=1,
            output_enabled=False,
            priority=index,
        )
        layers.append(layer)

        all_points = [point for polyline in geometry.polylines for point in polyline]
        min_x = min(point[0] for point in all_points)
        max_x = max(point[0] for point in all_points)
        min_y = min(point[1] for point in all_points)
        max_y = max(point[1] for point in all_points)
        item = SceneObject.path(
            layer.id,
            [
                {"points": [[x, y] for x, y in polyline], "closed": False}
                for polyline in geometry.polylines
            ],
            name=f"{source_stem} · operation {index + 1}",
            center=((min_x + max_x) / 2.0, (min_y + max_y) / 2.0),
            source_name=source_name,
        )
        item.metadata.update(
            {
                "gcode_import_review_required": True,
                "gcode_source": source_name,
                "gcode_feed_mm_min": geometry.feed_mm_min,
                "gcode_power_s": geometry.power_s,
                "gcode_power_scale": power_scale,
                "gcode_laser_mode": f"M{geometry.laser_mode}",
            }
        )
        objects.append(item)

    _recenter_objects(objects, center)
    warn(
        "Foreign travel moves, controller setup, dwell timing, and program-end commands are not retained; "
        "E3 will regenerate its own guarded program from the imported vectors."
    )
    return GcodeImportResult(
        layers=layers,
        objects=objects,
        warnings=warnings,
        source_name=source_name,
        line_count=state.line_count,
        powered_move_count=state.powered_move_count,
        travel_move_count=state.travel_move_count,
        power_scale=power_scale,
    )


def load_gcode_project(
    path: str | Path,
    *,
    center: tuple[float, float] = (0.0, 0.0),
    max_file_bytes: int = MAX_GCODE_FILE_BYTES,
) -> GcodeImportResult:
    """Load a supported G-code file and translate it into native E3 project data."""

    source = Path(path)
    if source.suffix.casefold() not in SUPPORTED_GCODE_SUFFIXES:
        raise GcodeImportError(
            "G-code import accepts .gc, .gcode, .nc, and .tap files"
        )
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise GcodeImportError(f"Could not read G-code file metadata: {exc}") from exc
    if size > max_file_bytes:
        raise GcodeImportError(
            f"G-code file exceeds the {max_file_bytes:,}-byte import limit"
        )
    try:
        payload = source.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GcodeImportError("G-code file is not valid UTF-8/ASCII text") from exc
    except OSError as exc:
        raise GcodeImportError(f"Could not read G-code file: {exc}") from exc
    manifest = scan_gcode_project(
        payload,
        source_name=source.name,
        source_suffix=source.suffix.casefold(),
        source_size_bytes=size,
        max_file_bytes=max_file_bytes,
    )
    _raise_for_blocked_gcode_manifest(manifest)
    return parse_gcode_project(payload, source_name=source.name, center=center)
