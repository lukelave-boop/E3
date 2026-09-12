"""The focus companion preserves operator files and applies only a known baseline."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from scripts.package_laser_focus import PREVIOUS, package


@pytest.fixture
def kit(tmp_path, monkeypatch):
    bundle = package(tmp_path / "dist")
    spec = importlib.util.spec_from_file_location("focus_installer", bundle / "install_laser_focus.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    project = tmp_path / "project"
    (project / "laser_aligner/machine").mkdir(parents=True)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    for entry in manifest["files"]:
        if entry["previous_sha256_lf"]:
            original = b"# previous installed source\r\n"
            entry["previous_sha256_lf"] = hashlib.sha256(original.replace(b"\r\n", b"\n")).hexdigest()
            (project / entry["path"]).write_bytes(original)
    data = json.dumps(manifest).encode()
    (bundle / "manifest.json").write_bytes(data)
    installer.MANIFEST_SHA256 = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(installer, "inactive", lambda: None)
    return installer, project, bundle


def test_upgrade_preserves_configuration_and_cooling_and_is_idempotent(kit):
    installer, project, bundle = kit
    config = project / "config"
    config.mkdir()
    for name in ("pi-hardware.json", "pi-hardware.json.z-limits.json"):
        (config / name).write_bytes(b"operator settings\n")
    cooler = project / "laser_aligner/machine/cpu_cooling.py"
    cooler.write_bytes(b"existing temperature control\n")
    result = installer.install(project, bundle=bundle, apply=True)
    assert not result["service_started"]
    assert len(result["files"]) == len(PREVIOUS)
    assert cooler.read_bytes() == b"existing temperature control\n"
    assert all(path.read_bytes() == b"operator settings\n" for path in config.iterdir())
    for entry in result["files"]:
        if "backup" in entry:
            assert Path(entry["backup"]).read_bytes() == b"# previous installed source\r\n"
            content = Path(entry["path"]).read_bytes()
            assert content.count(b"\r\n") == content.count(b"\n")
    again = installer.install(project, bundle=bundle, apply=True)
    assert all(e["status"] == "already_current" for e in again["files"])


def test_unknown_local_edit_rejects_before_any_file_is_replaced(kit):
    installer, project, bundle = kit
    original = (project / "laser_aligner/config.py").read_bytes()
    (project / "laser_aligner/machine/service.py").write_text("operator changes")
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert (project / "laser_aligner/config.py").read_bytes() == original
    assert (project / "laser_aligner/machine/laser_focus.py").read_bytes() == b"# previous installed source\r\n"


def test_dry_run_and_active_service_leave_sources_untouched(kit, monkeypatch):
    installer, project, bundle = kit
    assert not installer.install(project, bundle=bundle)["applied"]
    def reject():
        raise ValueError("service active")
    monkeypatch.setattr(installer, "inactive", reject)
    with pytest.raises(ValueError, match="service active"):
        installer.install(project, bundle=bundle, apply=True)
    assert (project / "laser_aligner/machine/laser_focus.py").read_bytes() == b"# previous installed source\r\n"


@pytest.mark.parametrize("relative", ["manifest.json", "laser_aligner/machine/laser_focus.py"])
def test_tampered_bundle_is_rejected(kit, relative):
    installer, project, bundle = kit
    with (bundle / relative).open("ab") as stream:
        stream.write(b" altered")
    with pytest.raises(ValueError, match="checksum"):
        installer.install(project, bundle=bundle, apply=True)
    assert (project / "laser_aligner/machine/laser_focus.py").read_bytes() == b"# previous installed source\r\n"
