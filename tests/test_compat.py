from __future__ import annotations

import enum
import sys
import types
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_python_310_string_enum_fallback_preserves_required_behavior() -> None:
    """Exercise the fallback even when the host interpreter has native StrEnum."""

    fake_enum = types.ModuleType("enum")
    fake_enum.Enum = enum.Enum
    original_enum = sys.modules["enum"]
    namespace = {
        "__name__": "laser_aligner._compat_fallback_test",
        "__package__": "laser_aligner",
    }
    try:
        sys.modules["enum"] = fake_enum
        source = (ROOT / "laser_aligner" / "_compat.py").read_text(
            encoding="utf-8"
        )
        exec(compile(source, "laser_aligner/_compat.py", "exec"), namespace)
    finally:
        sys.modules["enum"] = original_enum

    fallback = namespace["StrEnum"]

    class Example(fallback):
        VALUE = "value"

    assert Example.VALUE == "value"
    assert str(Example.VALUE) == "value"
    assert Example("value") is Example.VALUE
