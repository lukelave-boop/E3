from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .preview import exact_codes, parse_spot_offset_comment, parse_words, strip_comment


@dataclass(slots=True, frozen=True)
class PlannedMove:
    index: int
    line_number: int
    start_x: float
    start_y: float
    end_x: float
    end_y: float
    rapid: bool
    laser_on: bool
    power: float
    feed_mm_min: float
    layer_id: str
    layer_name: str
    layer_color: str
    layer_mode: str
    pass_index: int
    pass_count: int
    source_name: str
    distance_mm: float
    duration_seconds: float
    start_seconds: float
    end_seconds: float
    vector_power_correction: float = 0.0
    raster_power_correction: float = 0.0


@dataclass(slots=True, frozen=True)
class JobPlan:
    moves: tuple[PlannedMove, ...]
    bounds_mm: tuple[float, float, float, float]
    cut_distance_mm: float
    travel_distance_mm: float
    cut_seconds: float
    travel_seconds: float
    total_seconds: float
    maximum_power: float
    power_max: int
    warnings: tuple[str, ...]
    planner_mode: str = "source order"
    source_order_travel_mm: float | None = None
    planner_savings_mm: float = 0.0
    spot_offset_x: float = 0.0
    spot_offset_y: float = 0.0
    acceleration_mm_s2: float | None = None
    command_delay_ms: float = 0.0

    @property
    def powered(self) -> bool:
        return any(move.laser_on and move.power > 0 for move in self.moves)


def e3_metadata_line(kind: str, payload: Mapping[str, Any]) -> str:
    """Return a controller-ignored metadata comment for exact preview context."""

    serialized = json.dumps(dict(payload), ensure_ascii=True, separators=(",", ":"))
    return f"; @E3_{kind.upper()} {serialized}"


def _metadata(raw_line: str) -> tuple[str, dict[str, Any]] | None:
    stripped = raw_line.strip()
    if not stripped.startswith("; @E3_"):
        return None
    prefix, _, payload = stripped.partition(" ")
    prefix, _, payload = payload.partition(" ")
    if not prefix.startswith("@E3_") or not payload:
        return None
    try:
        parsed = json.loads(payload)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(parsed, dict):
        return None
    return prefix[4:].lower(), parsed


def build_job_plan(
    text: str,
    *,
    power_max: int,
    default_feed_mm_min: float = 1000.0,
    start_position: tuple[float, float] = (0.0, 0.0),
    acceleration_mm_s2: float | None = None,
    command_delay_ms: float = 0.0,
) -> JobPlan:
    """Build an immutable preview model from the exact streamable program."""

    x, y = (float(value) for value in start_position)
    absolute = True
    laser_on = False
    power = 0.0
    feed = max(1.0, float(default_feed_mm_min))
    spot_offset_x = spot_offset_y = 0.0
    elapsed = 0.0
    layer: dict[str, Any] = {}
    pass_metadata: dict[str, Any] = {}
    source: dict[str, Any] = {}
    job_metadata: dict[str, Any] = {}
    planner_metadata: dict[str, Any] = {}
    moves: list[PlannedMove] = []
    warnings: list[str] = []

    for line_number, raw_line in enumerate(text.splitlines(), 1):
        metadata = _metadata(raw_line)
        if metadata is not None:
            kind, payload = metadata
            if kind == "layer":
                layer = payload
            elif kind == "job":
                job_metadata = payload
                if not moves and "start_x" in payload and "start_y" in payload:
                    try:
                        start_x = float(payload["start_x"])
                        start_y = float(payload["start_y"])
                    except (TypeError, ValueError):
                        warnings.append("Job start metadata is invalid; using the supplied start pose")
                    else:
                        if math.isfinite(start_x) and math.isfinite(start_y):
                            x, y = start_x, start_y
                        else:
                            warnings.append(
                                "Job start metadata is non-finite; using the supplied start pose"
                            )
            elif kind == "planner":
                planner_metadata = payload
            elif kind == "pass":
                pass_metadata = payload
            elif kind == "path":
                source = payload
            continue
        spot_offset = parse_spot_offset_comment(raw_line)
        if spot_offset is not None:
            spot_offset_x, spot_offset_y = spot_offset
            continue
        line = strip_comment(raw_line)
        if not line:
            continue
        words = parse_words(line)
        g_codes = exact_codes(line, "G")
        m_codes = exact_codes(line, "M")
        values = {word.letter: word.value for word in words}
        if 90 in g_codes:
            absolute = True
        if 91 in g_codes:
            absolute = False
        if 5 in m_codes:
            laser_on = False
            power = 0.0
        if "S" in values:
            power = float(values["S"])
        if m_codes.intersection({3, 4}):
            laser_on = power > 0
        if "F" in values:
            feed = max(1.0, float(values["F"]))

        motion = 0 if 0 in g_codes else 1 if 1 in g_codes else None
        if motion is None:
            continue
        target_x = float(values.get("X", 0.0))
        target_y = float(values.get("Y", 0.0))
        new_x = target_x if absolute and "X" in values else x + target_x if "X" in values else x
        new_y = target_y if absolute and "Y" in values else y + target_y if "Y" in values else y
        if new_x == x and new_y == y:
            continue
        rapid = motion == 0
        if rapid and laser_on:
            warnings.append(f"Line {line_number}: rapid motion requested while laser is on")
        distance = math.hypot(new_x - x, new_y - y)
        velocity = feed / 60.0
        acceleration = (
            None
            if acceleration_mm_s2 is None or acceleration_mm_s2 <= 0
            else float(acceleration_mm_s2)
        )
        if acceleration is None:
            duration = distance / velocity
        else:
            ramp_distance = velocity * velocity / acceleration
            duration = (
                2.0 * math.sqrt(distance / acceleration)
                if distance < ramp_distance
                else 2.0 * velocity / acceleration
                + (distance - ramp_distance) / velocity
            )
        duration += max(0.0, float(command_delay_ms)) / 1000.0
        move = PlannedMove(
            index=len(moves),
            line_number=line_number,
            start_x=x + spot_offset_x,
            start_y=y + spot_offset_y,
            end_x=new_x + spot_offset_x,
            end_y=new_y + spot_offset_y,
            rapid=rapid,
            laser_on=laser_on and not rapid,
            power=power if laser_on and not rapid else 0.0,
            feed_mm_min=feed,
            layer_id=str(layer.get("id", "")),
            layer_name=str(layer.get("name", "Unassigned")),
            layer_color=str(layer.get("color", "#E35D6A")),
            layer_mode=str(layer.get("mode", "line")),
            pass_index=int(pass_metadata.get("index", 1)),
            pass_count=int(pass_metadata.get("count", 1)),
            source_name=str(source.get("name", "")),
            distance_mm=distance,
            duration_seconds=duration,
            start_seconds=elapsed,
            end_seconds=elapsed + duration,
            vector_power_correction=float(layer.get("vector_power_correction", 0.0)),
            raster_power_correction=float(layer.get("raster_power_correction", 0.0)),
        )
        moves.append(move)
        elapsed += duration
        x, y = new_x, new_y

    points = [
        coordinate
        for move in moves
        for coordinate in ((move.start_x, move.start_y), (move.end_x, move.end_y))
    ]
    if points:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        bounds = (min(xs), min(ys), max(xs), max(ys))
    else:
        bounds = (0.0, 0.0, 0.0, 0.0)
        warnings.append("The program contains no motion")
    cut_moves = [move for move in moves if move.laser_on]
    travel_moves = [move for move in moves if not move.laser_on]
    return JobPlan(
        moves=tuple(moves),
        bounds_mm=bounds,
        cut_distance_mm=sum(move.distance_mm for move in cut_moves),
        travel_distance_mm=sum(move.distance_mm for move in travel_moves),
        cut_seconds=sum(move.duration_seconds for move in cut_moves),
        travel_seconds=sum(move.duration_seconds for move in travel_moves),
        total_seconds=elapsed,
        maximum_power=max((move.power for move in cut_moves), default=0.0),
        power_max=max(1, int(power_max)),
        warnings=tuple(dict.fromkeys(warnings)),
        planner_mode=str(job_metadata.get("planner", "source order")),
        source_order_travel_mm=(
            float(planner_metadata["source_order_travel_mm"])
            if "source_order_travel_mm" in planner_metadata
            else None
        ),
        planner_savings_mm=float(planner_metadata.get("savings_mm", 0.0)),
        spot_offset_x=spot_offset_x,
        spot_offset_y=spot_offset_y,
        acceleration_mm_s2=acceleration_mm_s2,
        command_delay_ms=command_delay_ms,
    )


def restart_program_from_move(
    plan: JobPlan,
    move_index: int,
    *,
    power_mode: str = "M4",
    start_position: tuple[float, float] | None = None,
) -> tuple[str, JobPlan]:
    """Create a guarded absolute program beginning at a reviewed move boundary.

    ``start_position`` is the controller pose from which the replacement job
    will actually be streamed.  Recording it keeps the laser-off approach in
    the exact Preview; callers without a known pose retain the legacy behavior
    of beginning the model at the selected boundary.
    """

    if not 0 <= int(move_index) < len(plan.moves):
        raise ValueError("Start Here move is outside the generated job")
    selected = plan.moves[int(move_index)]
    controller_start_x = selected.start_x - plan.spot_offset_x
    controller_start_y = selected.start_y - plan.spot_offset_y
    if start_position is None:
        actual_start_x, actual_start_y = controller_start_x, controller_start_y
    else:
        actual_start_x, actual_start_y = (float(value) for value in start_position)
        if not math.isfinite(actual_start_x) or not math.isfinite(actual_start_y):
            raise ValueError("Start Here controller start position must be finite")
    lines = [
        "; E3 Positioning System reviewed Start Here job",
        f"; Starts at preview move {selected.index + 1}/{len(plan.moves)}",
        e3_metadata_line(
            "job",
            {"start_x": actual_start_x, "start_y": actual_start_y},
        ),
        "G21 ; millimetres",
        "G90 ; absolute positioning",
        "M5 ; laser off before positioning",
    ]
    if abs(plan.spot_offset_x) >= 1e-12 or abs(plan.spot_offset_y) >= 1e-12:
        lines.append(
            "; Laser spot offset (spot = controller + offset): "
            f"X{plan.spot_offset_x:g} Y{plan.spot_offset_y:g}"
        )
    lines.append(
        f"G0 X{controller_start_x:.3f} Y{controller_start_y:.3f} "
        f"F{selected.feed_mm_min:.3f}"
    )
    active_context: tuple[str, int, str] | None = None
    active_power = 0.0
    for move in plan.moves[int(move_index) :]:
        context = (move.layer_id, move.pass_index, move.source_name)
        if context != active_context:
            lines.extend(
                [
                    e3_metadata_line(
                        "layer",
                        {
                            "id": move.layer_id,
                            "name": move.layer_name,
                            "color": move.layer_color,
                            "mode": move.layer_mode,
                        },
                    ),
                    e3_metadata_line(
                        "pass",
                        {"index": move.pass_index, "count": move.pass_count},
                    ),
                    e3_metadata_line("path", {"name": move.source_name}),
                ]
            )
            active_context = context
        end_x = move.end_x - plan.spot_offset_x
        end_y = move.end_y - plan.spot_offset_y
        if move.laser_on:
            if active_power != move.power:
                lines.append(f"{power_mode.upper()} S{move.power:g}")
                active_power = move.power
            code = "G1"
        else:
            if active_power:
                lines.append("M5")
                active_power = 0.0
            code = "G0" if move.rapid else "G1"
        lines.append(
            f"{code} X{end_x:.3f} Y{end_y:.3f} F{move.feed_mm_min:.3f}"
        )
    lines.extend(["M5", "; End of reviewed Start Here job", ""])
    text = "\n".join(lines)
    restarted = build_job_plan(
        text,
        power_max=plan.power_max,
        default_feed_mm_min=selected.feed_mm_min,
        start_position=(actual_start_x, actual_start_y),
        acceleration_mm_s2=plan.acceleration_mm_s2,
        command_delay_ms=plan.command_delay_ms,
    )
    return text, restarted
