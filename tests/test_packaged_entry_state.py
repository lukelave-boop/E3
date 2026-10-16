from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from laser_aligner import deployment, state_scope, storage
from laser_aligner.identity import REVISION_ENVIRONMENT_VARIABLE, VERSION_ENVIRONMENT_VARIABLE


@pytest.mark.parametrize("explicit_token", [None, "explicit-process-token"])
def test_packaged_relaunch_reads_its_scoped_token_before_legacy_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, explicit_token: str | None,
) -> None:
    """Frozen startup must choose its independent state before token lookup."""
    legacy = tmp_path / "legacy"
    config = legacy / "config" / "network-local.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"app": {"data_dir": "../data"}}), encoding="utf-8")
    token_file = legacy / "secrets" / "bridge-token.txt"
    token_file.parent.mkdir()
    token_file.write_text("initial-legacy-token", encoding="utf-8")
    build = SimpleNamespace(version="0.8.1", revision="a" * 40)
    monkeypatch.setattr(deployment, "load_build_info", lambda: build)
    monkeypatch.setattr(deployment, "application_root", lambda: tmp_path / "bundle")
    monkeypatch.setattr(storage, "platform_user_data_dir", lambda: legacy)
    monkeypatch.setattr(state_scope, "platform_user_data_dir", lambda: legacy)
    monkeypatch.setattr(state_scope, "legacy_user_data_dir", lambda: tmp_path / "absent")
    monkeypatch.setattr(state_scope, "_runtime_publication", None)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    environment_keys = (
        "E3_USER_STATE_DIR", "E3_CONFIG_PATH", "E3_STATE_ISOLATED", "E3_DEV_TEST",
        "E3_BRIDGE_TOKEN", "E3_BRIDGE_TOKEN_FILE",
        REVISION_ENVIRONMENT_VARIABLE, VERSION_ENVIRONMENT_VARIABLE,
    )
    for key in environment_keys:
        monkeypatch.delenv(key, raising=False)

    state_scope.prepare_runtime_state()
    scoped_config = Path(os.environ["E3_CONFIG_PATH"])
    scoped_token = Path(os.environ["E3_USER_STATE_DIR"]) / "secrets" / "bridge-token.txt"
    scoped_token.write_text("this-installed-build-token", encoding="utf-8")
    token_file.write_text("changed-by-other-version", encoding="utf-8")
    before = {path: path.read_bytes() for path in (config, token_file, scoped_config, scoped_token)}
    for key in environment_keys:
        monkeypatch.delenv(key, raising=False)
    if explicit_token is not None:
        monkeypatch.setenv("E3_BRIDGE_TOKEN", explicit_token)

    calls = []
    desktop = ModuleType("laser_aligner.desktop.main")

    def desktop_main(arguments):
        calls.append(arguments)
        assert arguments == ["--config", str(scoped_config)]
        assert os.environ["E3_BRIDGE_TOKEN"] == (explicit_token or "this-installed-build-token")
        assert deployment.read_bridge_token() == (explicit_token or "this-installed-build-token")
        return 19

    desktop.main = desktop_main
    monkeypatch.setitem(sys.modules, "laser_aligner.desktop.main", desktop)
    entry = Path(__file__).resolve().parents[1] / "packaging" / "e3_entry.py"
    spec = importlib.util.spec_from_file_location("packaged_entry_state_test", entry)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.run() == 19
    assert len(calls) == 1
    assert os.environ[REVISION_ENVIRONMENT_VARIABLE] == build.revision
    assert os.environ[VERSION_ENVIRONMENT_VARIABLE] == build.version
    assert all(path.read_bytes() == contents for path, contents in before.items())
