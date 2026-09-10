import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from scripts.package_cpu_cooling import package


@pytest.fixture
def kit(tmp_path, monkeypatch):
    bundle = package(tmp_path / "dist")
    spec = importlib.util.spec_from_file_location("cooling_installer", bundle / "install_cpu_cooling.py")
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


def test_apply_adds_only_expected_files_and_keeps_backup(kit):
    installer, project, bundle = kit
    result = installer.install(project, bundle=bundle, apply=True)
    assert len(result["files"]) == 4
    assert not result["service_started"]
    for entry in result["files"]:
        assert entry["status"] == "updated"
        if "backup" in entry:
            assert Path(entry["backup"]).read_bytes() == b"# known previous source\n"
    again = installer.install(project, bundle=bundle, apply=True)
    assert all(e["status"] == "already_current" for e in again["files"])


def test_dry_run_does_not_write(kit, monkeypatch):
    installer, project, bundle = kit
    monkeypatch.setattr(installer, "inactive", lambda: pytest.fail("service checked"))
    result = installer.install(project, bundle=bundle)
    assert not result["applied"]
    assert not (project / "laser_aligner/cpu_cooling.py").exists()


def test_unknown_edit_validated_before_any_replacement(kit):
    installer, project, bundle = kit
    (project / "laser_aligner/machine/service.py").write_text("# operator change")
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert (project / "laser_aligner/remote_node.py").read_bytes() == b"# known previous source\n"


def test_source_tamper_prevents_install(kit):
    installer, project, bundle = kit
    (bundle / "laser_aligner/cpu_cooling.py").write_text("# tampered")
    with pytest.raises(ValueError, match="checksum"):
        installer.install(project, bundle=bundle, apply=True)
    assert not (project / "laser_aligner/cpu_cooling.py").exists()


def test_active_service_prevents_all_writes(kit, monkeypatch):
    installer, project, bundle = kit
    def fail():
        raise ValueError("service active")
    monkeypatch.setattr(installer, "inactive", fail)
    with pytest.raises(ValueError, match="service active"):
        installer.install(project, bundle=bundle, apply=True)
    assert not (project / "laser_aligner/cpu_cooling.py").exists()


def test_crlf_preserved(kit):
    installer, project, bundle = kit
    target = project / "laser_aligner/remote_node.py"
    target.write_bytes(b"# known previous source\r\n")
    installer.install(project, bundle=bundle, apply=True)
    b = target.read_bytes()
    assert b.count(b"\r\n") == b.count(b"\n")
