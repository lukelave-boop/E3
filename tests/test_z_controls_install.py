"""Companion packaging uses the established atomic, hash-checked Pi installer."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from scripts.package_z_controls import PREVIOUS, package


@pytest.fixture
def kit(tmp_path, monkeypatch):
    bundle = package(tmp_path / "dist")
    spec = importlib.util.spec_from_file_location("z_installer", bundle / "install_z_controls.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    project = tmp_path / "project"
    (project / "laser_aligner/machine").mkdir(parents=True)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    for entry in manifest["files"]:
        if entry["previous_sha256_lf"]:
            old = b"# known previous source\n"
            entry["previous_sha256_lf"] = hashlib.sha256(old).hexdigest()
            (project / entry["path"]).write_bytes(old)
    data = json.dumps(manifest).encode()
    (bundle / "manifest.json").write_bytes(data)
    installer.MANIFEST_SHA256 = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(installer, "inactive", lambda: None)
    return installer, project, bundle


def test_companion_preserves_existing_probe_and_cooling(kit):
    installer, project, bundle = kit
    retained = project / "laser_aligner/machine/z_probe.py"
    retained.write_bytes(b"# existing compact probe patch\n")
    assert "laser_aligner/machine/z_probe.py" not in PREVIOUS
    assert "laser_aligner/machine/cpu_cooling.py" not in PREVIOUS
    result = installer.install(project, bundle=bundle, apply=True)
    assert len(result["files"]) == len(PREVIOUS)
    assert not result["service_started"]
    assert retained.read_bytes() == b"# existing compact probe patch\n"
    assert all(entry["status"] == "updated" for entry in result["files"])
    for entry in result["files"]:
        if "backup" in entry:
            assert Path(entry["backup"]).read_bytes() == b"# known previous source\n"
    again = installer.install(project, bundle=bundle, apply=True)
    assert all(e["status"] == "already_current" for e in again["files"])


def test_unknown_source_blocks_every_replacement(kit):
    installer, project, bundle = kit
    original = (project / "laser_aligner/config.py").read_bytes()
    (project / "laser_aligner/machine/service.py").write_text("# operator changes")
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert (project / "laser_aligner/config.py").read_bytes() == original
    assert not (project / "laser_aligner/machine/z_limits.py").exists()


def test_dry_run_and_active_service_cannot_install(kit, monkeypatch):
    installer, project, bundle = kit
    assert not installer.install(project, bundle=bundle)["applied"]
    assert not (project / "laser_aligner/machine/z_limits.py").exists()
    def fail():
        raise ValueError("service active")
    monkeypatch.setattr(installer, "inactive", fail)
    with pytest.raises(ValueError, match="service active"):
        installer.install(project, bundle=bundle, apply=True)
    assert not (project / "laser_aligner/machine/z_limits.py").exists()


def test_tampered_source_cannot_install(kit):
    installer, project, bundle = kit
    (bundle / "laser_aligner/machine/z_limits.py").write_text("# tampered")
    with pytest.raises(ValueError, match="checksum"):
        installer.install(project, bundle=bundle, apply=True)
    assert not (project / "laser_aligner/machine/z_limits.py").exists()
