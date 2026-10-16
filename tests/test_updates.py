from __future__ import annotations

import io
import json
import os
import urllib.error
from pathlib import Path
from types import SimpleNamespace

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
    assert source.count("if: github.ref == 'refs/heads/main'") == 4


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


@pytest.mark.parametrize("replace_current", [False, True])
def test_linux_update_starts_a_new_frozen_scope_without_changing_old_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, replace_current: bool,
) -> None:
    from laser_aligner import deployment, state_scope

    profile = tmp_path / "profile"
    seed = profile / "xdg" / "e3-positioning-system"
    config = seed / "config" / "network-local.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"app": {"data_dir": "../data"}}), encoding="utf-8")
    calibration = seed / "data" / "calibration.json"
    calibration.parent.mkdir()
    calibration.write_text('{"fixture":"original calibration"}', encoding="utf-8")
    # Use a private mapping because real startup publishes several variables.
    # Restoring the mapping also contains those direct production mutations.
    monkeypatch.setattr(os, "environ", {
        "HOME": str(profile), "XDG_DATA_HOME": str(profile / "xdg"),
        "PATH": "/usr/local/bin:/usr/bin", "KEEP_CALLER_SETTING": "unchanged",
    })
    monkeypatch.setattr(state_scope, "platform_user_data_dir", lambda: Path(os.environ["XDG_DATA_HOME"]) / seed.name)
    monkeypatch.setattr(state_scope, "legacy_user_data_dir", lambda: tmp_path / "absent-legacy")
    monkeypatch.setattr(state_scope, "_runtime_publication", None)
    monkeypatch.setattr(state_scope.sys, "frozen", True, raising=False)
    monkeypatch.setattr(deployment, "application_root", lambda: tmp_path / "bundle")
    monkeypatch.setattr(deployment, "load_build_info", lambda: SimpleNamespace(version="0.7.153", revision="a" * 40))
    state_scope.prepare_runtime_state()
    state_scope.record_runtime_startup()
    previous = Path(os.environ["E3_USER_STATE_DIR"])
    edited = previous / "data" / "calibration.json"
    edited.write_text('{"fixture":"operator edit"}', encoding="utf-8")
    previous_files = {path: path.read_bytes() for path in previous.rglob("*") if path.is_file()}
    os.environ.update(
        E3_BRIDGE_TOKEN="synthetic-private-token", E3_BRIDGE_TOKEN_FILE=str(previous / "token"),
        E3_BUILD_INFO=str(previous / "old-build-info.json"),
        E3_DEV_TEST="1", E3_DEV_TEST_VERSION="0.7.153",
    )
    downloaded = tmp_path / "download.AppImage"
    downloaded.write_bytes(b"inert downloaded fixture")
    current = tmp_path / "current.AppImage"
    if replace_current:
        current.write_bytes(b"inert previous fixture")
        os.environ["APPIMAGE"] = str(current)
    parent_environment = dict(os.environ)
    launches: list[tuple[list[str], dict[str, object]]] = []
    monkeypatch.setattr(updates.subprocess, "Popen", lambda argv, **kwargs: launches.append((argv, kwargs)))

    updates._launch_linux_appimage(downloaded)

    assert len(launches) == 1
    argv, options = launches[0]
    assert argv == [str(current if replace_current else downloaded)]
    assert options["close_fds"] is True and options["start_new_session"] is True
    environment = options["env"]
    assert isinstance(environment, dict)
    assert environment["HOME"] == str(profile)
    assert environment["XDG_DATA_HOME"] == str(profile / "xdg")
    assert environment["KEEP_CALLER_SETTING"] == "unchanged"
    assert environment["PATH"] == parent_environment["PATH"]
    assert not any(key.startswith(("E3_STATE_", "E3_DEV_TEST")) for key in environment)
    for key in ("E3_USER_STATE_DIR", "E3_CONFIG_PATH", "E3_BRIDGE_TOKEN", "E3_BRIDGE_TOKEN_FILE", "E3_BUILD_INFO"):
        assert key not in environment
    assert dict(os.environ) == parent_environment
    with monkeypatch.context() as child:
        child.setattr(os, "environ", environment.copy())
        child.setattr(deployment, "load_build_info", lambda: SimpleNamespace(version="0.8.0", revision="b" * 40))
        state_scope.prepare_runtime_state()
        next_scope = Path(os.environ["E3_USER_STATE_DIR"])
        assert next_scope == seed / "state-scopes" / "installed" / f"0.8.0-{'b' * 40}"
        assert Path(os.environ["E3_CONFIG_PATH"]).is_relative_to(next_scope)
        copied = next_scope / "data" / "calibration.json"
        assert copied.read_bytes() == edited.read_bytes()
        copied.write_text('{"fixture":"new build edit"}', encoding="utf-8")
    assert dict(os.environ) == parent_environment
    assert {path: path.read_bytes() for path in previous.rglob("*") if path.is_file()} == previous_files


def test_linux_update_restores_profile_and_drops_explicit_previous_launch_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = {
        "LOCALAPPDATA": None, "APPDATA": None, "USERPROFILE": None,
        "HOME": str(tmp_path / "home"), "XDG_DATA_HOME": str(tmp_path / "custom-xdg"),
    }
    parent = {
        **dict.fromkeys(original, str(tmp_path / "redirected")),
        "E3_STATE_ORIGINAL_ENV": json.dumps(original),
        "E3_USER_STATE_DIR": "explicit-old-root", "E3_CONFIG_PATH": "explicit-old-config",
        "LASER_ALIGNER_CONFIG": "explicit-legacy-config", "E3_STATE_ISOLATED": "1",
        "PATH": "/tmp/old-bundle:/usr/bin", "KEEP": "yes",
    }
    monkeypatch.setattr(os, "environ", parent)
    monkeypatch.setattr(updates.sys, "_MEIPASS", "/tmp/old-bundle", raising=False)
    package = tmp_path / "inert.AppImage"
    package.write_bytes(b"inert fixture")
    launches = []
    monkeypatch.setattr(updates.subprocess, "Popen", lambda argv, **kwargs: launches.append(kwargs))
    updates._launch_linux_appimage(package)
    environment = launches[0]["env"]
    for key, value in original.items():
        assert environment.get(key) == value
    assert environment["PATH"] == parent["PATH"]  # No Windows PATH rules on Linux.
    assert environment["KEEP"] == "yes"
    assert not any(key.startswith("E3_") for key in environment)
    assert "LASER_ALIGNER_CONFIG" not in environment
    assert os.environ is parent and parent["E3_CONFIG_PATH"] == "explicit-old-config"


@pytest.mark.parametrize("snapshot", ["{bad-json", '{}', '{"HOME": 4}'])
def test_linux_update_rejects_invalid_profile_before_replacing_appimage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, snapshot: str,
) -> None:
    current = tmp_path / "current.AppImage"
    current.write_bytes(b"inert old fixture")
    package = tmp_path / "download.AppImage"
    package.write_bytes(b"inert new fixture")
    monkeypatch.setattr(os, "environ", {"APPIMAGE": str(current), "E3_STATE_ORIGINAL_ENV": snapshot})
    monkeypatch.setattr(updates.subprocess, "Popen", lambda *args, **kwargs: pytest.fail("must not launch"))
    with pytest.raises(UpdateError, match="original user environment"):
        updates._launch_linux_appimage(package)
    assert current.read_bytes() == b"inert old fixture"
    assert package.read_bytes() == b"inert new fixture"
    assert not current.with_name(f"{current.name}.previous").exists()
