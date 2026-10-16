from __future__ import annotations

import hashlib
import importlib.util
import json

import pytest

from scripts import package_job_pause as packager


def test_pause_bundle_contains_complete_exact_sources_without_git(tmp_path, monkeypatch):
    monkeypatch.setattr(packager.subprocess, "check_output", lambda *a, **kw: pytest.fail("Unexpected Git/network call"))
    bundle = packager.package(tmp_path / "dist")
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert len(manifest["files"]) == 11
    assert {entry["path"] for entry in manifest["files"]} == packager.PREVIOUS.keys()
    for entry in manifest["files"]:
        raw = (bundle / entry["path"]).read_bytes()
        assert raw == (packager.ROOT / entry["path"]).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(raw).hexdigest() == entry["sha256_lf"]
    assert b"$32=1" in (bundle / "TESTING.md").read_bytes()
    assert "inactive" in (bundle / "INSTALL.md").read_text()


def test_pause_installer_rejects_unknown_and_preserves_backups(tmp_path, monkeypatch):
    bundle = packager.package(tmp_path / "dist")
    spec = importlib.util.spec_from_file_location("pause_installer", bundle / "install_job_pause.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    project = tmp_path / "project"
    originals = {}
    for name, old_hash in packager.PREVIOUS.items():
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        if old_hash is not None:
            raw = f"# synthetic predecessor {name}\r\n".encode()
            originals[name] = raw
            target.write_bytes(raw)
            manifest["predecessors"][0]["files"][name] = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
    encoded = json.dumps(manifest).encode()
    (bundle / "manifest.json").write_bytes(encoded)
    installer.MANIFEST_SHA256 = hashlib.sha256(encoded).hexdigest()
    monkeypatch.setattr(installer, "inactive", lambda: None)
    bad = project / "laser_aligner/machine/service.py"
    bad.write_bytes(b"# unknown edits\n")
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert (project / "laser_aligner/machine/job_pause.py").read_bytes() == originals["laser_aligner/machine/job_pause.py"]
    bad.write_bytes(originals["laser_aligner/machine/service.py"])
    assert not installer.install(project, bundle=bundle)["applied"]
    result = installer.install(project, bundle=bundle, apply=True)
    for item in result["files"]:
        if "backup" in item:
            from pathlib import Path
            source = Path(item["path"]).relative_to(project).as_posix()
            assert Path(item["backup"]).read_bytes() == originals[source]
    assert all(item["status"] == "already_current" for item in installer.install(project, bundle=bundle, apply=True)["files"])
