"""Pinned companion dependencies selected from the actual packaged sources."""
from __future__ import annotations

import ast
from collections.abc import Callable, Mapping

SURFACE_MODULE = "laser_aligner/machine/material_surface.py"
PREVIEW_MODULE = "laser_aligner/gcode/preview.py"
MATERIAL_PREDECESSORS = {
    SURFACE_MODULE: None,
    PREVIEW_MODULE: "0620b59f8f64d25f19aab48a8081c490631f9bc65c730ab3c9e0de9c8eb0fff3",
}


def requires_material_surface(sources: Mapping[str, bytes]) -> bool:
    """Inspect imports, including guarded/lazy imports, without importing hardware."""
    return any(
        isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[-1] == "material_surface"
        for name, content in sources.items() if name.endswith(".py")
        for node in ast.walk(ast.parse(content, filename=name))
    )


def material_dependencies(sources: dict[str, bytes], source: Callable[[str], bytes]) -> dict[str, str | None]:
    """Add required runtime/preview files with exact absent-or-pinned baselines.

    Historical selected source that has no material-plane imports stays unchanged.
    The pinned preview predecessor is shared by the recorded installed baselines;
    no old Git objects are needed to build a current payload in shallow CI.
    """
    if not requires_material_surface(sources):
        return {}
    for name in MATERIAL_PREDECESSORS:
        sources.setdefault(name, source(name))
    return dict(MATERIAL_PREDECESSORS)
