from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path


def _module():
    path = Path("packaging/version_for_build.py")
    spec = importlib.util.spec_from_file_location("e3_version_for_build", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_version_commit_count_is_predictable() -> None:
    module = _module()
    assert module.version_from_commit_count(0) == "0.7.0"
    assert module.version_from_commit_count(1) == "0.7.1"
    assert module.version_from_commit_count(12) == "0.7.12"


def test_version_override(monkeypatch) -> None:
    module = _module()
    monkeypatch.setenv("E3_BUILD_VERSION", "1.2.3")
    assert module.build_version(Path(".")) == "1.2.3"


def test_runtime_version_override(monkeypatch) -> None:
    module = _module()
    monkeypatch.setenv("E3_POSITIONING_SYSTEM_VERSION", "9.8.7")
    assert module.application_version(Path(".")) == "9.8.7"


def test_release_tag_is_the_version_baseline(tmp_path, monkeypatch) -> None:
    module = _module()
    monkeypatch.delenv("E3_BUILD_VERSION", raising=False)
    monkeypatch.delenv("E3_POSITIONING_SYSTEM_VERSION", raising=False)
    monkeypatch.delenv("E3_BUILD_INFO", raising=False)

    def git(*args: str) -> None:
        subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True, capture_output=True, text=True,
        )

    git("init")
    git("-c", "user.name=Version Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "release")
    git("tag", "v0.7.0")
    assert module.build_version(tmp_path) == "0.7.0"
    assert module.application_version(tmp_path) == "0.7.0"
    git("-c", "user.name=Version Test", "-c", "user.email=test@example.invalid",
        "commit", "--allow-empty", "-m", "next change")
    assert module.build_version(tmp_path) == "0.7.1"
    assert module.application_version(tmp_path) == "0.7.1"
