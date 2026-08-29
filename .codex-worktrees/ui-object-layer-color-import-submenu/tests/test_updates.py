from __future__ import annotations

import json
from pathlib import Path

import pytest

from laser_aligner.deployment import BuildInfo
from laser_aligner.updates import (
    UpdateError,
    _same_revision,
    _update_available,
    parse_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def _manifest_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "version": "0.2.0.dev0",
        "revision": "0123456789abcdef0123456789abcdef01234567",
        "channel": "development",
        "published_at": "2026-08-16T20:00:00+00:00",
        "assets": {
            "windows-x86_64": {
                "name": "E3-Setup.exe",
                "url": "https://example.com/E3-Setup.exe",
                "sha256": "a" * 64,
                "size": 123,
            }
        },
    }


def test_parse_manifest() -> None:
    manifest = parse_manifest(json.dumps(_manifest_payload()))

    assert manifest.channel == "development"
    assert manifest.assets["windows-x86_64"].name == "E3-Setup.exe"


def test_manifest_rejects_non_https_asset() -> None:
    payload = _manifest_payload()
    assets = payload["assets"]
    assert isinstance(assets, dict)
    asset = assets["windows-x86_64"]
    assert isinstance(asset, dict)
    asset["url"] = "http://example.com/E3-Setup.exe"

    with pytest.raises(UpdateError, match="HTTPS"):
        parse_manifest(json.dumps(payload))


def test_short_and_full_revision_match() -> None:
    assert _same_revision("01234567", "0123456789abcdef") is True
    assert _same_revision("89abcdef", "0123456789abcdef") is False

def test_development_update_workflow_publishes_only_main() -> None:
    source = (
        ROOT / ".github" / "workflows" / "publish-development-update.yml"
    ).read_text(encoding="utf-8")

    assert "branches:\n      - main" in source
    assert "fix/live-monitor-display-throughput" not in source
    assert source.count("if: github.ref == 'refs/heads/main'") == 3

def _build_info(*, version: str, revision: str) -> BuildInfo:
    return BuildInfo(
        schema_version=1,
        version=version,
        revision=revision,
        channel="development",
        repository="lukelave-boop/E3",
        manifest_url="https://example.com/update-manifest.json",
        platform_key="windows-x86_64",
        packaged=True,
    )


def test_update_available_for_newer_published_build() -> None:
    payload = _manifest_payload()
    payload["version"] = "0.6.42"
    payload["revision"] = "b" * 40
    manifest = parse_manifest(json.dumps(payload))

    assert _update_available(
        _build_info(version="0.6.41", revision="a" * 40),
        manifest,
    ) is True


def test_update_refuses_older_published_build() -> None:
    payload = _manifest_payload()
    payload["version"] = "0.6.40"
    payload["revision"] = "b" * 40
    manifest = parse_manifest(json.dumps(payload))

    assert _update_available(
        _build_info(version="0.6.41", revision="a" * 40),
        manifest,
    ) is False


def test_equal_version_still_uses_revision_identity() -> None:
    payload = _manifest_payload()
    payload["version"] = "0.6.41"
    payload["revision"] = "b" * 40
    manifest = parse_manifest(json.dumps(payload))

    assert _update_available(
        _build_info(version="0.6.41", revision="a" * 40),
        manifest,
    ) is True
