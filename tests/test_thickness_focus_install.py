"""Exact predecessor upgrade and unknown-edit rejection for the Pi companion."""
import hashlib
import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

from scripts.package_thickness_focus import (
    COOLING_REVISION,
    INTEGRATED_REVISION,
    REJECTION_REVISION,
    THICKNESS_REVISION,
    package,
    source,
)


@pytest.fixture(params=["synthetic", "integrated", "thickness", "cooling", "rejection"])
def kit(tmp_path, monkeypatch, request):
    bundle = package(tmp_path / "dist", "HEAD", "0.7.133")
    spec = importlib.util.spec_from_file_location("thickness_installer", bundle / "install_thickness_focus.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    monkeypatch.setattr(installer, "inactive", lambda: None)  # No physical service involved.
    project = tmp_path / "project"
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    for entry in manifest["files"]:
        path = project / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        if request.param in {"integrated", "thickness", "cooling", "rejection"}:
            try:
                revision = {"integrated": INTEGRATED_REVISION, "thickness": THICKNESS_REVISION,
                            "cooling": COOLING_REVISION, "rejection": REJECTION_REVISION}[request.param]
                content = source(revision, entry["path"])
            except subprocess.CalledProcessError:
                pytest.skip("Historical integrated Git source is unavailable in this shallow checkout")
        else:
            content = f"# synthetic predecessor: {entry['path']}\n".encode()
            manifest["predecessors"][1]["files"][entry["path"]] = hashlib.sha256(content).hexdigest()
        path.write_bytes(content)
    if request.param == "synthetic":
        manifest_bytes = json.dumps(manifest).encode()
        (bundle / "manifest.json").write_bytes(manifest_bytes)
        installer.MANIFEST_SHA256 = hashlib.sha256(manifest_bytes).hexdigest()
    (project / "operator.json").write_bytes(b"preserve operator data\r\n")
    return installer, project, bundle


def test_exact_integrated_upgrade_preserves_operator_data_and_source_backups(kit):
    installer, project, bundle = kit
    preview = installer.install(project, bundle=bundle)
    assert not preview["applied"]
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    result = installer.install(project, bundle=bundle, apply=True)
    assert result["applied"] and not result["service_started"]
    for entry in result["files"]:
        target = Path(entry["path"])
        assert target.read_bytes() == (bundle / target.relative_to(project)).read_bytes()
        if "backup" in entry:
            assert Path(entry["backup"]).read_bytes() == before[target]
    assert (project / "operator.json").read_bytes() == b"preserve operator data\r\n"
    assert all(e["status"] == "already_current" for e in installer.install(project, bundle=bundle)["files"])


def test_unknown_local_edit_rejects_before_any_write(kit):
    installer, project, bundle = kit
    changed = project / "laser_aligner/machine/laser_focus.py"
    changed.write_bytes(changed.read_bytes() + b"\n# unknown operator edit\n")
    before = {p: p.read_bytes() for p in project.rglob("*") if p.is_file()}
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert {p: p.read_bytes() for p in project.rglob("*") if p.is_file()} == before
