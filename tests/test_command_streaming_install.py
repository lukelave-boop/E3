"""The Pi streaming companion preserves exact source and operator-data guards."""
from __future__ import annotations

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

from scripts import package_command_streaming as packager


@pytest.fixture(autouse=True)
def historical_streaming_contract(tmp_path, monkeypatch, request):
    """Installer unit tests use a minimal historical three-module API fixture.

    Current service sources now require the larger pause companion. The separate
    Git-backed integration test below still imports the actual historical runtime.
    """
    if request.node.name in {"test_current_pause_sources_reject_partial_package", "test_exact_recorded_pi_upgrade_hashes_and_imports_without_hardware"}:
        return
    original = packager.ROOT
    root = tmp_path / "historical-source"
    sources = {
        "laser_aligner/machine/grbl_stream.py": (original / "laser_aligner/machine/grbl_stream.py").read_bytes(),
        "laser_aligner/machine/service.py": b"from .grbl_stream import GrblStreamCompactor\n",
        "laser_aligner/machine/controller_receiver.py": (
            b"class ControllerReceiver:\n"
            b"    def begin_stream(self): pass\n    def stream_write(self): pass\n"
            b"    def stream_read_ack(self): pass\n    def finish_stream(self): pass\n"
        ),
        "scripts/install_workpiece_focus.py": (original / "scripts/install_workpiece_focus.py").read_bytes(),
    }
    for name, data in sources.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    monkeypatch.setattr(packager, "ROOT", root)


def test_current_pause_sources_reject_partial_package(tmp_path):
    with pytest.raises(ValueError, match="package_job_pause"):
        packager.package(tmp_path / "dist")
    assert not (tmp_path / "dist").exists()


def load_installer(bundle):
    spec = importlib.util.spec_from_file_location("streaming_installer", bundle / "install_command_streaming.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    return installer


def snapshot(project):
    return {path.relative_to(project).as_posix(): path.read_bytes()
            for path in project.rglob("*") if path.is_file()}


@pytest.fixture
def kit(tmp_path, monkeypatch):
    bundle = packager.package(tmp_path / "dist")
    installer = load_installer(bundle)
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    project = tmp_path / "project"
    (project / "laser_aligner/machine").mkdir(parents=True)
    for name, digest in manifest["predecessors"][0]["files"].items():
        if digest is not None:
            original = f"# exact unit-test predecessor: {name}\r\n".encode()
            (project / name).write_bytes(original)
            manifest["predecessors"][0]["files"][name] = hashlib.sha256(original.replace(b"\r\n", b"\n")).hexdigest()
    encoded = json.dumps(manifest).encode()
    (bundle / "manifest.json").write_bytes(encoded)
    installer.MANIFEST_SHA256 = hashlib.sha256(encoded).hexdigest()
    monkeypatch.setattr(installer, "inactive", lambda: None)  # Fixture only; no actual service calls.
    return installer, project, bundle


def test_package_pins_exact_three_file_baseline_and_needs_no_git_history(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **kw: pytest.fail("Unexpected Git or hardware command"))
    bundle = packager.package(tmp_path / "dist")
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert manifest["source_revision"] is None
    assert manifest["predecessors"] == [{"revision": packager.PREVIOUS_REVISION, "files": packager.PREVIOUS}]
    assert len(manifest["files"]) == 3
    assert {entry["path"] for entry in manifest["files"]} == packager.PREVIOUS.keys()
    assert packager.PREVIOUS["laser_aligner/machine/grbl_stream.py"] is None
    assert {path.relative_to(bundle).as_posix() for path in (bundle / "laser_aligner").rglob("*.py")} == packager.PREVIOUS.keys()
    for entry in manifest["files"]:
        expected = (packager.ROOT / entry["path"]).read_bytes().replace(b"\r\n", b"\n")
        assert (bundle / entry["path"]).read_bytes() == expected
        assert hashlib.sha256(expected).hexdigest() == entry["sha256_lf"]
    installer = load_installer(bundle)
    assert hashlib.sha256((bundle / "manifest.json").read_bytes()).hexdigest() == installer.MANIFEST_SHA256
    guide = (bundle / "INSTALL.md").read_text(encoding="utf-8")
    assert str(bundle.resolve()).replace("'", "''") in guide
    assert f"e3_streaming=/home/greenhouse-climate/{bundle.name}" in guide
    assert '--project "$e3_project"\n' in guide
    assert '--project "$e3_project" --apply &&\n' in guide
    assert "current Windows E3 client remains compatible" in guide
    assert "teach the gauge again" not in guide and "firmware flash" not in guide
    assert "[INSTALL.md](INSTALL.md)" in (bundle / "README.md").read_text(encoding="utf-8")


def test_selected_revision_packages_exact_payload_without_working_tree_fallback(tmp_path, monkeypatch):
    revision = "f" * 40
    selected = {name: (packager.ROOT / name).read_bytes().replace(b"\r\n", b"\n") + b"\n# Selected revision fixture.\n"
                for name in packager.PREVIOUS}
    calls = []

    def output(command, **kwargs):
        calls.append(command)
        if command[:2] == ["git", "rev-parse"]:
            assert command == ["git", "rev-parse", "--verify", "--end-of-options", "selected^{commit}"]
            return revision + "\n"
        assert command[:2] == ["git", "show"]
        selected_revision, name = command[2].split(":", 1)
        assert selected_revision == revision
        return selected[name]

    monkeypatch.setattr(packager.subprocess, "check_output", output)
    bundle = packager.package(tmp_path / "dist", revision="selected")
    manifest = json.loads((bundle / "manifest.json").read_bytes())
    assert manifest["source_revision"] == revision
    assert len(calls) == 4
    assert all((bundle / name).read_bytes() == data for name, data in selected.items())


def test_incompatible_selected_receiver_rejects_before_package_output(tmp_path, monkeypatch):
    selected_root = tmp_path / "selected"
    for name in packager.PREVIOUS:
        target = selected_root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((packager.ROOT / name).read_bytes())
    (selected_root / "laser_aligner/machine/controller_receiver.py").write_bytes(b"# No streaming API.\n")
    monkeypatch.setattr(packager, "ROOT", selected_root)
    destination = tmp_path / "must-not-exist"
    with pytest.raises(ValueError, match="incompatible source API"):
        packager.package(destination)
    assert not destination.exists()


def test_dry_run_does_not_check_service_or_modify_sources(kit, monkeypatch):
    installer, project, bundle = kit
    monkeypatch.setattr(installer, "inactive", lambda: pytest.fail("Dry run must not check the live service"))
    before = snapshot(project)
    result = installer.install(project, bundle=bundle)
    assert not result["applied"] and not result["service_started"]
    assert result["compatible_predecessors"] == [packager.PREVIOUS_REVISION]
    assert all(entry["status"] == "ready_to_apply" for entry in result["files"])
    assert snapshot(project) == before


def test_apply_preserves_exact_backups_newlines_operator_data_and_idempotence(kit):
    installer, project, bundle = kit
    preserved = ("config/pi-hardware.json", "config/calibration.json", "config/saved-z.json",
                 "firmware/firmware.bin", "laser_aligner/machine/cpu_cooling.py")
    for name in preserved:
        path = project / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"operator bytes\r\n")
    before = snapshot(project)
    result = installer.install(project, bundle=bundle, apply=True)
    assert result["applied"] and not result["service_started"]
    for entry in result["files"]:
        target = Path(entry["path"])
        relative = target.relative_to(project).as_posix()
        assert entry["status"] == "updated"
        assert target.read_bytes().replace(b"\r\n", b"\n") == (bundle / relative).read_bytes()
        if relative in before:
            assert Path(entry["backup"]).read_bytes() == before[relative]
            assert target.read_bytes().count(b"\r\n") == target.read_bytes().count(b"\n")
        else:
            assert "backup" not in entry
    assert all((project / name).read_bytes() == before[name] for name in preserved)
    again = installer.install(project, bundle=bundle, apply=True)
    assert all(entry["status"] == "already_current" and "backup" not in entry for entry in again["files"])


@pytest.mark.parametrize("name", packager.PREVIOUS)
def test_unknown_existing_or_new_module_rejects_before_any_replacement(kit, name):
    installer, project, bundle = kit
    (project / name).write_bytes(b"# unknown operator changes\n")
    before = snapshot(project)
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == before


@pytest.mark.parametrize("name", ["laser_aligner/machine/controller_receiver.py", "laser_aligner/machine/service.py"])
def test_required_predecessor_cannot_be_missing(kit, name):
    installer, project, bundle = kit
    (project / name).unlink()
    before = snapshot(project)
    with pytest.raises(ValueError, match="unknown local changes"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == before


@pytest.mark.parametrize("name", ["manifest.json", *packager.PREVIOUS])
def test_tampered_bundle_rejects_before_source_changes(kit, name):
    installer, project, bundle = kit
    with (bundle / name).open("ab") as stream:
        stream.write(b" altered")
    before = snapshot(project)
    with pytest.raises(ValueError, match="checksum"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == before


def test_active_service_blocks_all_writes(kit, monkeypatch):
    installer, project, bundle = kit
    before = snapshot(project)

    def active():
        raise ValueError("service active")

    monkeypatch.setattr(installer, "inactive", active)
    with pytest.raises(ValueError, match="service active"):
        installer.install(project, bundle=bundle, apply=True)
    assert snapshot(project) == before


def test_partial_success_resumes_only_from_current_or_exact_predecessor(kit):
    installer, project, bundle = kit
    new_module = "laser_aligner/machine/grbl_stream.py"
    (project / new_module).write_bytes((bundle / new_module).read_bytes())
    result = installer.install(project, bundle=bundle, apply=True)
    assert result["files"][0]["status"] == "already_current"
    assert all(entry["status"] == "updated" for entry in result["files"][1:])


@pytest.mark.parametrize("state", ["active", "activating", "deactivating", "failed", ""])
def test_real_service_gate_rejects_every_noninactive_state(tmp_path, monkeypatch, state):
    installer = load_installer(packager.package(tmp_path / "dist"))
    monkeypatch.setattr(installer.sys, "platform", "linux")
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, state, "")

    monkeypatch.setattr(installer.subprocess, "run", run)
    with pytest.raises(ValueError, match="Stop e3-hardware-node.service"):
        installer.inactive()
    assert calls == [["systemctl", "show", "e3-hardware-node.service", "--property=ActiveState", "--value"]]


def test_service_gate_accepts_only_linux_inactive_and_never_starts_it(tmp_path, monkeypatch):
    installer = load_installer(packager.package(tmp_path / "dist"))
    monkeypatch.setattr(installer.sys, "platform", "win32")
    with pytest.raises(ValueError, match="requires Linux"):
        installer.inactive()
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setattr(installer.subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 0, "inactive\n", ""))
    installer.inactive()


def test_exact_recorded_pi_upgrade_hashes_and_imports_without_hardware(tmp_path, monkeypatch):
    selected = "34902b8"
    if shutil.which("git") is None or subprocess.run(
        ["git", "cat-file", "-e", selected], cwd=packager.ROOT, capture_output=True,
    ).returncode:
        pytest.skip("Historical streaming revision is absent in this shallow checkout")
    revision = packager.PREVIOUS_REVISION
    if shutil.which("git") is None or subprocess.run(
        ["git", "cat-file", "-e", revision], cwd=packager.ROOT, capture_output=True,
    ).returncode:
        pytest.skip("Recorded Pi source revision is unavailable in this shallow checkout")
    archive_bytes = subprocess.check_output(["git", "archive", "--format=zip", revision, "laser_aligner"], cwd=packager.ROOT)
    project = tmp_path / "recorded-pi"
    project.mkdir()
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        assert all((project / member.filename).resolve().is_relative_to(project.resolve()) for member in archive.infolist())
        archive.extractall(project)
    for name, expected in packager.PREVIOUS.items():
        target = project / name
        assert (hashlib.sha256(target.read_bytes().replace(b"\r\n", b"\n")).hexdigest() if target.exists() else None) == expected
    bundle = packager.package(tmp_path / "dist", revision=selected)
    installer = load_installer(bundle)
    monkeypatch.setattr(installer, "inactive", lambda: None)  # Isolated fixture, no real service.
    before = snapshot(project)
    assert not installer.install(project, bundle=bundle)["applied"]
    assert snapshot(project) == before
    installer.install(project, bundle=bundle, apply=True)
    assert all((project / name).read_bytes() == data for name, data in before.items() if name not in packager.PREVIOUS)
    checks = (
        "from laser_aligner.machine.service import MachineService\n"
        "from laser_aligner.machine.controller_receiver import ControllerReceiver\n"
        "from laser_aligner.machine.grbl_stream import GrblStreamCompactor\n"
        "from laser_aligner.machine.pi_job_service import PiJobService\n"
        "from laser_aligner.machine.pi_machine_server import PiMachineServer\n"
        "import laser_aligner.remote_node\n"
        "assert callable(ControllerReceiver.stream_write)\n"
        "assert GrblStreamCompactor().encode('G1 X1 Y2 F3 S0') == 'G1X1Y2F3S0'\n"
    )
    subprocess.run([sys.executable, "-c", checks], cwd=project, check=True, capture_output=True)
