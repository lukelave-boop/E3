"""Create the first v0.8.0 baseline once, or verify its release version lineage.

Run only after compatibility, package and manifest checks. The sole remote write
is atomic creation of refs/tags/v0.8.0; an existing tag is never changed or deleted.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from publish_development_update import PublicationError, immutable_asset_names, validate_publication_inputs
from verify_release_metadata import MetadataError, _read_object, verify_release_metadata
from version_for_build import BASELINE_REVISION, version_from_commit_count

from laser_aligner.storage import strict_json_loads

BASELINE_TAG = "v0.8.0"
RELEASE_TAG = "e3-development"
MAX_MANIFEST_BYTES = 1_048_576
Run = Callable[..., subprocess.CompletedProcess[str]]


class BaselineError(RuntimeError):
    """The immutable release baseline could not be established safely."""


def _sha(value: Any) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise BaselineError("Expected an exact lowercase 40-character Git revision")
    return value


def _run(runner: Run, arguments: list[str], *, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return runner(arguments, input=input_text, capture_output=True, text=True,
                      encoding="utf-8", check=False, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BaselineError(f"Could not run {arguments[0]}: {exc}") from exc


def _timestamp(value: Any) -> None:
    try:
        if not isinstance(value, str) or datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is None:
            raise ValueError("missing timezone")
    except ValueError as exc:
        raise BaselineError("Manifest publication time must be an ISO timestamp with timezone") from exc


class GhBaselineClient:
    """Use the publish job's existing GH_TOKEN, without modifying any existing ref."""

    def __init__(self, repository: str, *, runner: Run = subprocess.run) -> None:
        if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository) is None:
            raise BaselineError("Expected a GitHub owner/repository")
        self.repository = repository
        self.runner = runner

    def _api(self, method: str, endpoint: str, *, payload: dict[str, str] | None = None,
             require_object: bool = True) -> tuple[int, Any]:
        arguments = ["gh", "api", "--hostname", "github.com", "--include", "--method", method,
                     "-H", "Accept: application/vnd.github+json", "-H", "X-GitHub-Api-Version: 2022-11-28",
                     f"repos/{self.repository}/{endpoint}"]
        if payload is not None:
            arguments.extend(("--input", "-"))
        completed = _run(self.runner, arguments, input_text=json.dumps(payload) if payload is not None else None)
        # Inspect the actual HTTP status, never an ambiguous 'Not Found' message.
        header, separator, body = completed.stdout.replace("\r\n", "\n").partition("\n\n")
        match = re.fullmatch(r"HTTP/\S+ ([0-9]{3})(?: [^\n]*)?", header.split("\n", 1)[0])
        if not separator or match is None:
            raise BaselineError("GitHub CLI did not return an HTTP response")
        status = int(match.group(1))
        if (200 <= status < 300) != (completed.returncode == 0):
            raise BaselineError("GitHub CLI exit status disagrees with its HTTP response")
        try:
            value = strict_json_loads(body)
        except (ValueError, RecursionError) as exc:
            raise BaselineError("GitHub CLI returned invalid JSON") from exc
        if require_object and not isinstance(value, dict):
            raise BaselineError("GitHub CLI returned a non-object response")
        return status, value

    @staticmethod
    def _object(value: dict[str, Any]) -> tuple[str, str]:
        target = value.get("object")
        if not isinstance(target, dict) or target.get("type") not in ("commit", "tag"):
            raise BaselineError("GitHub reference must target a commit or annotated tag")
        return target["type"], _sha(target.get("sha"))

    def _reference(self, name: str, *, missing_ok: bool = False) -> dict[str, Any] | None:
        status, value = self._api("GET", f"git/ref/{name}")
        if status == 404 and missing_ok:
            return None
        if status != 200 or value.get("ref") != f"refs/{name}":
            raise BaselineError(f"Could not inspect exact refs/{name} (HTTP {status})")
        return value

    def main_revision(self) -> str:
        value = self._reference("heads/main")
        assert value is not None
        kind, revision = self._object(value)
        if kind != "commit":
            raise BaselineError("Remote main must point directly to a commit")
        return revision

    def baseline_revision(self) -> str | None:
        value = self._reference(f"tags/{BASELINE_TAG}", missing_ok=True)
        if value is None:
            return None
        seen: set[str] = set()
        for _ in range(16):
            kind, revision = self._object(value)
            if kind == "commit":
                return revision
            if revision in seen:
                raise BaselineError("Cyclic annotated baseline tag")
            seen.add(revision)
            status, value = self._api("GET", f"git/tags/{revision}")
            if status != 200 or value.get("sha") != revision:
                raise BaselineError("Could not inspect exact annotated baseline tag")
        raise BaselineError("Annotated baseline tag nesting exceeds the supported limit")

    def verify_initial_release(self) -> str:
        """Require intact public 0.7.x publication before a missing baseline can bootstrap.

        This blocks silent rebaselining after deletion of an established 0.8 tag.
        It cannot distinguish a deleted tag whose first publication never finished.
        """
        status, release = self._api("GET", f"releases/tags/{RELEASE_TAG}")
        release_id = release.get("id")
        if (status != 200 or type(release_id) is not int or release_id <= 0
                or release.get("tag_name") != RELEASE_TAG or release.get("draft") is not False
                or release.get("prerelease") is not True
                or release.get("html_url") != f"https://github.com/{self.repository}/releases/tag/{RELEASE_TAG}"):
            raise BaselineError("Missing or invalid public development release for baseline bootstrap")
        assets = []
        # Inspect every page so duplicate stable names can never be hidden.
        for page in range(1, 101):
            status, batch = self._api("GET", f"releases/{release_id}/assets?per_page=100&page={page}",
                                      require_object=False)
            if status != 200 or not isinstance(batch, list) or not all(isinstance(item, dict) for item in batch):
                raise BaselineError("Could not inspect development release assets")
            assets.extend(batch)
            if len(batch) < 100:
                break
        else:
            raise BaselineError("Development release asset pagination exceeds the supported limit")
        matches = [asset for asset in assets if asset.get("name") == "update-manifest.json"]
        if len(matches) != 1:
            raise BaselineError("Expected exactly one public stable development manifest")
        stable = matches[0]
        expected_url = f"https://github.com/{self.repository}/releases/download/{RELEASE_TAG}/update-manifest.json"
        size, digest, asset_id = stable.get("size"), stable.get("digest"), stable.get("id")
        if (type(asset_id) is not int or asset_id <= 0 or stable.get("state") != "uploaded"
                or type(size) is not int or not 0 < size <= MAX_MANIFEST_BYTES
                or not isinstance(digest, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None
                or stable.get("browser_download_url") != expected_url):
            raise BaselineError("Invalid stable development manifest asset identity")
        with tempfile.TemporaryDirectory(prefix="e3-baseline-") as directory:
            path = Path(directory) / "update-manifest.json"
            result = _run(self.runner, ["gh", "release", "download", RELEASE_TAG,
                                       "--repo", f"github.com/{self.repository}",
                                       "--pattern", "update-manifest.json", "--output", str(path)])
            if result.returncode != 0:
                raise BaselineError("Could not download stable development manifest")
            try:
                if path.stat().st_size != size:
                    raise BaselineError("Stable development manifest size changed")
                content = path.read_bytes()
                if "sha256:" + hashlib.sha256(content).hexdigest() != digest:
                    raise BaselineError("Stable development manifest digest changed")
                manifest = _read_object(path)
            except (OSError, MetadataError) as exc:
                raise BaselineError(f"Could not read stable development manifest: {exc}") from exc
        revision = _sha(manifest.get("revision"))
        version = manifest.get("version")
        if (type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1
                or manifest.get("channel") != "development" or not isinstance(version, str)
                or re.fullmatch(r"0\.7\.(?:0|[1-9][0-9]*)", version) is None
                or not isinstance(manifest.get("published_at"), str) or not manifest["published_at"].strip()):
            raise BaselineError("Missing baseline requires a verified prior 0.7.x development manifest")
        _timestamp(manifest["published_at"])
        entries = manifest.get("assets")
        if not isinstance(entries, dict) or set(entries) != {"windows-x86_64", "linux-x86_64"}:
            raise BaselineError("Prior development manifest must identify both platform assets")
        for platform, name in zip(("windows-x86_64", "linux-x86_64"), immutable_asset_names(revision), strict=True):
            entry = entries[platform]
            if (not isinstance(entry, dict) or entry.get("name") != name
                    or entry.get("url") != f"https://github.com/{self.repository}/releases/download/{RELEASE_TAG}/{name}"
                    or type(entry.get("size")) is not int or entry["size"] <= 0
                    or not isinstance(entry.get("sha256"), str) or re.fullmatch(r"[0-9a-f]{64}", entry["sha256"]) is None):
                raise BaselineError("Invalid prior development manifest package identity")
        return revision

    def create_baseline(self, revision: str) -> None:
        revision = _sha(revision)
        status, value = self._api("POST", "git/refs", payload={"ref": f"refs/tags/{BASELINE_TAG}", "sha": revision})
        if status == 201:
            if value.get("ref") != f"refs/tags/{BASELINE_TAG}" or self._object(value) != ("commit", revision):
                raise BaselineError("GitHub returned an unexpected created baseline")
        elif status not in (409, 422):
            raise BaselineError(f"Could not create immutable baseline (HTTP {status})")
        # 409/422 can be another creator winning. The caller MUST reread the tag
        # and accept only this exact commit, including after a successful POST.


class GitCheckout:
    def __init__(self, root: Path, *, runner: Run = subprocess.run) -> None:
        self.root = root
        self.runner = runner

    def _git(self, *arguments: str) -> str:
        result = _run(self.runner, ["git", "-C", str(self.root), *arguments])
        if result.returncode != 0:
            raise BaselineError(f"Could not verify local Git history: {result.stderr.strip()}")
        return result.stdout.strip()

    def verify_revision(self, revision: str) -> None:
        if self._git("rev-parse", "--is-shallow-repository") != "false":
            raise BaselineError("Release verification requires complete, non-shallow history")
        if self._git("rev-parse", "--verify", "HEAD^{commit}") != revision:
            raise BaselineError("Local HEAD does not match the workflow revision")

    def baseline_revision(self) -> str | None:
        reference = f"refs/tags/{BASELINE_TAG}"
        result = _run(self.runner, ["git", "-C", str(self.root), "show-ref", "--verify", "--quiet", reference])
        if result.returncode == 1:
            return None
        if result.returncode != 0:
            raise BaselineError("Could not inspect the checkout's baseline reference")
        return _sha(self._git("rev-parse", "--verify", f"{reference}^{{commit}}"))

    def commits_since(self, baseline: str, revision: str) -> int:
        baseline, revision = _sha(baseline), _sha(revision)
        if self._git("cat-file", "-t", baseline) != "commit":
            raise BaselineError("Baseline does not resolve to a local commit")
        self._git("merge-base", "--is-ancestor", baseline, revision)
        count = self._git("rev-list", "--count", f"{baseline}..{revision}")
        if re.fullmatch(r"0|[1-9][0-9]*", count) is None:
            raise BaselineError("Invalid post-baseline commit count")
        return int(count)


def ensure_release_baseline(
    client: GhBaselineClient,
    checkout: GitCheckout,
    *,
    revision: str,
    windows_build_info: Path,
    linux_build_info: Path,
    manifest: Path,
    windows_path: Path,
    linux_path: Path,
    channel: str = "development",
) -> str:
    revision = _sha(revision)
    if channel != "development":
        raise BaselineError("Baseline automation is limited to the development channel")
    if BASELINE_REVISION != BASELINE_TAG or version_from_commit_count(0) != "0.8.0":
        raise BaselineError("Baseline automation must be reviewed before changing the release series")
    checkout.verify_revision(revision)

    def verify_main() -> None:
        if client.main_revision() != revision:
            raise BaselineError("Remote main no longer matches the exact workflow revision")

    verify_main()
    baseline = client.baseline_revision()
    local_baseline = checkout.baseline_revision()
    if local_baseline is not None and local_baseline != baseline:
        raise BaselineError("Remote baseline conflicts with the checkout's existing baseline; refusing to rebaseline")
    count = 0 if baseline is None else checkout.commits_since(baseline, revision)
    version = version_from_commit_count(count)
    try:
        verify_release_metadata(
            [("windows-x86_64", windows_build_info), ("linux-x86_64", linux_build_info)],
            revision=revision, channel=channel, version=version, manifest=manifest,
        )
        expected_url = f"https://github.com/{client.repository}/releases/download/{RELEASE_TAG}/update-manifest.json"
        for path in (windows_build_info, linux_build_info):
            info = _read_object(path)
            if info.get("repository") != client.repository or info.get("manifest_url") != expected_url:
                raise BaselineError(f"{path}: packaged repository or manifest_url does not match the publisher")
        validate_publication_inputs(
            repository=client.repository, release_tag=RELEASE_TAG, revision=revision, channel=channel,
            windows_path=windows_path, linux_path=linux_path, manifest_path=manifest,
        )
        manifest_info = _read_object(manifest)
        _timestamp(manifest_info["published_at"])
        if any(type(entry["size"]) is not int for entry in manifest_info["assets"].values()):
            raise BaselineError("Manifest package size must be an integer")
    except (MetadataError, PublicationError, OSError) as exc:
        raise BaselineError(str(exc)) from exc
    if baseline is None:
        predecessor = client.verify_initial_release()
        if checkout.commits_since(predecessor, revision) <= 0:
            raise BaselineError("Initial 0.8 release must follow its published 0.7 predecessor")
    verify_main()
    if baseline is None:
        client.create_baseline(revision)
        baseline = revision
    if client.baseline_revision() != baseline:
        raise BaselineError("Immutable baseline is missing or has a conflicting target; refusing to change it")
    verify_main()
    return f"verified immutable {BASELINE_TAG}={baseline} version={version} revision={revision}"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--windows-build-info", type=Path, required=True)
    parser.add_argument("--linux-build-info", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--windows", type=Path, required=True)
    parser.add_argument("--linux", type=Path, required=True)
    parser.add_argument("--channel", default="development")
    arguments = parser.parse_args(argv)
    try:
        result = ensure_release_baseline(
            GhBaselineClient(arguments.repository), GitCheckout(Path(__file__).resolve().parents[1]),
            revision=arguments.revision, windows_build_info=arguments.windows_build_info,
            linux_build_info=arguments.linux_build_info, manifest=arguments.manifest, channel=arguments.channel,
            windows_path=arguments.windows, linux_path=arguments.linux,
        )
    except BaselineError as exc:
        parser.error(str(exc))
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
