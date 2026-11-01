from __future__ import annotations

import hashlib
import math
from pathlib import Path

import pytest

from laser_aligner.project.stroke_font import STROKE_FONT_NAME, STROKE_FONTS, stroke_text_lines


def test_default_font_has_the_original_smooth_three():
    strokes = stroke_text_lines("3")
    assert STROKE_FONT_NAME == "Relief SingleLine"
    assert len(strokes) == 2
    assert sum(map(len, strokes)) > 80
    # Both bowls reach the right-hand side; the original hand-drawn zigzag did not.
    assert max(x for x, _ in strokes[0]) > 3.9
    assert max(x for x, _ in strokes[1]) > 3.9


@pytest.mark.parametrize("font", STROKE_FONTS)
def test_corpus_preserves_real_font_geometry_and_unicode(font):
    strokes = stroke_text_lines("E3 Aa Bb 0123456789 Café", font)
    assert all(math.isfinite(n) for stroke in strokes for point in stroke for n in point)
    assert stroke_text_lines("Cafe\u0301", font) == stroke_text_lines("Café", font)
    assert stroke_text_lines("E3", font) != stroke_text_lines("E3", "Hershey Sans" if font == STROKE_FONT_NAME else STROKE_FONT_NAME)


def test_font_advance_widths_and_kerning_are_used():
    # Relief's I intentionally omits horiz-adv-x: it inherits the font's 280.
    single = stroke_text_lines("I")
    pair = stroke_text_lines("II")
    assert pair[2][0][0] - single[0][0][0] == pytest.approx(280 * 6 / 680)
    # Original AV pair has 75 units of kerning after A's 676-unit advance.
    v = stroke_text_lines("V")
    av = stroke_text_lines("AV")
    assert av[-1][0][0] - v[0][0][0] == pytest.approx((676 - 75) * 6 / 680)


@pytest.mark.parametrize("text,font", [("E3", "Arial"), ("漢", STROKE_FONT_NAME), ("A" * 4097, STROKE_FONT_NAME)])
def test_unknown_font_unsupported_text_and_excessive_input_reject(text, font):
    with pytest.raises(ValueError):
        stroke_text_lines(text, font)


def test_aggregate_geometry_is_bounded(monkeypatch):
    import laser_aligner.project.stroke_font as module

    monkeypatch.setattr(module, "MAX_NATIVE_PATH_FLATTENED_POINTS", 10)
    with pytest.raises(ValueError, match="point limit"):
        stroke_text_lines("3")


def test_distributed_fonts_are_unmodified_and_include_licenses():
    root = Path(__file__).resolve().parents[1] / "laser_aligner/project/fonts"
    expected = {
        "ReliefSingleLineSVG-Regular.svg": "75f05a5b64ed6039c9816628ee051d98e16c19148a8268c63f5eccf8382479e2",
        "HersheySans1.svg": "998705d56cf5148e5b6ef8149801edda3e594ff778f65c84e97977e809db5ed3",
        "HersheyScript1.svg": "26854b00c2147c2a0a27dba488d7a696db581603ff43f4b85f07b435246d473f",
    }
    for filename, digest in expected.items():
        # Git may use native CRLF when checking out the unchanged upstream text.
        assert hashlib.sha256((root / filename).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == digest
    assert "SIL OPEN FONT LICENSE Version 1.1" in (root / "Relief-OFL.txt").read_text()
    assert "James Hurt" in (root / "HersheySans1.svg").read_text()
    assert "https://github.com/isdat-type/Relief-SingleLine" in (root / "FONT_NOTICES.txt").read_text()
