from __future__ import annotations

import importlib.util
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
    assert module.version_from_commit_count(0) == "0.6.0"
    assert module.version_from_commit_count(1) == "0.6.1"
    assert module.version_from_commit_count(12) == "0.6.12"


def test_version_override(monkeypatch) -> None:
    module = _module()
    monkeypatch.setenv("E3_BUILD_VERSION", "1.2.3")
    assert module.build_version(Path(".")) == "1.2.3"


def test_runtime_version_override(monkeypatch) -> None:
    module = _module()
    monkeypatch.setenv("E3_POSITIONING_SYSTEM_VERSION", "9.8.7")
    assert module.application_version(Path(".")) == "9.8.7"
