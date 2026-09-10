from __future__ import annotations

import hashlib
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from firmware.marlin_mainboard_compact import install_pi_support as installer

ROOT = Path(__file__).resolve().parents[1]
DESIRED = (ROOT / installer.RELATIVE_TARGET).read_bytes().replace(b"\r\n", b"\n")
F103 = DESIRED.replace(b'                "E3AUX1 UPDATER 0.3.0 BOARD=0401C013",\n', b"")
OLD = DESIRED.replace(
    b'            elif text in {\n'
    b'                "E3AUX1 UPDATER 0.2.0 BOARD=0401E013",\n'
    b'                "E3AUX1 UPDATER 0.2.0 BOARD=0103E013",\n'
    b'                "E3AUX1 UPDATER 0.3.0 BOARD=0401C013",\n'
    b'                "ERR UNSUPPORTED",\n'
    b'            }:\n',
    b'            elif text == "E3AUX1 UPDATER 0.2.0 BOARD=0401E013" or text == "ERR UNSUPPORTED":\n',
)


@pytest.fixture
def files(tmp_path):
    project = tmp_path / "project"
    target = project / installer.RELATIVE_TARGET
    target.parent.mkdir(parents=True)
    target.write_bytes(OLD)
    source = tmp_path / "secondary_startup.py"
    source.write_bytes(DESIRED)
    return project, target, source


@pytest.fixture
def inactive(monkeypatch):
    calls = []
    monkeypatch.setattr(installer.sys, "platform", "linux")

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="inactive\n")

    monkeypatch.setattr(installer.subprocess, "run", run)
    return calls


def test_pinned_sources_match_exact_repository_variants():
    assert hashlib.sha256(DESIRED).hexdigest() == installer.DESIRED_SHA256
    assert {hashlib.sha256(data).hexdigest() for data in (OLD, F103, DESIRED)} == set(
        installer.ACCEPTED_SOURCE_SHA256
    )


@pytest.mark.parametrize("current", [OLD, F103])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_apply_known_source_preserves_original_backup_and_newlines(files, inactive, current, newline):
    project, target, source = files
    original = current.replace(b"\n", newline)
    target.write_bytes(original)
    prior_mode = stat.S_IMODE(target.stat().st_mode)
    result = installer.install_support(project, source=source, apply=True)
    assert result["status"] == "updated" and result["changed"]
    assert target.read_bytes() == DESIRED.replace(b"\n", newline)
    backup = Path(result["backup"])
    assert backup.parent == target.parent and backup.read_bytes() == original
    assert stat.S_IMODE(target.stat().st_mode) == prior_mode
    assert stat.S_IMODE(backup.stat().st_mode) == prior_mode
    assert len(inactive) == 1
    assert inactive[0][0] == ["systemctl", "show", installer.SERVICE, "--property=ActiveState", "--value"]
    assert not list(target.parent.glob("*.e3-new-*"))


def test_check_only_never_calls_service_or_changes_files(files, monkeypatch):
    project, target, source = files
    monkeypatch.setattr(installer, "service_inactive", lambda: pytest.fail("dry run called systemctl"))
    result = installer.install_support(project, source=source)
    assert result["status"] == "ready_to_apply" and not result["changed"]
    assert target.read_bytes() == OLD
    assert list(target.parent.iterdir()) == [target]


def test_already_current_is_idempotent(files, inactive):
    project, target, source = files
    target.write_bytes(DESIRED)
    result = installer.install_support(project, source=source, apply=True)
    assert result["status"] == "already_current" and not result["changed"]
    assert "backup" not in result
    assert list(target.parent.iterdir()) == [target]


def test_unique_backups_do_not_overwrite_an_earlier_original(files, inactive):
    project, target, source = files
    first = installer.install_support(project, source=source, apply=True)
    target.write_bytes(F103)
    second = installer.install_support(project, source=source, apply=True)
    assert first["backup"] != second["backup"]
    assert Path(first["backup"]).read_bytes() == OLD
    assert Path(second["backup"]).read_bytes() == F103


@pytest.mark.parametrize("apply", [False, True])
def test_unknown_local_edits_are_preserved_before_service_check(files, monkeypatch, apply):
    project, target, source = files
    edited = OLD + b"# local change\n"
    target.write_bytes(edited)
    monkeypatch.setattr(installer, "service_inactive", lambda: pytest.fail("unknown source reached apply"))
    with pytest.raises(installer.SupportError, match="unknown local edits"):
        installer.install_support(project, source=source, apply=apply)
    assert target.read_bytes() == edited and list(target.parent.iterdir()) == [target]


def test_corrupt_bundled_source_is_rejected(files, monkeypatch):
    project, target, source = files
    source.write_bytes(DESIRED + b"# changed\n")
    monkeypatch.setattr(installer, "service_inactive", lambda: pytest.fail("corrupt bundle reached apply"))
    with pytest.raises(installer.SupportError, match="pinned SHA-256"):
        installer.install_support(project, source=source, apply=True)
    assert target.read_bytes() == OLD


@pytest.mark.parametrize("state,returncode", [("active", 0), ("activating", 0), ("failed", 0), ("", 0), ("inactive", 1)])
def test_apply_requires_confirmed_inactive_service(files, monkeypatch, state, returncode):
    project, target, source = files
    monkeypatch.setattr(installer.sys, "platform", "linux")
    monkeypatch.setattr(installer.subprocess, "run", lambda *_a, **_kw: SimpleNamespace(
        returncode=returncode, stdout=state + "\n",
    ))
    with pytest.raises(installer.SupportError, match="inactive state"):
        installer.install_support(project, source=source, apply=True)
    assert target.read_bytes() == OLD and list(target.parent.iterdir()) == [target]


@pytest.mark.parametrize("error", [OSError("missing systemctl"), subprocess.TimeoutExpired("systemctl", 5)])
def test_service_read_failure_preserves_target(files, monkeypatch, error):
    project, target, source = files
    monkeypatch.setattr(installer.sys, "platform", "linux")

    def failed(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(installer.subprocess, "run", failed)
    with pytest.raises(installer.SupportError, match="Could not confirm"):
        installer.install_support(project, source=source, apply=True)
    assert target.read_bytes() == OLD


def test_apply_refuses_non_linux_host(files, monkeypatch):
    project, target, source = files
    monkeypatch.setattr(installer.sys, "platform", "win32")
    with pytest.raises(installer.SupportError, match="Linux Pi"):
        installer.install_support(project, source=source, apply=True)
    assert target.read_bytes() == OLD


def test_change_during_service_check_is_preserved(files, monkeypatch):
    project, target, source = files
    edited = OLD + b"# new concurrent edit\n"
    monkeypatch.setattr(installer, "service_inactive", lambda: target.write_bytes(edited))
    with pytest.raises(installer.SupportError, match="changed during preparation"):
        installer.install_support(project, source=source, apply=True)
    assert target.read_bytes() == edited
    assert list(target.parent.iterdir()) == [target]


def test_atomic_replace_failure_preserves_original_and_backup(files, inactive, monkeypatch):
    project, target, source = files

    def failed(*_args):
        raise OSError("replace failed")

    monkeypatch.setattr(installer.os, "replace", failed)
    with pytest.raises(OSError, match="replace failed"):
        installer.install_support(project, source=source, apply=True)
    assert target.read_bytes() == OLD
    backups = list(target.parent.glob("*.e3-backup-*"))
    assert len(backups) == 1 and backups[0].read_bytes() == OLD
    assert not list(target.parent.glob("*.e3-new-*"))


def test_mixed_newlines_and_oversized_sources_are_preserved(files):
    project, target, source = files
    target.write_bytes(OLD.replace(b"\n", b"\r\n", 1))
    with pytest.raises(installer.SupportError, match="mixed line endings"):
        installer.install_support(project, source=source)
    target.write_bytes(b"#" * 65537)
    with pytest.raises(installer.SupportError, match="64 KiB"):
        installer.install_support(project, source=source)


def test_symlink_source_target_is_rejected(files):
    project, target, source = files
    linked = target.with_name("linked.py")
    linked.write_bytes(OLD)
    target.unlink()
    try:
        target.symlink_to(linked)
    except OSError:
        pytest.skip("Creating symlinks requires additional privileges on this Windows host")
    with pytest.raises(installer.SupportError, match="symlink"):
        installer.install_support(project, source=source)
    assert linked.read_bytes() == OLD


def test_cli_requires_explicit_project():
    with pytest.raises(SystemExit) as error:
        installer.main([])
    assert error.value.code == 2
