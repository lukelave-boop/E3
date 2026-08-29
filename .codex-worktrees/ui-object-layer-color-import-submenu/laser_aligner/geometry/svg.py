from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass, field
from numbers import Real

import numpy as np

from ..errors import SvgError
from .transforms import apply_matrix, identity, parse_transform, translate

_NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?"
_TOKEN_RE = re.compile(rf"[AaCcHhLlMmQqSsTtVvZz]|{_NUMBER}")
_LENGTH_RE = re.compile(rf"^\s*({_NUMBER})\s*([a-zA-Z%]*)\s*$")
_POINTS_RE = re.compile(_NUMBER)
_XML_STYLESHEET_RE = re.compile(r"<\?xml-stylesheet\b", re.IGNORECASE)
_XML_DECLARATION_RE = re.compile(r"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_CSS_PX_TO_MM = 25.4 / 96.0
MAX_SVG_TEXT_CHARACTERS = 10_000_000
_MAX_SVG_PATHS = 50_000
_MAX_SVG_POINTS = 250_000
_MAX_SVG_FLATTENED_POINTS = 32_768
_MAX_SVG_ELEMENT_VISITS = 100_000
_MAX_SVG_NESTING = 256
_MAX_SVG_PATH_TOKENS = 600_000
_MAX_SVG_XML_MARKERS = 100_000
_PRESERVE_ASPECT_ALIGNMENTS = {
    "none",
    "xMinYMin",
    "xMidYMin",
    "xMaxYMin",
    "xMinYMid",
    "xMidYMid",
    "xMaxYMid",
    "xMinYMax",
    "xMidYMax",
    "xMaxYMax",
}
_UNSUPPORTED_GEOMETRY_CSS = {
    "clip-path",
    "fill-rule",
    "mask",
    "marker",
    "marker-end",
    "marker-mid",
    "marker-start",
    "offset-path",
    "stroke-dasharray",
    "stroke-dashoffset",
    "transform",
    "transform-origin",
}
_UNSUPPORTED_PRESENTATION_ATTRIBUTES = _UNSUPPORTED_GEOMETRY_CSS - {
    "transform"
}


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
    user_to_mm: np.ndarray | None = field(default=None, repr=False)

    @property
    def width(self) -> float:
        return self.bounds[2] - self.bounds[0]

    @property
    def height(self) -> float:
        return self.bounds[3] - self.bounds[1]

    @property
    def point_count(self) -> int:
        return sum(len(line.points) for line in self.polylines)

    def physical_polylines(self) -> list[Polyline]:
        """Return the parsed paths in physical millimetres."""

        if self.user_to_mm is None:
            raise SvgError("SVG geometry does not include a physical-unit mapping")
        return [
            Polyline(
                apply_matrix(line.points, self.user_to_mm),
                closed=line.closed,
                source_tag=line.source_tag,
            )
            for line in self.polylines
        ]


@dataclass(slots=True)
class _PathState:
    current: np.ndarray = field(default_factory=lambda: np.zeros(2, dtype=np.float64))
    start: np.ndarray = field(default_factory=lambda: np.zeros(2, dtype=np.float64))
    previous_cubic_control: np.ndarray | None = None
    previous_quadratic_control: np.ndarray | None = None
    previous_command: str = ""


class _SvgComplexityError(SvgError):
    """Raised when supported geometry exceeds a bounded parser workload."""


@dataclass(slots=True)
class _GeometryBudget:
    path_count: int = 0
    point_count: int = 0
    flattened_point_count: int = 0
    element_visits: int = 0

    def consume_element(self, depth: int) -> None:
        if depth > _MAX_SVG_NESTING:
            raise _SvgComplexityError(
                f"SVG nesting exceeds the {_MAX_SVG_NESTING}-level parser limit"
            )
        if self.element_visits >= _MAX_SVG_ELEMENT_VISITS:
            raise _SvgComplexityError(
                f"SVG expansion exceeds the {_MAX_SVG_ELEMENT_VISITS:,}-element parser limit"
            )
        self.element_visits += 1

    def consume_flattened(self, count: int = 1) -> None:
        if count < 0 or self.flattened_point_count + count > _MAX_SVG_FLATTENED_POINTS:
            raise _SvgComplexityError(
                "SVG curve expansion exceeds the "
                f"{_MAX_SVG_FLATTENED_POINTS:,}-point flattening limit; "
                "simplify the source curves"
            )
        self.flattened_point_count += count

    def ensure_pending_points(self, count: int) -> None:
        if count < 0 or self.point_count + count > _MAX_SVG_POINTS:
            raise _SvgComplexityError(
                f"SVG geometry exceeds the {_MAX_SVG_POINTS:,}-point parser limit; "
                "simplify the source geometry"
            )

    def commit(self, paths: list[Polyline]) -> None:
        additional_paths = len(paths)
        additional_points = sum(len(path.points) for path in paths)
        if self.path_count + additional_paths > _MAX_SVG_PATHS:
            raise _SvgComplexityError(
                f"SVG geometry exceeds the {_MAX_SVG_PATHS:,}-path parser limit; "
                "combine or simplify the source paths"
            )
        self.ensure_pending_points(additional_points)
        self.path_count += additional_paths
        self.point_count += additional_points


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _style_declarations(value: str) -> dict[str, str]:
    declarations: dict[str, str] = {}
    for item in value.split(";"):
        if ":" not in item:
            continue
        key, raw_value = item.split(":", 1)
        declarations[key.strip().lower()] = raw_value.strip().lower()
    return declarations


def _parse_style(element: ET.Element) -> dict[str, str]:
    style = _style_declarations(element.get("style", ""))
    for key in ("display", "visibility", "opacity", "stroke", "fill", "stroke-opacity", "fill-opacity"):
        if element.get(key) is not None:
            style[key] = str(element.get(key)).strip().lower()
    return style


def _css_value_is_inert(name: str, value: str) -> bool:
    normalized = value.strip().lower()
    if name == "fill-rule":
        return normalized == "nonzero"
    if name == "stroke-dashoffset":
        try:
            return float(normalized) == 0.0
        except ValueError:
            return False
    return normalized in {"", "none"}


def _validate_render_semantics(svg_text: str, root: ET.Element) -> None:
    unsupported: set[str] = set()
    if _XML_STYLESHEET_RE.search(svg_text):
        unsupported.add("external CSS stylesheets")

    for element in root.iter():
        tag = _local_name(element.tag).lower()
        if tag == "style":
            unsupported.add("CSS <style> rules")
        elif tag == "clippath":
            unsupported.add("<clipPath>")
        elif tag == "mask":
            unsupported.add("<mask>")
        elif tag == "link" and str(element.get("rel", "")).lower() == "stylesheet":
            unsupported.add("linked CSS stylesheets")

        declarations = _style_declarations(element.get("style", ""))
        for name in _UNSUPPORTED_GEOMETRY_CSS:
            value = declarations.get(name)
            if value is not None and not _css_value_is_inert(name, value):
                unsupported.add(f"CSS {name}")

        for raw_name, raw_value in element.attrib.items():
            name = _local_name(raw_name).lower()
            if (
                name in _UNSUPPORTED_PRESENTATION_ATTRIBUTES
                and not _css_value_is_inert(name, raw_value)
            ):
                unsupported.add(name)

    if unsupported:
        details = ", ".join(sorted(unsupported))
        raise SvgError(
            "SVG uses unsupported rendering semantics: "
            f"{details}. Convert CSS, clipping, masks, markers, and dashed "
            "strokes to explicit vector paths before importing."
        )


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


def _root_length_mm(root: ET.Element, name: str) -> float | None:
    raw = root.get(name)
    if raw is None or not raw.strip():
        return None
    value = _length_to_mm(raw)
    if value is None:
        raise SvgError(
            f"SVG {name} must use an absolute mm, cm, in, px, pt, pc, or q length"
        )
    if not math.isfinite(value) or value <= 0:
        raise SvgError(f"SVG {name} must be a positive finite length")
    return value


def _parse_view_box(value: str | None) -> tuple[float, float, float, float] | None:
    if value is None or not value.strip():
        return None
    tokens = [token for token in re.split(r"[\s,]+", value.strip()) if token]
    if len(tokens) != 4 or any(re.fullmatch(_NUMBER, token) is None for token in tokens):
        raise SvgError("SVG viewBox must contain exactly four finite numbers")
    parsed = tuple(float(token) for token in tokens)
    if not all(math.isfinite(number) for number in parsed):
        raise SvgError("SVG viewBox must contain exactly four finite numbers")
    if parsed[2] <= 0 or parsed[3] <= 0:
        raise SvgError("SVG viewBox width and height must be positive")
    return parsed  # type: ignore[return-value]


def _parse_preserve_aspect_ratio(value: str | None) -> tuple[str, str]:
    tokens = (value or "xMidYMid meet").split()
    if tokens and tokens[0] == "defer":
        tokens = tokens[1:]
    if not tokens or len(tokens) > 2:
        raise SvgError("Invalid SVG preserveAspectRatio value")
    alignment = tokens[0]
    mode = tokens[1] if len(tokens) == 2 else "meet"
    if alignment not in _PRESERVE_ASPECT_ALIGNMENTS:
        raise SvgError(f"Unsupported SVG preserveAspectRatio alignment: {alignment}")
    if mode not in {"meet", "slice"}:
        raise SvgError(f"Unsupported SVG preserveAspectRatio mode: {mode}")
    return alignment, mode


def _user_to_mm_transform(
    root: ET.Element,
    view_box: tuple[float, float, float, float] | None,
    width_mm: float | None,
    height_mm: float | None,
    geometry_bounds: tuple[float, float, float, float],
) -> np.ndarray:
    if view_box is None:
        source_width = geometry_bounds[2] - geometry_bounds[0]
        source_height = geometry_bounds[3] - geometry_bounds[1]
        scale_x = (
            width_mm / source_width if width_mm is not None else _CSS_PX_TO_MM
        )
        scale_y = (
            height_mm / source_height
            if height_mm is not None
            else _CSS_PX_TO_MM
        )
        return np.array(
            [
                [scale_x, 0.0, 0.0],
                [0.0, scale_y, 0.0],
                [0.0, 0.0, 1.0],
            ],
            dtype=np.float64,
        )

    view_x, view_y, view_width, view_height = view_box
    if width_mm is None and height_mm is None:
        viewport_width = view_width * _CSS_PX_TO_MM
        viewport_height = view_height * _CSS_PX_TO_MM
    elif width_mm is None:
        assert height_mm is not None
        viewport_height = height_mm
        viewport_width = height_mm * view_width / view_height
    elif height_mm is None:
        viewport_width = width_mm
        viewport_height = width_mm * view_height / view_width
    else:
        viewport_width = width_mm
        viewport_height = height_mm

    scale_x = viewport_width / view_width
    scale_y = viewport_height / view_height
    alignment, mode = _parse_preserve_aspect_ratio(root.get("preserveAspectRatio"))
    offset_x = 0.0
    offset_y = 0.0
    if alignment != "none":
        uniform_scale = (
            min(scale_x, scale_y) if mode == "meet" else max(scale_x, scale_y)
        )
        scale_x = uniform_scale
        scale_y = uniform_scale
        remaining_x = viewport_width - view_width * uniform_scale
        remaining_y = viewport_height - view_height * uniform_scale
        if alignment.startswith("xMid"):
            offset_x = remaining_x / 2.0
        elif alignment.startswith("xMax"):
            offset_x = remaining_x
        if alignment.endswith("YMid"):
            offset_y = remaining_y / 2.0
        elif alignment.endswith("YMax"):
            offset_y = remaining_y

    return np.array(
        [
            [scale_x, 0.0, offset_x - view_x * scale_x],
            [0.0, scale_y, offset_y - view_y * scale_y],
            [0.0, 0.0, 1.0],
        ],
        dtype=np.float64,
    )


def _number(element: ET.Element, name: str, default: float = 0.0) -> float:
    raw = element.get(name)
    if raw is None:
        return default
    match = _LENGTH_RE.match(raw)
    if not match:
        raise SvgError(
            f"SVG <{_local_name(element.tag)}> {name} must be a finite number"
        )
    unit = match.group(2).lower()
    if unit not in {"", "px"}:
        raise SvgError(
            f"SVG <{_local_name(element.tag)}> {name} uses unsupported unit {unit!r}; "
            "convert geometry to user units or px"
        )
    value = float(match.group(1))
    if not math.isfinite(value):
        raise SvgError(f"SVG <{_local_name(element.tag)}> {name} must be finite")
    return value


def _require_token_separator(value: str, *, label: str, offset: int) -> None:
    if re.fullmatch(r"[\s,]*", value) is not None:
        return
    fragment = value.strip()[:24]
    raise SvgError(f"{label} contains invalid token {fragment!r} near character {offset}")


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
    budget: _GeometryBudget,
    depth: int = 0,
) -> list[np.ndarray]:
    if depth >= 14 or max(_distance_to_line(p1, p0, p3), _distance_to_line(p2, p0, p3)) <= tolerance:
        budget.consume_flattened()
        return [p3]
    p01 = (p0 + p1) / 2
    p12 = (p1 + p2) / 2
    p23 = (p2 + p3) / 2
    p012 = (p01 + p12) / 2
    p123 = (p12 + p23) / 2
    middle = (p012 + p123) / 2
    return _flatten_cubic(p0, p01, p012, middle, tolerance, budget, depth + 1) + _flatten_cubic(
        middle, p123, p23, p3, tolerance, budget, depth + 1
    )


def _flatten_quadratic(
    p0: np.ndarray,
    p1: np.ndarray,
    p2: np.ndarray,
    tolerance: float,
    budget: _GeometryBudget,
    depth: int = 0,
) -> list[np.ndarray]:
    if depth >= 14 or _distance_to_line(p1, p0, p2) <= tolerance:
        budget.consume_flattened()
        return [p2]
    p01 = (p0 + p1) / 2
    p12 = (p1 + p2) / 2
    middle = (p01 + p12) / 2
    return _flatten_quadratic(p0, p01, middle, tolerance, budget, depth + 1) + _flatten_quadratic(
        middle, p12, p2, tolerance, budget, depth + 1
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
    budget: _GeometryBudget,
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
    budget.consume_flattened(segments)
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


def _path_to_polylines(
    data: str,
    tolerance: float,
    transform: np.ndarray,
    budget: _GeometryBudget,
) -> list[Polyline]:
    tokens: list[str] = []
    token_end = 0
    for match in _TOKEN_RE.finditer(data):
        _require_token_separator(
            data[token_end : match.start()],
            label="SVG path data",
            offset=token_end,
        )
        if len(tokens) >= _MAX_SVG_PATH_TOKENS:
            raise _SvgComplexityError(
                f"SVG path data exceeds the {_MAX_SVG_PATH_TOKENS:,}-token parser limit; "
                "simplify the source path"
            )
        tokens.append(match.group(0))
        token_end = match.end()
    _require_token_separator(
        data[token_end:],
        label="SVG path data",
        offset=token_end,
    )
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
        if not all(math.isfinite(value) for value in values):
            raise SvgError(f"SVG path contains a non-finite value near token {index}")
        index += count
        return values

    def append_current(point: np.ndarray) -> None:
        budget.ensure_pending_points(len(current_points) + 1)
        current_points.append(point.copy())

    def extend_current(points: list[np.ndarray]) -> None:
        budget.ensure_pending_points(len(current_points) + len(points))
        current_points.extend(points)

    def begin_current(point: np.ndarray) -> None:
        nonlocal current_points
        budget.ensure_pending_points(1)
        current_points = [point.copy()]

    def finish(closed: bool = False) -> None:
        nonlocal current_points
        before = len(output)
        _append_path(output, current_points, closed, transform, "path")
        budget.commit(output[before:])
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
                begin_current(point)
                code = "L"
                command = "l" if relative else "L"
            elif code == "L":
                point = np.array(values[:2], dtype=float) + (base if relative else 0)
                if not current_points:
                    begin_current(base)
                    state.start = base.copy()
                append_current(point)
                state.current = point
            elif code == "H":
                x = values[0] + (base[0] if relative else 0)
                point = np.array([x, base[1]], dtype=float)
                if not current_points:
                    begin_current(base)
                    state.start = base.copy()
                append_current(point)
                state.current = point
            elif code == "V":
                y = values[0] + (base[1] if relative else 0)
                point = np.array([base[0], y], dtype=float)
                if not current_points:
                    begin_current(base)
                    state.start = base.copy()
                append_current(point)
                state.current = point
            elif code == "C":
                c1 = np.array(values[0:2], dtype=float) + (base if relative else 0)
                c2 = np.array(values[2:4], dtype=float) + (base if relative else 0)
                point = np.array(values[4:6], dtype=float) + (base if relative else 0)
                if not current_points:
                    begin_current(base)
                    state.start = base.copy()
                extend_current(_flatten_cubic(base, c1, c2, point, tolerance, budget))
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
                    begin_current(base)
                    state.start = base.copy()
                extend_current(_flatten_cubic(base, c1, c2, point, tolerance, budget))
                state.current = point
                state.previous_cubic_control = c2
                state.previous_quadratic_control = None
            elif code == "Q":
                control = np.array(values[0:2], dtype=float) + (base if relative else 0)
                point = np.array(values[2:4], dtype=float) + (base if relative else 0)
                if not current_points:
                    begin_current(base)
                    state.start = base.copy()
                extend_current(_flatten_quadratic(base, control, point, tolerance, budget))
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
                    begin_current(base)
                    state.start = base.copy()
                extend_current(_flatten_quadratic(base, control, point, tolerance, budget))
                state.current = point
                state.previous_quadratic_control = control
                state.previous_cubic_control = None
            elif code == "A":
                rx, ry, rotation_deg, large_flag, sweep_flag, x, y = values
                if large_flag not in {0.0, 1.0} or sweep_flag not in {0.0, 1.0}:
                    raise SvgError("SVG arc flags must each be exactly 0 or 1")
                point = np.array([x, y], dtype=float) + (base if relative else 0)
                if not current_points:
                    begin_current(base)
                    state.start = base.copy()
                extend_current(
                    _flatten_arc(
                        base,
                        rx,
                        ry,
                        rotation_deg,
                        bool(round(large_flag)),
                        bool(round(sweep_flag)),
                        point,
                        tolerance,
                        budget,
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


def _sample_ellipse(
    cx: float,
    cy: float,
    rx: float,
    ry: float,
    tolerance: float,
    budget: _GeometryBudget,
) -> np.ndarray:
    radius = max(rx, ry)
    if radius <= 0:
        return np.empty((0, 2))
    if tolerance >= radius:
        segments = 24
    else:
        step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - tolerance / radius)))
        segments = max(24, min(2048, int(math.ceil(2 * math.pi / max(step, 1e-3)))))
    budget.consume_flattened(segments + 1)
    angles = np.linspace(0, 2 * math.pi, segments, endpoint=False)
    points = np.column_stack([cx + rx * np.cos(angles), cy + ry * np.sin(angles)])
    return np.vstack([points, points[0]])


def _shape_polylines(
    element: ET.Element,
    tolerance: float,
    matrix: np.ndarray,
    budget: _GeometryBudget,
) -> list[Polyline]:
    tag = _local_name(element.tag).lower()
    output: list[Polyline] = []
    if tag == "path":
        data = element.get("d", "").strip()
        return _path_to_polylines(data, tolerance, matrix, budget) if data else []
    if tag == "line":
        points = np.array(
            [[_number(element, "x1"), _number(element, "y1")], [_number(element, "x2"), _number(element, "y2")]]
        )
        _append_path(output, list(points), False, matrix, tag)
    elif tag in {"polyline", "polygon"}:
        values: list[float] = []
        maximum_values = max(0, (_MAX_SVG_POINTS - budget.point_count) * 2)
        points_text = element.get("points", "")
        token_end = 0
        for match in _POINTS_RE.finditer(points_text):
            _require_token_separator(
                points_text[token_end : match.start()],
                label=f"SVG <{tag}> points",
                offset=token_end,
            )
            if len(values) >= maximum_values:
                raise _SvgComplexityError(
                    f"SVG geometry exceeds the {_MAX_SVG_POINTS:,}-point parser limit; "
                    "simplify the source geometry"
                )
            values.append(float(match.group(0)))
            token_end = match.end()
        _require_token_separator(
            points_text[token_end:],
            label=f"SVG <{tag}> points",
            offset=token_end,
        )
        if not all(math.isfinite(value) for value in values):
            raise SvgError(f"SVG <{tag}> contains a non-finite coordinate")
        if len(values) >= 4 and len(values) % 2 == 0:
            budget.ensure_pending_points(len(values) // 2 + int(tag == "polygon"))
            points = np.asarray(values, dtype=float).reshape(-1, 2)
            _append_path(output, list(points), tag == "polygon", matrix, tag)
    elif tag == "rect":
        x, y = _number(element, "x"), _number(element, "y")
        width, height = _number(element, "width"), _number(element, "height")
        rx, ry = _number(element, "rx"), _number(element, "ry")
        if width < 0 or height < 0 or rx < 0 or ry < 0:
            raise SvgError("SVG <rect> dimensions and corner radii cannot be negative")
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
        radius = _number(element, "r")
        if radius < 0:
            raise SvgError("SVG <circle> radius cannot be negative")
        points = _sample_ellipse(
            _number(element, "cx"),
            _number(element, "cy"),
            radius,
            radius,
            tolerance,
            budget,
        )
        _append_path(output, list(points), True, matrix, tag)
    elif tag == "ellipse":
        radius_x = _number(element, "rx")
        radius_y = _number(element, "ry")
        if radius_x < 0 or radius_y < 0:
            raise SvgError("SVG <ellipse> radii cannot be negative")
        points = _sample_ellipse(
            _number(element, "cx"),
            _number(element, "cy"),
            radius_x,
            radius_y,
            tolerance,
            budget,
        )
        _append_path(output, list(points), True, matrix, tag)
    budget.commit(output)
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
    if isinstance(curve_tolerance_ratio, bool) or not isinstance(
        curve_tolerance_ratio,
        Real,
    ):
        raise SvgError("curve_tolerance_ratio must be a finite positive number")
    tolerance_ratio = float(curve_tolerance_ratio)
    if not math.isfinite(tolerance_ratio) or tolerance_ratio <= 0.0:
        raise SvgError("curve_tolerance_ratio must be a finite positive number")
    if len(svg_text) > MAX_SVG_TEXT_CHARACTERS:
        raise SvgError("SVG is larger than the 10 MB parser limit")
    if svg_text.count("<") > _MAX_SVG_XML_MARKERS:
        raise SvgError(
            f"SVG XML exceeds the {_MAX_SVG_XML_MARKERS:,}-element-marker parser limit"
        )
    if _XML_DECLARATION_RE.search(svg_text):
        raise SvgError("SVG DTD and entity declarations are not supported")
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError as exc:
        raise SvgError(f"Invalid SVG XML: {exc}") from exc
    if _local_name(root.tag).lower() != "svg":
        raise SvgError("Document root is not an <svg> element")

    _validate_render_semantics(svg_text, root)
    view_box = _parse_view_box(root.get("viewBox"))
    width_mm = _root_length_mm(root, "width")
    height_mm = _root_length_mm(root, "height")
    reference = max(view_box[2], view_box[3]) if view_box else 1000.0
    tolerance = max(reference * tolerance_ratio, 1e-4)

    id_map: dict[str, ET.Element] = {}
    for element in root.iter():
        element_id = element.get("id")
        if not element_id:
            continue
        if element_id in id_map:
            raise SvgError(f"SVG contains duplicate element id {element_id!r}")
        id_map[element_id] = element
    warnings: list[str] = []
    polylines: list[Polyline] = []
    ignored_tags: set[str] = set()
    budget = _GeometryBudget()

    def visit(
        element: ET.Element,
        parent_matrix: np.ndarray,
        in_defs: bool = False,
        chain: frozenset[str] = frozenset(),
        depth: int = 0,
    ) -> None:
        budget.consume_element(depth)
        tag = _local_name(element.tag).lower()
        if tag in {"metadata", "title", "desc"}:
            return
        if tag in {"defs", "symbol", "clippath", "mask", "pattern", "marker"}:
            in_defs = True
        if not _is_visible(element):
            return
        try:
            local_matrix = parent_matrix @ parse_transform(element.get("transform"))
        except ValueError as exc:
            raise SvgError(f"Invalid SVG <{tag}> transform: {exc}") from exc

        if tag == "use":
            href = element.get("href") or element.get("{http://www.w3.org/1999/xlink}href")
            if href and href.startswith("#"):
                element_id = href[1:]
                target = id_map.get(element_id)
                if target is not None and element_id not in chain:
                    use_matrix = local_matrix @ translate(_number(element, "x"), _number(element, "y"))
                    visit(target, use_matrix, False, chain | {element_id}, depth + 1)
                else:
                    warnings.append(f"Could not resolve <use> reference {href}")
            return

        supported = {"path", "line", "polyline", "polygon", "rect", "circle", "ellipse"}
        if tag in supported and not in_defs:
            try:
                polylines.extend(
                    _shape_polylines(element, tolerance, local_matrix, budget)
                )
            except SvgError as exc:
                raise SvgError(f"Could not parse SVG <{tag}>: {exc}") from exc
        elif tag in {"text", "image"} and not in_defs:
            ignored_tags.add(tag)

        for child in list(element):
            visit(child, local_matrix, in_defs, chain, depth + 1)

    visit(root, identity())
    if ignored_tags:
        warnings.append("Ignored unsupported elements: " + ", ".join(sorted(ignored_tags)))
    geometry_bounds = _bounds(polylines)
    user_to_mm = _user_to_mm_transform(
        root,
        view_box,
        width_mm,
        height_mm,
        geometry_bounds,
    )
    physical_bounds = _bounds(
        Polyline(
            apply_matrix(line.points, user_to_mm),
            closed=line.closed,
            source_tag=line.source_tag,
        )
        for line in polylines
    )
    intrinsic_width = physical_bounds[2] - physical_bounds[0]
    intrinsic_height = physical_bounds[3] - physical_bounds[1]

    return SvgGeometry(
        polylines=polylines,
        bounds=geometry_bounds,
        intrinsic_width_mm=intrinsic_width,
        intrinsic_height_mm=intrinsic_height,
        view_box=view_box,
        warnings=warnings,
        user_to_mm=user_to_mm,
    )
