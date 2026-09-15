"""The reusable-focus companion admits recorded baselines without losing edits."""
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

from scripts.package_source_dependencies import MATERIAL_PREDECESSORS
from scripts.package_workpiece_focus import (
    INSTALLED_FOCUS_REVISION,
    PREDECESSORS,
    RECORDED_INSTALLED_FOCUS_NAME,
    ROOT,
    SAVED_Z,
    package,
)

RECORDED_REMOTE_REVISION = "050498c0e31d7778c161688c9c232224aceb2717"
REMOTE_SERVICE = "laser_aligner/machine/remote_service.py"
PI_JOB_PROTOCOL = "laser_aligner/machine/pi_job_protocol.py"
SETUP_MOTION = "laser_aligner/machine/setup_motion.py"


def load_installer(bundle):
    spec = importlib.util.spec_from_file_location("workpiece_installer", bundle / "install_workpiece_focus.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    return installer


@pytest.fixture(params=range(len(PREDECESSORS)), ids=list(PREDECESSORS))
def kit(tmp_path, monkeypatch, request):
    bundle = package(tmp_path / "dist")
    installer = load_installer(bundle)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    originals = []
    for index, predecessor in enumerate(manifest["predecessors"]):
        content = {}
        for name, digest in predecessor["files"].items():
            data = None if digest is None else f"# predecessor {index}: {name}\r\n".encode()
            content[name] = data
            predecessor["files"][name] = None if data is None else hashlib.sha256(
                data.replace(b"\r\n", b"\n")).hexdigest()
        originals.append(content)
    data = json.dumps(manifest).encode()
    (bundle / "manifest.json").write_bytes(data)
    installer.MANIFEST_SHA256 = hashlib.sha256(data).hexdigest()
    project = tmp_path / "project"
    (project / "laser_aligner/machine").mkdir(parents=True)
    for name, original in originals[request.param].items():
        if original is not None:
            (project / name).parent.mkdir(parents=True, exist_ok=True)
            (project / name).write_bytes(original)
    monkeypatch.setattr(installer, "inactive", lambda: None)
    return installer, project, bundle, originals, request.param


def test_upgrade_preserves_operator_data_backups_and_newlines_and_is_idempotent(kit):
    installer, project, bundle, originals, baseline = kit
    configuration = project / "config"
    configuration.mkdir()
    for name in ("pi-hardware.json", "pi-hardware.json.z-limits.json", "pi-hardware.json.laser-focus.json",
                 "pi-hardware.json.laser-focus.json.z-retention.json"):
        (configuration / name).write_bytes(b"operator data\r\n")
    cooler = project / "laser_aligner/machine/cpu_cooling.py"
    cooler.write_bytes(b"preserve cooling\n")
    result = installer.install(project, bundle=bundle, apply=True)
    assert result["applied"] and not result["service_started"]
    assert result["compatible_predecessors"] == [list(PREDECESSORS)[baseline]]
    assert len(result["files"]) == len({**SAVED_Z, **MATERIAL_PREDECESSORS}) == 19
    for entry in result["files"]:
        target = Path(entry["path"])
        relative = target.relative_to(project).as_posix()
        original = originals[baseline][relative]
        if original is None:
            assert "backup" not in entry
        else:
            assert Path(entry["backup"]).read_bytes() == original
            assert target.read_bytes().count(b"\r\n") == target.read_bytes().count(b"\n")
        assert target.read_bytes().replace(b"\r\n", b"\n") == (bundle / relative).read_bytes()
    assert cooler.read_bytes() == b"preserve cooling\n"
    assert all(path.read_bytes() == b"operator data\r\n" for path in configuration.iterdir())
    again = installer.install(project, bundle=bundle, apply=True)
    assert all(entry["status"] == "already_current" for entry in again["files"])


def test_already_updated_files_allow_retry_from_the_same_predecessor(kit):
    installer, project, bundle, _, _ = kit
    relative = "laser_aligner/machine/service.py"
    (project / relative).write_bytes((bundle / relative).read_bytes())
    result = installer.install(project, bundle=bundle, apply=True)
    assert next(entry for entry in result["files"] if entry["path"].endswith("service.py"))["status"] == "already_current"


def test_mixed_known_predecessors_reject_before_any_replacement(kit):
    installer, project, bundle, originals, baseline = kit
    relative = "laser_aligner/machine/service.py"
    other_baseline = (baseline + 1) % len(originals)
    (project / relative).write_bytes(originals[other_baseline][relative])
    with pytest.raises(ValueError, match="mixed predecessor revisions"):
        installer.install(project, bundle=bundle, apply=True)
    assert (project / relative).read_bytes() == originals[other_baseline][relative]
    assert not list(project.rglob("*.e3-backup-*"))


@pytest.mark.parametrize("relative", ["laser_aligner/machine/service.py", REMOTE_SERVICE, PI_JOB_PROTOCOL,
                                      "laser_aligner/machine/z_retention.py"])
def test_unknown_local_edit_rejects_before_any_replacement(kit, relative):
    installer, project, bundle, originals, baseline = kit
    (project / relative).write_bytes(b"# operator changes\n")
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert (project / relative).read_bytes() == b"# operator changes\n"
    assert (project / "laser_aligner/config.py").read_bytes() == originals[baseline]["laser_aligner/config.py"]
    assert not list(project.rglob("*.e3-backup-*"))


def test_dry_run_and_active_service_never_modify_sources(kit, monkeypatch):
    installer, project, bundle, originals, baseline = kit
    assert not installer.install(project, bundle=bundle)["applied"]

    def reject():
        raise ValueError("service active")

    monkeypatch.setattr(installer, "inactive", reject)
    with pytest.raises(ValueError, match="service active"):
        installer.install(project, bundle=bundle, apply=True)
    assert (project / "laser_aligner/machine/service.py").read_bytes() == originals[baseline][
        "laser_aligner/machine/service.py"]
    assert not list(project.rglob("*.e3-backup-*"))


@pytest.mark.parametrize("relative", ["manifest.json", "laser_aligner/machine/laser_focus.py"])
def test_tampered_bundle_rejects_before_any_replacement(kit, relative):
    installer, project, bundle, _, _ = kit
    with (bundle / relative).open("ab") as stream:
        stream.write(b" altered")
    with pytest.raises(ValueError, match="checksum"):
        installer.install(project, bundle=bundle, apply=True)
    assert not list(project.rglob("*.e3-backup-*"))


def test_missing_file_is_not_accepted_when_baseline_requires_it(kit):
    installer, project, bundle, _, _ = kit
    (project / "laser_aligner/machine/job_focus.py").unlink()
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert not list(project.rglob("*.e3-backup-*"))


def test_package_pins_complete_baselines_and_copies_only_application_sources(tmp_path):
    bundle = package(tmp_path / "dist")
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    expected = {revision: {**files, **MATERIAL_PREDECESSORS} for revision, files in PREDECESSORS.items()}
    assert {entry["revision"]: entry["files"] for entry in manifest["predecessors"]} == expected
    assert {entry["path"] for entry in manifest["files"]} == SAVED_Z.keys() | MATERIAL_PREDECESSORS.keys()
    for previous in manifest["predecessors"]:
        assert previous["files"].keys() == SAVED_Z.keys() | MATERIAL_PREDECESSORS.keys()
    assert all(path.startswith("laser_aligner/") for path in SAVED_Z)
    assert {PI_JOB_PROTOCOL, SETUP_MOTION} <= SAVED_Z.keys()
    assert all(previous["files"][SETUP_MOTION] is None for previous in manifest["predecessors"])
    guide = (bundle / "LASER_FOCUS.md").read_text(encoding="utf-8")
    assert "__PI_PACKAGE__" not in guide
    assert "install_laser_focus.py" not in guide
    assert "install_workpiece_focus.py" in guide
    assert "install_z_setup_speed.py" not in guide
    assert "package_z_setup_speed.py" not in guide
    assert "[INSTALL.md](INSTALL.md)" in guide
    installation_section = guide.split("## Pi companion installation\n", 1)[1]
    assert "568b1cc9" not in installation_section
    assert "`e57adbb5` / `12dbb3d` / `59c31d7`" in installation_section
    assert "pinned saved-Z predecessor" in installation_section
    assert "## Setup travel speeds" in guide
    assert "Normal Z moves require the new firmware's exact" in guide
    assert (bundle / "Z_RETENTION.md").is_file()
    installation = (bundle / "INSTALL.md").read_text(encoding="utf-8")
    assert str(bundle.resolve()).replace("'", "''") in installation
    assert f"e3_workpiece=/home/greenhouse-climate/{bundle.name}" in installation
    assert "e3_project=/home/greenhouse-climate/Projects/laser-camera-aligner" in installation
    assert '--project "$e3_project"\n' in installation  # The first command is a dry run.
    assert '--project "$e3_project" --apply &&\n' in installation
    assert "__PI_PACKAGE__" not in installation
    assert "[INSTALL.md](INSTALL.md)" in (bundle / "README.md").read_text(encoding="utf-8")


def exact_predecessor_project(tmp_path, baseline):
    revision = INSTALLED_FOCUS_REVISION if baseline == RECORDED_INSTALLED_FOCUS_NAME else baseline
    overrides = {REMOTE_SERVICE: RECORDED_REMOTE_REVISION} if baseline == RECORDED_INSTALLED_FOCUS_NAME else {}
    for required_revision in {revision, *overrides.values()}:
        if shutil.which("git") is None or subprocess.run(
            ["git", "cat-file", "-e", required_revision], cwd=ROOT, capture_output=True,
        ).returncode:
            pytest.skip("The pinned source revisions are required for the real predecessor fixture")
    source = subprocess.check_output(["git", "archive", "--format=zip", revision, "laser_aligner"], cwd=ROOT)
    project = (tmp_path / "pinned-main").resolve()
    project.mkdir()
    with zipfile.ZipFile(io.BytesIO(source)) as archive:
        assert all((project / item.filename).resolve().is_relative_to(project) for item in archive.infolist())
        archive.extractall(project)
    for name, source_revision in overrides.items():
        (project / name).write_bytes(subprocess.check_output(
            ["git", "show", f"{source_revision}:{name}"], cwd=ROOT))
    assert len(PREDECESSORS[baseline]) == 17
    for name, digest in PREDECESSORS[baseline].items():
        target = project / name
        if digest is None:
            assert not target.exists()
        else:
            assert hashlib.sha256(target.read_bytes().replace(b"\r\n", b"\n")).hexdigest() == digest
    return project


def source_snapshot(project):
    return {path.relative_to(project).as_posix(): path.read_bytes()
            for path in project.rglob("*") if path.is_file()}


@pytest.mark.parametrize("baseline", PREDECESSORS)
def test_exact_git_predecessor_upgrade_imports_without_hardware(tmp_path, monkeypatch, baseline):
    project = exact_predecessor_project(tmp_path, baseline)
    bundle = package(tmp_path / "dist")
    installer = load_installer(bundle)
    monkeypatch.setattr(installer, "inactive", lambda: None)  # Isolated fixture, no real service.
    original = source_snapshot(project)
    preview = installer.install(project, bundle=bundle)
    assert not preview["applied"] and not preview["service_started"]
    assert preview["compatible_predecessors"] == [baseline]
    assert source_snapshot(project) == original
    assert not list(project.rglob("*.e3-backup-*"))
    result = installer.install(project, bundle=bundle, apply=True)
    assert not result["service_started"]
    assert all(entry["status"] == "already_current" for entry in installer.install(project, bundle=bundle)["files"])
    modules = [name.removesuffix(".py").replace("/", ".") for name in SAVED_Z]
    assert {"laser_aligner.machine.pi_job_protocol", "laser_aligner.machine.setup_motion"} <= set(modules)
    subprocess.run([sys.executable, "-c", "import " + ", ".join(modules)],
                   cwd=project, check=True, capture_output=True)


def test_recorded_composition_still_rejects_unknown_remote_service_edits(tmp_path, monkeypatch):
    project = exact_predecessor_project(tmp_path, RECORDED_INSTALLED_FOCUS_NAME)
    bundle = package(tmp_path / "dist")
    installer = load_installer(bundle)
    monkeypatch.setattr(installer, "inactive", lambda: None)
    target = project / REMOTE_SERVICE
    target.write_bytes(target.read_bytes() + b"\n# operator modification\n")
    original = source_snapshot(project)
    with pytest.raises(ValueError, match="unknown local changes: laser_aligner/machine/remote_service.py"):
        installer.install(project, bundle=bundle, apply=True)
    assert source_snapshot(project) == original
    assert not list(project.rglob("*.e3-backup-*"))
