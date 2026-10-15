"""Historical partial kits must not emit an incomplete raster-travel runtime."""
from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
BUILDERS = (
    "cpu_cooling", "z_controls", "thickness_focus", "operation_focus",
    "raster_capacity", "large_job_start", "two_million",
)


def _baseline_bytes(revision: str, name: str) -> bytes:
    # Distinct from the selected source, so substituting new payload hashes for
    # recorded predecessors cannot accidentally satisfy the assertions below.
    return f"# Recorded predecessor {revision}: {name}\n".encode()


def _historical_bytes(builder_name: str, name: str) -> bytes:
    if name.startswith("scripts/"):
        return b"MANIFEST_SHA256 = 'BUILD_REPLACES_MANIFEST_SHA256'\n"
    if name.startswith("docs/"):
        return b"Historical companion instructions.\n"
    if name == "laser_aligner/gcode/limits.py":
        limit = "2_000_000" if builder_name == "two_million" else "1_000_000"
        return f"MAX_STREAM_COMMANDS = {limit}\n".encode()
    return b"# Historical source with no raster-travel dependency.\nCAPABILITY = 'pi-operation-focus-v1'\n"


def _selected_names(builder) -> tuple[str, ...]:
    for key in ("FILES", "PREDECESSOR", "PREVIOUS"):
        value = getattr(builder, key, None)
        if value is not None:
            return tuple(value)
    raise AssertionError("Unknown partial-builder source contract")


def _prepare_builder(builder_name, tmp_path, monkeypatch, *, current_sources):
    builder = importlib.import_module("scripts.package_" + builder_name)
    names = _selected_names(builder)
    sources = {
        name: ((ROOT / name).read_bytes().replace(b"\r\n", b"\n")
               if current_sources else _historical_bytes(builder_name, name))
        for name in names
    }
    if builder_name in {"cpu_cooling", "z_controls"}:
        selected_root = tmp_path / "selected-source"
        for name, data in {
            **sources,
            "scripts/install_cpu_cooling.py": _historical_bytes(builder_name, "scripts/installer.py"),
            "docs/MAINBOARD_Z_CONTROLS.md": _historical_bytes(builder_name, "docs/guide.md"),
        }.items():
            target = selected_root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        monkeypatch.setattr(builder, "ROOT", selected_root)
        arguments = {}
    else:
        def selected_source(revision, name):
            if revision != REVISION:
                assert revision == builder.BASELINE
                return _baseline_bytes(revision, name)
            return sources.get(name, _historical_bytes(builder_name, name))

        def resolve_revision(command, **_kwargs):
            assert command == ["git", "rev-parse", "--verify", "selected^{commit}"]
            return REVISION + "\n"

        monkeypatch.setattr(builder, "source", selected_source)
        monkeypatch.setattr(builder, "subprocess", SimpleNamespace(check_output=resolve_revision))
        arguments = {"revision": "selected"}
        if builder_name in {"thickness_focus", "operation_focus"}:
            arguments["version"] = "test-version"

    def invoke(destination):
        return builder.package(destination, **arguments)

    return builder, sources, invoke


def _expected_predecessors(builder_name, builder):
    if builder_name == "operation_focus":
        return [{"revision": builder.BASELINE_REVISION, "files": dict(builder.PREDECESSOR)}]
    if builder_name == "thickness_focus":
        return [
            {"revision": "installed-workpiece-focus-568b1cc9", "files": dict(builder.PREVIOUS)},
            *({"revision": getattr(builder, key + "_REVISION"), "files": dict(getattr(builder, key))}
              for key in ("INTEGRATED", "THICKNESS", "COOLING", "REJECTION", "HONEYCOMB", "HOME_COOLING")),
        ]
    return [{
        "revision": builder.BASELINE,
        "files": {
            name: (None if builder_name == "raster_capacity" and name.endswith("limits.py")
                   else hashlib.sha256(_baseline_bytes(builder.BASELINE, name)).hexdigest())
            for name in builder.FILES
        },
    }]


@pytest.mark.parametrize("builder_name", BUILDERS)
def test_partial_builder_rejects_current_raster_sources_before_output(builder_name, tmp_path, monkeypatch):
    _builder, sources, invoke = _prepare_builder(
        builder_name, tmp_path, monkeypatch, current_sources=True,
    )
    assert b"raster_safety import" in sources["laser_aligner/machine/service.py"]
    destination = tmp_path / "must-not-exist"

    with pytest.raises(ValueError, match="historical partial companion.*matched full raster-overscan"):
        invoke(destination)

    assert not destination.exists()


@pytest.mark.parametrize("builder_name", BUILDERS)
def test_partial_builder_preserves_historical_payload_and_predecessors(builder_name, tmp_path, monkeypatch):
    builder, sources, invoke = _prepare_builder(
        builder_name, tmp_path, monkeypatch, current_sources=False,
    )
    old_map = dict(builder.PREVIOUS) if builder_name in {"cpu_cooling", "z_controls"} else None
    expected = None if old_map is not None else _expected_predecessors(builder_name, builder)
    destination = tmp_path / "packages"

    package = invoke(destination)

    assert package.parent == destination
    manifest = json.loads((package / "manifest.json").read_text(encoding="utf-8"))
    assert {entry["path"] for entry in manifest["files"]} == set(sources)
    for entry in manifest["files"]:
        data = (package / entry["path"]).read_bytes()
        assert data == sources[entry["path"]]
        assert entry["sha256_lf"] == hashlib.sha256(data).hexdigest()
    if old_map is not None:
        assert {entry["path"]: entry["previous_sha256_lf"] for entry in manifest["files"]} == old_map
        assert builder.PREVIOUS == old_map
    else:
        assert manifest["predecessors"] == expected
        assert manifest["revision"] == REVISION
    assert not (package / "laser_aligner/raster_safety.py").exists()
