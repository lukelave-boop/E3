from __future__ import annotations

import json
from pathlib import Path

from laser_aligner import deployment


def test_preserved_machine_config_has_priority(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "state"
    config = root / "config" / "network-local.json"
    config.parent.mkdir(parents=True)
    config.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("E3_USER_STATE_DIR", str(root))
    monkeypatch.delenv("E3_CONFIG_PATH", raising=False)

    profile = deployment.resolve_launch_profile()

    assert profile.config_path == config.resolve()
    assert profile.hardware_enabled is True
    assert profile.laser_lockout is True


def test_bridge_token_prefers_environment(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("E3_USER_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("E3_BRIDGE_TOKEN", "environment-token")
    token_file = tmp_path / "secrets" / "bridge-token.txt"
    token_file.parent.mkdir(parents=True)
    token_file.write_text("file-token", encoding="utf-8")

    assert deployment.read_bridge_token() == "environment-token"


def test_load_build_info(monkeypatch, tmp_path: Path) -> None:
    path = tmp_path / "build-info.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "version": "1.2.3",
                "revision": "0123456789abcdef",
                "channel": "development",
                "repository": "owner/repo",
                "manifest_url": "https://example.com/manifest.json",
                "platform_key": "windows-x86_64",
                "packaged": True,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("E3_BUILD_INFO", str(path))

    info = deployment.load_build_info()

    assert info.version == "1.2.3"
    assert info.short_revision == "01234567"
    assert info.packaged is True
