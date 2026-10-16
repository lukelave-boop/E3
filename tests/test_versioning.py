from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_version_environment(monkeypatch):
    for name in ("E3_BUILD_VERSION", "E3_POSITIONING_SYSTEM_VERSION", "E3_BUILD_INFO"):
        monkeypatch.delenv(name, raising=False)


def _git(root: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Version Test",
         "-c", "user.email=test@example.invalid", *arguments],
        check=True, capture_output=True, text=True, timeout=15,
    ).stdout.strip()


def _commit(root: Path, message: str) -> None:
    _git(root, "commit", "--allow-empty", "-m", message)


def _module():
    path = Path("packaging/version_for_build.py")
    spec = importlib.util.spec_from_file_location("e3_version_for_build", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_commit_count_is_predictable() -> None:
    module = _module()
    assert module.version_from_commit_count(0) == "0.8.0"
    assert module.version_from_commit_count(1) == "0.8.1"
    assert module.version_from_commit_count(12) == "0.8.12"


def test_version_override(monkeypatch) -> None:
    module = _module()
    monkeypatch.setenv("E3_BUILD_VERSION", "1.2.3")
    assert module.build_version(Path(".")) == "1.2.3"


def test_runtime_version_override(monkeypatch) -> None:
    module = _module()
    monkeypatch.setenv("E3_POSITIONING_SYSTEM_VERSION", "9.8.7")
    assert module.application_version(Path(".")) == "9.8.7"


def test_release_tag_is_the_version_baseline(tmp_path: Path) -> None:
    module = _module()
    _git(tmp_path, "init")
    _commit(tmp_path, "previous series")
    _git(tmp_path, "tag", "v0.7.0")
    _commit(tmp_path, "release")

    # The release resolves identically before and after its final main SHA is
    # tagged. An older series tag must not contribute to the new patch count.
    assert module.build_version(tmp_path) == "0.8.0"
    assert module.application_version(tmp_path) == "0.8.0"
    _git(tmp_path, "tag", "v0.8.0")
    assert module.build_version(tmp_path) == "0.8.0"
    assert module.application_version(tmp_path) == "0.8.0"
    _commit(tmp_path, "next change")
    assert module.build_version(tmp_path) == "0.8.1"
    assert module.application_version(tmp_path) == "0.8.1"


def test_patch_counts_merged_commits_without_moving_the_baseline(tmp_path: Path) -> None:
    module = _module()
    _git(tmp_path, "init")
    _commit(tmp_path, "release")
    _git(tmp_path, "tag", "v0.8.0")
    release = _git(tmp_path, "rev-parse", "v0.8.0")
    _git(tmp_path, "checkout", "-b", "feature")
    _commit(tmp_path, "feature change")
    _git(tmp_path, "checkout", "-b", "integration", "v0.8.0")
    _git(tmp_path, "merge", "--no-ff", "feature", "-m", "integrate feature")
    assert module.build_version(tmp_path) == "0.8.2"
    assert _git(tmp_path, "rev-parse", "v0.8.0") == release


def test_source_archive_without_git_uses_the_new_base_version(tmp_path: Path) -> None:
    module = _module()
    assert module.build_version(tmp_path) == "0.8.0"
    assert module.application_version(tmp_path) == "0.8.0"


def test_frozen_older_build_keeps_its_version_after_series_rollover(tmp_path: Path, monkeypatch) -> None:
    module = _module()
    _git(tmp_path, "init")
    _commit(tmp_path, "release")
    _git(tmp_path, "tag", "v0.8.0")
    _commit(tmp_path, "next change")
    bundle = tmp_path / "old-frozen-bundle"
    bundle.mkdir()
    metadata = json.dumps({"schema_version": 1, "version": "0.7.153", "revision": "a" * 40})
    info = bundle / "build-info.json"
    info.write_text(metadata, encoding="utf-8")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle / "E3.exe"))
    assert module.application_version(tmp_path) == "0.7.153"
    assert module.build_version(tmp_path) == "0.8.1"
    assert info.read_text(encoding="utf-8") == metadata
