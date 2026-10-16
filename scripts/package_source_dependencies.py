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
COMMAND_STREAMING_FILES = (
    "laser_aligner/machine/grbl_stream.py",
    "laser_aligner/machine/controller_receiver.py",
    "laser_aligner/machine/service.py",
)


def requires_command_streaming(sources: Mapping[str, bytes]) -> bool:
    """Detect the coupled streaming dependency without importing the runtime."""
    return any(
        (isinstance(node, ast.ImportFrom)
         and ((node.module or "").split(".")[-1] == "grbl_stream"
              or any(alias.name == "grbl_stream" for alias in node.names)))
        or (isinstance(node, ast.Import)
            and any(alias.name.split(".")[-1] == "grbl_stream" for alias in node.names))
        for name, content in sources.items() if name.endswith(".py")
        for node in ast.walk(ast.parse(content, filename=name))
    )


def reject_partial_streaming_sources(sources: Mapping[str, bytes]) -> None:
    """Old baseline manifests cannot silently acquire the new receiver protocol."""
    if requires_command_streaming(sources):
        raise ValueError(
            "This historical partial companion cannot package command-streaming sources; "
            "use scripts/package_command_streaming.py with the recorded complete Pi baseline"
        )


def require_command_streaming_sources(sources: Mapping[str, bytes]) -> None:
    """Require the complete, matching source API before emitting a streaming kit."""
    if set(sources) != set(COMMAND_STREAMING_FILES) or not requires_command_streaming(sources):
        raise ValueError("Command-streaming companion requires the complete matching three-file payload")
    expected = {
        COMMAND_STREAMING_FILES[0]: {"GrblStreamCompactor", "canonical_linear_move", "parse_stream_window"},
        COMMAND_STREAMING_FILES[1]: {"begin_stream", "stream_write", "stream_read_ack", "finish_stream"},
    }
    for name, names in expected.items():
        definitions = {
            node.name for node in ast.walk(ast.parse(sources[name], filename=name))
            if isinstance(node, (ast.ClassDef, ast.FunctionDef))
        }
        if not names <= definitions:
            raise ValueError(f"Command-streaming companion has an incompatible source API: {name}")


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
    reject_partial_streaming_sources(sources)
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
    reject_partial_streaming_sources(sources)
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
    reject_partial_streaming_sources(sources)
