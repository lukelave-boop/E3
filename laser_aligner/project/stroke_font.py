"""Layout licensed, bundled open-path fonts without Qt or installed-font lookup."""

from __future__ import annotations

import math
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from ..geometry.svg import _GeometryBudget, _path_to_polylines
from .path_geometry import MAX_NATIVE_PATH_FLATTENED_POINTS

STROKE_FONT_NAME = "Twin Sans"
STROKE_FONTS = {
    STROKE_FONT_NAME: "TwinSans.svg",
    "Relief SingleLine": "ReliefSingleLineSVG-Regular.svg",
    "Hershey Sans": "HersheySans1.svg",
    "Hershey Script": "HersheyScript1.svg",
}
_NS = "{http://www.w3.org/2000/svg}"


@dataclass(frozen=True)
class _Font:
    glyphs: dict[str, ET.Element]
    advance: float
    cap_height: float
    line_height: float
    kerning: dict[tuple[str, str], float]


@lru_cache(maxsize=len(STROKE_FONTS))
def _font(name: str) -> _Font:
    if name not in STROKE_FONTS:
        raise ValueError(f"Unknown single-line font: {name}")
    root = ET.parse(Path(__file__).with_name("fonts") / STROKE_FONTS[name]).getroot()
    font = root.find(f"{_NS}defs/{_NS}font")
    if font is None:
        raise ValueError(f"Missing bundled single-line font: {name}")
    face = font.find(f"{_NS}font-face")
    if face is None:
        raise ValueError(f"Missing font metrics: {name}")
    glyphs = {g.get("unicode"): g for g in font.findall(f"{_NS}glyph") if len(g.get("unicode", "")) == 1}
    kerning = {}
    for pair in font.findall(f"{_NS}hkern"):
        for left in pair.get("g1", "").split(","):
            for right in pair.get("g2", "").split(","):
                kerning[left, right] = float(pair.get("k", "0"))
    return _Font(
        glyphs, float(font.get("horiz-adv-x", "0")), float(face.get("cap-height", "1")),
        (float(face.get("ascent", "800")) + abs(float(face.get("descent", "-200")))) * 1.2,
        kerning,
    )


@lru_cache(maxsize=1024)
def _glyph_strokes(name: str, character: str) -> tuple[tuple[tuple[float, float], ...], ...]:
    data = _font(name).glyphs[character].get("d", "")
    if not data:
        return ()
    # Normalize tolerance by cap height: Twin Sans uses 64/em, Relief 1000/em.
    # Both retain the same curve precision after text is scaled for engraving.
    tolerance = _font(name).cap_height / 6800.0
    paths = _path_to_polylines(data, tolerance, np.eye(3), _GeometryBudget(strict_tolerance=True))
    return tuple(tuple((float(x), float(y)) for x, y in path.points) for path in paths)


def stroke_text_lines(text: str, font_name: str = STROKE_FONT_NAME) -> list[list[tuple[float, float]]]:
    """Lay out original font strokes, with advance widths, kerning and line breaks."""
    if len(text) > 4096:
        raise ValueError("Single-line text is limited to 4096 characters per object")
    font = _font(font_name)
    text = unicodedata.normalize("NFC", text).expandtabs(4)
    unsupported = sorted(set(text) - set(font.glyphs) - {"\n", "\r"})
    if unsupported:
        raise ValueError(f"Unsupported characters in {font_name}: {''.join(unsupported)!r}")
    layouts = []
    for line in text.splitlines():
        pen_x = 0.0
        previous = ""
        placed = []
        for character in line:
            glyph = font.glyphs[character]
            glyph_name = glyph.get("glyph-name", "")
            pen_x -= font.kerning.get((previous, glyph_name), 0.0)
            placed.append((pen_x, character))
            advance = float(glyph.get("horiz-adv-x", str(font.advance)))
            if not math.isfinite(advance) or advance <= 0:
                raise ValueError(f"Invalid glyph advance in {font_name}: {character!r}")
            pen_x += advance
            previous = glyph_name
        layouts.append((pen_x, placed))
    widest = max((width for width, _ in layouts), default=0.0)
    scale = 6.0 / font.cap_height
    result = []
    point_count = 0
    for row, (width, placed) in enumerate(layouts):
        offset = (widest - width) / 2.0
        for pen_x, character in placed:
            for stroke in _glyph_strokes(font_name, character):
                point_count += len(stroke)
                if point_count > MAX_NATIVE_PATH_FLATTENED_POINTS:
                    raise ValueError("Single-line text exceeds the path point limit; use shorter text")
                result.append([
                    ((offset + pen_x + x) * scale, (row * font.line_height - y) * scale)
                    for x, y in stroke
                ])
    if not result:
        raise ValueError("Text contains no engravable strokes")
    return result
