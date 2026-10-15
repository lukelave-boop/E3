"""Sparse companion dependency selection never guesses historical source bytes."""
import pytest

from scripts.package_source_dependencies import RUNTIME_PREDECESSORS, runtime_dependencies


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
