import hashlib
import importlib.util
from pathlib import Path

import pytest

from scripts.package_compact_probe_fix import ROOT, package


@pytest.fixture
def kit(tmp_path):
    path = package(tmp_path / "dist")
    spec = importlib.util.spec_from_file_location("probe_fix_installer", path / "install_probe_fix.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    project = tmp_path / "project"
    target = project / "laser_aligner/machine/z_probe.py"
    target.parent.mkdir(parents=True)
    old = b"# previous operator source fixture\n"
    module.ACCEPTED_SOURCE_SHA256[hashlib.sha256(old).hexdigest()] = "test fixture"
    assert module.DESIRED_SHA256 == hashlib.sha256(
        (ROOT / "laser_aligner/machine/z_probe.py").read_bytes().replace(b"\r\n", b"\n")
    ).hexdigest()
    target.write_bytes(old)
    return module, project, target, old, path


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_pinned_patch_preserves_backup_newlines_and_is_idempotent(kit, monkeypatch, newline):
    module, project, target, old, path = kit
    original = old.replace(b"\n", newline)
    target.write_bytes(original)
    monkeypatch.setattr(module, "service_inactive", lambda: None)
    result = module.install_support(project, apply=True)
    assert result["status"] == "updated"
    assert Path(result["backup"]).read_bytes() == original
    assert target.read_bytes() == (path / "z_probe.py").read_bytes().replace(b"\n", newline)
    assert module.install_support(project, apply=True)["status"] == "already_current"


def test_check_only_does_not_write_or_require_service(kit, monkeypatch):
    module, project, target, old, _ = kit
    monkeypatch.setattr(module, "service_inactive", lambda: pytest.fail("queried service"))
    assert module.install_support(project)["status"] == "ready_to_apply"
    assert target.read_bytes() == old


def test_unknown_local_changes_are_preserved(kit):
    module, project, target, old, _ = kit
    target.write_bytes(old + b"# local change\n")
    with pytest.raises(module.SupportError, match="unknown local edits"):
        module.install_support(project, apply=True)
    assert target.read_bytes() == old + b"# local change\n"


def test_active_service_prevents_patch(kit, monkeypatch):
    module, project, target, old, _ = kit
    def reject():
        raise module.SupportError("service active")
    monkeypatch.setattr(module, "service_inactive", reject)
    with pytest.raises(module.SupportError, match="service active"):
        module.install_support(project, apply=True)
    assert target.read_bytes() == old


def test_corrupted_bundle_is_rejected(kit):
    module, project, target, old, path = kit
    (path / "z_probe.py").write_bytes(b"bad source")
    with pytest.raises(module.SupportError, match="pinned SHA-256"):
        module.install_support(project, apply=True)
    assert target.read_bytes() == old
