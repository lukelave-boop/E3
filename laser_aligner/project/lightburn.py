"""Safe, Qt-neutral import of LightBurn ``.lbrn`` and ``.lbrn2`` projects.

The importer deliberately translates LightBurn content into E3's ordinary
project model.  Imported operation layers are always output-disabled so that
foreign speed and power values must be reviewed before they can produce laser
output.
"""

from __future__ import annotations

import hashlib
import math
import re
import uuid
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .model import LayerMode, OperationLayer, SceneObject

if TYPE_CHECKING:
    from .import_manifest import ImportLayerManifest, ImportScanManifest

LIGHTBURN_FILE_DIALOG_FILTER = "LightBurn Projects (*.lbrn *.lbrn2 *.LBRN *.LBRN2)"
SUPPORTED_LIGHTBURN_SUFFIXES = {".lbrn", ".lbrn2"}
MAX_LIGHTBURN_FILE_BYTES = 64 * 1024 * 1024
MAX_LIGHTBURN_XML_ELEMENTS = 100_000
MAX_LIGHTBURN_SHAPES = 20_000
MAX_LIGHTBURN_VERTICES = 500_000
MAX_LIGHTBURN_POLYLINE_POINTS = 1_000_000
MAX_LIGHTBURN_LIST_TEXT = 24 * 1024 * 1024

_NUMBER_PATTERN = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_VERTEX_PATTERN = re.compile(
    rf"V\s*({_NUMBER_PATTERN})\s+({_NUMBER_PATTERN})(.*?)(?=V|$)",
    re.DOTALL,
)
_CONTROL_PATTERN = re.compile(rf"(c[01][xy])\s*({_NUMBER_PATTERN})")
_PRIMITIVE_PATTERN = re.compile(r"([A-Za-z])\s*(-?\d+)\s+(-?\d+)")
_UNSAFE_XML_PATTERN = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)

# LightBurn's standard layer palette.  Keeping this in the Qt-neutral project
# module avoids importing desktop code merely to preserve familiar layer colors.
_LIGHTBURN_PALETTE = (
    "#101010",
    "#185CFF",
    "#F02C3D",
    "#2DD12D",
    "#E5DA19",
    "#FF8A18",
    "#18C9D4",
    "#ED23D2",
    "#8A8A8A",
    "#25358E",
    "#A71927",
    "#178A35",
    "#9A941A",
    "#A75E1D",
    "#15828A",
    "#9A248B",
    "#B2B2B2",
    "#5367B5",
    "#B35F70",
    "#61B471",
    "#B9B35D",
    "#C88A56",
    "#5EAFB5",
    "#B065A8",
    "#D0D0D0",
    "#7184DC",
    "#DA7D8A",
    "#83CB91",
    "#D6CD77",
    "#DD9B68",
)


class LightBurnImportError(ValueError):
    """Raised when a LightBurn project cannot be translated without loss."""


@dataclass(slots=True)
class LightBurnImportResult:
    """Native E3 objects and layers produced by one LightBurn import."""

    layers: list[OperationLayer]
    objects: list[SceneObject]
    warnings: list[str] = field(default_factory=list)
    app_version: str = ""
    format_version: str = ""
    source_name: str = ""


@dataclass(slots=True, frozen=True)
class _Vertex:
    x: float
    y: float
    c0x: float | None = None
    c0y: float | None = None
    c1x: float | None = None
    c1y: float | None = None


@dataclass(slots=True)
class _RawShape:
    cut_index: int
    shape_type: str
    name: str
    polylines: list[dict[str, Any]]
    group_id: str | None = None


@dataclass(slots=True)
class _ImportState:
    source_name: str
    app_version: str
    format_version: str
    warnings: list[str] = field(default_factory=list)
    vertex_cache: dict[int, str] = field(default_factory=dict)
    primitive_cache: dict[int, str] = field(default_factory=dict)
    shape_count: int = 0
    vertex_count: int = 0
    point_count: int = 0

    def add_shape(self) -> None:
        self.shape_count += 1
        if self.shape_count > MAX_LIGHTBURN_SHAPES:
            raise LightBurnImportError(
                f"LightBurn project exceeds the {MAX_LIGHTBURN_SHAPES:,}-shape import limit"
            )

    def add_vertices(self, count: int) -> None:
        self.vertex_count += count
        if self.vertex_count > MAX_LIGHTBURN_VERTICES:
            raise LightBurnImportError(
                f"LightBurn project exceeds the {MAX_LIGHTBURN_VERTICES:,}-vertex import limit"
            )

    def add_points(self, count: int) -> None:
        self.point_count += count
        if self.point_count > MAX_LIGHTBURN_POLYLINE_POINTS:
            raise LightBurnImportError(
                "LightBurn project produces too many sampled vector points "
                f"(limit {MAX_LIGHTBURN_POLYLINE_POINTS:,})"
            )


_Affine = tuple[float, float, float, float, float, float]
_IDENTITY: _Affine = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _local_name(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _normalized_key(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).casefold())


def _finite(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise LightBurnImportError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise LightBurnImportError(f"{label} must be a finite number")
    return number


def _integer(value: Any, label: str) -> int:
    number = _finite(value, label)
    rounded = round(number)
    if abs(number - rounded) > 1e-9:
        raise LightBurnImportError(f"{label} must be an integer")
    return int(rounded)


def _attribute(element: ET.Element, name: str, default: Any = None) -> Any:
    wanted = _normalized_key(name)
    for key, value in element.attrib.items():
        if _normalized_key(key) == wanted:
            return value
    return default


def _direct_children(element: ET.Element, name: str) -> list[ET.Element]:
    wanted = name.casefold()
    return [child for child in element if _local_name(child.tag).casefold() == wanted]


def _first_direct_child(element: ET.Element, name: str) -> ET.Element | None:
    matches = _direct_children(element, name)
    return matches[0] if matches else None


def _element_value(element: ET.Element) -> str | None:
    for key, value in element.attrib.items():
        if _normalized_key(key) in {"value", "val"}:
            return str(value).strip()
    text = (element.text or "").strip()
    return text or None


def _flatten_setting(element: ET.Element) -> dict[str, str]:
    values: dict[str, str] = {}
    for key, value in element.attrib.items():
        values[_normalized_key(key)] = str(value).strip()
    for descendant in element.iter():
        if descendant is element:
            continue
        key = _normalized_key(_local_name(descendant.tag))
        value = _element_value(descendant)
        if key and value is not None:
            values.setdefault(key, value)
        # A few LightBurn versions use generic property nodes with name/value
        # attributes.  Preserve those without making them supersede explicit tags.
        property_name = _attribute(descendant, "name")
        property_value = _attribute(descendant, "value")
        if property_name is not None and property_value is not None:
            values.setdefault(_normalized_key(property_name), str(property_value).strip())
    return values


def _first_value(values: Mapping[str, str], *names: str) -> str | None:
    for name in names:
        value = values.get(_normalized_key(name))
        if value not in {None, ""}:
            return value
    return None


def _boolean_value(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().casefold()
    if normalized in {"1", "true", "yes", "on", "enabled"}:
        return True
    if normalized in {"0", "false", "no", "off", "disabled"}:
        return False
    return default


def _parse_color(value: str | None, index: int) -> str:
    fallback = _LIGHTBURN_PALETTE[index % len(_LIGHTBURN_PALETTE)]
    if value is None:
        return fallback
    text = str(value).strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.upper()
    match = re.fullmatch(r"\s*(\d{1,3})\s*[,; ]\s*(\d{1,3})\s*[,; ]\s*(\d{1,3})\s*", text)
    if match:
        channels = tuple(max(0, min(255, int(part))) for part in match.groups())
        return "#{:02X}{:02X}{:02X}".format(*channels)
    return fallback


def _bounded_value(
    raw: str | None,
    *,
    default: float,
    minimum: float,
    maximum: float,
    label: str,
    warnings: list[str],
) -> float:
    if raw is None:
        return default
    value = _finite(raw, label)
    bounded = max(minimum, min(maximum, value))
    if bounded != value:
        warnings.append(f"{label} {value:g} was limited to {bounded:g}")
    return bounded


def _layer_mode(values: Mapping[str, str], warnings: list[str], index: int) -> LayerMode:
    kind = (_first_value(values, "type", "mode", "cutmode") or "").casefold()
    if any(token in kind for token in ("scan", "raster", "image")):
        return LayerMode.RASTER
    fill = "fill" in kind or _boolean_value(
        _first_value(values, "fill", "fillenabled", "dofill")
    )
    line_raw = _first_value(values, "line", "lineenabled", "doline", "outline")
    line = _boolean_value(line_raw, default=not fill)
    if fill:
        if line:
            warnings.append(
                f"LightBurn layer {index} requests both fill and outline; E3 imported its fill "
                "settings. Duplicate the objects onto a line layer when an outline is required."
            )
        return LayerMode.FILL
    return LayerMode.LINE


def _operation_layer(
    element: ET.Element | None,
    *,
    cut_index: int,
    priority: int,
    warnings: list[str],
) -> OperationLayer:
    values = {} if element is None else _flatten_setting(element)
    name = _first_value(values, "name", "desc", "description")
    if not name:
        name = f"C{cut_index:02d}" if cut_index >= 0 else f"Index {cut_index}"
    display_name = f"LightBurn · {name}"[:80]
    mode = _layer_mode(values, warnings, cut_index)

    speed_raw = _first_value(values, "speed", "cutspeed", "speed1", "speed2")
    speed_mm_s = _bounded_value(
        speed_raw,
        default=20.0,
        minimum=0.001,
        maximum=10_000.0,
        label=f"LightBurn layer {cut_index} speed (mm/s)",
        warnings=warnings,
    )
    maximum_power_raw = _first_value(
        values,
        "maxpower",
        "power",
        "maxpower1",
        "maxpower2",
        "minpower",
    )
    power = _bounded_value(
        maximum_power_raw,
        default=10.0,
        minimum=0.0,
        maximum=100.0,
        label=f"LightBurn layer {cut_index} power",
        warnings=warnings,
    )
    minimum_power_raw = _first_value(values, "minpower", "minpower1")
    if minimum_power_raw is not None and maximum_power_raw is not None:
        minimum_power = _finite(
            minimum_power_raw,
            f"LightBurn layer {cut_index} minimum power",
        )
        maximum_power = _finite(
            maximum_power_raw,
            f"LightBurn layer {cut_index} maximum power",
        )
        if abs(minimum_power - maximum_power) > 1e-9:
            warnings.append(
                f"LightBurn layer {cut_index} stores separate minimum and maximum power; "
                "E3 imported the maximum value only"
            )
    pass_raw = _first_value(values, "passcount", "passes", "numpasses", "pass")
    passes = 1 if pass_raw is None else max(1, _integer(pass_raw, f"LightBurn layer {cut_index} passes"))
    if passes > 10_000:
        warnings.append(f"LightBurn layer {cut_index} passes {passes} was limited to 10,000")
        passes = 10_000

    interval = _bounded_value(
        _first_value(values, "interval", "lineinterval", "scaninterval", "scanstep"),
        default=0.10,
        minimum=0.001,
        maximum=100.0,
        label=f"LightBurn layer {cut_index} line interval",
        warnings=warnings,
    )
    angle = _bounded_value(
        _first_value(values, "angle", "scanangle", "scanangledeg"),
        default=0.0,
        minimum=-360_000.0,
        maximum=360_000.0,
        label=f"LightBurn layer {cut_index} scan angle",
        warnings=warnings,
    )
    overscan = _bounded_value(
        _first_value(values, "overscan", "overscanning", "overscanpercent"),
        default=2.5,
        minimum=0.0,
        maximum=100.0,
        label=f"LightBurn layer {cut_index} overscan",
        warnings=warnings,
    )
    visible = not _boolean_value(_first_value(values, "hidden", "ishidden"), default=False)
    color = _parse_color(_first_value(values, "color", "colour", "layercolor"), cut_index)

    unsupported_parameters = [
        label
        for aliases, label in (
            (("frequency",), "frequency"),
            (("qpulsewidth", "pulsewidth"), "pulse width"),
            (("ppi",), "PPI"),
            (("zoffset", "zstep", "zperpass"), "Z-axis settings"),
            (("tabcount", "perflen", "perfgap"), "tabs/perforation"),
            (("wobble", "wobblewidth", "wobblestep"), "wobble"),
        )
        if _first_value(values, *aliases) is not None
    ]
    if unsupported_parameters:
        warnings.append(
            f"LightBurn layer {cut_index} contains "
            + ", ".join(unsupported_parameters)
            + "; E3 cannot represent those controller-specific settings"
        )

    return OperationLayer(
        name=display_name,
        color=color,
        mode=mode,
        speed_mm_min=speed_mm_s * 60.0,
        power_percent=power,
        passes=passes,
        line_interval_mm=interval,
        scan_angle_deg=angle,
        overscan_percent=overscan,
        air_assist=_boolean_value(_first_value(values, "airassist", "runblower", "air")),
        # Foreign settings are data, never execution authority.  The operator must
        # explicitly review and enable every imported layer in E3.
        output_enabled=False,
        visible=visible,
        priority=priority,
    )


def _parse_affine(element: ET.Element, label: str) -> _Affine:
    xform = _first_direct_child(element, "XForm")
    raw = _element_value(xform) if xform is not None else _attribute(element, "XForm")
    if raw in {None, ""}:
        return _IDENTITY
    parts = str(raw).split()
    if len(parts) != 6:
        raise LightBurnImportError(f"{label} XForm must contain six numbers")
    return tuple(_finite(part, f"{label} XForm") for part in parts)  # type: ignore[return-value]


def _compose(parent: _Affine, child: _Affine) -> _Affine:
    pa, pb, pc, pd, pe, pf = parent
    ca, cb, cc, cd, ce, cf = child
    return (
        pa * ca + pc * cb,
        pb * ca + pd * cb,
        pa * cc + pc * cd,
        pb * cc + pd * cd,
        pa * ce + pc * cf + pe,
        pb * ce + pd * cf + pf,
    )


def _apply(transform: _Affine, point: tuple[float, float]) -> tuple[float, float]:
    a, b, c, d, e, f = transform
    x, y = point
    result = (a * x + c * y + e, b * x + d * y + f)
    if not all(math.isfinite(value) for value in result):
        raise LightBurnImportError("LightBurn transform produced a non-finite point")
    return result


def _transform_polyline(
    points: Sequence[tuple[float, float]],
    transform: _Affine,
) -> list[list[float]]:
    return [[x, y] for x, y in (_apply(transform, point) for point in points)]


def _rectangle_points(width: float, height: float, radius: float) -> list[tuple[float, float]]:
    if width <= 0.0 or height <= 0.0:
        raise LightBurnImportError("LightBurn rectangle width and height must be positive")
    half_width = width / 2.0
    half_height = height / 2.0
    radius = max(0.0, min(radius, half_width, half_height))
    if radius <= 1e-9:
        return [
            (-half_width, -half_height),
            (half_width, -half_height),
            (half_width, half_height),
            (-half_width, half_height),
        ]
    points: list[tuple[float, float]] = []
    for center_x, center_y, start_angle in (
        (half_width - radius, -half_height + radius, -90.0),
        (half_width - radius, half_height - radius, 0.0),
        (-half_width + radius, half_height - radius, 90.0),
        (-half_width + radius, -half_height + radius, 180.0),
    ):
        for step in range(9):
            angle = math.radians(start_angle + step * 90.0 / 8.0)
            points.append(
                (center_x + radius * math.cos(angle), center_y + radius * math.sin(angle))
            )
    return points


def _ellipse_points(radius_x: float, radius_y: float) -> list[tuple[float, float]]:
    if radius_x <= 0.0 or radius_y <= 0.0:
        raise LightBurnImportError("LightBurn ellipse radii must be positive")
    return [
        (
            radius_x * math.cos(2.0 * math.pi * step / 72.0),
            radius_y * math.sin(2.0 * math.pi * step / 72.0),
        )
        for step in range(72)
    ]


def _parse_vertices(raw: str, state: _ImportState, label: str) -> list[_Vertex]:
    if len(raw.encode("utf-8")) > MAX_LIGHTBURN_LIST_TEXT:
        raise LightBurnImportError(f"{label} VertList is too large")
    vertices: list[_Vertex] = []
    for match in _VERTEX_PATTERN.finditer(raw):
        controls: dict[str, float] = {}
        for control in _CONTROL_PATTERN.finditer(match.group(3)):
            controls[control.group(1)] = _finite(control.group(2), f"{label} control point")
        vertices.append(
            _Vertex(
                _finite(match.group(1), f"{label} vertex x"),
                _finite(match.group(2), f"{label} vertex y"),
                controls.get("c0x"),
                controls.get("c0y"),
                controls.get("c1x"),
                controls.get("c1y"),
            )
        )
    if not vertices:
        raise LightBurnImportError(f"{label} contains no usable vertices")
    state.add_vertices(len(vertices))
    return vertices


def _sample_cubic(
    start: _Vertex,
    end: _Vertex,
    state: _ImportState,
    label: str,
) -> list[tuple[float, float]]:
    if None in {start.c0x, start.c0y, end.c1x, end.c1y}:
        state.warnings.append(f"{label} is missing Bezier controls; imported that segment as a line")
        return [(end.x, end.y)]
    control_1 = (float(start.c0x), float(start.c0y))
    control_2 = (float(end.c1x), float(end.c1y))
    p0 = (start.x, start.y)
    p3 = (end.x, end.y)
    control_length = (
        math.dist(p0, control_1)
        + math.dist(control_1, control_2)
        + math.dist(control_2, p3)
    )
    steps = max(8, min(96, int(math.ceil(control_length / 0.4))))
    output: list[tuple[float, float]] = []
    for step in range(1, steps + 1):
        t = step / steps
        inverse = 1.0 - t
        x = (
            inverse**3 * p0[0]
            + 3.0 * inverse**2 * t * control_1[0]
            + 3.0 * inverse * t**2 * control_2[0]
            + t**3 * p3[0]
        )
        y = (
            inverse**3 * p0[1]
            + 3.0 * inverse**2 * t * control_1[1]
            + 3.0 * inverse * t**2 * control_2[1]
            + t**3 * p3[1]
        )
        output.append((x, y))
    state.add_points(len(output))
    return output


def _parse_path_polylines(
    element: ET.Element,
    transform: _Affine,
    state: _ImportState,
    label: str,
) -> list[dict[str, Any]]:
    vertex_id_raw = _attribute(element, "VertID")
    primitive_id_raw = _attribute(element, "PrimID")
    vertex_id = None if vertex_id_raw is None else _integer(vertex_id_raw, f"{label} VertID")
    primitive_id = (
        None if primitive_id_raw is None else _integer(primitive_id_raw, f"{label} PrimID")
    )

    vertex_node = _first_direct_child(element, "VertList")
    primitive_node = _first_direct_child(element, "PrimList")
    vertex_text = _element_value(vertex_node) if vertex_node is not None else None
    primitive_text = _element_value(primitive_node) if primitive_node is not None else None

    if vertex_text:
        if vertex_id is not None:
            state.vertex_cache[vertex_id] = vertex_text
    elif vertex_id is not None:
        vertex_text = state.vertex_cache.get(vertex_id)
    if primitive_text:
        if primitive_id is not None:
            state.primitive_cache[primitive_id] = primitive_text
    elif primitive_id is not None:
        primitive_text = state.primitive_cache.get(primitive_id)

    if not vertex_text:
        raise LightBurnImportError(f"{label} is missing its VertList data")
    if not primitive_text:
        raise LightBurnImportError(f"{label} is missing its PrimList data")
    if len(primitive_text.encode("utf-8")) > MAX_LIGHTBURN_LIST_TEXT:
        raise LightBurnImportError(f"{label} PrimList is too large")

    vertices = _parse_vertices(vertex_text, state, label)
    primitive_kind = re.sub(r"\s+", "", primitive_text).casefold()
    if primitive_kind in {"lineclosed", "lineopen"}:
        state.add_points(len(vertices))
        return [
            {
                "points": _transform_polyline(
                    [(vertex.x, vertex.y) for vertex in vertices],
                    transform,
                ),
                "closed": primitive_kind == "lineclosed",
            }
        ]

    primitives = list(_PRIMITIVE_PATTERN.finditer(primitive_text))
    if not primitives:
        raise LightBurnImportError(f"{label} contains no supported path primitives")
    residue = _PRIMITIVE_PATTERN.sub("", primitive_text)
    if residue.strip():
        tokens = sorted(set(re.findall(r"[A-Za-z]+", residue)))
        raise LightBurnImportError(
            f"{label} uses unsupported path primitive data: {', '.join(tokens) or residue[:40]}"
        )

    polylines: list[dict[str, Any]] = []
    current_points: list[tuple[float, float]] = []
    first_index: int | None = None
    current_index: int | None = None

    def finish() -> None:
        nonlocal current_points, first_index, current_index
        if len(current_points) >= 2:
            closed = first_index is not None and current_index == first_index
            if closed and math.dist(current_points[0], current_points[-1]) <= 1e-9:
                current_points = current_points[:-1]
            polylines.append(
                {
                    "points": _transform_polyline(current_points, transform),
                    "closed": closed,
                }
            )
        current_points = []
        first_index = None
        current_index = None

    for primitive in primitives:
        primitive_type = primitive.group(1).upper()
        start_index = int(primitive.group(2))
        end_index = int(primitive.group(3))
        if primitive_type not in {"L", "B"}:
            raise LightBurnImportError(f"{label} uses unsupported primitive {primitive_type}")
        if not 0 <= start_index < len(vertices) or not 0 <= end_index < len(vertices):
            raise LightBurnImportError(
                f"{label} primitive references vertex {start_index}/{end_index} outside the VertList"
            )
        if current_points and current_index != start_index:
            finish()
        if not current_points:
            start = vertices[start_index]
            current_points = [(start.x, start.y)]
            first_index = start_index
        start = vertices[start_index]
        end = vertices[end_index]
        if primitive_type == "L":
            current_points.append((end.x, end.y))
            state.add_points(1)
        else:
            current_points.extend(_sample_cubic(start, end, state, label))
        current_index = end_index
    finish()
    if not polylines:
        raise LightBurnImportError(f"{label} produced no usable polylines")
    return polylines


def _shape_name(element: ET.Element, shape_type: str, sequence: int) -> str:
    raw = _attribute(element, "Name") or _attribute(element, "Label")
    return str(raw).strip()[:160] if raw else f"LightBurn {shape_type} {sequence:03d}"


def _shape_cut_index(element: ET.Element, inherited: int | None, label: str) -> int:
    raw = _attribute(element, "CutIndex")
    if raw is None:
        if inherited is None:
            return 0
        return inherited
    return _integer(raw, f"{label} CutIndex")


def _parse_shape(
    element: ET.Element,
    parent_transform: _Affine,
    state: _ImportState,
    *,
    inherited_cut_index: int | None = None,
    group_id: str | None = None,
) -> list[_RawShape]:
    state.add_shape()
    shape_type = str(_attribute(element, "Type", _local_name(element.tag))).strip()
    type_key = shape_type.casefold()
    label = f"LightBurn {shape_type or 'shape'} {state.shape_count}"
    cut_index = _shape_cut_index(element, inherited_cut_index, label)
    transform = _compose(parent_transform, _parse_affine(element, label))
    name = _shape_name(element, shape_type or "Shape", state.shape_count)

    if type_key in {"group", "shapecontainer"}:
        children_parent = _first_direct_child(element, "Children")
        candidates = (
            list(children_parent)
            if children_parent is not None
            else _direct_children(element, "Shape")
        )
        children = [
            child
            for child in candidates
            if _local_name(child.tag).casefold() == "shape"
        ]
        if not children:
            state.warnings.append(f"{label} is empty and was not added")
            return []
        child_group_id = group_id or f"group-{uuid.uuid4().hex}"
        if group_id is not None:
            warning = "Nested LightBurn groups were flattened into one E3 object group"
            if warning not in state.warnings:
                state.warnings.append(warning)
        output: list[_RawShape] = []
        for child in children:
            output.extend(
                _parse_shape(
                    child,
                    transform,
                    state,
                    inherited_cut_index=cut_index,
                    group_id=child_group_id,
                )
            )
        return output

    if type_key == "text":
        backup = _first_direct_child(element, "BackupPath")
        if backup is None:
            raise LightBurnImportError(
                f"{label} has no vector BackupPath. Convert the text to paths in LightBurn, then retry."
            )
        backup_shapes = [child for child in backup if _local_name(child.tag).casefold() == "shape"]
        if backup_shapes:
            output: list[_RawShape] = []
            for child in backup_shapes:
                output.extend(
                    _parse_shape(
                        child,
                        parent_transform,
                        state,
                        inherited_cut_index=cut_index,
                        group_id=group_id,
                    )
                )
            for index, item in enumerate(output, start=1):
                item.name = name if len(output) == 1 else f"{name} {index}"
                item.shape_type = "Text (vector backup)"
            return output
        if str(_attribute(backup, "Type", "")).casefold() == "path":
            output = _parse_shape(
                backup,
                parent_transform,
                state,
                inherited_cut_index=cut_index,
                group_id=group_id,
            )
            for item in output:
                item.name = name
                item.shape_type = "Text (vector backup)"
            return output
        raise LightBurnImportError(
            f"{label} BackupPath does not contain a usable vector Shape"
        )

    if type_key in {"bitmap", "image"}:
        raise LightBurnImportError(
            f"{label} is an embedded bitmap. This first native importer is vector-only; "
            "export that bitmap from LightBurn as PNG and import it into E3 separately."
        )

    if type_key in {"rect", "rectangle"}:
        width = _finite(_attribute(element, "W"), f"{label} width")
        height = _finite(_attribute(element, "H"), f"{label} height")
        radius = _finite(_attribute(element, "Cr", 0.0), f"{label} corner radius")
        points = _rectangle_points(width, height, radius)
        state.add_points(len(points))
        polylines = [{"points": _transform_polyline(points, transform), "closed": True}]
    elif type_key in {"ellipse", "circle"}:
        radius_x = _finite(_attribute(element, "Rx"), f"{label} radius x")
        radius_y = _finite(_attribute(element, "Ry", radius_x), f"{label} radius y")
        points = _ellipse_points(radius_x, radius_y)
        state.add_points(len(points))
        polylines = [{"points": _transform_polyline(points, transform), "closed": True}]
    elif type_key == "path":
        polylines = _parse_path_polylines(element, transform, state, label)
    else:
        raise LightBurnImportError(
            f"{label} uses unsupported shape type {shape_type!r}. Convert it to vector paths in LightBurn."
        )

    return [
        _RawShape(
            cut_index=cut_index,
            shape_type=shape_type,
            name=name,
            polylines=polylines,
            group_id=group_id,
        )
    ]


def _root_from_xml(xml: str | bytes) -> ET.Element:
    raw = xml.encode("utf-8") if isinstance(xml, str) else bytes(xml)
    if len(raw) > MAX_LIGHTBURN_FILE_BYTES:
        raise LightBurnImportError(
            f"LightBurn project exceeds the {MAX_LIGHTBURN_FILE_BYTES // (1024 * 1024)} MiB import limit"
        )
    if _UNSAFE_XML_PATTERN.search(raw):
        raise LightBurnImportError("LightBurn project contains prohibited XML declarations")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise LightBurnImportError(f"Invalid LightBurn XML: {exc}") from exc
    if _local_name(root.tag).casefold() != "lightburnproject":
        raise LightBurnImportError("File is not a LightBurnProject document")
    element_count = sum(1 for _ in root.iter())
    if element_count > MAX_LIGHTBURN_XML_ELEMENTS:
        raise LightBurnImportError(
            f"LightBurn project exceeds the {MAX_LIGHTBURN_XML_ELEMENTS:,}-element XML limit"
        )
    return root


def _collect_cut_settings(root: ET.Element) -> list[tuple[int, ET.Element]]:
    output: list[tuple[int, ET.Element]] = []
    seen: set[int] = set()
    for element in root:
        if _local_name(element.tag).casefold() != "cutsetting":
            continue
        values = _flatten_setting(element)
        raw_index = _first_value(values, "index", "cutindex")
        index = len(output) if raw_index is None else _integer(raw_index, "LightBurn cut-setting index")
        if index in seen:
            raise LightBurnImportError(f"LightBurn project contains duplicate CutSetting index {index}")
        seen.add(index)
        output.append((index, element))
    return output


def _shape_points(shape: _RawShape) -> Iterable[tuple[float, float]]:
    for polyline in shape.polylines:
        for point in polyline["points"]:
            yield float(point[0]), float(point[1])



def _scan_lightburn_shapes(
    root: ET.Element,
    *,
    warnings: list[str],
    approximations: list[str],
    unsupported_features: list[str],
    errors: list[str],
) -> tuple[dict[int, int], int]:
    """Inspect LightBurn shape structure without vectorizing source geometry."""

    counts_by_cut: dict[int, int] = {}
    shape_count = 0
    vertex_cache_ids: set[int] = set()
    primitive_cache_ids: set[int] = set()

    def add_once(target: list[str], message: str) -> None:
        if message not in target:
            target.append(message)

    def scan_path(element: ET.Element, label: str) -> None:
        vertex_id_raw = _attribute(element, "VertID")
        primitive_id_raw = _attribute(element, "PrimID")
        try:
            vertex_id = (
                None
                if vertex_id_raw is None
                else _integer(vertex_id_raw, f"{label} VertID")
            )
            primitive_id = (
                None
                if primitive_id_raw is None
                else _integer(primitive_id_raw, f"{label} PrimID")
            )
        except LightBurnImportError as exc:
            add_once(errors, str(exc))
            return

        vertex_node = _first_direct_child(element, "VertList")
        primitive_node = _first_direct_child(element, "PrimList")
        vertex_text = _element_value(vertex_node) if vertex_node is not None else None
        primitive_text = (
            _element_value(primitive_node) if primitive_node is not None else None
        )

        if vertex_text and vertex_id is not None:
            vertex_cache_ids.add(vertex_id)
        elif not vertex_text and vertex_id is not None and vertex_id not in vertex_cache_ids:
            add_once(errors, f"{label} is missing its VertList data")

        if primitive_text and primitive_id is not None:
            primitive_cache_ids.add(primitive_id)
        elif (
            not primitive_text
            and primitive_id is not None
            and primitive_id not in primitive_cache_ids
        ):
            add_once(errors, f"{label} is missing its PrimList data")

        for text, list_name in (
            (vertex_text, "VertList"),
            (primitive_text, "PrimList"),
        ):
            if text and len(text.encode("utf-8")) > MAX_LIGHTBURN_LIST_TEXT:
                add_once(errors, f"{label} {list_name} is too large")

        if not primitive_text:
            return

        primitive_kind = re.sub(r"\s+", "", primitive_text).casefold()
        if primitive_kind in {"lineclosed", "lineopen"}:
            return

        primitives = list(_PRIMITIVE_PATTERN.finditer(primitive_text))
        if not primitives:
            add_once(
                unsupported_features,
                f"{label} contains no supported path primitives",
            )
            return

        residue = _PRIMITIVE_PATTERN.sub("", primitive_text)
        if residue.strip():
            tokens = sorted(set(re.findall(r"[A-Za-z]+", residue)))
            add_once(
                unsupported_features,
                f"{label} uses unsupported path primitive data: "
                f"{', '.join(tokens) or residue[:40]}",
            )
        primitive_types = {match.group(1).upper() for match in primitives}
        for primitive_type in sorted(primitive_types.difference({"L", "B"})):
            add_once(
                unsupported_features,
                f"{label} uses unsupported primitive {primitive_type}",
            )
        if "B" in primitive_types:
            add_once(
                approximations,
                "Bezier path segments will be flattened to bounded polylines",
            )

    def scan_shape(
        element: ET.Element,
        *,
        inherited_cut_index: int | None = None,
        group_depth: int = 0,
    ) -> None:
        nonlocal shape_count
        shape_count += 1
        if shape_count > MAX_LIGHTBURN_SHAPES:
            add_once(
                errors,
                f"LightBurn project exceeds the {MAX_LIGHTBURN_SHAPES:,}-shape import limit",
            )
            return

        shape_type = str(
            _attribute(element, "Type", _local_name(element.tag))
        ).strip()
        type_key = shape_type.casefold()
        label = f"LightBurn {shape_type or 'shape'} {shape_count}"
        try:
            cut_index = _shape_cut_index(element, inherited_cut_index, label)
        except LightBurnImportError as exc:
            add_once(errors, str(exc))
            return

        if type_key in {"group", "shapecontainer"}:
            children_parent = _first_direct_child(element, "Children")
            candidates = (
                list(children_parent)
                if children_parent is not None
                else _direct_children(element, "Shape")
            )
            children = [
                child
                for child in candidates
                if _local_name(child.tag).casefold() == "shape"
            ]
            if not children:
                add_once(warnings, f"{label} is empty and will not add geometry")
                return
            if group_depth:
                add_once(
                    approximations,
                    "Nested LightBurn groups will be flattened into one E3 object group",
                )
            for child in children:
                scan_shape(
                    child,
                    inherited_cut_index=cut_index,
                    group_depth=group_depth + 1,
                )
            return

        if type_key == "text":
            backup = _first_direct_child(element, "BackupPath")
            if backup is None:
                add_once(
                    unsupported_features,
                    f"{label} has no vector BackupPath. Convert the text to paths in LightBurn, then retry.",
                )
                return
            backup_shapes = [
                child
                for child in backup
                if _local_name(child.tag).casefold() == "shape"
            ]
            if backup_shapes:
                add_once(
                    approximations,
                    "LightBurn text will use its stored vector BackupPath rather than editable font text",
                )
                for child in backup_shapes:
                    scan_shape(
                        child,
                        inherited_cut_index=cut_index,
                        group_depth=group_depth,
                    )
                return
            if str(_attribute(backup, "Type", "")).casefold() == "path":
                add_once(
                    approximations,
                    "LightBurn text will use its stored vector BackupPath rather than editable font text",
                )
                scan_shape(
                    backup,
                    inherited_cut_index=cut_index,
                    group_depth=group_depth,
                )
                return
            add_once(
                unsupported_features,
                f"{label} BackupPath does not contain a usable vector Shape",
            )
            return

        if type_key in {"bitmap", "image"}:
            add_once(
                unsupported_features,
                f"{label} is an embedded bitmap. This native importer is vector-only; "
                "export that bitmap from LightBurn as PNG and import it into E3 separately.",
            )
            return

        if type_key in {"rect", "rectangle"}:
            radius_raw = _attribute(element, "Cr", 0.0)
            try:
                radius = _finite(radius_raw, f"{label} corner radius")
            except LightBurnImportError as exc:
                add_once(errors, str(exc))
                return
            if abs(radius) > 1e-12:
                add_once(
                    approximations,
                    "Rounded LightBurn rectangles will be sampled into bounded vector polylines",
                )
        elif type_key in {"ellipse", "circle"}:
            add_once(
                approximations,
                "LightBurn ellipses/circles will be sampled into bounded vector polylines",
            )
        elif type_key == "path":
            scan_path(element, label)
        else:
            add_once(
                unsupported_features,
                f"{label} uses unsupported shape type {shape_type!r}. "
                "Convert it to vector paths in LightBurn.",
            )
            return

        counts_by_cut[cut_index] = counts_by_cut.get(cut_index, 0) + 1

    for element in root:
        if _local_name(element.tag).casefold() == "shape":
            scan_shape(element)

    return counts_by_cut, shape_count


def _scan_lightburn_layers(
    root: ET.Element,
    counts_by_cut: Mapping[int, int],
    *,
    warnings: list[str],
    approximations: list[str],
) -> tuple[ImportLayerManifest, ...]:
    from .import_manifest import ImportLayerManifest

    settings = _collect_cut_settings(root)
    settings_by_index = {index: element for index, element in settings}
    setting_rank: dict[int, tuple[float, int]] = {}
    for ordinal, (index, element) in enumerate(settings):
        values = _flatten_setting(element)
        raw_priority = _first_value(values, "priority", "order")
        priority = (
            float(ordinal)
            if raw_priority is None
            else _finite(raw_priority, f"LightBurn layer {index} priority")
        )
        setting_rank[index] = (priority, ordinal)

    referenced_indices = list(counts_by_cut)
    layer_order = sorted(
        (index for index in referenced_indices if index in settings_by_index),
        key=lambda index: setting_rank[index],
    )
    layer_order.extend(
        index for index in referenced_indices if index not in settings_by_index
    )

    output: list[ImportLayerManifest] = []
    for index in layer_order:
        element = settings_by_index.get(index)
        values = {} if element is None else _flatten_setting(element)
        if element is None:
            warnings.append(
                f"No CutSetting was stored for LightBurn layer {index}; "
                "strict import will use conservative defaults"
            )

        name = _first_value(values, "name", "desc", "description")
        if not name:
            name = f"C{index:02d}" if index >= 0 else f"Index {index}"

        mode_warnings: list[str] = []
        mode = _layer_mode(values, mode_warnings, index)
        for warning in mode_warnings:
            if warning not in approximations:
                approximations.append(warning)

        unsupported_parameters = [
            label
            for aliases, label in (
                (("frequency",), "frequency"),
                (("qpulsewidth", "pulsewidth"), "pulse width"),
                (("ppi",), "PPI"),
                (("zoffset", "zstep", "zperpass"), "Z-axis settings"),
                (("tabcount", "perflen", "perfgap"), "tabs/perforation"),
                (("wobble", "wobblewidth", "wobblestep"), "wobble"),
            )
            if _first_value(values, *aliases) is not None
        ]
        if unsupported_parameters:
            message = (
                f"LightBurn layer {index} contains "
                + ", ".join(unsupported_parameters)
                + "; E3 cannot represent those controller-specific settings"
            )
            if message not in approximations:
                approximations.append(message)

        output.append(
            ImportLayerManifest(
                source_key=f"cut:{index}",
                name=str(name)[:80],
                mode_hint=mode.value,
                object_count=counts_by_cut[index],
            )
        )
    return tuple(output)


def scan_lightburn_project(
    xml: str | bytes,
    *,
    source_name: str = "untitled.lbrn2",
    source_suffix: str | None = None,
    source_size_bytes: int | None = None,
    max_file_bytes: int = MAX_LIGHTBURN_FILE_BYTES,
    source_sha256: str | None = None,
) -> ImportScanManifest:
    """Return bounded, non-mutating LightBurn facts before strict vector parsing."""

    from .import_manifest import LIGHTBURN_IMPORTER_SPEC, ImportScanManifest

    raw = xml.encode("utf-8") if isinstance(xml, str) else bytes(xml)
    suffix = (
        Path(source_name).suffix.casefold()
        if source_suffix is None
        else str(source_suffix).casefold()
    )
    if not suffix:
        suffix = ".lbrn2"
    size = len(raw) if source_size_bytes is None else int(source_size_bytes)
    limit = int(max_file_bytes)
    if limit < 1:
        raise ValueError("max_file_bytes must be positive")

    base = {
        "importer_id": LIGHTBURN_IMPORTER_SPEC.importer_id,
        "source_name": source_name,
        "source_suffix": suffix,
        "source_size_bytes": max(0, size),
        "capabilities": LIGHTBURN_IMPORTER_SPEC.capabilities,
        "source_sha256": (
            hashlib.sha256(raw).hexdigest()
            if source_sha256 is None
            else source_sha256
        ),
    }

    if suffix not in SUPPORTED_LIGHTBURN_SUFFIXES:
        return ImportScanManifest(
            **base,
            errors=("LightBurn projects must use the .lbrn or .lbrn2 extension",),
        )
    if size < 0:
        return ImportScanManifest(
            **base,
            errors=("LightBurn source size must not be negative",),
        )
    if size > limit or len(raw) > limit:
        measured = max(size, len(raw))
        return ImportScanManifest(
            **base,
            errors=(
                f"LightBurn project is {measured / (1024 * 1024):.1f} MiB; "
                f"import limit is {limit / (1024 * 1024):.1f} MiB",
            ),
        )

    try:
        root = _root_from_xml(raw)
    except LightBurnImportError as exc:
        return ImportScanManifest(**base, errors=(str(exc),))

    warnings = [
        "Imported LightBurn operation layers will remain output-disabled until reviewed in E3"
    ]
    approximations: list[str] = []
    unsupported_features: list[str] = []
    errors: list[str] = []

    counts_by_cut, _shape_count = _scan_lightburn_shapes(
        root,
        warnings=warnings,
        approximations=approximations,
        unsupported_features=unsupported_features,
        errors=errors,
    )

    layers: tuple[ImportLayerManifest, ...] = ()
    if counts_by_cut:
        try:
            layers = _scan_lightburn_layers(
                root,
                counts_by_cut,
                warnings=warnings,
                approximations=approximations,
            )
        except LightBurnImportError as exc:
            errors.append(str(exc))

    if not counts_by_cut and not unsupported_features and not errors:
        errors.append("LightBurn project contains no usable vector shapes")

    return ImportScanManifest(
        **base,
        format_version=str(_attribute(root, "FormatVersion", "")),
        layers=layers,
        coordinate_facts=(
            "LightBurn affine transforms are resolved during strict import before E3 recenters the imported design",
        ),
        warnings=tuple(dict.fromkeys(warnings)),
        approximations=tuple(dict.fromkeys(approximations)),
        unsupported_features=tuple(dict.fromkeys(unsupported_features)),
        errors=tuple(dict.fromkeys(errors)),
    )


def scan_lightburn_file(
    path: str | Path,
    *,
    max_file_bytes: int = MAX_LIGHTBURN_FILE_BYTES,
) -> ImportScanManifest:
    """Read one LightBurn file once and return a bounded pre-parse manifest."""

    from .import_manifest import LIGHTBURN_IMPORTER_SPEC, ImportScanManifest

    source = Path(path)
    suffix = source.suffix.casefold() or ".lbrn2"
    limit = int(max_file_bytes)
    if limit < 1:
        raise ValueError("max_file_bytes must be positive")

    try:
        size = source.stat().st_size
    except OSError as exc:
        return ImportScanManifest(
            importer_id=LIGHTBURN_IMPORTER_SPEC.importer_id,
            source_name=source.name or "untitled.lbrn2",
            source_suffix=suffix,
            source_size_bytes=0,
            capabilities=LIGHTBURN_IMPORTER_SPEC.capabilities,
            errors=(f"Could not inspect LightBurn project: {exc}",),
        )

    if suffix not in SUPPORTED_LIGHTBURN_SUFFIXES:
        return ImportScanManifest(
            importer_id=LIGHTBURN_IMPORTER_SPEC.importer_id,
            source_name=source.name,
            source_suffix=suffix,
            source_size_bytes=max(0, size),
            capabilities=LIGHTBURN_IMPORTER_SPEC.capabilities,
            errors=("LightBurn projects must use the .lbrn or .lbrn2 extension",),
        )
    if size > limit:
        return ImportScanManifest(
            importer_id=LIGHTBURN_IMPORTER_SPEC.importer_id,
            source_name=source.name,
            source_suffix=suffix,
            source_size_bytes=size,
            capabilities=LIGHTBURN_IMPORTER_SPEC.capabilities,
            errors=(
                f"LightBurn project is {size / (1024 * 1024):.1f} MiB; "
                f"import limit is {limit / (1024 * 1024):.1f} MiB",
            ),
        )

    try:
        payload = source.read_bytes()
    except OSError as exc:
        return ImportScanManifest(
            importer_id=LIGHTBURN_IMPORTER_SPEC.importer_id,
            source_name=source.name,
            source_suffix=suffix,
            source_size_bytes=size,
            capabilities=LIGHTBURN_IMPORTER_SPEC.capabilities,
            errors=(f"Could not read LightBurn project: {exc}",),
        )

    return scan_lightburn_project(
        payload,
        source_name=source.name,
        source_suffix=suffix,
        source_size_bytes=len(payload),
        max_file_bytes=limit,
        source_sha256=hashlib.sha256(payload).hexdigest(),
    )


def _raise_for_blocked_lightburn_manifest(manifest: ImportScanManifest) -> None:
    if manifest.errors:
        raise LightBurnImportError(manifest.errors[0])
    if manifest.unsupported_features:
        raise LightBurnImportError(manifest.unsupported_features[0])


def parse_lightburn_project(
    xml: str | bytes,
    *,
    source_name: str = "",
    center: tuple[float, float] = (0.0, 0.0),
) -> LightBurnImportResult:
    """Parse a LightBurn project into output-disabled native E3 layers and objects."""

    root = _root_from_xml(xml)
    app_version = str(_attribute(root, "AppVersion", ""))
    format_version = str(_attribute(root, "FormatVersion", ""))
    state = _ImportState(
        source_name=source_name,
        app_version=app_version,
        format_version=format_version,
    )

    raw_shapes: list[_RawShape] = []
    for element in root:
        if _local_name(element.tag).casefold() == "shape":
            raw_shapes.extend(_parse_shape(element, _IDENTITY, state))
    if not raw_shapes:
        raise LightBurnImportError("LightBurn project contains no usable vector shapes")

    all_points = [point for shape in raw_shapes for point in _shape_points(shape)]
    if not all_points:
        raise LightBurnImportError("LightBurn project contains no usable vector points")
    min_x = min(point[0] for point in all_points)
    max_x = max(point[0] for point in all_points)
    min_y = min(point[1] for point in all_points)
    max_y = max(point[1] for point in all_points)
    target_x = _finite(center[0], "LightBurn import center x")
    target_y = _finite(center[1], "LightBurn import center y")
    shift_x = target_x - (min_x + max_x) / 2.0
    shift_y = target_y - (min_y + max_y) / 2.0

    settings = _collect_cut_settings(root)
    settings_by_index = {index: element for index, element in settings}
    setting_rank: dict[int, tuple[float, int]] = {}
    for ordinal, (index, element) in enumerate(settings):
        values = _flatten_setting(element)
        raw_priority = _first_value(values, "priority", "order")
        priority = float(ordinal) if raw_priority is None else _finite(
            raw_priority,
            f"LightBurn layer {index} priority",
        )
        setting_rank[index] = (priority, ordinal)
    referenced_indices = list(dict.fromkeys(shape.cut_index for shape in raw_shapes))
    layer_order = sorted(
        (index for index in referenced_indices if index in settings_by_index),
        key=lambda index: setting_rank[index],
    )
    layer_order.extend(index for index in referenced_indices if index not in settings_by_index)
    layers: list[OperationLayer] = []
    layer_by_index: dict[int, OperationLayer] = {}
    for priority, index in enumerate(layer_order):
        element = settings_by_index.get(index)
        if element is None:
            state.warnings.append(
                f"No CutSetting was stored for LightBurn layer {index}; E3 used conservative defaults"
            )
        layer = _operation_layer(
            element,
            cut_index=index,
            priority=priority,
            warnings=state.warnings,
        )
        layers.append(layer)
        layer_by_index[index] = layer

    objects: list[SceneObject] = []
    for raw_shape in raw_shapes:
        shifted: list[dict[str, Any]] = []
        shifted_points: list[tuple[float, float]] = []
        for polyline in raw_shape.polylines:
            points = [
                [float(point[0]) + shift_x, float(point[1]) + shift_y]
                for point in polyline["points"]
            ]
            shifted.append({"points": points, "closed": bool(polyline["closed"])})
            shifted_points.extend((point[0], point[1]) for point in points)
        shape_center = (
            (min(point[0] for point in shifted_points) + max(point[0] for point in shifted_points)) / 2.0,
            (min(point[1] for point in shifted_points) + max(point[1] for point in shifted_points)) / 2.0,
        )
        item = SceneObject.path(
            layer_by_index[raw_shape.cut_index].id,
            shifted,
            name=raw_shape.name,
            center=shape_center,
            source_name=source_name,
        )
        item.group_id = raw_shape.group_id
        item.metadata.update(
            {
                "lightburn_source": source_name,
                "lightburn_shape_type": raw_shape.shape_type,
                "lightburn_cut_index": raw_shape.cut_index,
                "lightburn_app_version": app_version,
                "lightburn_format_version": format_version,
                "lightburn_settings_review_required": True,
            }
        )
        objects.append(item)

    return LightBurnImportResult(
        layers=layers,
        objects=objects,
        warnings=list(dict.fromkeys(state.warnings)),
        app_version=app_version,
        format_version=format_version,
        source_name=source_name,
    )


def load_lightburn_project(
    path: str | Path,
    *,
    center: tuple[float, float] = (0.0, 0.0),
    max_file_bytes: int = MAX_LIGHTBURN_FILE_BYTES,
    expected_source_sha256: str | None = None,
) -> LightBurnImportResult:
    """Read and parse one ``.lbrn`` or ``.lbrn2`` file with a bounded allocation."""

    source = Path(path)
    suffix = source.suffix.casefold()
    if suffix not in SUPPORTED_LIGHTBURN_SUFFIXES:
        raise LightBurnImportError("LightBurn projects must use the .lbrn or .lbrn2 extension")
    try:
        size = source.stat().st_size
    except OSError as exc:
        raise LightBurnImportError(f"Could not inspect LightBurn project: {exc}") from exc
    limit = int(max_file_bytes)
    if limit < 1:
        raise ValueError("max_file_bytes must be positive")
    if size > limit:
        raise LightBurnImportError(
            f"LightBurn project is {size / (1024 * 1024):.1f} MiB; import limit is "
            f"{limit / (1024 * 1024):.1f} MiB"
        )
    try:
        payload = source.read_bytes()
    except OSError as exc:
        raise LightBurnImportError(f"Could not read LightBurn project: {exc}") from exc
    source_sha256 = hashlib.sha256(payload).hexdigest()
    if (
        expected_source_sha256 is not None
        and source_sha256 != str(expected_source_sha256).strip().casefold()
    ):
        raise LightBurnImportError(
            "LightBurn source changed after import review; select and review the file again"
        )
    manifest = scan_lightburn_project(
        payload,
        source_name=source.name,
        source_suffix=suffix,
        source_size_bytes=len(payload),
        max_file_bytes=limit,
        source_sha256=source_sha256,
    )
    _raise_for_blocked_lightburn_manifest(manifest)
    return parse_lightburn_project(payload, source_name=source.name, center=center)
