import hashlib
import importlib.util
from pathlib import Path

import pytest

from scripts import package_compact_probe_fix
from scripts.package_compact_probe_fix import ROOT, package


@pytest.fixture
def kit(tmp_path, monkeypatch):
    historical_root = tmp_path / "historical-source"
    source = historical_root / "laser_aligner/machine/z_probe.py"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"# independent historical probe implementation\n")
    template = historical_root / "firmware/marlin_mainboard_compact/install_pi_support.py"
    template.parent.mkdir(parents=True)
    template.write_bytes((ROOT / template.relative_to(historical_root)).read_bytes())
    monkeypatch.setattr(package_compact_probe_fix, "ROOT", historical_root)
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
        source.read_bytes()
    ).hexdigest()
    target.write_bytes(old)
    return module, project, target, old, path


def test_current_probe_source_requires_complete_setup_speed_companion(tmp_path):
    with pytest.raises(ValueError, match="use scripts/package_z_setup_speed.py"):
        package(tmp_path / "dist")
    assert not (tmp_path / "dist").exists()


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
