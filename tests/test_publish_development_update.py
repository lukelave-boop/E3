from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
REPOSITORY = "lukelave-boop/E3"
RELEASE_TAG = "e3-development"


def _publisher_module() -> ModuleType:
    path = ROOT / "packaging" / "publish_development_update.py"
    spec = importlib.util.spec_from_file_location("e3_publish_development_update", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load development publisher from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


publisher = _publisher_module()


class FakeReleaseClient:
    def __init__(self, *, release_exists: bool = True) -> None:
        self.release = (
            publisher.Release(release_id=1, draft=False, prerelease=True)
            if release_exists
            else None
        )
        self.assets: dict[int, Any] = {}
        self.next_asset_id = 10
        self.events: list[tuple[Any, ...]] = []
        self.fail_upload_name: str | None = None
        self.wrong_digest_name: str | None = None
        self.fail_manifest_switch = False
        self.fail_cleanup = False
        self.current_revision: str | None = None
        self.release_metadata: tuple[str, str, str, bool] | None = None
        if release_exists:
            self._add_asset("E3-Setup.exe", b"old windows", asset_id=1)
            self._add_asset("E3-x86_64.AppImage", b"old linux", asset_id=2)
            self._add_asset("update-manifest.json", b'{"old": true}\n', asset_id=3)

    def _add_asset(self, name: str, content: bytes, *, asset_id: int | None = None) -> Any:
        if asset_id is None:
            asset_id = self.next_asset_id
            self.next_asset_id += 1
        asset = publisher.ReleaseAsset(
            asset_id=asset_id,
            name=name,
            size=len(content),
            digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
            state="uploaded",
        )
        self.assets[asset_id] = asset
        return asset

    def get_release(self, tag: str) -> Any:
        assert tag == RELEASE_TAG
        return self.release

    def create_draft_release(
        self,
        tag: str,
        revision: str,
        title: str,
        notes: str,
    ) -> Any:
        assert tag == RELEASE_TAG
        self.events.append(("create_draft", revision, title, notes))
        self.release = publisher.Release(release_id=1, draft=True, prerelease=True)
        return self.release

    def list_assets(self, release_id: int) -> list[Any]:
        assert release_id == 1
        return list(self.assets.values())

    def upload_asset(self, tag: str, path: Path) -> None:
        assert tag == RELEASE_TAG
        self.events.append(("upload", path.name))
        if path.name == self.fail_upload_name:
            raise publisher.PublicationError(f"simulated upload failure for {path.name}")
        content = path.read_bytes()
        asset = self._add_asset(path.name, content)
        if path.name == self.wrong_digest_name:
            self.assets[asset.asset_id] = publisher.ReleaseAsset(
                asset_id=asset.asset_id,
                name=asset.name,
                size=asset.size,
                digest=f"sha256:{'0' * 64}",
                state=asset.state,
            )

    def rename_asset(self, asset_id: int, name: str) -> None:
        current = self.assets[asset_id]
        self.events.append(("rename", asset_id, current.name, name))
        if (
            self.fail_manifest_switch
            and name == "update-manifest.json"
            and current.name.startswith("update-manifest-a")
        ):
            raise publisher.PublicationError("simulated authoritative rename failure")
        if any(asset.asset_id != asset_id and asset.name == name for asset in self.assets.values()):
            raise publisher.PublicationError(f"duplicate asset name {name}")
        self.assets[asset_id] = publisher.ReleaseAsset(
            asset_id=current.asset_id,
            name=name,
            size=current.size,
            digest=current.digest,
            state=current.state,
        )

    def delete_asset(self, asset_id: int) -> None:
        self.events.append(("delete", asset_id))
        if self.fail_cleanup:
            raise publisher.PublicationError("simulated cleanup failure")
        del self.assets[asset_id]

    def update_tag(self, tag: str, revision: str) -> None:
        assert tag == RELEASE_TAG
        self.events.append(("update_tag", revision))
        self.current_revision = revision

    def update_release(
        self,
        release_id: int,
        revision: str,
        title: str,
        notes: str,
        *,
        draft: bool,
    ) -> None:
        assert release_id == 1
        self.events.append(("update_release", revision, draft))
        self.release_metadata = (revision, title, notes, draft)
        self.release = publisher.Release(release_id=1, draft=draft, prerelease=True)


def _publication_files(tmp_path: Path) -> tuple[Path, Path, Path]:
    windows_name, linux_name = publisher.immutable_asset_names(REVISION)
    manifest_name = publisher.staged_manifest_name(REVISION, "123-1")
    windows = tmp_path / windows_name
    linux = tmp_path / linux_name
    manifest = tmp_path / manifest_name
    windows.write_bytes(b"new windows installer")
    linux.write_bytes(b"new linux appimage")
    payload = {
        "schema_version": 1,
        "version": "0.6.129",
        "revision": REVISION,
        "channel": "development",
        "published_at": "2026-08-28T20:00:00+00:00",
        "assets": {},
    }
    for platform_key, path in (
        ("windows-x86_64", windows),
        ("linux-x86_64", linux),
    ):
        payload["assets"][platform_key] = {
            "name": path.name,
            "url": (
                f"https://github.com/{REPOSITORY}/releases/download/"
                f"{RELEASE_TAG}/{path.name}"
            ),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "size": path.stat().st_size,
        }
    manifest.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return windows, linux, manifest


def _publish(client: FakeReleaseClient, tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    windows, linux, manifest = _publication_files(tmp_path)
    recovery_state = tmp_path / "manifest-switch-recovery.json"
    publisher.publish_development_update(
        client,
        repository=REPOSITORY,
        release_tag=RELEASE_TAG,
        revision=REVISION,
        channel="development",
        windows_path=windows,
        linux_path=linux,
        manifest_path=manifest,
        title=f"E3 Development {REVISION}",
        notes=f"Automated build from main at {REVISION}.",
        recovery_state=recovery_state,
    )
    return windows, linux, manifest, recovery_state


def _stable_asset(client: FakeReleaseClient) -> Any:
    matches = [asset for asset in client.assets.values() if asset.name == "update-manifest.json"]
    assert len(matches) == 1
    return matches[0]


def test_publish_uploads_and_verifies_immutable_assets_before_manifest_switch(
    tmp_path: Path,
) -> None:
    client = FakeReleaseClient()

    windows, linux, manifest, recovery_state = _publish(client, tmp_path)

    uploads = [event for event in client.events if event[0] == "upload"]
    assert uploads == [
        ("upload", windows.name),
        ("upload", linux.name),
        ("upload", manifest.name),
    ]
    first_rename = next(index for index, event in enumerate(client.events) if event[0] == "rename")
    assert all(event[0] == "upload" for event in client.events[:first_rename])
    assert client.events[first_rename][2:] == (
        "update-manifest.json",
        "update-manifest.previous-3.json",
    )
    assert client.events[first_rename + 1][2:] == (
        manifest.name,
        "update-manifest.json",
    )
    stable = _stable_asset(client)
    assert stable.digest == f"sha256:{hashlib.sha256(manifest.read_bytes()).hexdigest()}"
    assert {asset.name for asset in client.assets.values()} >= {
        "E3-Setup.exe",
        "E3-x86_64.AppImage",
        windows.name,
        linux.name,
        "update-manifest.json",
    }
    assert client.current_revision == REVISION
    assert client.release is not None and client.release.prerelease is True
    assert client.release_metadata == (
        REVISION,
        f"E3 Development {REVISION}",
        f"Automated build from main at {REVISION}.",
        False,
    )
    assert not recovery_state.exists()


def test_failed_binary_upload_leaves_old_manifest_and_assets_live(tmp_path: Path) -> None:
    client = FakeReleaseClient()
    _windows_name, linux_name = publisher.immutable_asset_names(REVISION)
    client.fail_upload_name = linux_name

    with pytest.raises(publisher.PublicationError, match="simulated upload failure"):
        _publish(client, tmp_path)

    assert _stable_asset(client).asset_id == 3
    assert {asset.name for asset in client.assets.values()} >= {
        "E3-Setup.exe",
        "E3-x86_64.AppImage",
    }
    assert not any(event[0] == "rename" for event in client.events)


def test_remote_hash_mismatch_blocks_manifest_publication(tmp_path: Path) -> None:
    client = FakeReleaseClient()
    _windows_name, linux_name = publisher.immutable_asset_names(REVISION)
    client.wrong_digest_name = linux_name

    with pytest.raises(publisher.PublicationError, match="wrong SHA-256"):
        _publish(client, tmp_path)

    assert _stable_asset(client).asset_id == 3
    assert not any(event[0] == "rename" for event in client.events)


def test_failed_manifest_switch_rolls_back_old_manifest(tmp_path: Path) -> None:
    client = FakeReleaseClient()
    client.fail_manifest_switch = True

    with pytest.raises(publisher.PublicationError, match="authoritative rename failure"):
        _publish(client, tmp_path)

    assert _stable_asset(client).asset_id == 3
    assert [event[3] for event in client.events if event[0] == "rename"] == [
        "update-manifest.previous-3.json",
        "update-manifest.json",
        "update-manifest.json",
    ]


def test_recovery_restores_old_manifest_after_interrupted_switch(tmp_path: Path) -> None:
    client = FakeReleaseClient()
    _windows, _linux, manifest = _publication_files(tmp_path)
    staged = client._add_asset(manifest.name, manifest.read_bytes())
    client.rename_asset(3, "update-manifest.previous-3.json")
    recovery_state = tmp_path / "manifest-switch-recovery.json"
    recovery_state.write_text(
        json.dumps(
            {
                "repository": REPOSITORY,
                "release_tag": RELEASE_TAG,
                "release_id": 1,
                "old_asset_id": 3,
                "new_asset_id": staged.asset_id,
                "backup_name": "update-manifest.previous-3.json",
            }
        ),
        encoding="utf-8",
    )

    publisher.recover_manifest(
        client,
        repository=REPOSITORY,
        release_tag=RELEASE_TAG,
        recovery_state=recovery_state,
    )

    assert _stable_asset(client).asset_id == 3
    assert not recovery_state.exists()


def test_cleanup_failure_does_not_roll_back_published_manifest(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = FakeReleaseClient()
    client.fail_cleanup = True

    _windows, _linux, manifest, recovery_state = _publish(client, tmp_path)

    stable = _stable_asset(client)
    assert stable.digest == f"sha256:{hashlib.sha256(manifest.read_bytes()).hexdigest()}"
    assert "old manifest cleanup failed" in capsys.readouterr().err
    assert not recovery_state.exists()


def test_first_release_stays_draft_until_complete_manifest_is_ready(tmp_path: Path) -> None:
    client = FakeReleaseClient(release_exists=False)

    windows, linux, manifest, _recovery_state = _publish(client, tmp_path)

    uploads = [event for event in client.events if event[0] == "upload"]
    assert uploads == [
        ("upload", windows.name),
        ("upload", linux.name),
        ("upload", manifest.name),
    ]
    publish_event = next(event for event in client.events if event[0] == "update_release")
    assert publish_event == ("update_release", REVISION, False)
    assert _stable_asset(client).digest == (
        f"sha256:{hashlib.sha256(manifest.read_bytes()).hexdigest()}"
    )
    assert client.release is not None
    assert client.release.draft is False
    assert client.release.prerelease is True
