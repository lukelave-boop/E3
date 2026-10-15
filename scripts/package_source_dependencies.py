"""Pinned companion dependencies selected from the actual packaged sources."""
from __future__ import annotations

import ast
from collections.abc import Callable, Mapping

SURFACE_MODULE = "laser_aligner/machine/material_surface.py"
PREVIEW_MODULE = "laser_aligner/gcode/preview.py"
RUNTIME_PREDECESSORS = {
    "laser_aligner/raster_safety.py": None,
    "laser_aligner/gcode/limits.py": None,
    "laser_aligner/operation_focus.py": None,
}
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

    Historical selected source without these newer imports stays unchanged.
    The pinned preview predecessor is shared by the recorded installed baselines;
    no old Git objects are needed to build a current payload in shallow CI.
    """
    dependencies = {}
    if requires_material_surface(sources):
        for name in MATERIAL_PREDECESSORS:
            sources.setdefault(name, source(name))
        dependencies.update(MATERIAL_PREDECESSORS)
    dependencies.update(runtime_dependencies(sources, source))
    return dependencies


def runtime_dependencies(sources: dict[str, bytes], source: Callable[[str], bytes]) -> dict[str, None]:
    """Include new shared modules only when the selected payload imports them.

    Every recorded predecessor predates these modules. Existing unknown files
    therefore remain rejected by the installer's absent-only predecessor guard.
    Historical selected source does not try to read modules it did not import.
    """
    imports = {
        node.module or ""
        for name, content in sources.items() if name.endswith(".py")
        for node in ast.walk(ast.parse(content, filename=name))
        if isinstance(node, ast.ImportFrom)
    }
    dependencies = {}
    for name in RUNTIME_PREDECESSORS:
        module = name.removesuffix(".py").replace("/", ".")
        if module in imports or module.removeprefix("laser_aligner.") in imports:
            if name not in sources:
                sources[name] = source(name)
                dependencies[name] = None
    return dependencies


def reject_partial_raster_sources(sources: Mapping[str, bytes]) -> None:
    """Historical partial updates cannot deploy the coupled raster protocol.

    Keep their historical payload and predecessor contracts intact; reject
    newer selected sources before writing an incomplete companion.
    """
    dependent = sorted(
        name for name, content in sources.items() if name.endswith(".py")
        if any(
            isinstance(node, ast.ImportFrom)
            and (node.module or "").split(".")[-1] == "raster_safety"
            for node in ast.walk(ast.parse(content, filename=name))
        )
    )
    if dependent:
        raise ValueError(
            "This historical partial companion cannot package raster-travel sources "
            f"({', '.join(dependent)}); use a matched full raster-overscan "
            "companion/source update instead"
        )
