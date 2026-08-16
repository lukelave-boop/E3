from __future__ import annotations

import json

import pytest

from laser_aligner.updates import UpdateError, _same_revision, parse_manifest


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
