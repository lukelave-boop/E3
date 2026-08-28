from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import FrameType
from typing import Any, Protocol

_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_STABLE_MANIFEST_NAME = "update-manifest.json"


class PublicationError(RuntimeError):
    """Raised when a development update cannot be published safely."""


class PublicationCancelled(PublicationError):
    """Raised after a deferred cancellation reaches a safe manifest state."""


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    asset_id: int
    name: str
    size: int
    digest: str
    state: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> ReleaseAsset:
        digest = value.get("digest")
        return cls(
            asset_id=int(value["id"]),
            name=str(value["name"]),
            size=int(value["size"]),
            digest=str(digest) if digest is not None else "",
            state=str(value["state"]),
        )


@dataclass(frozen=True, slots=True)
class Release:
    release_id: int
    draft: bool
    prerelease: bool

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> Release:
        return cls(
            release_id=int(value["id"]),
            draft=bool(value["draft"]),
            prerelease=bool(value["prerelease"]),
        )


class ReleaseClient(Protocol):
    def get_release(self, tag: str) -> Release | None: ...

    def create_draft_release(
        self,
        tag: str,
        revision: str,
        title: str,
        notes: str,
    ) -> Release: ...

    def list_assets(self, release_id: int) -> list[ReleaseAsset]: ...

    def upload_asset(self, tag: str, path: Path) -> None: ...

    def rename_asset(self, asset_id: int, name: str) -> None: ...

    def delete_asset(self, asset_id: int) -> None: ...

    def update_tag(self, tag: str, revision: str) -> None: ...

    def update_release(
        self,
        release_id: int,
        revision: str,
        title: str,
        notes: str,
        *,
        draft: bool,
    ) -> None: ...


class GhReleaseClient:
    def __init__(self, repository: str) -> None:
        _validate_repository(repository)
        self.repository = repository

    def _run(
        self,
        arguments: Sequence[str],
        *,
        input_text: str | None = None,
        check: bool = True,
        timeout: float | None = 60.0,
    ) -> subprocess.CompletedProcess[str]:
        try:
            result = subprocess.run(
                ["gh", *arguments],
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise PublicationError(f"Could not run GitHub CLI: {exc}") from exc
        if check and result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise PublicationError(
                f"GitHub CLI failed ({' '.join(arguments)}): {detail}"
            )
        return result

    def _json(
        self,
        arguments: Sequence[str],
        *,
        input_value: Mapping[str, Any] | None = None,
    ) -> Any:
        command = list(arguments)
        input_text = None
        if input_value is not None:
            command.extend(("--input", "-"))
            input_text = json.dumps(input_value)
        result = self._run(command, input_text=input_text)
        try:
            return json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise PublicationError("GitHub CLI returned invalid JSON") from exc

    def get_release(self, tag: str) -> Release | None:
        _validate_tag(tag)
        result = self._run(
            ("api", f"repos/{self.repository}/releases/tags/{tag}"),
            check=False,
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            if "HTTP 404" in detail or "Not Found" in detail:
                return None
            raise PublicationError(f"Could not inspect development release: {detail}")
        try:
            value = json.loads(result.stdout)
        except ValueError as exc:
            raise PublicationError("GitHub CLI returned invalid release JSON") from exc
        return Release.from_mapping(value)

    def create_draft_release(
        self,
        tag: str,
        revision: str,
        title: str,
        notes: str,
    ) -> Release:
        value = self._json(
            ("api", "--method", "POST", f"repos/{self.repository}/releases"),
            input_value={
                "tag_name": tag,
                "target_commitish": revision,
                "name": title,
                "body": notes,
                "draft": True,
                "prerelease": True,
            },
        )
        return Release.from_mapping(value)

    def list_assets(self, release_id: int) -> list[ReleaseAsset]:
        value = self._json(
            (
                "api",
                "--paginate",
                "--slurp",
                f"repos/{self.repository}/releases/{release_id}/assets?per_page=100",
            )
        )
        if not isinstance(value, list):
            raise PublicationError("GitHub returned an invalid release asset list")
        pages = value if value and isinstance(value[0], list) else [value]
        assets: list[ReleaseAsset] = []
        for page in pages:
            if not isinstance(page, list):
                raise PublicationError("GitHub returned an invalid release asset page")
            assets.extend(ReleaseAsset.from_mapping(item) for item in page)
        return assets

    def upload_asset(self, tag: str, path: Path) -> None:
        self._run(
            (
                "release",
                "upload",
                tag,
                os.fspath(path),
                "--repo",
                self.repository,
            ),
            timeout=None,
        )

    def rename_asset(self, asset_id: int, name: str) -> None:
        self._json(
            (
                "api",
                "--method",
                "PATCH",
                f"repos/{self.repository}/releases/assets/{asset_id}",
            ),
            input_value={"name": name},
        )

    def delete_asset(self, asset_id: int) -> None:
        self._run(
            (
                "api",
                "--method",
                "DELETE",
                f"repos/{self.repository}/releases/assets/{asset_id}",
            )
        )

    def update_tag(self, tag: str, revision: str) -> None:
        self._json(
            (
                "api",
                "--method",
                "PATCH",
                f"repos/{self.repository}/git/refs/tags/{tag}",
            ),
            input_value={"sha": revision, "force": True},
        )

    def update_release(
        self,
        release_id: int,
        revision: str,
        title: str,
        notes: str,
        *,
        draft: bool,
    ) -> None:
        self._json(
            (
                "api",
                "--method",
                "PATCH",
                f"repos/{self.repository}/releases/{release_id}",
            ),
            input_value={
                "target_commitish": revision,
                "name": title,
                "body": notes,
                "draft": draft,
                "prerelease": True,
                "make_latest": "false",
            },
        )


class _DeferredSignals:
    def __init__(self) -> None:
        self._previous: dict[int, Any] = {}
        self._pending: list[int] = []

    def __enter__(self) -> _DeferredSignals:
        for candidate in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
            if candidate is None:
                continue
            signum = int(candidate)
            try:
                self._previous[signum] = signal.getsignal(candidate)
                signal.signal(candidate, self._handle)
            except (OSError, RuntimeError, ValueError):
                self._previous.pop(signum, None)
        return self

    def _handle(self, signum: int, _frame: FrameType | None) -> None:
        self._pending.append(signum)

    def __exit__(self, exc_type: Any, _exc: Any, _traceback: Any) -> bool:
        for signum, handler in self._previous.items():
            signal.signal(signum, handler)
        if exc_type is None and self._pending:
            raise PublicationCancelled(
                "Publication was cancelled after the manifest reached a usable state"
            )
        return False


def _validate_repository(repository: str) -> None:
    if _REPOSITORY_RE.fullmatch(repository) is None:
        raise PublicationError(f"Invalid GitHub repository name: {repository!r}")


def _validate_tag(tag: str) -> None:
    if _TAG_RE.fullmatch(tag) is None:
        raise PublicationError(f"Invalid development release tag: {tag!r}")


def _validate_revision(revision: str) -> str:
    normalized = revision.strip().lower()
    if _REVISION_RE.fullmatch(normalized) is None:
        raise PublicationError("Revision must be a full 40-character Git SHA")
    return normalized


def immutable_asset_names(revision: str) -> tuple[str, str]:
    short_revision = _validate_revision(revision)[:12]
    return (
        f"E3-Setup-{short_revision}.exe",
        f"E3-{short_revision}-x86_64.AppImage",
    )


def staged_manifest_name(revision: str, publication_id: str) -> str:
    short_revision = _validate_revision(revision)[:12]
    if re.fullmatch(r"[A-Za-z0-9.-]+", publication_id) is None:
        raise PublicationError("Manifest publication ID contains unsupported characters")
    return f"update-manifest-{short_revision}-{publication_id}.json"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1_048_576):
            digest.update(chunk)
    return digest.hexdigest()


def _load_manifest(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PublicationError(f"Could not read generated update manifest: {exc}") from exc
    if not isinstance(value, Mapping):
        raise PublicationError("Generated update manifest root must be an object")
    return value


def validate_publication_inputs(
    *,
    repository: str,
    release_tag: str,
    revision: str,
    channel: str,
    windows_path: Path,
    linux_path: Path,
    manifest_path: Path,
) -> None:
    _validate_repository(repository)
    _validate_tag(release_tag)
    revision = _validate_revision(revision)
    windows_name, linux_name = immutable_asset_names(revision)
    expected_paths = (
        (windows_path, windows_name),
        (linux_path, linux_name),
    )
    for path, expected_name in expected_paths:
        if path.name != expected_name:
            raise PublicationError(
                f"Expected immutable asset name {expected_name!r}, got {path.name!r}"
            )
        if not path.is_file() or path.stat().st_size <= 0:
            raise PublicationError(f"Publication asset is missing or empty: {path}")
    manifest_pattern = rf"update-manifest-{revision[:12]}-[A-Za-z0-9.-]+\.json"
    if re.fullmatch(manifest_pattern, manifest_path.name) is None:
        raise PublicationError(
            f"Staged manifest name is not revision-specific: {manifest_path.name!r}"
        )
    if not manifest_path.is_file() or manifest_path.stat().st_size <= 0:
        raise PublicationError(f"Publication asset is missing or empty: {manifest_path}")

    manifest = _load_manifest(manifest_path)
    if manifest.get("schema_version") != 1:
        raise PublicationError("Generated update manifest must use schema 1")
    if manifest.get("revision") != revision:
        raise PublicationError("Generated update manifest revision does not match the build")
    if manifest.get("channel") != channel:
        raise PublicationError("Generated update manifest channel does not match the build")
    if not isinstance(manifest.get("version"), str) or not manifest["version"].strip():
        raise PublicationError("Generated update manifest version is missing")
    if not isinstance(manifest.get("published_at"), str) or not manifest["published_at"].strip():
        raise PublicationError("Generated update manifest publication time is missing")
    assets = manifest.get("assets")
    if not isinstance(assets, Mapping) or set(assets) != {"windows-x86_64", "linux-x86_64"}:
        raise PublicationError("Generated update manifest must contain Windows and Linux assets")

    for platform_key, path in (
        ("windows-x86_64", windows_path),
        ("linux-x86_64", linux_path),
    ):
        value = assets[platform_key]
        if not isinstance(value, Mapping):
            raise PublicationError(f"Manifest asset {platform_key!r} must be an object")
        expected_url = (
            f"https://github.com/{repository}/releases/download/{release_tag}/{path.name}"
        )
        expected = {
            "name": path.name,
            "url": expected_url,
            "sha256": _sha256(path),
            "size": path.stat().st_size,
        }
        for field, expected_value in expected.items():
            if value.get(field) != expected_value:
                raise PublicationError(
                    f"Manifest {platform_key} {field} does not match {path.name}"
                )


def _asset_by_name(assets: Sequence[ReleaseAsset], name: str) -> ReleaseAsset | None:
    matches = [asset for asset in assets if asset.name == name]
    if len(matches) > 1:
        raise PublicationError(f"Release contains duplicate assets named {name!r}")
    return matches[0] if matches else None


def _verify_asset(asset: ReleaseAsset, path: Path) -> None:
    expected_digest = f"sha256:{_sha256(path)}"
    if asset.state != "uploaded":
        raise PublicationError(f"Release asset {asset.name!r} is not fully uploaded")
    if asset.size != path.stat().st_size:
        raise PublicationError(f"Release asset {asset.name!r} has the wrong size")
    if asset.digest != expected_digest:
        raise PublicationError(f"Release asset {asset.name!r} has the wrong SHA-256")


def _ensure_uploaded(
    client: ReleaseClient,
    release: Release,
    tag: str,
    path: Path,
) -> ReleaseAsset:
    assets = client.list_assets(release.release_id)
    asset = _asset_by_name(assets, path.name)
    if asset is None:
        client.upload_asset(tag, path)
        assets = client.list_assets(release.release_id)
        asset = _asset_by_name(assets, path.name)
    if asset is None:
        raise PublicationError(f"Uploaded release asset {path.name!r} was not found")
    _verify_asset(asset, path)
    return asset


def _write_recovery_state(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def recover_manifest(
    client: ReleaseClient,
    *,
    repository: str,
    release_tag: str,
    recovery_state: Path,
) -> None:
    if not recovery_state.exists():
        print("No interrupted manifest switch needs recovery.")
        return
    try:
        state = json.loads(recovery_state.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise PublicationError(f"Could not read manifest recovery state: {exc}") from exc
    if not isinstance(state, Mapping):
        raise PublicationError("Manifest recovery state must be an object")
    if state.get("repository") != repository or state.get("release_tag") != release_tag:
        raise PublicationError("Manifest recovery state does not match this release")

    release = client.get_release(release_tag)
    if release is None:
        raise PublicationError("Cannot recover a manifest for a missing development release")
    assets = client.list_assets(release.release_id)
    if _asset_by_name(assets, _STABLE_MANIFEST_NAME) is not None:
        recovery_state.unlink(missing_ok=True)
        print("The stable manifest is already present; no recovery was required.")
        return

    old_asset_id = int(state["old_asset_id"])
    new_asset_id = int(state["new_asset_id"])
    old_asset = next((asset for asset in assets if asset.asset_id == old_asset_id), None)
    new_asset = next((asset for asset in assets if asset.asset_id == new_asset_id), None)
    candidate = old_asset or new_asset
    if candidate is None:
        raise PublicationError("No verified manifest asset is available for recovery")
    client.rename_asset(candidate.asset_id, _STABLE_MANIFEST_NAME)
    restored = _asset_by_name(client.list_assets(release.release_id), _STABLE_MANIFEST_NAME)
    if restored is None or restored.asset_id != candidate.asset_id:
        raise PublicationError("Manifest recovery did not restore the stable asset name")
    recovery_state.unlink(missing_ok=True)
    print(f"Recovered {_STABLE_MANIFEST_NAME} from asset {candidate.asset_id}.")


def publish_development_update(
    client: ReleaseClient,
    *,
    repository: str,
    release_tag: str,
    revision: str,
    channel: str,
    windows_path: Path,
    linux_path: Path,
    manifest_path: Path,
    title: str,
    notes: str,
    recovery_state: Path,
) -> None:
    revision = _validate_revision(revision)
    validate_publication_inputs(
        repository=repository,
        release_tag=release_tag,
        revision=revision,
        channel=channel,
        windows_path=windows_path,
        linux_path=linux_path,
        manifest_path=manifest_path,
    )
    if recovery_state.exists():
        recover_manifest(
            client,
            repository=repository,
            release_tag=release_tag,
            recovery_state=recovery_state,
        )

    release = client.get_release(release_tag)
    created_draft = release is None
    if release is None:
        release = client.create_draft_release(release_tag, revision, title, notes)
    elif not release.draft and not release.prerelease:
        raise PublicationError("The existing development release is not a prerelease")

    _ensure_uploaded(client, release, release_tag, windows_path)
    _ensure_uploaded(client, release, release_tag, linux_path)
    staged_manifest = _ensure_uploaded(client, release, release_tag, manifest_path)

    assets = client.list_assets(release.release_id)
    active_manifest = _asset_by_name(assets, _STABLE_MANIFEST_NAME)
    backup_manifest: ReleaseAsset | None = None
    if created_draft or release.draft:
        if active_manifest is not None:
            raise PublicationError("A draft development release already has a stable manifest")
        client.rename_asset(staged_manifest.asset_id, _STABLE_MANIFEST_NAME)
        with _DeferredSignals():
            client.update_release(
                release.release_id,
                revision,
                title,
                notes,
                draft=False,
            )
    else:
        if active_manifest is None:
            raise PublicationError("The live development release has no stable manifest")
        backup_name = f"update-manifest.previous-{active_manifest.asset_id}.json"
        _write_recovery_state(
            recovery_state,
            {
                "repository": repository,
                "release_tag": release_tag,
                "release_id": release.release_id,
                "old_asset_id": active_manifest.asset_id,
                "new_asset_id": staged_manifest.asset_id,
                "backup_name": backup_name,
            },
        )
        with _DeferredSignals():
            client.rename_asset(active_manifest.asset_id, backup_name)
            try:
                client.rename_asset(staged_manifest.asset_id, _STABLE_MANIFEST_NAME)
            except PublicationError as switch_error:
                try:
                    client.rename_asset(active_manifest.asset_id, _STABLE_MANIFEST_NAME)
                except PublicationError as rollback_error:
                    raise PublicationError(
                        f"Manifest switch failed ({switch_error}); rollback also failed "
                        f"({rollback_error})"
                    ) from switch_error
                raise
        backup_manifest = ReleaseAsset(
            asset_id=active_manifest.asset_id,
            name=backup_name,
            size=active_manifest.size,
            digest=active_manifest.digest,
            state=active_manifest.state,
        )

    published = _asset_by_name(client.list_assets(release.release_id), _STABLE_MANIFEST_NAME)
    if published is None or published.asset_id != staged_manifest.asset_id:
        raise PublicationError("The staged manifest did not become authoritative")
    _verify_asset(published, manifest_path)

    client.update_tag(release_tag, revision)
    client.update_release(
        release.release_id,
        revision,
        title,
        notes,
        draft=False,
    )

    if backup_manifest is not None:
        try:
            client.delete_asset(backup_manifest.asset_id)
        except PublicationError as exc:
            print(
                f"::warning::The new update is live, but old manifest cleanup failed: {exc}",
                file=sys.stderr,
            )
    recovery_state.unlink(missing_ok=True)
    print(
        f"Published {revision} through {_STABLE_MANIFEST_NAME}; immutable binaries remain available."
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Publish or recover the E3 development update release"
    )
    commands = result.add_subparsers(dest="command", required=True)

    publish = commands.add_parser("publish", help="publish a verified development update")
    publish.add_argument("--repository", required=True)
    publish.add_argument("--release-tag", default="e3-development")
    publish.add_argument("--revision", required=True)
    publish.add_argument("--channel", default="development")
    publish.add_argument("--windows", type=Path, required=True)
    publish.add_argument("--linux", type=Path, required=True)
    publish.add_argument("--manifest", type=Path, required=True)
    publish.add_argument("--title", required=True)
    publish.add_argument("--notes", required=True)
    publish.add_argument("--recovery-state", type=Path, required=True)

    recover = commands.add_parser("recover", help="restore an interrupted manifest switch")
    recover.add_argument("--repository", required=True)
    recover.add_argument("--release-tag", default="e3-development")
    recover.add_argument("--recovery-state", type=Path, required=True)
    return result


def main() -> int:
    arguments = parser().parse_args()
    try:
        client = GhReleaseClient(arguments.repository)
        if arguments.command == "recover":
            recover_manifest(
                client,
                repository=arguments.repository,
                release_tag=arguments.release_tag,
                recovery_state=arguments.recovery_state,
            )
        else:
            publish_development_update(
                client,
                repository=arguments.repository,
                release_tag=arguments.release_tag,
                revision=arguments.revision,
                channel=arguments.channel,
                windows_path=arguments.windows,
                linux_path=arguments.linux,
                manifest_path=arguments.manifest,
                title=arguments.title,
                notes=arguments.notes,
                recovery_state=arguments.recovery_state,
            )
    except PublicationError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
