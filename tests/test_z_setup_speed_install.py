"""Faster setup installs only over the recorded companion and preserves data."""
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

from scripts.package_laser_focus import PREVIOUS_REVISION as BASE_REVISION
from scripts.package_z_setup_speed import (
    DESKTOP_REVISION,
    DESKTOP_VERSION,
    INSTALLED_NAME,
    INSTALLED_REVISION,
    PREVIOUS,
    ROOT,
    package,
)

SETUP_MOTION = "laser_aligner/machine/setup_motion.py"
PRIORITY_PROTOCOL = "laser_aligner/machine/pi_job_protocol.py"


def load_installer(bundle):
    spec = importlib.util.spec_from_file_location("setup_speed_installer", bundle / "install_z_setup_speed.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    return installer


def snapshot(project):
    return {path.relative_to(project).as_posix(): path.read_bytes()
            for path in project.rglob("*") if path.is_file()}


@pytest.fixture
def kit(tmp_path, monkeypatch):
    bundle = package(tmp_path / "dist")
    installer = load_installer(bundle)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    project = tmp_path / "project"
    (project / "laser_aligner/machine").mkdir(parents=True)
    for relative, digest in manifest["predecessors"][0]["files"].items():
        if digest is not None:
            content = f"# installed predecessor: {relative}\r\n".encode()
            (project / relative).write_bytes(content)
            manifest["predecessors"][0]["files"][relative] = hashlib.sha256(
                content.replace(b"\r\n", b"\n")).hexdigest()
    data = json.dumps(manifest).encode()
    (bundle / "manifest.json").write_bytes(data)
    installer.MANIFEST_SHA256 = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(installer, "inactive", lambda: None)  # Fixture only; no real service.
    return installer, project, bundle


def test_upgrade_preserves_data_backups_and_newlines_and_is_idempotent(kit):
    installer, project, bundle = kit
    configuration = project / "config"
    configuration.mkdir()
    for name in ("pi-hardware.json", "pi-hardware.json.z-limits.json", "pi-hardware.json.laser-focus.json",
                 "pi-hardware.json.laser-focus.json.z-retention.json"):
        (configuration / name).write_bytes(b"operator data\r\n")
    cooler = project / "laser_aligner/machine/cpu_cooling.py"
    cooler.write_bytes(b"preserve cooling\n")
    original = snapshot(project)
    result = installer.install(project, bundle=bundle, apply=True)
    assert result["applied"] and not result["service_started"]
    assert result["compatible_predecessors"] == [INSTALLED_NAME]
    assert len(result["files"]) == len(PREVIOUS) == 17
    for entry in result["files"]:
        target = Path(entry["path"])
        relative = target.relative_to(project).as_posix()
        if relative == SETUP_MOTION:
            assert "backup" not in entry
        else:
            assert Path(entry["backup"]).read_bytes() == original[relative]
            assert target.read_bytes().count(b"\r\n") == target.read_bytes().count(b"\n")
        assert target.read_bytes().replace(b"\r\n", b"\n") == (bundle / relative).read_bytes()
    for name, content in original.items():
        if name not in PREVIOUS:
            assert (project / name).read_bytes() == content
    assert all(entry["status"] == "already_current"
               for entry in installer.install(project, bundle=bundle, apply=True)["files"])


def test_partial_upgrade_can_resume_from_same_predecessor(kit):
    installer, project, bundle = kit
    for relative in (SETUP_MOTION, "laser_aligner/machine/service.py"):
        (project / relative).write_bytes((bundle / relative).read_bytes())
    result = installer.install(project, bundle=bundle, apply=True)
    for entry in result["files"]:
        if Path(entry["path"]).name in ("setup_motion.py", "service.py"):
            assert entry["status"] == "already_current" and "backup" not in entry


@pytest.mark.parametrize("installed_paths", [
    (PRIORITY_PROTOCOL, "laser_aligner/machine/pi_machine_server.py", "laser_aligner/machine/remote_service.py"),
    (SETUP_MOTION, "laser_aligner/machine/service.py", "laser_aligner/machine/mainboard.py",
     "laser_aligner/machine/laser_focus.py", "laser_aligner/machine/job_focus.py", "laser_aligner/machine/z_probe.py"),
], ids=["priority_already_installed", "speed_already_installed"])
def test_combined_upgrade_preserves_already_installed_feature_sources(kit, installed_paths):
    installer, project, bundle = kit
    for relative in installed_paths:
        (project / relative).write_bytes((bundle / relative).read_bytes())
    result = installer.install(project, bundle=bundle, apply=True)
    for entry in result["files"]:
        relative = Path(entry["path"]).relative_to(project).as_posix()
        if relative in installed_paths:
            assert entry["status"] == "already_current" and "backup" not in entry
        assert (project / relative).read_bytes().replace(b"\r\n", b"\n") == (bundle / relative).read_bytes()


@pytest.mark.parametrize("relative", PREVIOUS)
def test_unknown_edit_in_any_payload_path_rejects_before_replacement(kit, relative):
    installer, project, bundle = kit
    (project / relative).write_bytes(b"# operator changes\n")
    original = snapshot(project)
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == original


def test_missing_required_source_rejects_before_replacement(kit):
    installer, project, bundle = kit
    (project / "laser_aligner/machine/mainboard.py").unlink()
    original = snapshot(project)
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == original


def test_dry_run_and_active_service_do_not_modify_sources(kit, monkeypatch):
    installer, project, bundle = kit
    original = snapshot(project)

    def reject_active():
        raise ValueError("service active")

    monkeypatch.setattr(installer, "inactive", reject_active)
    preview = installer.install(project, bundle=bundle)
    assert not preview["applied"] and not preview["service_started"]
    assert snapshot(project) == original
    with pytest.raises(ValueError, match="service active"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == original


@pytest.mark.parametrize("relative", ["manifest.json", SETUP_MOTION, "laser_aligner/machine/laser_focus.py"])
def test_tampered_bundle_rejects_before_replacement(kit, relative):
    installer, project, bundle = kit
    with (bundle / relative).open("ab") as stream:
        stream.write(b" altered")
    original = snapshot(project)
    with pytest.raises(ValueError, match="checksum"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == original


def test_package_pins_complete_installed_baseline_and_exact_application_payload(tmp_path):
    bundle = package(tmp_path / "dist")
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert manifest["predecessors"] == [{"revision": INSTALLED_NAME, "files": PREVIOUS}]
    assert manifest["predecessor_application_revision"] == INSTALLED_REVISION
    assert manifest["required_firmware_capability"] == "Cap:E3_Z_SETUP_SPEED_V1:1"
    assert manifest["compatible_windows_version"] == DESKTOP_VERSION == "0.7.132"
    assert manifest["compatible_windows_revision"] == DESKTOP_REVISION
    assert manifest["pi_control_capability"] == "pi-control-owner-v1"
    assert {entry["path"] for entry in manifest["files"]} == PREVIOUS.keys()
    assert PREVIOUS[SETUP_MOTION] is None
    assert PREVIOUS[PRIORITY_PROTOCOL] == "62aa7a9a68348cedbb848f2ede93b62797af78e0d4190e67aa56ba9904229328"
    assert all(path.startswith("laser_aligner/") for path in PREVIOUS)
    for entry in manifest["files"]:
        content = (bundle / entry["path"]).read_bytes()
        assert content == (ROOT / entry["path"]).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(content).hexdigest() == entry["sha256_lf"]
    installer = load_installer(bundle)
    assert hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest() == installer.MANIFEST_SHA256
    installation = (bundle / "INSTALL.md").read_text(encoding="utf-8")
    assert str(bundle.resolve()).replace("'", "''") in installation
    assert f"e3_setup_speed=/home/greenhouse-climate/{bundle.name}" in installation
    assert '--project "$e3_project"\n' in installation  # First command is read-only.
    assert '--project "$e3_project" --apply &&\n' in installation
    assert "__PI_PACKAGE__" not in installation
    assert "install_z_setup_speed.py" in installation
    assert "Cap:E3_Z_SETUP_SPEED_V1:1" in installation
    assert "teach the 7 mm gauge again" in installation
    assert f"E3 DEV TEST {DESKTOP_VERSION}" in installation and DESKTOP_REVISION in installation
    assert "all 17 payload paths" in installation
    assert "all E3 instances are disconnected" in installation
    assert "explicit Disconnect or a Pi service restart" in installation
    assert "[INSTALL.md](INSTALL.md)" in (bundle / "README.md").read_text(encoding="utf-8")


def test_exact_recorded_installed_upgrade_imports_without_hardware(tmp_path, monkeypatch):
    for revision in (BASE_REVISION, INSTALLED_REVISION):
        if shutil.which("git") is None or subprocess.run(
            ["git", "cat-file", "-e", revision], cwd=ROOT, capture_output=True,
        ).returncode:
            pytest.skip("Pinned source revisions are required for the real installed fixture")
    # Before 568b1cc9, the recorded combination matched bb37be7 except for the
    # remote client. The companion replaced that client and the other 14 paths.
    # Preserve the base tree outside the recorded payload instead of assuming
    # the Pi had received the entire later application revision.
    project = (tmp_path / "installed-pi").resolve()
    project.mkdir()
    source = subprocess.check_output(["git", "archive", "--format=zip", BASE_REVISION, "laser_aligner"], cwd=ROOT)
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        assert all((project / item.filename).resolve().is_relative_to(project) for item in archive.infolist())
        archive.extractall(project)
    # The untouched protocol is pinned from the actual preceding base too.
    assert hashlib.sha256((project / PRIORITY_PROTOCOL).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == PREVIOUS[PRIORITY_PROTOCOL]
    paths = [path for path, digest in PREVIOUS.items() if digest is not None]
    installed = subprocess.check_output(["git", "archive", "--format=zip", INSTALLED_REVISION, *paths], cwd=ROOT)
    with zipfile.ZipFile(io.BytesIO(installed)) as archive:
        assert all((project / item.filename).resolve().is_relative_to(project) for item in archive.infolist())
        archive.extractall(project)
    for relative, digest in PREVIOUS.items():
        target = project / relative
        if digest is None:
            assert not target.exists()
        else:
            assert hashlib.sha256(target.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == digest
    bundle = package(tmp_path / "dist")
    installer = load_installer(bundle)
    monkeypatch.setattr(installer, "inactive", lambda: None)  # Fixture only; no real service.
    original = snapshot(project)
    preview = installer.install(project, bundle=bundle)
    assert not preview["applied"] and preview["compatible_predecessors"] == [INSTALLED_NAME]
    assert snapshot(project) == original
    result = installer.install(project, bundle=bundle, apply=True)
    assert not result["service_started"]
    for relative, content in original.items():
        if relative not in PREVIOUS:
            assert (project / relative).read_bytes() == content
    modules = [name.removesuffix(".py").replace("/", ".") for name in PREVIOUS]
    checks = ("import " + ", ".join(modules) + "\n"
              "from laser_aligner.machine.pi_job_protocol import CAPABILITY_PI_CONTROL_OWNER\n"
              "from laser_aligner.machine.setup_motion import z_feed_mm_min\n"
              "assert CAPABILITY_PI_CONTROL_OWNER == 'pi-control-owner-v1'\n"
              "assert z_feed_mm_min(20, 30) == 1200 and z_feed_mm_min(30, 20) == 600\n")
    subprocess.run([sys.executable, "-c", checks],
                   cwd=project, check=True, capture_output=True)
    assert all(entry["status"] == "already_current"
               for entry in installer.install(project, bundle=bundle)["files"])
