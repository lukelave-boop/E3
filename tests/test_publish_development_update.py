from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
        self.fail_label = False
        self.current_revision: str | None = None
        self.release_metadata: tuple[str, str, str, bool] | None = None
        if release_exists:
            self._add_asset("E3-Setup.exe", b"old windows", asset_id=1)
            self._add_asset("E3-x86_64.AppImage", b"old linux", asset_id=2)
            self._add_asset("update-manifest.json", b'{"old": true}\n', asset_id=3)

    def _add_asset(
        self,
        name: str,
        content: bytes,
        *,
        asset_id: int | None = None,
        created_at: str = "2020-01-01T00:00:00Z",
        label: str = "",
    ) -> Any:
        if asset_id is None:
            asset_id = self.next_asset_id
            self.next_asset_id += 1
        asset = publisher.ReleaseAsset(
            asset_id=asset_id,
            name=name,
            size=len(content),
            digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
            state="uploaded",
            created_at=created_at,
            label=label,
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
            self.assets[asset.asset_id] = replace(asset, digest=f"sha256:{'0' * 64}")

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
        self.assets[asset_id] = replace(current, name=name)

    def label_asset(self, asset_id: int, label: str) -> None:
        self.events.append(("label", asset_id, label))
        if self.fail_label:
            raise publisher.PublicationError("simulated label failure")
        self.assets[asset_id] = replace(self.assets[asset_id], label=label)

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


RETENTION_NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


def _revision(number: int) -> str:
    return f"{number:012x}" + "0" * 28


def _retired_label(asset: Any, when: datetime) -> str:
    return f"{asset.name} [E3 retired {when.isoformat()}]"


def _add_pair(
    client: FakeReleaseClient,
    revision: str,
    *,
    days_old: int,
    retired_days: int | None = None,
) -> list[Any]:
    members = []
    for name in publisher.immutable_asset_names(revision):
        asset = client._add_asset(
            name,
            b"package",
            created_at=(RETENTION_NOW - timedelta(days=days_old)).isoformat(),
        )
        if retired_days is not None:
            asset = replace(
                asset,
                label=_retired_label(asset, RETENTION_NOW - timedelta(days=retired_days)),
            )
            client.assets[asset.asset_id] = asset
        members.append(asset)
    return members


def _retention_client() -> tuple[FakeReleaseClient, list[Any]]:
    client = FakeReleaseClient()
    del client.assets[1]
    del client.assets[2]
    _add_pair(client, REVISION, days_old=60)
    _add_pair(client, _revision(1), days_old=2)
    _add_pair(client, _revision(2), days_old=1)
    old = _add_pair(client, _revision(3), days_old=40)
    return client, old


def _plan(client: FakeReleaseClient, *, now: datetime = RETENTION_NOW) -> list[Any]:
    return publisher.plan_asset_retention(
        list(client.assets.values()), revision=REVISION, now=now,
    )


@pytest.fixture
def fixed_retention_time(monkeypatch: pytest.MonkeyPatch) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> datetime:
            return RETENTION_NOW.astimezone(tz) if tz is not None else RETENTION_NOW.replace(
                tzinfo=None,
            )

    monkeypatch.setattr(publisher, "datetime", FrozenDatetime)


def test_first_retention_marks_21_of_24_pairs_without_deleting_any() -> None:
    client = FakeReleaseClient()
    del client.assets[1]
    del client.assets[2]
    current = _add_pair(client, REVISION, days_old=90)
    pairs = {
        number: _add_pair(client, _revision(number), days_old=number)
        for number in range(1, 24)
    }
    before = dict(client.assets)

    actions = _plan(client)

    assert len(actions) == 42
    assert {action.asset.asset_id for action in actions} == {
        asset.asset_id for number, pair in pairs.items() if number > 2 for asset in pair
    }
    assert all(action.label == _retired_label(action.asset, RETENTION_NOW) for action in actions)
    assert not {asset.asset_id for asset in current} & {a.asset.asset_id for a in actions}
    assert client.assets == before


def test_recent_pair_order_uses_later_member_creation_and_not_asset_order() -> None:
    client, old = _retention_client()
    client.assets[old[1].asset_id] = replace(
        old[1], created_at=(RETENTION_NOW - timedelta(hours=1)).isoformat(),
    )
    client.assets = dict(reversed(list(client.assets.items())))

    assert {action.asset.name for action in _plan(client)} == set(
        publisher.immutable_asset_names(_revision(1)),
    )


@pytest.mark.parametrize(
    ("elapsed", "delete"),
    [(timedelta(days=7, seconds=-1), False), (timedelta(days=7), True)],
)
def test_retention_requires_full_seven_days(elapsed: timedelta, delete: bool) -> None:
    client, old = _retention_client()
    for asset in old:
        client.assets[asset.asset_id] = replace(
            asset, label=_retired_label(asset, RETENTION_NOW - elapsed),
        )

    actions = _plan(client)

    assert [action.asset.asset_id for action in actions] == (
        [asset.asset_id for asset in old] if delete else []
    )
    assert all(action.label is None for action in actions)


def test_retention_waits_for_latest_pair_marker() -> None:
    client, old = _retention_client()
    for asset, age in zip(old, (30, 6), strict=True):
        client.assets[asset.asset_id] = replace(
            asset, label=_retired_label(asset, RETENTION_NOW - timedelta(days=age)),
        )

    assert _plan(client) == []
    assert len(_plan(client, now=RETENTION_NOW + timedelta(days=1))) == 2


def test_half_marked_pair_restarts_grace_for_both_members() -> None:
    client, old = _retention_client()
    client.assets[old[0].asset_id] = replace(
        old[0], label=_retired_label(old[0], RETENTION_NOW - timedelta(days=30)),
    )

    actions = _plan(client)

    assert len(actions) == 2
    assert all(action.label == _retired_label(action.asset, RETENTION_NOW) for action in actions)


def test_protected_pairs_clear_old_retirement_markers() -> None:
    client, old = _retention_client()
    protected_names = {
        name for revision in (REVISION, _revision(1), _revision(2))
        for name in publisher.immutable_asset_names(revision)
    }
    for asset in list(client.assets.values()):
        if asset.name in protected_names:
            client.assets[asset.asset_id] = replace(
                asset, label=_retired_label(asset, RETENTION_NOW - timedelta(days=30)),
            )

    actions = _plan(client)

    assert {action.asset.name for action in actions if action.label == ""} == protected_names
    assert {action.asset.asset_id for action in actions if action.label} == {
        asset.asset_id for asset in old
    }
    assert not any(action.label is None for action in actions)


@pytest.mark.parametrize(
    "changes",
    [
        {"created_at": ""},
        {"created_at": "invalid"},
        {"created_at": "2026-08-01T00:00:00"},
        {"created_at": "2099-01-01T00:00:00Z"},
        {"state": "starter"},
        {"size": 0},
        {"label": "Operator rollback build"},
        {"label": "E3 retired 2020-01-01T00:00:00Z"},
    ],
)
def test_retention_preserves_pair_with_unknown_or_incomplete_metadata(
    changes: dict[str, Any],
) -> None:
    client, old = _retention_client()
    client.assets[old[0].asset_id] = replace(old[0], **changes)

    assert _plan(client) == []


@pytest.mark.parametrize("stamp", [
    "invalid", "2026-01-01T00:00:00", "2099-01-01T00:00:00Z",
    "0001-01-01T00:00:00+01:00", "9999-12-31T23:59:59-01:00",
])
def test_bad_or_future_retirement_marker_never_authorizes_deletion(stamp: str) -> None:
    client, old = _retention_client()
    for asset in old:
        client.assets[asset.asset_id] = replace(
            asset, label=f"{asset.name} [E3 retired {stamp}]",
        )

    assert _plan(client) == []


def test_duplicate_package_name_makes_its_group_ineligible() -> None:
    client, old = _retention_client()
    client._add_asset(old[0].name, b"duplicate", created_at=old[0].created_at)

    assert _plan(client) == []


def test_unmarked_incomplete_pair_is_preserved() -> None:
    client, old = _retention_client()
    del client.assets[old[1].asset_id]

    assert _plan(client) == []


def test_expired_marked_survivor_finishes_interrupted_pair_deletion() -> None:
    client, old = _retention_client()
    del client.assets[old[1].asset_id]
    survivor = replace(
        old[0], label=_retired_label(old[0], RETENTION_NOW - timedelta(days=8)),
    )
    client.assets[survivor.asset_id] = survivor

    actions = _plan(client)

    assert len(actions) == 1
    assert actions[0].asset == survivor
    assert actions[0].label is None


def test_legacy_package_pair_receives_same_grace() -> None:
    client, old = _retention_client()
    for asset in old:
        del client.assets[asset.asset_id]
    legacy = [
        client._add_asset(name, b"old legacy")
        for name in ("E3-Setup.exe", "E3-x86_64.AppImage")
    ]

    actions = _plan(client)

    assert {action.asset.asset_id for action in actions} == {asset.asset_id for asset in legacy}
    assert all(action.label == _retired_label(action.asset, RETENTION_NOW) for action in actions)


def test_unrelated_assets_and_staged_manifests_are_never_retention_targets() -> None:
    client, old = _retention_client()
    for name in (
        "E3-Setup-abcdef.exe",
        "E3-Setup-ABCDEF123456.exe",
        "E3-Setup-abcdef123456.exe.bak",
        "operator-notes.txt",
        "update-manifest.previous-123.json",
        "update-manifest-abcdef123456-123-1.json",
    ):
        client._add_asset(name, b"retained")

    assert {action.asset.asset_id for action in _plan(client)} == {
        asset.asset_id for asset in old
    }


def test_retention_rejects_naive_clock() -> None:
    client, _old = _retention_client()

    with pytest.raises(publisher.PublicationError, match="timezone-aware"):
        _plan(client, now=RETENTION_NOW.replace(tzinfo=None))


@pytest.mark.parametrize("fail", [False, True])
def test_republication_clears_current_markers_before_manifest_switch(
    tmp_path: Path, fail: bool,
) -> None:
    client = FakeReleaseClient()
    windows, linux, _manifest = _publication_files(tmp_path)
    current = []
    for path in (windows, linux):
        asset = client._add_asset(path.name, path.read_bytes())
        client.assets[asset.asset_id] = replace(
            asset, label=_retired_label(asset, RETENTION_NOW - timedelta(days=30)),
        )
        current.append(asset)
    client.fail_label = fail

    if fail:
        with pytest.raises(publisher.PublicationError, match="simulated label failure"):
            _publish(client, tmp_path)
        assert _stable_asset(client).asset_id == 3
        assert not any(event[0] in {"rename", "update_tag", "update_release"} for event in client.events)
    else:
        _publish(client, tmp_path)
        first_rename = next(i for i, event in enumerate(client.events) if event[0] == "rename")
        assert [event for event in client.events[:first_rename] if event[0] == "label"] == [
            ("label", asset.asset_id, "") for asset in current
        ]
        assert all(client.assets[asset.asset_id].label == "" for asset in current)


def test_retention_label_failure_is_best_effort_after_publication(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], fixed_retention_time: None,
) -> None:
    client, _old = _retention_client()
    for name in publisher.immutable_asset_names(REVISION):
        matching = next(asset for asset in client.assets.values() if asset.name == name)
        del client.assets[matching.asset_id]
    client.fail_label = True

    _windows, _linux, manifest, recovery_state = _publish(client, tmp_path)

    assert _stable_asset(client).digest == f"sha256:{hashlib.sha256(manifest.read_bytes()).hexdigest()}"
    assert client.current_revision == REVISION
    assert not recovery_state.exists()
    assert "package retention deferred" in capsys.readouterr().err
    label_index = next(i for i, event in enumerate(client.events) if event[0] == "label")
    assert any(event[0] == "update_release" for event in client.events[:label_index])


@pytest.mark.parametrize("change", ["stable_digest", "stable_id", "stable_missing", "candidate_label"])
def test_retention_rechecks_manifest_and_candidate_before_mutation(
    monkeypatch: pytest.MonkeyPatch, fixed_retention_time: None, change: str,
) -> None:
    client, old = _retention_client()
    stable = _stable_asset(client)
    original_list = client.list_assets
    calls = 0

    def changed_list(release_id: int) -> list[Any]:
        nonlocal calls
        calls += 1
        if calls == 2:
            if change == "stable_digest":
                client.assets[stable.asset_id] = replace(stable, digest="sha256:" + "0" * 64)
            elif change == "stable_id":
                del client.assets[stable.asset_id]
                client.assets[99] = replace(stable, asset_id=99)
            elif change == "stable_missing":
                del client.assets[stable.asset_id]
            else:
                client.assets[old[0].asset_id] = replace(old[0], label="Operator changed label")
        return original_list(release_id)

    monkeypatch.setattr(client, "list_assets", changed_list)

    with pytest.raises(publisher.PublicationError, match="Retention stopped"):
        publisher.retain_release_assets(
            client, release=client.release, published=stable, revision=REVISION,
        )

    assert client.events == []


def test_recovery_backup_blocks_retention(fixed_retention_time: None) -> None:
    client, _old = _retention_client()
    client._add_asset("update-manifest.previous-123.json", b"backup")

    with pytest.raises(publisher.PublicationError, match="recovery backup remains"):
        publisher.retain_release_assets(
            client, release=client.release, published=_stable_asset(client), revision=REVISION,
        )

    assert client.events == []


def test_abandoned_staged_manifest_is_preserved_without_blocking_retention(
    fixed_retention_time: None,
) -> None:
    client, old = _retention_client()
    staged = client._add_asset("update-manifest-abcdef123456-123-1.json", b"abandoned staged")

    publisher.retain_release_assets(
        client, release=client.release, published=_stable_asset(client), revision=REVISION,
    )

    assert client.assets[staged.asset_id] == staged
    assert client.events == [
        ("label", asset.asset_id, _retired_label(asset, RETENTION_NOW)) for asset in old
    ]


def test_retention_can_retry_after_deleting_only_first_member(
    monkeypatch: pytest.MonkeyPatch, fixed_retention_time: None,
) -> None:
    client, old = _retention_client()
    for asset in old:
        client.assets[asset.asset_id] = replace(
            asset, label=_retired_label(asset, RETENTION_NOW - timedelta(days=8)),
        )
    original_delete = client.delete_asset

    def fail_second_delete(asset_id: int) -> None:
        if asset_id == old[1].asset_id:
            raise publisher.PublicationError("simulated second deletion failure")
        original_delete(asset_id)

    monkeypatch.setattr(client, "delete_asset", fail_second_delete)
    with pytest.raises(publisher.PublicationError, match="second deletion failure"):
        publisher.retain_release_assets(
            client, release=client.release, published=_stable_asset(client), revision=REVISION,
        )
    assert old[0].asset_id not in client.assets
    assert old[1].asset_id in client.assets
    monkeypatch.setattr(client, "delete_asset", original_delete)

    publisher.retain_release_assets(
        client, release=client.release, published=_stable_asset(client), revision=REVISION,
    )

    assert old[1].asset_id not in client.assets


def test_release_client_paginates_and_preserves_retention_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = publisher.GhReleaseClient(REPOSITORY)
    entries = [
        {
            "id": index,
            "name": f"asset-{index}",
            "size": 10,
            "digest": "sha256:" + "a" * 64,
            "state": "uploaded",
            "created_at": "2026-08-01T00:00:00Z",
            "label": None if index != 101 else "retirement metadata",
        }
        for index in range(1, 102)
    ]
    calls = []

    def run(arguments: Any, **kwargs: Any) -> Any:
        calls.append((tuple(arguments), kwargs))
        return subprocess.CompletedProcess(arguments, 0, json.dumps([entries[:100], entries[100:]]))

    monkeypatch.setattr(client, "_run", run)

    assets = client.list_assets(1)

    assert len(assets) == 101
    assert assets[0].label == ""
    assert assets[-1].asset_id == 101
    assert assets[-1].label == "retirement metadata"
    assert assets[-1].created_at == "2026-08-01T00:00:00Z"
    assert calls[0][0] == (
        "api", "--paginate", "--slurp",
        f"repos/{REPOSITORY}/releases/1/assets?per_page=100",
    )


@pytest.mark.parametrize("payload", [[], [[]]])
def test_release_client_accepts_empty_asset_pages(
    monkeypatch: pytest.MonkeyPatch, payload: Any,
) -> None:
    client = publisher.GhReleaseClient(REPOSITORY)
    monkeypatch.setattr(client, "_json", lambda _arguments: payload)

    assert client.list_assets(1) == []


@pytest.mark.parametrize("payload", [{"assets": []}, [[], {"invalid": "page"}]])
def test_release_client_rejects_malformed_asset_pages(
    monkeypatch: pytest.MonkeyPatch, payload: Any,
) -> None:
    client = publisher.GhReleaseClient(REPOSITORY)
    monkeypatch.setattr(client, "_json", lambda _arguments: payload)

    with pytest.raises(publisher.PublicationError, match="invalid release asset"):
        client.list_assets(1)


def test_release_client_labels_without_renaming_or_reuploading(monkeypatch: pytest.MonkeyPatch) -> None:
    client = publisher.GhReleaseClient(REPOSITORY)
    calls = []

    def run(arguments: Any, **kwargs: Any) -> Any:
        calls.append((tuple(arguments), kwargs))
        return subprocess.CompletedProcess(arguments, 0, "{}")

    monkeypatch.setattr(client, "_run", run)

    client.label_asset(123, "package.exe [E3 retired 2026-09-06T12:00:00+00:00]")

    assert len(calls) == 1
    assert calls[0][0] == (
        "api", "--method", "PATCH", f"repos/{REPOSITORY}/releases/assets/123", "--input", "-",
    )
    assert json.loads(calls[0][1]["input_text"]) == {
        "label": "package.exe [E3 retired 2026-09-06T12:00:00+00:00]",
    }
