from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path

import pytest

import laser_aligner.updates as updates
from laser_aligner.deployment import BuildInfo
from laser_aligner.updates import (
    UpdateError,
    _same_revision,
    _update_available,
    download_update,
    fetch_manifest,
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


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.com/update-manifest.json",
        code,
        "simulated transient response",
        hdrs=None,
        fp=None,
    )


def test_manifest_fetch_retries_404_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: list[object] = [
        _http_error(404),
        io.BytesIO(json.dumps(_manifest_payload()).encode("utf-8")),
    ]
    sleeps: list[float] = []

    def urlopen(_request: object, *, timeout: float) -> object:
        assert timeout == 15.0
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(updates.time, "sleep", sleeps.append)

    manifest = fetch_manifest("https://example.com/update-manifest.json")

    assert manifest.revision == _manifest_payload()["revision"]
    assert responses == []
    assert sleeps == [0.5]


def test_manifest_fetch_retries_500_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: list[object] = [
        _http_error(500),
        io.BytesIO(json.dumps(_manifest_payload()).encode("utf-8")),
    ]
    sleeps: list[float] = []

    def urlopen(_request: object, *, timeout: float) -> object:
        assert timeout == 15.0
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(updates.time, "sleep", sleeps.append)

    assert fetch_manifest("https://example.com/update-manifest.json").channel == "development"
    assert responses == []
    assert sleeps == [0.5]


def test_manifest_fetch_surfaces_repeated_transient_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def urlopen(_request: object, *, timeout: float) -> object:
        nonlocal calls
        assert timeout == 15.0
        calls += 1
        raise _http_error(503)

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(updates.time, "sleep", sleeps.append)

    with pytest.raises(UpdateError, match="after 4 attempts.*HTTP Error 503"):
        fetch_manifest("https://example.com/update-manifest.json")

    assert calls == 4
    assert sleeps == [0.5, 1.0, 2.0]


def test_malformed_downloaded_manifest_fails_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    sleeps: list[float] = []

    def urlopen(_request: object, *, timeout: float) -> object:
        nonlocal calls
        assert timeout == 15.0
        calls += 1
        return io.BytesIO(b"{not-json")

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(updates.time, "sleep", sleeps.append)

    with pytest.raises(UpdateError, match="not valid UTF-8 JSON"):
        fetch_manifest("https://example.com/update-manifest.json")

    assert calls == 1
    assert sleeps == []


def test_malformed_manifest_url_fails_before_network_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_urlopen(_request: object, *, timeout: float) -> object:
        pytest.fail(f"urlopen should not be called with timeout {timeout}")

    monkeypatch.setattr(updates.urllib.request, "urlopen", unexpected_urlopen)

    with pytest.raises(UpdateError, match="absolute HTTPS URL"):
        fetch_manifest("not-a-url")


def test_download_hash_mismatch_is_discarded_without_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls = 0
    payload = _manifest_payload()
    asset_value = payload["assets"]
    assert isinstance(asset_value, dict)
    windows_value = asset_value["windows-x86_64"]
    assert isinstance(windows_value, dict)
    windows_value["size"] = 3
    asset = parse_manifest(json.dumps(payload)).assets["windows-x86_64"]

    def urlopen(_request: object, *, timeout: float) -> object:
        nonlocal calls
        assert timeout == 60.0
        calls += 1
        return io.BytesIO(b"bad")

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)

    with pytest.raises(UpdateError, match="failed SHA-256 verification"):
        download_update(asset, destination_dir=tmp_path)

    assert calls == 1
    assert not (tmp_path / asset.name).exists()
    assert not (tmp_path / f".{asset.name}.part").exists()


def test_manifest_retry_never_bypasses_asset_verification(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payload = _manifest_payload()
    assets = payload["assets"]
    assert isinstance(assets, dict)
    windows = assets["windows-x86_64"]
    assert isinstance(windows, dict)
    windows["size"] = 3
    requests: list[str] = []
    responses: list[object] = [
        _http_error(404),
        io.BytesIO(json.dumps(payload).encode("utf-8")),
        io.BytesIO(b"bad"),
    ]

    def urlopen(request: object, *, timeout: float) -> object:
        assert timeout in {15.0, 60.0}
        requests.append(str(request.full_url))  # type: ignore[attr-defined]
        response = responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    monkeypatch.setattr(updates.urllib.request, "urlopen", urlopen)
    monkeypatch.setattr(updates.time, "sleep", lambda _delay: None)

    manifest = fetch_manifest("https://example.com/update-manifest.json")
    asset = manifest.assets["windows-x86_64"]
    with pytest.raises(UpdateError, match="failed SHA-256 verification"):
        download_update(asset, destination_dir=tmp_path)

    assert requests == [
        "https://example.com/update-manifest.json",
        "https://example.com/update-manifest.json",
        "https://example.com/E3-Setup.exe",
    ]
    assert responses == []
    assert not (tmp_path / asset.name).exists()


def test_short_and_full_revision_match() -> None:
    assert _same_revision("01234567", "0123456789abcdef") is True
    assert _same_revision("89abcdef", "0123456789abcdef") is False


def test_development_update_workflow_publishes_only_main() -> None:
    source = (
        ROOT / ".github" / "workflows" / "publish-development-update.yml"
    ).read_text(encoding="utf-8")

    assert "branches:\n      - main" in source
    assert "  workflow_dispatch:" in source
    assert "  cancel-in-progress: true" in source
    assert "fix/live-monitor-display-throughput" not in source
    assert source.count("if: github.ref == 'refs/heads/main'") == 3


def test_development_update_workflow_switches_verified_manifest_last() -> None:
    source = (
        ROOT / ".github" / "workflows" / "publish-development-update.yml"
    ).read_text(encoding="utf-8")

    assert "gh release delete" not in source
    assert "--cleanup-tag" not in source
    assert "gh release upload" not in source
    assert "packaging/publish_development_update.py publish" in source
    assert "packaging/publish_development_update.py recover" in source
    assert 'short_sha="${GITHUB_SHA:0:12}"' in source
    assert 'publication_id="${GITHUB_RUN_ID}-${GITHUB_RUN_ATTEMPT}"' in source
    assert 'E3-Setup-${short_sha}.exe' in source
    assert 'E3-${short_sha}-x86_64.AppImage' in source
    assert 'update-manifest-${short_sha}-${publication_id}.json' in source
    assert "if: ${{ always() }}" in source


def test_development_update_workflow_ignores_only_non_product_paths() -> None:
    source = (
        ROOT / ".github" / "workflows" / "publish-development-update.yml"
    ).read_text(encoding="utf-8")
    lines = source.splitlines()
    ignore_start = lines.index("    paths-ignore:") + 1
    ignored_paths: list[str] = []
    for line in lines[ignore_start:]:
        if not line.startswith("      - "):
            break
        ignored_paths.append(line.removeprefix("      - "))

    assert set(ignored_paths) == {
        ".github/ISSUE_TEMPLATE/**",
        ".github/pull_request_template.md",
        ".github/workflows/ci.yml",
        ".github/workflows/fast-ci.yml",
        "AGENTS.md",
        "CHANGELOG.md",
        "CONTRIBUTING.md",
        "CURRENT_STATE.md",
        "PROJECT_STATUS.md",
        "README.md",
        "ROADMAP.md",
        "SAFETY.md",
        "docs/**",
        "requirements-dev.txt",
        "tests/**",
    }
    assert {
        ".github/workflows/publish-development-update.yml",
        "laser_aligner/**",
        "packaging/**",
        "pyproject.toml",
        "requirements-desktop.txt",
        "requirements.txt",
    }.isdisjoint(ignored_paths)


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


def test_older_development_build_accepts_immutable_revision_asset_name() -> None:
    payload = _manifest_payload()
    payload["version"] = "0.6.129"
    payload["revision"] = "b" * 40
    assets = payload["assets"]
    assert isinstance(assets, dict)
    windows = assets["windows-x86_64"]
    assert isinstance(windows, dict)
    windows["name"] = "E3-Setup-bbbbbbbbbbbb.exe"
    windows["url"] = (
        "https://github.com/lukelave-boop/E3/releases/download/"
        "e3-development/E3-Setup-bbbbbbbbbbbb.exe"
    )

    manifest = parse_manifest(json.dumps(payload))

    assert manifest.assets["windows-x86_64"].name == "E3-Setup-bbbbbbbbbbbb.exe"
    assert _update_available(
        _build_info(version="0.6.128", revision="a" * 40),
        manifest,
    ) is True
