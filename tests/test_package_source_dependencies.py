"""Sparse companion dependency selection never guesses historical source bytes."""
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.package_source_dependencies import (
    COMMAND_STREAMING_FILES,
    RUNTIME_PREDECESSORS,
    material_dependencies,
    reject_partial_raster_sources,
    require_command_streaming_sources,
    runtime_dependencies,
)


def test_historical_source_without_new_imports_does_not_read_new_modules():
    sources = {"laser_aligner/machine/service.py": b"from ..errors import SafetyError\n"}
    original = dict(sources)

    def unexpected_read(name):
        pytest.fail(f"Historical source must not request {name}")

    assert runtime_dependencies(sources, unexpected_read) == {}
    assert sources == original


def test_current_shared_dependencies_are_complete_and_absent_only():
    sources = {"laser_aligner/machine/service.py": (
        b"from ..gcode.limits import MAX_STREAM_COMMANDS\n"
        b"from ..operation_focus import PREFIX\n"
        b"def lazy():\n    from ..raster_safety import RasterTravelEnvelope\n"
    )}
    payloads = {name: f"# exact selected source: {name}\n".encode() for name in RUNTIME_PREDECESSORS}
    reads = []

    def source(name):
        reads.append(name)
        return payloads[name]

    assert runtime_dependencies(sources, source) == RUNTIME_PREDECESSORS
    assert all(digest is None for digest in RUNTIME_PREDECESSORS.values())
    assert reads == list(RUNTIME_PREDECESSORS)
    assert all(sources[name] == payload for name, payload in payloads.items())


def test_missing_selected_dependency_fails_instead_of_falling_back():
    sources = {"laser_aligner/config.py": b"from .raster_safety import RasterTravelEnvelope\n"}

    def missing(name):
        raise FileNotFoundError(name)

    with pytest.raises(FileNotFoundError, match="raster_safety"):
        runtime_dependencies(sources, missing)


def test_existing_payload_module_preserves_its_predecessor_contract():
    sources = {
        "laser_aligner/machine/service.py": b"from ..operation_focus import PREFIX\n",
        "laser_aligner/operation_focus.py": b"# already covered by the caller's pinned manifest\n",
    }
    assert runtime_dependencies(sources, lambda name: pytest.fail(name)) == {}


@pytest.mark.parametrize("dependency_selector", [runtime_dependencies, material_dependencies])
@pytest.mark.parametrize("statement", [
    b"from .grbl_stream import GrblStreamCompactor\n",
    b"from laser_aligner.machine.grbl_stream import GrblStreamCompactor\n",
    b"def stream():\n    from .grbl_stream import GrblStreamCompactor\n",
    b"from . import grbl_stream\n",
    b"import laser_aligner.machine.grbl_stream\n",
])
def test_old_baseline_selectors_reject_streaming_before_reading_or_mutating_payload(dependency_selector, statement):
    sources = {"laser_aligner/machine/service.py": statement}
    original = dict(sources)
    with pytest.raises(ValueError, match="historical partial companion.*package_command_streaming"):
        dependency_selector(sources, lambda name: pytest.fail(f"Unexpected dependency read: {name}"))
    assert sources == original


def test_old_partial_guard_also_rejects_streaming_without_raster_import():
    with pytest.raises(ValueError, match="command-streaming"):
        reject_partial_raster_sources({"laser_aligner/machine/service.py": b"from .grbl_stream import GrblStreamCompactor\n"})


@pytest.mark.parametrize("builder_name", ["laser_focus", "workpiece_focus", "z_setup_speed", "pi_control_priority"])
def test_historical_builders_reject_current_streaming_sources_before_output(tmp_path, builder_name):
    builder = importlib.import_module("scripts.package_" + builder_name)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="command-streaming.*package_command_streaming"):
        builder.package(destination)
    assert not destination.exists()


def test_material_builder_rejects_current_streaming_source_selection(tmp_path, monkeypatch):
    from scripts import package_material_surface as builder

    monkeypatch.setattr(builder, "source", lambda revision, name: (builder.ROOT / name).read_bytes())
    monkeypatch.setattr(builder, "subprocess", SimpleNamespace(check_output=lambda *a, **kw: "a" * 40 + "\n"))
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="command-streaming.*package_command_streaming"):
        builder.package(destination, "selected", "test-version")
    assert not destination.exists()


@pytest.mark.parametrize("missing", COMMAND_STREAMING_FILES)
def test_streaming_payload_requires_all_three_modules(missing):
    root = Path(__file__).resolve().parents[1]
    sources = {name: (root / name).read_bytes() for name in COMMAND_STREAMING_FILES if name != missing}
    with pytest.raises(ValueError, match="complete matching three-file"):
        require_command_streaming_sources(sources)


@pytest.mark.parametrize("old_module", COMMAND_STREAMING_FILES[:2])
def test_streaming_payload_rejects_receiver_or_codec_without_the_required_api(old_module):
    root = Path(__file__).resolve().parents[1]
    sources = {name: (root / name).read_bytes() for name in COMMAND_STREAMING_FILES}
    sources[old_module] = b"# predecessor module without streaming API\n"
    with pytest.raises(ValueError, match="incompatible source API"):
        require_command_streaming_sources(sources)
