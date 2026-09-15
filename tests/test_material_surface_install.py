"""Exact material-plane Pi bundles preserve installed source and operator data."""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts import package_material_surface as packager


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    # Unit-test payload comes from an explicit snapshot. Production package()
    # always calls git show on one resolved commit; no working-tree fallback.
    names = (*packager.SOURCE_FILES, "scripts/install_workpiece_focus.py", "docs/MATERIAL_HEIGHT.md",
             "docs/PRECISION_PLACEMENT.md")
    source_snapshot = {name: (packager.ROOT / name).read_bytes().replace(b"\r\n", b"\n") for name in names}
    calls = []
    def source(revision, name):
        calls.append((revision, name))
        return source_snapshot[name]
    monkeypatch.setattr(packager, "source", source)
    folder = packager.package(tmp_path / "dist", "HEAD", "0.7.999-test")
    spec = importlib.util.spec_from_file_location("material_surface_installer", folder / "install_material_surface.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    monkeypatch.setattr(installer, "inactive", lambda: None)
    return folder, installer, calls


@pytest.fixture(params=list(packager.PREDECESSORS))
def kit(tmp_path, bundle, request):
    folder, installer, _ = bundle
    manifest = json.loads((folder / "manifest.json").read_bytes())
    originals = {}
    for index, previous in enumerate(manifest["predecessors"]):
        contents = {}
        for name, digest in previous["files"].items():
            data = None if digest is None else f"# predecessor-{index}: {name}\r\n".encode()
            contents[name] = data
            previous["files"][name] = None if data is None else hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
        originals[previous["revision"]] = contents
    encoded = json.dumps(manifest).encode()
    (folder / "manifest.json").write_bytes(encoded)
    installer.MANIFEST_SHA256 = hashlib.sha256(encoded).hexdigest()
    project = tmp_path / "project"
    for name, content in originals[request.param].items():
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if content is not None:
            path.write_bytes(content)
    for name in ("config.json", "focus.json", "honeycomb.json", "xy-offset.json", "saved-z.json", "cooling.conf"):
        (project / name).write_bytes(b"preserve operator data\r\n")
    return folder, installer, project, originals, request.param


def test_complete_pinned_manifest_and_exact_committed_source_contract(bundle):
    folder, _, calls = bundle
    manifest = json.loads((folder / "manifest.json").read_bytes())
    assert manifest["required_capability"] == "pi-material-surface-v1"
    assert manifest["compatible_windows_version"] == "0.7.999-test"
    assert len(manifest["revision"]) == 40
    assert {revision for revision, _ in calls} == {manifest["revision"]}
    assert {entry["path"] for entry in manifest["files"]} == set(packager.SOURCE_FILES)
    assert len(packager.SOURCE_FILES) == 19
    assert all(set(previous["files"]) == set(packager.SOURCE_FILES) for previous in manifest["predecessors"])
    assert {previous["revision"]: previous["files"] for previous in manifest["predecessors"]} == packager.PREDECESSORS
    assert all(previous["files"][packager.SURFACE_MODULE] is None for previous in manifest["predecessors"])
    for entry in manifest["files"]:
        assert hashlib.sha256((folder / entry["path"]).read_bytes()).hexdigest() == entry["sha256_lf"]
    assert "--apply &&" in (folder / "INSTALL.md").read_text()
    assert "does not survive" in (folder / "INSTALL.md").read_text()
    assert "[precision placement workflow](PRECISION_PLACEMENT.md)" in (folder / "INSTALL.md").read_text()
    assert (folder / "PRECISION_PLACEMENT.md").read_bytes() == (packager.ROOT / "docs/PRECISION_PLACEMENT.md").read_bytes().replace(b"\r\n", b"\n")


def test_upgrade_all_predecessors_preserves_data_backups_and_current_sources(kit):
    folder, installer, project, originals, baseline = kit
    before = snapshot(project)
    assert installer.install(project, bundle=folder)["applied"] is False
    assert snapshot(project) == before
    result = installer.install(project, bundle=folder, apply=True)
    assert result["compatible_predecessors"] == [baseline]
    assert result["applied"] is True and result["service_started"] is False
    for entry in result["files"]:
        target = Path(entry["path"])
        name = target.relative_to(project).as_posix()
        assert target.read_bytes().replace(b"\r\n", b"\n") == (folder / name).read_bytes()
        if originals[baseline][name] is not None:
            assert Path(entry["backup"]).read_bytes() == originals[baseline][name]
        else:
            assert "backup" not in entry
    assert all((project / name).read_bytes() == content for name, content in before.items() if not name.startswith("laser_aligner/"))
    assert all(entry["status"] == "already_current" for entry in installer.install(project, bundle=folder)["files"])


@pytest.mark.parametrize("relative", [packager.SURFACE_MODULE, packager.PREVIEW_MODULE, "laser_aligner/machine/laser_focus.py"])
def test_unknown_new_module_or_existing_edits_reject_before_any_write(kit, relative):
    folder, installer, project, _, _ = kit
    (project / relative).write_bytes(b"# unknown operator source\n")
    before = snapshot(project)
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=folder, apply=True)
    assert snapshot(project) == before


def test_retry_current_files_and_reject_active_service(kit, monkeypatch):
    folder, installer, project, _, _ = kit
    (project / packager.SURFACE_MODULE).write_bytes((folder / packager.SURFACE_MODULE).read_bytes())
    assert next(entry for entry in installer.install(project, bundle=folder)["files"]
                if entry["path"].endswith("material_surface.py"))["status"] == "already_current"
    before = snapshot(project)
    def active():
        raise ValueError("service active")
    monkeypatch.setattr(installer, "inactive", active)
    with pytest.raises(ValueError, match="service active"):
        installer.install(project, bundle=folder, apply=True)
    assert snapshot(project) == before


def test_exact_installed_stop_recovery_predecessor_upgrades_and_imports(tmp_path, bundle):
    folder, installer, _ = bundle
    revision = packager.STOP_RECOVERY_REVISION
    result = subprocess.run(["git", "cat-file", "-e", revision], cwd=packager.ROOT, capture_output=True)
    if result.returncode:
        pytest.skip("Pinned installed STOP-recovery revision is unavailable in this shallow checkout")
    archive_bytes = subprocess.check_output(["git", "archive", "--format=zip", revision, "laser_aligner"], cwd=packager.ROOT)
    project = tmp_path / "installed-stop-recovery"
    project.mkdir()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert all((project / member.filename).resolve().is_relative_to(project.resolve()) for member in archive.infolist())
        archive.extractall(project)
    for name, expected in packager.PREDECESSORS[revision].items():
        path = project / name
        assert (hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() if path.exists() else None) == expected
    before = snapshot(project)
    assert revision in installer.install(project, bundle=folder)["compatible_predecessors"]
    assert snapshot(project) == before
    installer.install(project, bundle=folder, apply=True)
    subprocess.run(
        [sys.executable, "-c", "from laser_aligner.machine.pi_machine_server import SERVER_CAPABILITIES; "
         "from laser_aligner.machine.material_surface import CAPABILITY; assert CAPABILITY in SERVER_CAPABILITIES; "
         "from laser_aligner.machine.service import MachineService; from laser_aligner.machine.remote_service import RemoteMachineService"],
        cwd=project, check=True, capture_output=True,
    )
