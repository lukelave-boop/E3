"""The focus companion preserves operator files and applies only a known baseline."""
import hashlib
import importlib.util
import io
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from scripts.package_laser_focus import PREVIOUS, PREVIOUS_REVISION, ROOT, package


def test_packaged_guide_uses_its_own_installer_baseline_and_existing_install_link(tmp_path):
    bundle = package(tmp_path / "dist")
    guide = (bundle / "README.md").read_text(encoding="utf-8")
    assert "install_laser_focus.py" in guide
    assert "install_workpiece_focus.py" not in guide
    assert "install_z_setup_speed.py" not in guide
    assert "package_z_setup_speed.py" not in guide
    assert "[INSTALL.md](INSTALL.md)" in guide
    section = guide.split("## Pi companion installation\n", 1)[1]
    assert PREVIOUS_REVISION in section
    assert "568b1cc9" not in section
    assert "## Setup travel speeds" in guide
    assert "Normal Z moves require the new firmware's exact" in guide
    installation = (bundle / "INSTALL.md").read_text(encoding="utf-8")
    assert str(bundle.resolve()).replace("'", "''") in installation
    assert f"e3_focus=/home/greenhouse-climate/{bundle.name}" in installation
    assert '"$e3_focus/install_laser_focus.py" --project "$e3_project"\n' in installation
    assert '"$e3_focus/install_laser_focus.py" --project "$e3_project" --apply &&\n' in installation
    assert "Cap:E3_Z_SETUP_SPEED_V1:1" in installation


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
    for name in ("pi-hardware.json", "pi-hardware.json.z-limits.json",
                 "pi-hardware.json.laser-focus.json", "pi-hardware.json.laser-focus.json.z-retention.json"):
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


def test_companion_includes_complete_retention_lifecycle_and_new_module_is_absent_only(kit):
    installer, project, bundle = kit
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    entries = {entry["path"]: entry for entry in manifest["files"]}
    required = {"laser_aligner/machine/z_retention.py", "laser_aligner/machine/job_focus.py",
                "laser_aligner/machine/service.py", "laser_aligner/machine/pi_job_service.py",
                "laser_aligner/machine/secondary_controller.py", "laser_aligner/remote_node.py"}
    assert required <= entries.keys()
    assert manifest["predecessor_revision"] == PREVIOUS_REVISION
    assert entries["laser_aligner/machine/z_retention.py"]["previous_sha256_lf"] is None
    assert not (project / "laser_aligner/machine/z_retention.py").exists()
    result = installer.install(project, bundle=bundle, apply=True)
    added = next(entry for entry in result["files"] if entry["path"].endswith("z_retention.py"))
    assert added["status"] == "updated" and "backup" not in added
    assert (project / "laser_aligner/machine/z_retention.py").read_bytes() == (
        bundle / "laser_aligner/machine/z_retention.py").read_bytes()


def test_unknown_existing_retention_module_rejects_before_any_update(kit):
    installer, project, bundle = kit
    original = (project / "laser_aligner/machine/service.py").read_bytes()
    target = project / "laser_aligner/machine/z_retention.py"
    target.write_bytes(b"# operator's existing implementation\n")
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert target.read_bytes() == b"# operator's existing implementation\n"
    assert (project / "laser_aligner/machine/service.py").read_bytes() == original
    assert not list(project.rglob("*.e3-backup-*"))


def test_package_applies_to_exact_main_predecessor_and_imports_without_hardware(tmp_path, monkeypatch):
    if shutil.which("git") is None or subprocess.run(
        ["git", "cat-file", "-e", PREVIOUS_REVISION], cwd=ROOT, capture_output=True,
    ).returncode:
        pytest.skip("The pinned main revision is required for the real predecessor fixture")
    source = subprocess.check_output(["git", "archive", "--format=zip", PREVIOUS_REVISION, "laser_aligner"], cwd=ROOT)
    project = (tmp_path / "pinned-main").resolve()
    project.mkdir()
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        assert all((project / item.filename).resolve().is_relative_to(project) for item in archive.infolist())
        archive.extractall(project)
    for name, digest in PREVIOUS.items():
        path = project / name
        if digest is None:
            assert not path.exists()
        else:
            assert hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == digest
    configuration = project / "config"
    configuration.mkdir()
    for name in ("pi-hardware.json", "pi-hardware.json.laser-focus.json", "pi-hardware.json.z-limits.json"):
        (configuration / name).write_bytes(b"operator data must remain byte-identical\n")
    bundle = package(tmp_path / "dist")
    spec = importlib.util.spec_from_file_location("pinned_focus_installer", bundle / "install_laser_focus.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    monkeypatch.setattr(installer, "inactive", lambda: None)  # Isolated fixture; no real service.
    result = installer.install(project, bundle=bundle, apply=True)
    assert len(result["files"]) == len(PREVIOUS) and not result["service_started"]
    assert all(path.read_bytes() == b"operator data must remain byte-identical\n" for path in configuration.iterdir())
    assert all(entry["status"] == "already_current" for entry in installer.install(project, bundle=bundle)["files"])
    subprocess.run([sys.executable, "-c",
                    "import laser_aligner.machine.service, laser_aligner.machine.z_retention, "
                    "laser_aligner.machine.job_focus, laser_aligner.machine.pi_job_service, laser_aligner.remote_node"],
                   cwd=project, check=True, capture_output=True)


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
