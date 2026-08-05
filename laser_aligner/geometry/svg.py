from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Iterable

import numpy as np

from ..errors import SvgError
from .transforms import apply_matrix, identity, parse_transform, translate

_NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
_TOKEN_RE = re.compile(rf"[AaCcHhLlMmQqSsTtVvZz]|{_NUMBER}")
_LENGTH_RE = re.compile(rf"^\s*({_NUMBER})\s*([a-zA-Z%]*)\s*$")
_POINTS_RE = re.compile(_NUMBER)


@dataclass(slots=True)
class Polyline:
    points: np.ndarray
    closed: bool = False
    source_tag: str = ""

    def __post_init__(self) -> None:
        self.points = np.asarray(self.points, dtype=np.float64).reshape(-1, 2)


@dataclass(slots=True)
class SvgGeometry:
    polylines: list[Polyline]
    bounds: tuple[float, float, float, float]
    intrinsic_width_mm: float | None = None
    intrinsic_height_mm: float | None = None
    view_box: tuple[float, float, float, float] | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]

    @property
    def point_count(self) -> int:
        return sum(len(line.points) for line in self.polylines)


@dataclass(slots=True)
class _PathState:
    current: np.ndarray = field(default_factory=lambda: np.zeros(2, dtype=np.float64))
    start: np.ndarray = field(default_factory=lambda: np.zeros(2, dtype=np.float64))
    previous_cubic_control: np.ndarray | None = None
    previous_quadratic_control: np.ndarray | None = None
    previous_command: str = ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_style(element: ET.Element) -> dict[str, str]:
    style: dict[str, str] = {}
    raw = element.get("style", "")
    for item in raw.split(";"):
        if ":" in item:
            key, value = item.split(":", 1)
            style[key.strip().lower()] = value.strip().lower()
    for key in ("display", "visibility", "opacity", "stroke", "fill", "stroke-opacity", "fill-opacity"):
        if element.get(key) is not None:
            style[key] = str(element.get(key)).strip().lower()
    return style


def _is_visible(element: ET.Element) -> bool:
    style = _parse_style(element)
    if style.get("display") == "none" or style.get("visibility") == "hidden":
        return False
    try:
        if float(style.get("opacity", "1")) <= 0:
            return False
    except ValueError:
        pass
    if style.get("stroke") == "none" and style.get("fill") == "none":
        return False
    return True


def _length_to_mm(value: str | None) -> float | None:
    if not value:
        return None
    match = _LENGTH_RE.match(value)
    if not match or match.group(2) == "%":
        return None
    number = float(match.group(1))
    unit = match.group(2).lower()
    factors = {
        "": 25.4 / 96.0,
        "px": 25.4 / 96.0,
        "mm": 1.0,
        "cm": 10.0,
        "in": 25.4,
        "pt": 25.4 / 72.0,
        "pc": 25.4 / 6.0,
        "q": 0.25,
    }
    factor = factors.get(unit)
    return None if factor is None else number * factor


def _number(element: ET.Element, name: str, default: float = 0.0) -> float:
    raw = element.get(name)
    if raw is None:
        return default
    match = _LENGTH_RE.match(raw)
    return default if not match else float(match.group(1))


def _distance_to_line(point: np.ndarray, start: np.ndarray, end: np.ndarray) -> float:
    line = end - start
    length = float(np.linalg.norm(line))
    if length < 1e-15:
        return float(np.linalg.norm(point - start))
    delta = point - start
    cross = line[0] * delta[1] - line[1] * delta[0]
    return abs(float(cross)) / length


def _flatten_cubic(
    p0: np.ndarray,
    p1: np.ndarray,
    p2: np.ndarray,
    p3: np.ndarray,
    tolerance: float,
    depth: int = 0,
) -> list[np.ndarray]:
    if depth >= 14 or max(_distance_to_line(p1, p0, p3), _distance_to_line(p2, p0, p3)) <= tolerance:
        return [p3]
    p01 = (p0 + p1) / 2
    p12 = (p1 + p2) / 2
    p23 = (p2 + p3) / 2
    p012 = (p01 + p12) / 2
    p123 = (p12 + p23) / 2
    middle = (p012 + p123) / 2
    return _flatten_cubic(p0, p01, p012, middle, tolerance, depth + 1) + _flatten_cubic(
        middle, p123, p23, p3, tolerance, depth + 1
    )


def _flatten_quadratic(
    p0: np.ndarray,
    p1: np.ndarray,
    p2: np.ndarray,
    tolerance: float,
    depth: int = 0,
) -> list[np.ndarray]:
    if depth >= 14 or _distance_to_line(p1, p0, p2) <= tolerance:
        return [p2]
    p01 = (p0 + p1) / 2
    p12 = (p1 + p2) / 2
    middle = (p01 + p12) / 2
    return _flatten_quadratic(p0, p01, middle, tolerance, depth + 1) + _flatten_quadratic(
        middle, p12, p2, tolerance, depth + 1
    )


def _vector_angle(u: np.ndarray, v: np.ndarray) -> float:
    denominator = max(float(np.linalg.norm(u) * np.linalg.norm(v)), 1e-15)
    cosine = max(-1.0, min(1.0, float(np.dot(u, v)) / denominator))
    angle = math.acos(cosine)
    cross = u[0] * v[1] - u[1] * v[0]
    if float(cross) < 0:
        angle = -angle
    return angle


def _flatten_arc(
    start: np.ndarray,
    rx: float,
    ry: float,
    rotation_deg: float,
    large_arc: bool,
    sweep: bool,
    end: np.ndarray,
    tolerance: float,
) -> list[np.ndarray]:
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-12 or ry < 1e-12 or np.linalg.norm(end - start) < 1e-12:
        return [end]
    phi = math.radians(rotation_deg % 360.0)
    cosine, sine = math.cos(phi), math.sin(phi)
    delta = (start - end) / 2.0
    x1p = cosine * delta[0] + sine * delta[1]
    y1p = -sine * delta[0] + cosine * delta[1]
    radii_scale = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if radii_scale > 1.0:
        scale_factor = math.sqrt(radii_scale)
        rx *= scale_factor
        ry *= scale_factor

    numerator = max(0.0, rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p)
    denominator = max(rx * rx * y1p * y1p + ry * ry * x1p * x1p, 1e-30)
    sign = -1.0 if large_arc == sweep else 1.0
    factor = sign * math.sqrt(numerator / denominator)
    cxp = factor * (rx * y1p / ry)
    cyp = factor * (-ry * x1p / rx)
    center = np.array(
        [
            cosine * cxp - sine * cyp + (start[0] + end[0]) / 2.0,
            sine * cxp + cosine * cyp + (start[1] + end[1]) / 2.0,
        ]
    )
    start_vector = np.array([(x1p - cxp) / rx, (y1p - cyp) / ry])
    end_vector = np.array([(-x1p - cxp) / rx, (-y1p - cyp) / ry])
    theta_start = _vector_angle(np.array([1.0, 0.0]), start_vector)
    theta_delta = _vector_angle(start_vector, end_vector)
    if not sweep and theta_delta > 0:
        theta_delta -= 2 * math.pi
    elif sweep and theta_delta < 0:
        theta_delta += 2 * math.pi

    radius = max(rx, ry)
    if tolerance <= 0 or tolerance >= radius:
        max_step = math.pi / 8
    else:
        max_step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - tolerance / radius)))
        max_step = max(max_step, math.radians(1.0))
    segments = max(2, min(2048, int(math.ceil(abs(theta_delta) / max_step))))
    points: list[np.ndarray] = []
    for index in range(1, segments + 1):
        theta = theta_start + theta_delta * index / segments
        local_x, local_y = rx * math.cos(theta), ry * math.sin(theta)
        points.append(
            center
            + np.array(
                [cosine * local_x - sine * local_y, sine * local_x + cosine * local_y]
            )
        )
    points[-1] = end.copy()
    return points


def _append_path(
    output: list[Polyline],
    points: list[np.ndarray],
    closed: bool,
    transform: np.ndarray,
    source_tag: str,
) -> None:
    if len(points) < 2:
        return
    array = np.asarray(points, dtype=np.float64)
    if closed and np.linalg.norm(array[-1] - array[0]) > 1e-9:
        array = np.vstack([array, array[0]])
    transformed = apply_matrix(array, transform)
    # Remove repeated adjacent points that can produce zero-length G-code moves.
    keep = np.ones(len(transformed), dtype=bool)
    if len(transformed) > 1:
        keep[1:] = np.linalg.norm(np.diff(transformed, axis=0), axis=1) > 1e-10
    transformed = transformed[keep]
    if len(transformed) >= 2:
        output.append(Polyline(transformed, closed=closed, source_tag=source_tag))


def _path_to_polylines(data: str, tolerance: float, transform: np.ndarray) -> list[Polyline]:
    tokens = _TOKEN_RE.findall(data.replace(",", " "))
    if not tokens:
        return []
    index = 0
    command: str | None = None
    state = _PathState()
    current_points: list[np.ndarray] = []
    output: list[Polyline] = []

    def is_command(token: str) -> bool:
        return len(token) == 1 and token.isalpha()

    def can_read(count: int) -> bool:
        return index + count <= len(tokens) and all(not is_command(tokens[index + offset]) for offset in range(count))

    def read(count: int) -> list[float]:
        nonlocal index
        if not can_read(count):
            raise SvgError(f"Malformed SVG path near token {index}")
        values = [float(tokens[index + offset]) for offset in range(count)]
        index += count
        return values

    def finish(closed: bool = False) -> None:
        nonlocal current_points
        _append_path(output, current_points, closed, transform, "path")
        current_points = []

    while index < len(tokens):
        if is_command(tokens[index]):
            command = tokens[index]
            index += 1
        elif command is None:
            raise SvgError("SVG path starts with numbers instead of a command")
        assert command is not None
        relative = command.islower()
        code = command.upper()

        if code == "Z":
            if current_points:
                state.current = state.start.copy()
                finish(closed=True)
            state.previous_cubic_control = None
            state.previous_quadratic_control = None
            state.previous_command = code
            command = None
            continue

        counts = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}
        count = counts.get(code)
        if count is None:
            raise SvgError(f"Unsupported SVG path command: {command}")
        if not can_read(count):
            raise SvgError(f"SVG command {command} does not have enough parameters")

        first_for_command = True
        while can_read(count):
            values = read(count)
            base = state.current.copy()
            if code == "M":
                point = np.array(values[:2], dtype=float) + (base if relative else 0)
                if current_points:
                    finish(False)
                state.current = point
                state.start = point.copy()
                current_points = [point.copy()]
                code = "L"
                command = "l" if relative else "L"
            elif code == "L":
                point = np.array(values[:2], dtype=float) + (base if relative else 0)
                if not current_points:
                    current_points = [base.copy()]
                    state.start = base.copy()
                current_points.append(point.copy())
                state.current = point
            elif code == "H":
                x = values[0] + (base[0] if relative else 0)
                point = np.array([x, base[1]], dtype=float)
                if not current_points:
                    current_points = [base.copy()]
                    state.start = base.copy()
                current_points.append(point.copy())
                state.current = point
            elif code == "V":
                y = values[0] + (base[1] if relative else 0)
                point = np.array([base[0], y], dtype=float)
                if not current_points:
                    current_points = [base.copy()]
                    state.start = base.copy()
                current_points.append(point.copy())
                state.current = point
            elif code == "C":
                c1 = np.array(values[0:2], dtype=float) + (base if relative else 0)
                c2 = np.array(values[2:4], dtype=float) + (base if relative else 0)
                point = np.array(values[4:6], dtype=float) + (base if relative else 0)
                if not current_points:
                    current_points = [base.copy()]
                    state.start = base.copy()
                current_points.extend(_flatten_cubic(base, c1, c2, point, tolerance))
                state.current = point
                state.previous_cubic_control = c2
                state.previous_quadratic_control = None
            elif code == "S":
                if state.previous_command in {"C", "S"} and state.previous_cubic_control is not None:
                    c1 = 2 * base - state.previous_cubic_control
                else:
                    c1 = base.copy()
                c2 = np.array(values[0:2], dtype=float) + (base if relative else 0)
                point = np.array(values[2:4], dtype=float) + (base if relative else 0)
                if not current_points:
                    current_points = [base.copy()]
                    state.start = base.copy()
                current_points.extend(_flatten_cubic(base, c1, c2, point, tolerance))
                state.current = point
                state.previous_cubic_control = c2
                state.previous_quadratic_control = None
            elif code == "Q":
                control = np.array(values[0:2], dtype=float) + (base if relative else 0)
                point = np.array(values[2:4], dtype=float) + (base if relative else 0)
                if not current_points:
                    current_points = [base.copy()]
                    state.start = base.copy()
                current_points.extend(_flatten_quadratic(base, control, point, tolerance))
                state.current = point
                state.previous_quadratic_control = control
                state.previous_cubic_control = None
            elif code == "T":
                if state.previous_command in {"Q", "T"} and state.previous_quadratic_control is not None:
                    control = 2 * base - state.previous_quadratic_control
                else:
                    control = base.copy()
                point = np.array(values[0:2], dtype=float) + (base if relative else 0)
                if not current_points:
                    current_points = [base.copy()]
                    state.start = base.copy()
                current_points.extend(_flatten_quadratic(base, control, point, tolerance))
                state.current = point
                state.previous_quadratic_control = control
                state.previous_cubic_control = None
            elif code == "A":
                rx, ry, rotation_deg, large_flag, sweep_flag, x, y = values
                point = np.array([x, y], dtype=float) + (base if relative else 0)
                if not current_points:
                    current_points = [base.copy()]
                    state.start = base.copy()
                current_points.extend(
                    _flatten_arc(
                        base,
                        rx,
                        ry,
                        rotation_deg,
                        bool(round(large_flag)),
                        bool(round(sweep_flag)),
                        point,
                        tolerance,
                    )
                )
                state.current = point
                state.previous_cubic_control = None
                state.previous_quadratic_control = None
            state.previous_command = code
            if code not in {"C", "S"}:
                state.previous_cubic_control = None
            if code not in {"Q", "T"}:
                state.previous_quadratic_control = None
            first_for_command = False
            if index >= len(tokens) or is_command(tokens[index]):
                break
        if first_for_command:
            raise SvgError(f"Could not parse SVG command {command}")

    finish(False)
    return output


def _sample_ellipse(cx: float, cy: float, rx: float, ry: float, tolerance: float) -> np.ndarray:
    radius = max(rx, ry)
    if radius <= 0:
        return np.empty((0, 2))
    if tolerance >= radius:
        segments = 24
    else:
        step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - tolerance / radius)))
        segments = max(24, min(2048, int(math.ceil(2 * math.pi / max(step, 1e-3)))))
    angles = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    points = np.column_stack([cx + rx * np.cos(angles), cy + ry * np.sin(angles)])
    return np.vstack([points, points[0]])


def _shape_polylines(element: ET.Element, tolerance: float, matrix: np.ndarray) -> list[Polyline]:
    tag = _local_name(element.tag).lower()
    output: list[Polyline] = []
    if tag == "path":
        data = element.get("d", "").strip()
        return _path_to_polylines(data, tolerance, matrix) if data else []
    if tag == "line":
        points = np.array(
            [[_number(element, "x1"), _number(element, "y1")], [_number(element, "x2"), _number(element, "y2")]]
        )
        _append_path(output, list(points), False, matrix, tag)
    elif tag in {"polyline", "polygon"}:
        values = [float(value) for value in _POINTS_RE.findall(element.get("points", ""))]
        if len(values) >= 4 and len(values) % 2 == 0:
            points = np.asarray(values, dtype=float).reshape(-1, 2)
            _append_path(output, list(points), tag == "polygon", matrix, tag)
    elif tag == "rect":
        x, y = _number(element, "x"), _number(element, "y")
        width, height = _number(element, "width"), _number(element, "height")
        rx, ry = max(0.0, _number(element, "rx")), max(0.0, _number(element, "ry"))
        if width > 0 and height > 0:
            if rx <= 0 and ry <= 0:
                points = [
                    np.array([x, y]),
                    np.array([x + width, y]),
                    np.array([x + width, y + height]),
                    np.array([x, y + height]),
                ]
            else:
                if rx <= 0:
                    rx = ry
                if ry <= 0:
                    ry = rx
                rx, ry = min(rx, width / 2), min(ry, height / 2)
                points = []
                centers = [
                    (x + width - rx, y + ry, -math.pi / 2, 0),
                    (x + width - rx, y + height - ry, 0, math.pi / 2),
                    (x + rx, y + height - ry, math.pi / 2, math.pi),
                    (x + rx, y + ry, math.pi, 3 * math.pi / 2),
                ]
                for center_x, center_y, start, end in centers:
                    segment_angles = np.linspace(start, end, 8, endpoint=True)
                    arc = [np.array([center_x + rx * math.cos(a), center_y + ry * math.sin(a)]) for a in segment_angles]
                    points.extend(arc if not points else arc[1:])
            _append_path(output, points, True, matrix, tag)
    elif tag == "circle":
        points = _sample_ellipse(
            _number(element, "cx"), _number(element, "cy"), _number(element, "r"), _number(element, "r"), tolerance
        )
        _append_path(output, list(points), True, matrix, tag)
    elif tag == "ellipse":
        points = _sample_ellipse(
            _number(element, "cx"), _number(element, "cy"), _number(element, "rx"), _number(element, "ry"), tolerance
        )
        _append_path(output, list(points), True, matrix, tag)
    return output


def _bounds(polylines: Iterable[Polyline]) -> tuple[float, float, float, float]:
    arrays = [line.points for line in polylines if len(line.points)]
    if not arrays:
        raise SvgError("SVG contains no supported visible vector geometry")
    points = np.vstack(arrays)
    minimum = points.min(axis=0)
    maximum = points.max(axis=0)
    if maximum[0] - minimum[0] <= 1e-12 or maximum[1] - minimum[1] <= 1e-12:
        raise SvgError("SVG geometry has zero width or height")
    return float(minimum[0]), float(minimum[1]), float(maximum[0]), float(maximum[1])


def parse_svg(svg_text: str, curve_tolerance_ratio: float = 0.0005) -> SvgGeometry:
    if len(svg_text) > 10_000_000:
        raise SvgError("SVG is larger than the 10 MB parser limit")
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise SvgError(f"Invalid SVG XML: {exc}") from exc
    if _local_name(root.tag).lower() != "svg":
        raise SvgError("Document root is not an <svg> element")

    view_box: tuple[float, float, float, float] | None = None
    if root.get("viewBox"):
        values = [float(value) for value in _POINTS_RE.findall(root.get("viewBox", ""))]
        if len(values) == 4 and values[2] > 0 and values[3] > 0:
            view_box = tuple(values)  # type: ignore[assignment]
    width_mm = _length_to_mm(root.get("width"))
    height_mm = _length_to_mm(root.get("height"))
    reference = max(view_box[2], view_box[3]) if view_box else 1000.0
    tolerance = max(reference * curve_tolerance_ratio, 1e-4)

    id_map = {element.get("id"): element for element in root.iter() if element.get("id")}
    warnings: list[str] = []
    polylines: list[Polyline] = []
    ignored_tags: set[str] = set()

    def visit(element: ET.Element, parent_matrix: np.ndarray, in_defs: bool = False, chain: frozenset[str] = frozenset()) -> None:
        tag = _local_name(element.tag).lower()
        if tag in {"metadata", "title", "desc"}:
            return
        if tag in {"defs", "symbol", "clippath", "mask", "pattern", "marker"}:
            in_defs = True
        if not _is_visible(element):
            return
        local_matrix = parent_matrix @ parse_transform(element.get("transform"))

        if tag == "use":
            href = element.get("href") or element.get("{http://www.w3.org/1999/xlink}href")
            if href and href.startswith("#"):
                element_id = href[1:]
                target = id_map.get(element_id)
                if target is not None and element_id not in chain:
                    use_matrix = local_matrix @ translate(_number(element, "x"), _number(element, "y"))
                    visit(target, use_matrix, False, chain | {element_id})
                else:
                    warnings.append(f"Could not resolve <use> reference {href}")
            return

        supported = {"path", "line", "polyline", "polygon", "rect", "circle", "ellipse"}
        if tag in supported and not in_defs:
            try:
                polylines.extend(_shape_polylines(element, tolerance, local_matrix))
            except SvgError as exc:
                warnings.append(str(exc))
        elif tag in {"text", "image"} and not in_defs:
            ignored_tags.add(tag)

        for child in list(element):
            visit(child, local_matrix, in_defs, chain)

    visit(root, identity())
    if ignored_tags:
        warnings.append("Ignored unsupported elements: " + ", ".join(sorted(ignored_tags)))
    geometry_bounds = _bounds(polylines)

    if view_box and width_mm is not None:
        intrinsic_width = (geometry_bounds[2] - geometry_bounds[0]) * width_mm / view_box[2]
    elif width_mm is not None:
        intrinsic_width = width_mm
    else:
        intrinsic_width = (geometry_bounds[2] - geometry_bounds[0]) * 25.4 / 96.0
    if view_box and height_mm is not None:
        intrinsic_height = (geometry_bounds[3] - geometry_bounds[1]) * height_mm / view_box[3]
    elif height_mm is not None:
        intrinsic_height = height_mm
    else:
        intrinsic_height = (geometry_bounds[3] - geometry_bounds[1]) * 25.4 / 96.0

    return SvgGeometry(
        polylines=polylines,
        bounds=geometry_bounds,
        intrinsic_width_mm=intrinsic_width,
        intrinsic_height_mm=intrinsic_height,
        view_box=view_box,
        warnings=warnings,
    )
