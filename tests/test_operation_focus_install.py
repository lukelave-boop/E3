"""Exact predecessor acceptance and unknown-edit rejection for the Pi update."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from scripts import package_operation_focus as packager


@pytest.fixture
def kit(tmp_path, monkeypatch):
    # Package current source before its feature commit; baseline hashes stay pinned.
    monkeypatch.setattr(packager, "source", lambda revision, name: (packager.ROOT / name).read_bytes().replace(b"\r\n", b"\n"))
    bundle = packager.package(tmp_path / "dist", "HEAD", "0.7.181")
    spec = importlib.util.spec_from_file_location("operation_installer", bundle / "install_operation_focus.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    monkeypatch.setattr(installer, "inactive", lambda: None)
    project = tmp_path / "project"
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    # Use the real pinned predecessor, including the new module being absent.
    import subprocess
    for name, digest in packager.PREDECESSOR.items():
        if digest is None:
            continue
        content = subprocess.check_output(["git", "show", f"{packager.BASELINE_REVISION}:{name}"], cwd=packager.ROOT)
        assert hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest() == digest
        target = project / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return installer, project, bundle, manifest


def test_exact_upgrade_preserves_data_and_is_idempotent(kit):
    installer, project, bundle, manifest = kit
    saved = project / "operator.json"
    saved.write_bytes(b"preserve settings")
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    preview = installer.install(project, bundle=bundle)
    assert not preview["applied"]
    assert before == {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    result = installer.install(project, bundle=bundle, apply=True)
    assert result["applied"] and not result["service_started"]
    for entry in result["files"]:
        if "backup" in entry:
            assert Path(entry["backup"]).read_bytes() == before[Path(entry["path"])]
    for entry in manifest["files"]:
        assert hashlib.sha256((project / entry["path"]).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == entry["sha256_lf"]
    assert saved.read_bytes() == b"preserve settings"
    assert all(row["status"] == "already_current" for row in installer.install(project, bundle=bundle)["files"])


@pytest.mark.parametrize("path", ["laser_aligner/machine/service.py", "laser_aligner/operation_focus.py"])
def test_unknown_existing_or_new_module_is_preserved(kit, path):
    installer, project, bundle, _ = kit
    target = project / path
    target.write_bytes(b"# unknown local edit\n")
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert before == {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
