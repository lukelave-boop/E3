"""Priority companion upgrades preserve operator data and reject unknown edits."""
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

from scripts.package_pi_control_priority import PREVIOUS, PREVIOUS_PACKAGE, PREVIOUS_REVISION, ROOT, package
from scripts.package_source_dependencies import MATERIAL_PREDECESSORS
from scripts.package_z_setup_speed import PREVIOUS as COMPLETE_PREVIOUS

CURRENT_FILES = {**COMPLETE_PREVIOUS, **MATERIAL_PREDECESSORS}


def load_installer(bundle):
    spec = importlib.util.spec_from_file_location("priority_installer", bundle / "install_pi_control_priority.py")
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
    project = (tmp_path / "project").resolve()
    (project / "laser_aligner/machine").mkdir(parents=True)
    originals = {name: f"# installed {name}\r\n".encode() for name, digest in CURRENT_FILES.items() if digest is not None}
    for name, original in originals.items():
        (project / name).parent.mkdir(parents=True, exist_ok=True)
        (project / name).write_bytes(original)
        manifest["predecessors"][0]["files"][name] = hashlib.sha256(original.replace(b"\r\n", b"\n")).hexdigest()
    data = json.dumps(manifest).encode()
    (bundle / "manifest.json").write_bytes(data)
    installer.MANIFEST_SHA256 = hashlib.sha256(data).hexdigest()
    monkeypatch.setattr(installer, "inactive", lambda: None)  # Isolated fixtures, no real service.
    return installer, project, bundle, originals


def test_current_upgrade_includes_capability_implementation_and_preserves_operator_data(kit):
    installer, project, bundle, originals = kit
    preserved = ("config/pi-hardware.json", "config/pi-hardware.json.laser-focus.json",
                 "config/pi-hardware.json.z-limits.json", "config/pi-hardware.json.laser-focus.json.z-retention.json",
                 "laser_aligner/machine/cpu_cooling.py", "firmware/firmware.bin")
    for name in preserved:
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"operator bytes\r\n")
    before = snapshot(project)
    preview = installer.install(project, bundle=bundle)
    assert not preview["applied"] and not preview["service_started"]
    assert all(entry["status"] == "ready_to_apply" for entry in preview["files"])
    assert snapshot(project) == before
    result = installer.install(project, bundle=bundle, apply=True)
    assert result["compatible_predecessors"] == [PREVIOUS_PACKAGE]
    assert result["applied"] and not result["service_started"]
    assert len(result["files"]) == 19
    for entry in result["files"]:
        target = Path(entry["path"])
        relative = target.relative_to(project).as_posix()
        assert entry["status"] == "updated"
        if relative in originals:
            assert Path(entry["backup"]).read_bytes() == originals[relative]
            assert target.read_bytes().count(b"\r\n") == target.read_bytes().count(b"\n")
        else:
            assert "backup" not in entry
        assert target.read_bytes().replace(b"\r\n", b"\n") == (bundle / relative).read_bytes()
    assert all((project / name).read_bytes() == before[name] for name in preserved)
    again = installer.install(project, bundle=bundle, apply=True)
    assert all(entry["status"] == "already_current" for entry in again["files"])
    assert len(list(project.rglob("*.e3-backup-*"))) == len(originals)
    assert b"validate_program_binding" in (project / "laser_aligner/machine/service.py").read_bytes()


@pytest.mark.parametrize("name", PREVIOUS)
def test_unknown_or_missing_baseline_rejects_before_any_replacement(kit, name):
    installer, project, bundle, _ = kit
    target = project / name
    target.write_bytes(b"# operator modification\n")
    before = snapshot(project)
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == before
    target.unlink()
    before = snapshot(project)
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == before


def test_interrupted_update_can_resume_with_already_current_module(kit):
    installer, project, bundle, _ = kit
    name = next(iter(PREVIOUS))
    (project / name).write_bytes((bundle / name).read_bytes())
    result = installer.install(project, bundle=bundle, apply=True)
    assert result["files"][0]["status"] == "already_current"
    assert all(entry["status"] == "updated" for entry in result["files"][1:])


@pytest.mark.parametrize("name", ["manifest.json", *PREVIOUS])
def test_tampered_bundle_rejects_before_any_replacement(kit, name):
    installer, project, bundle, _ = kit
    target = bundle / name
    target.write_bytes(target.read_bytes() + b" altered")
    before = snapshot(project)
    with pytest.raises(ValueError, match="checksum"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == before


def test_active_service_rejects_before_any_replacement(kit, monkeypatch):
    installer, project, bundle, _ = kit

    def reject():
        raise ValueError("service active")

    monkeypatch.setattr(installer, "inactive", reject)
    before = snapshot(project)
    with pytest.raises(ValueError, match="service active"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == before


@pytest.mark.parametrize(("platform", "state", "returncode", "accepted"), [
    ("win32", "inactive", 0, False), ("linux", "active", 0, False),
    ("linux", "failed", 0, False), ("linux", "inactive", 1, False),
    ("linux", "inactive", 0, True),
])
def test_service_guard_requires_linux_and_successful_inactive_state(tmp_path, monkeypatch,
                                                                  platform, state, returncode, accepted):
    installer = load_installer(package(tmp_path / "dist"))
    monkeypatch.setattr(installer.sys, "platform", platform)
    calls = []

    def status(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, returncode, stdout=state, stderr="")

    monkeypatch.setattr(installer.subprocess, "run", status)
    if accepted:
        installer.inactive()
    else:
        with pytest.raises(ValueError):
            installer.inactive()
    assert calls == ([] if platform == "win32" else [[
        "systemctl", "show", "e3-hardware-node.service", "--property=ActiveState", "--value",
    ]])


def test_package_contains_exact_paths_and_reviewable_idle_installation_commands(tmp_path):
    bundle = package(tmp_path / "dist")
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert manifest["source_revision"] is None
    assert manifest["predecessors"] == [{"revision": PREVIOUS_PACKAGE, "files": CURRENT_FILES}]
    assert {entry["path"] for entry in manifest["files"]} == CURRENT_FILES.keys()
    assert {path.relative_to(bundle).as_posix() for path in (bundle / "laser_aligner").rglob("*")
            if path.is_file()} == CURRENT_FILES.keys()
    for entry in manifest["files"]:
        assert (bundle / entry["path"]).read_bytes() == (ROOT / entry["path"]).read_bytes().replace(b"\r\n", b"\n")
    guide = (bundle / "INSTALL.md").read_text(encoding="utf-8")
    assert str(bundle.resolve()).replace("'", "''") in guide
    assert f"e3_priority=/home/greenhouse-climate/{bundle.name}" in guide
    assert '--project "$e3_project"\n' in guide
    assert '--project "$e3_project" --apply &&\n' in guide
    assert "idle and all E3 instances are disconnected" in guide
    assert "explicit Disconnect or a Pi service restart" in guide
    assert "[INSTALL.md](INSTALL.md)" in (bundle / "README.md").read_text(encoding="utf-8")


def require_predecessor_git():
    if shutil.which("git") is None or subprocess.run(
        ["git", "cat-file", "-e", PREVIOUS_REVISION], cwd=ROOT, capture_output=True,
    ).returncode:
        pytest.skip("The pinned 671b235 Git revision is required for real predecessor verification")


def test_explicit_revision_packages_exact_git_payload(tmp_path):
    require_predecessor_git()
    bundle = package(tmp_path / "dist", revision=PREVIOUS_REVISION)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert manifest["source_revision"] == PREVIOUS_REVISION
    assert {entry["path"]: entry["sha256_lf"] for entry in manifest["files"]} == PREVIOUS


def test_current_package_needs_no_git_history(tmp_path, monkeypatch):
    def reject_history(*args, **kwargs):
        raise AssertionError("Current source packaging must work in a shallow checkout")
    monkeypatch.setattr(subprocess, "check_output", reject_history)
    bundle = package(tmp_path / "dist")
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert len(manifest["files"]) == 19


@pytest.mark.parametrize("name", [*MATERIAL_PREDECESSORS, "laser_aligner/machine/service.py"])
def test_current_capability_dependencies_reject_unknown_edits_before_replacement(kit, name):
    installer, project, bundle, _ = kit
    path = project / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"# unrecognized operator source\n")
    before = snapshot(project)
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == before


def test_real_installed_workpiece_predecessor_upgrades_and_imports_without_hardware(tmp_path, monkeypatch):
    require_predecessor_git()
    source = subprocess.check_output(["git", "archive", "--format=zip", PREVIOUS_REVISION, "laser_aligner"], cwd=ROOT)
    project = (tmp_path / "pinned-workpiece").resolve()
    project.mkdir()
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        assert all((project / entry.filename).resolve().is_relative_to(project) for entry in archive.infolist())
        archive.extractall(project)
    for name, digest in PREVIOUS.items():
        assert hashlib.sha256((project / name).read_bytes().replace(b"\r\n", b"\n")).hexdigest() == digest
    bundle = package(tmp_path / "dist")
    installer = load_installer(bundle)
    monkeypatch.setattr(installer, "inactive", lambda: None)
    before = snapshot(project)
    assert not installer.install(project, bundle=bundle)["applied"]
    assert snapshot(project) == before
    result = installer.install(project, bundle=bundle, apply=True)
    assert result["compatible_predecessors"] == [PREVIOUS_PACKAGE]
    assert not result["service_started"]
    assert all((project / name).read_bytes() == data for name, data in before.items() if name not in CURRENT_FILES)
    modules = [name.removesuffix(".py").replace("/", ".") for name in CURRENT_FILES]
    subprocess.run([sys.executable, "-c", "import " + ", ".join([*modules, "laser_aligner.remote_node"])],
                   cwd=project, check=True, capture_output=True)
    for name in PREVIOUS:
        original = (project / name).read_bytes()
        newline = b"\r\n" if b"\r\n" in original else b"\n"
        (project / name).write_bytes(original + newline + b"# unknown local edit" + newline)
        before_rejection = snapshot(project)
        with pytest.raises(ValueError, match="unknown local changes"):
            installer.install(project, bundle=bundle, apply=True)
        assert snapshot(project) == before_rejection
        (project / name).write_bytes(original)
