"""Immutable release baseline checks; GitHub requests always use injected fakes."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
REVISION = "a" * 40
OTHER = "b" * 40
TAG_OBJECT = "c" * 40
PREVIOUS = "d" * 40
REPOSITORY = "lukelave-boop/E3"


def _module():
    path = ROOT / "packaging" / "ensure_release_baseline.py"
    spec = importlib.util.spec_from_file_location("e3_release_baseline", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


baseline = _module()


class FakeClient:
    def __init__(self, *, tag=None, main=REVISION):
        self.tag = tag
        self.repository = REPOSITORY
        self.predecessor = PREVIOUS
        self.main = main
        self.events = []
        self.on_create = None
        self.main_reads = 0
        self.tag_reads = 0
        self.change_main_at = None
        self.change_tag_at = None

    def main_revision(self):
        self.main_reads += 1
        self.events.append(("main",))
        if self.main_reads == self.change_main_at:
            return OTHER
        return self.main

    def baseline_revision(self):
        self.tag_reads += 1
        self.events.append(("tag",))
        if self.tag_reads == self.change_tag_at:
            return OTHER
        return self.tag

    def verify_initial_release(self):
        self.events.append(("predecessor",))
        return self.predecessor

    def create_baseline(self, revision):
        self.events.append(("create", revision))
        if self.on_create:
            self.on_create(self)
        else:
            self.tag = revision


class FakeCheckout:
    def __init__(self, *, count=0, local_tag=None):
        self.count = count
        self.local_tag = local_tag
        self.events = []
        self.failure = None

    def verify_revision(self, revision):
        self.events.append(("verify", revision))
        if self.failure:
            raise baseline.BaselineError(self.failure)

    def baseline_revision(self):
        self.events.append(("local_tag",))
        return self.local_tag

    def commits_since(self, first, last):
        self.events.append(("count", first, last))
        return 1 if first == PREVIOUS else self.count


@pytest.fixture
def metadata(tmp_path):
    common = {"schema_version": 1, "version": "0.8.0", "revision": REVISION, "channel": "development"}
    paths = {}
    for key, platform in (("windows_build_info", "windows-x86_64"), ("linux_build_info", "linux-x86_64")):
        path = tmp_path / f"{platform}.json"
        path.write_text(json.dumps({**common, "platform_key": platform, "packaged": True,
                                    "repository": REPOSITORY, "manifest_url":
                                    f"https://github.com/{REPOSITORY}/releases/download/e3-development/update-manifest.json"}), encoding="utf-8")
        paths[key] = path
    paths["manifest"] = tmp_path / f"update-manifest-{REVISION[:12]}-123-1.json"
    assets = {}
    for key, platform, name in zip(("windows_path", "linux_path"), ("windows-x86_64", "linux-x86_64"),
                                    baseline.immutable_asset_names(REVISION), strict=True):
        path = tmp_path / name
        path.write_bytes(b"verified package bytes")
        paths[key] = path
        assets[platform] = {"name": name, "url": f"https://github.com/{REPOSITORY}/releases/download/e3-development/{name}",
                            "size": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    paths["manifest"].write_text(json.dumps({**common, "published_at": "2026-10-16T21:00:00+00:00", "assets": assets}),
                               encoding="utf-8")
    return paths


def _change(paths, **fields):
    for key, path in paths.items():
        if key in ("windows_path", "linux_path"):
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload.update(fields)
        path.write_text(json.dumps(payload), encoding="utf-8")


def _retarget(paths, revision):
    _change(paths, revision=revision)
    manifest = json.loads(paths["manifest"].read_text(encoding="utf-8"))
    for key, platform, name in zip(("windows_path", "linux_path"), ("windows-x86_64", "linux-x86_64"),
                                    baseline.immutable_asset_names(revision), strict=True):
        new_path = paths[key].with_name(name)
        paths[key].rename(new_path)
        paths[key] = new_path
        manifest["assets"][platform].update(name=name, url=f"https://github.com/{REPOSITORY}/releases/download/e3-development/{name}")
    new_manifest = paths["manifest"].with_name(f"update-manifest-{revision[:12]}-123-1.json")
    paths["manifest"].unlink()
    paths["manifest"] = new_manifest
    new_manifest.write_text(json.dumps(manifest), encoding="utf-8")


def _ensure(client, metadata, checkout=None, **kwargs):
    return baseline.ensure_release_baseline(client, checkout or FakeCheckout(), revision=REVISION, **metadata, **kwargs)


def _writes(client):
    return [event for event in client.events if event[0] == "create"]


def test_first_release_creates_only_exact_baseline_after_identity_verification(metadata):
    client = FakeClient()
    checkout = FakeCheckout()
    result = _ensure(client, metadata, checkout)
    assert result == f"verified immutable v0.8.0={REVISION} version=0.8.0 revision={REVISION}"
    assert client.events == [("main",), ("tag",), ("predecessor",), ("main",), ("create", REVISION), ("tag",), ("main",)]
    assert checkout.events == [("verify", REVISION), ("local_tag",), ("count", PREVIOUS, REVISION)]


def test_existing_exact_baseline_is_read_only_and_idempotent(metadata):
    client = FakeClient(tag=REVISION)
    checkout = FakeCheckout()
    assert "version=0.8.0" in _ensure(client, metadata, checkout)
    assert "version=0.8.0" in _ensure(client, metadata, checkout)
    assert _writes(client) == []
    assert checkout.events == [("verify", REVISION), ("local_tag",), ("count", REVISION, REVISION)] * 2


def test_later_release_counts_from_verified_remote_baseline_without_moving_it(metadata):
    _change(metadata, version="0.8.7")
    client = FakeClient(tag=OTHER)
    checkout = FakeCheckout(count=7)
    assert "version=0.8.7" in _ensure(client, metadata, checkout)
    assert checkout.events == [("verify", REVISION), ("local_tag",), ("count", OTHER, REVISION)]
    assert _writes(client) == [] and client.tag == OTHER


@pytest.mark.parametrize("version", ["0.7.0", "0.8.1", "1.0.0", "0.8.00", "0.8.0 ", 0, None])
def test_missing_tag_cannot_be_created_for_other_metadata_version(metadata, version):
    _change(metadata, version=version)
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match="version"):
        _ensure(client, metadata)
    assert _writes(client) == []


def test_existing_different_target_rejects_initial_version_without_moving_tag(metadata):
    client = FakeClient(tag=OTHER)
    with pytest.raises(baseline.BaselineError, match="version"):
        _ensure(client, metadata, FakeCheckout(count=1))
    assert _writes(client) == [] and client.tag == OTHER


@pytest.mark.parametrize("version", ["0.8.0", "0.8.6", "0.8.8", "0.7.7"])
def test_post_baseline_version_must_match_full_commit_count(metadata, version):
    _change(metadata, version=version)
    client = FakeClient(tag=OTHER)
    with pytest.raises(baseline.BaselineError, match="version"):
        _ensure(client, metadata, FakeCheckout(count=7))
    assert _writes(client) == []


@pytest.mark.parametrize("key", ["windows_build_info", "linux_build_info", "manifest"])
@pytest.mark.parametrize("field,value", [("revision", OTHER), ("channel", "stable"), ("version", "0.8.1"),
                                         ("schema_version", True), ("revision", "A" * 40)])
def test_every_metadata_identity_is_rechecked_before_any_tag_write(metadata, key, field, value):
    _change({key: metadata[key]}, **{field: value})
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match=field):
        _ensure(client, metadata)
    assert _writes(client) == []


@pytest.mark.parametrize("key", ["windows_build_info", "linux_build_info"])
@pytest.mark.parametrize("field,value", [("platform_key", "other"), ("packaged", False), ("packaged", 1)])
def test_platform_and_frozen_identity_are_required(metadata, key, field, value):
    _change({key: metadata[key]}, **{field: value})
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match=field):
        _ensure(client, metadata)
    assert _writes(client) == []


@pytest.mark.parametrize("payload", ["{}", "[]", "null", "broken", '{"version":"0.8.0","version":"0.8.0"}',
                                     '{"version":NaN}', '{"version":1e999}'])
def test_malformed_metadata_fails_closed(metadata, payload):
    metadata["manifest"].write_text(payload, encoding="utf-8")
    client = FakeClient()
    with pytest.raises(baseline.BaselineError):
        _ensure(client, metadata)
    assert _writes(client) == []


def test_missing_metadata_fails_closed(metadata):
    metadata["linux_build_info"].unlink()
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match="cannot read"):
        _ensure(client, metadata)
    assert _writes(client) == []


@pytest.mark.parametrize("revision", ["a" * 39, "a" * 41, "A" * 40, "g" * 40, "main", "", None])
def test_invalid_workflow_revision_never_accesses_remote(metadata, revision):
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match="revision"):
        baseline.ensure_release_baseline(client, FakeCheckout(), revision=revision, **metadata)
    assert client.events == []


@pytest.mark.parametrize("read", [1, 2, 3])
def test_main_movement_aborts_before_publication(metadata, read):
    client = FakeClient()
    client.change_main_at = read
    with pytest.raises(baseline.BaselineError, match="Remote main"):
        _ensure(client, metadata)
    assert len(_writes(client)) == (1 if read == 3 else 0)
    # An already created immutable baseline is never rolled back on later failure.
    assert client.tag == (REVISION if read == 3 else None)


def test_existing_tag_target_change_during_validation_is_rejected(metadata):
    client = FakeClient(tag=REVISION)
    client.change_tag_at = 2
    with pytest.raises(baseline.BaselineError, match="conflicting target"):
        _ensure(client, metadata)
    assert _writes(client) == []


@pytest.mark.parametrize("target", [REVISION, OTHER, None])
def test_atomic_creation_reread_accepts_only_exact_workflow_target(metadata, target):
    client = FakeClient()
    client.on_create = lambda client: setattr(client, "tag", target)
    if target == REVISION:
        assert "version=0.8.0" in _ensure(client, metadata)
    else:
        with pytest.raises(baseline.BaselineError, match="missing or has a conflicting target"):
            _ensure(client, metadata)
    assert _writes(client) == [("create", REVISION)]


def test_checkout_failure_precedes_any_remote_access(metadata):
    client, checkout = FakeClient(), FakeCheckout()
    checkout.failure = "not the workflow checkout"
    with pytest.raises(baseline.BaselineError, match=checkout.failure):
        _ensure(client, metadata, checkout)
    assert client.events == []


def test_shared_series_change_requires_explicit_helper_review(metadata, monkeypatch):
    monkeypatch.setattr(baseline, "BASELINE_REVISION", "v0.9.0")
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match="reviewed"):
        _ensure(client, metadata)
    assert client.events == []


def test_environment_version_override_cannot_bypass_baseline_count(metadata, monkeypatch):
    monkeypatch.setenv("E3_BUILD_VERSION", "0.8.99")
    assert "version=0.8.0" in _ensure(FakeClient(), metadata)
    _change(metadata, version="0.8.99")
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match="version"):
        _ensure(client, metadata)
    assert _writes(client) == []


def _reference(revision=REVISION, *, name="tags/v0.8.0", kind="commit"):
    return {"ref": f"refs/{name}", "object": {"type": kind, "sha": revision}}


def _response(status, value, *, returncode=None, raw=None):
    body = json.dumps(value) if raw is None else raw
    return subprocess.CompletedProcess([], int(status >= 300) if returncode is None else returncode,
                                       f"HTTP/2.0 {status} Result\r\nContent-Type: application/json\r\n\r\n{body}", "")


class ScriptedGh:
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    def __call__(self, arguments, **kwargs):
        if arguments[1] == "api":
            assert arguments[:6] == ["gh", "api", "--hostname", "github.com", "--include", "--method"]
        else:
            assert arguments[:6] == ["gh", "release", "download", "e3-development", "--repo", f"github.com/{REPOSITORY}"]
            assert arguments[6:9] == ["--pattern", "update-manifest.json", "--output"]
        assert kwargs["timeout"] == 60 and kwargs["check"] is False
        assert kwargs["capture_output"] and kwargs["text"] and kwargs["encoding"] == "utf-8"
        self.calls.append((arguments, kwargs))
        assert self.replies, "Unexpected API call"
        result = self.replies.pop(0)
        if isinstance(result, Exception):
            raise result
        if callable(result):
            return result(arguments, kwargs)
        return result


def _client(*replies):
    runner = ScriptedGh(replies)
    return baseline.GhBaselineClient(REPOSITORY, runner=runner), runner


def test_remote_lightweight_baseline_and_main_are_resolved_exactly():
    client, runner = _client(_response(200, _reference()), _response(200, _reference(name="heads/main")))
    assert client.baseline_revision() == REVISION
    assert client.main_revision() == REVISION
    assert runner.calls[0][0][-1] == f"repos/{REPOSITORY}/git/ref/tags/v0.8.0"
    assert runner.calls[1][0][-1] == f"repos/{REPOSITORY}/git/ref/heads/main"
    assert all(call[0][6] == "GET" for call in runner.calls)


def test_remote_annotated_and_nested_tags_are_peeled_to_commit():
    client, runner = _client(
        _response(200, _reference(TAG_OBJECT, kind="tag")),
        _response(200, {"sha": TAG_OBJECT, "object": {"type": "tag", "sha": OTHER}}),
        _response(200, {"sha": OTHER, "object": {"type": "commit", "sha": REVISION}}),
    )
    assert client.baseline_revision() == REVISION
    assert [call[0][-1] for call in runner.calls][1:] == [f"repos/{REPOSITORY}/git/tags/{TAG_OBJECT}",
                                                        f"repos/{REPOSITORY}/git/tags/{OTHER}"]


@pytest.mark.parametrize("object_value", [{"type": "tree", "sha": REVISION}, {"type": "blob", "sha": REVISION},
                                          {"type": "commit", "sha": "short"}, {}, None, []])
def test_remote_noncommit_or_malformed_tag_target_rejected(object_value):
    client, _ = _client(_response(200, {"ref": "refs/tags/v0.8.0", "object": object_value}))
    with pytest.raises(baseline.BaselineError):
        client.baseline_revision()


def test_main_cannot_be_an_annotated_tag():
    client, _ = _client(_response(200, _reference(TAG_OBJECT, name="heads/main", kind="tag")))
    with pytest.raises(baseline.BaselineError, match="directly"):
        client.main_revision()


def test_cyclic_annotated_tag_fails_without_unbounded_requests():
    client, runner = _client(_response(200, _reference(TAG_OBJECT, kind="tag")),
                             _response(200, {"sha": TAG_OBJECT, "object": {"type": "tag", "sha": TAG_OBJECT}}))
    with pytest.raises(baseline.BaselineError, match="Cyclic"):
        client.baseline_revision()
    assert len(runner.calls) == 2


def test_deep_annotated_tag_chain_is_bounded():
    replies = [_response(200, _reference(f"{0:040x}", kind="tag"))]
    replies.extend(_response(200, {"sha": f"{i:040x}", "object": {"type": "tag", "sha": f"{i+1:040x}"}})
                   for i in range(16))
    client, runner = _client(*replies)
    with pytest.raises(baseline.BaselineError, match="nesting"):
        client.baseline_revision()
    assert len(runner.calls) == 17


@pytest.mark.parametrize("status,value", [(200, {"sha": OTHER, "object": {"type": "commit", "sha": REVISION}}),
                                          (404, {"message": "Not Found"}), (500, {"message": "failure"})])
def test_unreadable_or_mismatched_annotated_tag_rejected(status, value):
    client, _ = _client(_response(200, _reference(TAG_OBJECT, kind="tag")), _response(status, value))
    with pytest.raises(baseline.BaselineError, match="annotated"):
        client.baseline_revision()


def test_only_actual_tag_404_is_missing_and_main_404_is_failure():
    client, _ = _client(_response(404, {"message": "Not Found"}), _response(404, {"message": "Not Found"}))
    assert client.baseline_revision() is None
    with pytest.raises(baseline.BaselineError, match="heads/main"):
        client.main_revision()


@pytest.mark.parametrize("status", [401, 403, 409, 422, 429, 500])
def test_failed_tag_read_is_never_treated_as_missing(status):
    client, _ = _client(_response(status, {"message": "Not Found"}))
    with pytest.raises(baseline.BaselineError, match=f"HTTP {status}"):
        client.baseline_revision()


@pytest.mark.parametrize("value", [_reference(name="tags/v0.8.00"), [_reference()], {}, None])
def test_inexact_or_malformed_reference_response_rejected(value):
    client, _ = _client(_response(200, value))
    with pytest.raises(baseline.BaselineError):
        client.baseline_revision()


@pytest.mark.parametrize("raw", ["not JSON", "", '{"ref":1,"ref":2}'])
def test_malformed_api_json_rejected(raw):
    client, _ = _client(_response(200, {}, raw=raw))
    with pytest.raises(baseline.BaselineError):
        client.baseline_revision()


@pytest.mark.parametrize("response", [subprocess.CompletedProcess([], 1, "", "gh: Not Found (HTTP 404)"),
                                       _response(200, _reference(), returncode=1),
                                       _response(404, {"message": "Not Found"}, returncode=0),
                                       OSError("missing gh"), subprocess.TimeoutExpired("gh", 60)])
def test_transport_failures_never_become_a_missing_tag(response):
    client, _ = _client(response)
    with pytest.raises(baseline.BaselineError):
        client.baseline_revision()


@pytest.mark.parametrize("repository", ["", "owner", "/repo", "owner/", "../repo", "owner/../repo", "owner/repo?x=y",
                                        "owner/repo\n", "-owner/repo", "https://github.com/owner/repo"])
def test_repository_validation_precedes_requests(repository):
    runner = ScriptedGh([])
    with pytest.raises(baseline.BaselineError):
        baseline.GhBaselineClient(repository, runner=runner)
    assert runner.calls == []


def test_create_uses_one_atomic_post_without_force_update_or_delete():
    client, runner = _client(_response(201, _reference()))
    client.create_baseline(REVISION)
    arguments, kwargs = runner.calls[0]
    assert arguments[6] == "POST"
    assert arguments[-3:] == [f"repos/{REPOSITORY}/git/refs", "--input", "-"]
    assert json.loads(kwargs["input"]) == {"ref": "refs/tags/v0.8.0", "sha": REVISION}
    assert len(runner.calls) == 1


@pytest.mark.parametrize("status", [409, 422])
@pytest.mark.parametrize("race_target", [REVISION, OTHER, None])
def test_real_client_create_races_reread_and_accept_only_exact_target(metadata, status, race_target):
    tag_reply = _response(404, {"message": "Not Found"}) if race_target is None else _response(200, _reference(race_target))
    client, runner = _client(
        _response(200, _reference(name="heads/main")), _response(404, {"message": "Not Found"}),
        _response(200, _reference(name="heads/main")), _response(status, {"message": "Reference already exists"}),
        tag_reply, *([_response(200, _reference(name="heads/main"))] if race_target == REVISION else []),
    )
    client.verify_initial_release = lambda: PREVIOUS
    if race_target == REVISION:
        assert "version=0.8.0" in _ensure(client, metadata)
    else:
        with pytest.raises(baseline.BaselineError, match="conflicting target"):
            _ensure(client, metadata)
    assert sum(call[0][6] == "POST" for call in runner.calls) == 1
    assert not runner.replies


@pytest.mark.parametrize("status", [200, 400, 401, 403, 404, 429, 500])
def test_create_unexpected_status_fails_without_retry(status):
    client, runner = _client(_response(status, {"message": "failure"}))
    with pytest.raises(baseline.BaselineError, match="create immutable"):
        client.create_baseline(REVISION)
    assert len(runner.calls) == 1


@pytest.mark.parametrize("value", [_reference(OTHER), _reference(name="heads/main"), _reference(kind="tag"), {}])
def test_create_malformed_success_response_is_rejected(value):
    client, runner = _client(_response(201, value))
    with pytest.raises(baseline.BaselineError):
        client.create_baseline(REVISION)
    assert len(runner.calls) == 1


def _git(root, *arguments):
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=Baseline Test", "-c", "user.email=test@example.invalid",
                           *arguments], check=True, capture_output=True, text=True, timeout=15).stdout.strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-b", "main")
    _git(root, "commit", "--allow-empty", "-m", "baseline")
    return root, _git(root, "rev-parse", "HEAD")


def test_real_history_and_annotated_remote_baseline_counts_merged_commits(repository, metadata):
    root, first = repository
    _git(root, "tag", "-a", "v0.8.0", "-m", "immutable baseline")
    tag = _git(root, "rev-parse", "refs/tags/v0.8.0")
    _git(root, "checkout", "-b", "feature")
    _git(root, "commit", "--allow-empty", "-m", "feature change")
    _git(root, "checkout", "main")
    _git(root, "merge", "--no-ff", "feature", "-m", "integration")
    revision = _git(root, "rev-parse", "HEAD")
    _retarget(metadata, revision)
    _change(metadata, version="0.8.2")
    tag_replies = [_response(200, _reference(tag, kind="tag")),
                   _response(200, {"sha": tag, "object": {"type": "commit", "sha": first}})]
    client, runner = _client(_response(200, _reference(revision, name="heads/main")), *tag_replies,
                             _response(200, _reference(revision, name="heads/main")), *tag_replies,
                             _response(200, _reference(revision, name="heads/main")))
    result = baseline.ensure_release_baseline(client, baseline.GitCheckout(root), revision=revision, **metadata)
    assert f"v0.8.0={first} version=0.8.2 revision={revision}" in result
    assert all(call[0][6] == "GET" for call in runner.calls)
    assert _git(root, "rev-parse", "v0.8.0") == tag


def test_real_first_release_then_idempotent_recheck(repository, metadata):
    root, first = repository
    _git(root, "commit", "--allow-empty", "-m", "initial 0.8 release")
    revision = _git(root, "rev-parse", "HEAD")
    _retarget(metadata, revision)
    client = FakeClient(main=revision)
    client.predecessor = first
    checkout = baseline.GitCheckout(root)
    assert "version=0.8.0" in baseline.ensure_release_baseline(client, checkout, revision=revision, **metadata)
    assert "version=0.8.0" in baseline.ensure_release_baseline(client, checkout, revision=revision, **metadata)
    assert _writes(client) == [("create", revision)]
    assert _git(root, "tag", "--list") == ""  # No local tag mutations either.


def test_real_history_rejects_nonancestor_baseline(repository, metadata):
    root, first = repository
    _git(root, "checkout", "-b", "unmerged")
    _git(root, "commit", "--allow-empty", "-m", "not on main")
    unmerged = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "main")
    _change(metadata, revision=first)
    client = FakeClient(tag=unmerged, main=first)
    with pytest.raises(baseline.BaselineError, match="Git history"):
        baseline.ensure_release_baseline(client, baseline.GitCheckout(root), revision=first, **metadata)
    assert _writes(client) == []


def test_real_history_rejects_unknown_baseline_object(repository):
    root, revision = repository
    with pytest.raises(baseline.BaselineError, match="Git history"):
        baseline.GitCheckout(root).commits_since(OTHER, revision)


def test_real_history_rejects_noncommit_object(repository):
    root, revision = repository
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    with pytest.raises(baseline.BaselineError, match="local commit"):
        baseline.GitCheckout(root).commits_since(tree, revision)


def test_real_history_rejects_wrong_checkout(repository):
    root, _ = repository
    with pytest.raises(baseline.BaselineError, match="Local HEAD"):
        baseline.GitCheckout(root).verify_revision(OTHER)


def test_real_shallow_checkout_is_rejected(repository, tmp_path):
    root, revision = repository
    shallow = tmp_path / "shallow"
    _git(tmp_path, "clone", "--depth=1", root.as_uri(), str(shallow))
    with pytest.raises(baseline.BaselineError, match="non-shallow"):
        baseline.GitCheckout(shallow).verify_revision(revision)


@pytest.mark.parametrize("count", ["-1", "1.0", "01", "x", ""])
def test_malformed_git_count_rejected(count):
    answers = iter(["commit", "", count])
    checkout = baseline.GitCheckout(ROOT, runner=lambda *args, **kwargs: subprocess.CompletedProcess([], 0, next(answers), ""))
    with pytest.raises(baseline.BaselineError, match="commit count"):
        checkout.commits_since(OTHER, REVISION)


def test_cli_requires_all_three_metadata_files_and_rejects_invalid_repository(metadata, monkeypatch, capsys):
    arguments = ["--repository", "invalid", "--revision", REVISION,
                 "--windows-build-info", str(metadata["windows_build_info"]),
                 "--linux-build-info", str(metadata["linux_build_info"]),
                 "--windows", str(metadata["windows_path"]), "--linux", str(metadata["linux_path"]),
                 "--manifest", str(metadata["manifest"])]
    with pytest.raises(SystemExit) as exc:
        baseline.main(arguments)
    assert exc.value.code == 2
    assert "owner/repository" in capsys.readouterr().err
    with pytest.raises(SystemExit) as exc:
        baseline.main(arguments[:-2])
    assert exc.value.code == 2
    assert "--manifest" in capsys.readouterr().err


def test_cli_wires_exact_inputs_and_reports_verified_result(metadata, monkeypatch, capsys):
    client = FakeClient()
    monkeypatch.setattr(baseline, "GhBaselineClient", lambda repository: client)
    monkeypatch.setattr(baseline, "GitCheckout", lambda root: FakeCheckout())
    arguments = ["--repository", REPOSITORY, "--revision", REVISION,
                 "--windows-build-info", str(metadata["windows_build_info"]),
                 "--linux-build-info", str(metadata["linux_build_info"]),
                 "--windows", str(metadata["windows_path"]), "--linux", str(metadata["linux_path"]),
                 "--manifest", str(metadata["manifest"])]
    assert baseline.main(arguments) == 0
    assert f"version=0.8.0 revision={REVISION}" in capsys.readouterr().out
    assert _writes(client) == [("create", REVISION)]


@pytest.mark.parametrize("key", ["windows_build_info", "linux_build_info"])
@pytest.mark.parametrize("fields", [{"repository": "other/repo"}, {"repository": None},
                                    {"manifest_url": "https://github.com/other/repo/releases/download/e3-development/update-manifest.json"},
                                    {"manifest_url": f"https://github.com/{REPOSITORY}/releases/download/stable/update-manifest.json"},
                                    {"manifest_url": "https://example.invalid/update-manifest.json"}])
def test_packaged_updater_destination_is_bound_before_tag_creation(metadata, key, fields):
    _change({key: metadata[key]}, **fields)
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match="repository or manifest_url"):
        _ensure(client, metadata)
    assert _writes(client) == []


@pytest.mark.parametrize("platform", ["windows-x86_64", "linux-x86_64"])
@pytest.mark.parametrize("field,value", [("size", 2), ("size", 22.0), ("size", True), ("sha256", "0" * 64),
                                         ("name", "wrong.exe"), ("url", "https://example.invalid/wrong")])
def test_candidate_manifest_assets_are_verified_before_tag_creation(metadata, platform, field, value):
    path = metadata["manifest"]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["assets"][platform][field] = value
    path.write_text(json.dumps(manifest), encoding="utf-8")
    client = FakeClient()
    with pytest.raises(baseline.BaselineError):
        _ensure(client, metadata)
    assert _writes(client) == []


def test_manifest_without_package_assets_cannot_create_baseline(metadata):
    path = metadata["manifest"]
    manifest = json.loads(path.read_text(encoding="utf-8"))
    del manifest["assets"]
    path.write_text(json.dumps(manifest), encoding="utf-8")
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match="Windows and Linux"):
        _ensure(client, metadata)
    assert _writes(client) == []


@pytest.mark.parametrize("key", ["windows_path", "linux_path"])
def test_missing_package_cannot_create_baseline(metadata, key):
    metadata[key].unlink()
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match="missing or empty"):
        _ensure(client, metadata)
    assert _writes(client) == []


def test_candidate_invalid_publication_time_cannot_create_baseline(metadata):
    _change({"manifest": metadata["manifest"]}, published_at="tomorrow")
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match="publication time"):
        _ensure(client, metadata)
    assert _writes(client) == []


def test_other_channel_cannot_bootstrap(metadata):
    client = FakeClient()
    with pytest.raises(baseline.BaselineError, match="development channel"):
        _ensure(client, metadata, channel="stable")
    assert client.events == []


def _prior_manifest(**changes):
    assets = {}
    for platform, name in zip(("windows-x86_64", "linux-x86_64"), baseline.immutable_asset_names(PREVIOUS), strict=True):
        assets[platform] = {"name": name, "url": f"https://github.com/{REPOSITORY}/releases/download/e3-development/{name}",
                            "sha256": "0" * 64, "size": 1234}
    return {"schema_version": 1, "version": "0.7.308", "revision": PREVIOUS, "channel": "development",
            "published_at": "2026-10-16T20:00:00+00:00", "assets": assets, **changes}


def _public_release(**changes):
    return {"id": 321, "tag_name": "e3-development", "draft": False, "prerelease": True,
            "html_url": f"https://github.com/{REPOSITORY}/releases/tag/e3-development", **changes}


def _stable_asset(content, **changes):
    return {"id": 654, "name": "update-manifest.json", "state": "uploaded", "size": len(content),
            "digest": "sha256:" + hashlib.sha256(content).hexdigest(),
            "browser_download_url": f"https://github.com/{REPOSITORY}/releases/download/e3-development/update-manifest.json",
            **changes}


class Download:
    def __init__(self, content, *, status=0):
        self.content = content
        self.status = status

    def __call__(self, arguments, kwargs):
        assert kwargs["input"] is None
        Path(arguments[-1]).write_bytes(self.content)
        return subprocess.CompletedProcess(arguments, self.status, "", "")


def _bootstrap_replies(manifest=None, *, raw=None):
    content = json.dumps(_prior_manifest() if manifest is None else manifest).encode() if raw is None else raw
    return [_response(200, _public_release()), _response(200, [_stable_asset(content)]), Download(content)]


def test_prior_manifest_bootstrap_guard_verifies_exact_public_asset():
    client, runner = _client(*_bootstrap_replies())
    assert client.verify_initial_release() == PREVIOUS
    assert runner.calls[0][0][-1] == f"repos/{REPOSITORY}/releases/tags/e3-development"
    assert runner.calls[1][0][-1] == f"repos/{REPOSITORY}/releases/321/assets?per_page=100&page=1"
    assert not runner.replies


def test_complete_new_baseline_path_with_real_client_and_bootstrap_guard(metadata):
    client, runner = _client(
        _response(200, _reference(name="heads/main")), _response(404, {"message": "Not Found"}),
        *_bootstrap_replies(), _response(200, _reference(name="heads/main")), _response(201, _reference()),
        _response(200, _reference()), _response(200, _reference(name="heads/main")),
    )
    assert "version=0.8.0" in _ensure(client, metadata)
    assert sum(call[0][1] == "api" and call[0][6] == "POST" for call in runner.calls) == 1
    assert not runner.replies


@pytest.mark.parametrize("version", ["0.8.0", "0.8.1", "0.9.0", "1.0.0", "0.6.9", "0.7.0308", "0.7.308+test", "0.7.308 ", None])
def test_missing_baseline_cannot_silently_rebaseline_or_use_ambiguous_series(metadata, version):
    client, runner = _client(_response(200, _reference(name="heads/main")), _response(404, {"message": "Not Found"}),
                             *_bootstrap_replies(_prior_manifest(version=version)))
    with pytest.raises(baseline.BaselineError, match="prior 0.7.x"):
        _ensure(client, metadata)
    assert all(call[0][1] != "api" or call[0][6] == "GET" for call in runner.calls)


@pytest.mark.parametrize("fields", [{"schema_version": True}, {"schema_version": 2}, {"channel": "stable"},
                                    {"published_at": ""}, {"published_at": "today"}, {"published_at": "2026-10-16T20:00:00"},
                                    {"revision": "short"}, {"assets": {}}, {"assets": []}])
def test_invalid_prior_manifest_is_rejected(fields):
    client, _ = _client(*_bootstrap_replies(_prior_manifest(**fields)))
    with pytest.raises(baseline.BaselineError):
        client.verify_initial_release()


@pytest.mark.parametrize("field,value", [("url", "https://example.invalid/a"), ("name", "wrong.exe"),
                                         ("sha256", "x" * 64), ("size", 1234.0), ("size", True), ("size", 0)])
def test_prior_manifest_packages_require_strict_identity(field, value):
    manifest = _prior_manifest()
    manifest["assets"]["windows-x86_64"][field] = value
    client, _ = _client(*_bootstrap_replies(manifest))
    with pytest.raises(baseline.BaselineError, match="package identity"):
        client.verify_initial_release()


@pytest.mark.parametrize("raw", [b"null", b"[]", b"broken", b'{"version":"0.7.308","version":"0.7.308"}',
                                 b'{"version":NaN}', b'\xff'])
def test_prior_manifest_malformed_utf8_or_json_is_rejected(raw):
    client, _ = _client(*_bootstrap_replies(raw=raw))
    with pytest.raises(baseline.BaselineError, match="stable development manifest"):
        client.verify_initial_release()


@pytest.mark.parametrize("fields", [{"id": True}, {"id": 0}, {"tag_name": "other"}, {"draft": True}, {"draft": 0},
                                    {"prerelease": False}, {"html_url": "https://example.invalid"}])
def test_bootstrap_requires_exact_public_development_release(fields):
    client, runner = _client(_response(200, _public_release(**fields)))
    with pytest.raises(baseline.BaselineError, match="public development release"):
        client.verify_initial_release()
    assert len(runner.calls) == 1


@pytest.mark.parametrize("status", [404, 403, 500])
def test_missing_or_unreadable_public_release_is_not_bootstrap_permission(status):
    client, _ = _client(_response(status, {"message": "Not Found"}))
    with pytest.raises(baseline.BaselineError, match="public development release"):
        client.verify_initial_release()


@pytest.mark.parametrize("assets", [[], [{"name": "update-manifest.json"}] * 2, [None], {}, None])
def test_missing_duplicate_or_malformed_stable_manifest_listing_is_rejected(assets):
    client, _ = _client(_response(200, _public_release()), _response(200, assets))
    with pytest.raises(baseline.BaselineError):
        client.verify_initial_release()


def test_bootstrap_checks_all_pages_and_rejects_duplicate_stable_names_across_pages():
    content = json.dumps(_prior_manifest()).encode()
    stable = _stable_asset(content)
    page = [stable] + [{"name": f"other-{i}"} for i in range(99)]
    client, runner = _client(_response(200, _public_release()), _response(200, page), _response(200, [stable]))
    with pytest.raises(baseline.BaselineError, match="exactly one"):
        client.verify_initial_release()
    assert runner.calls[-1][0][-1].endswith("page=2")


def test_bootstrap_accepts_single_stable_manifest_on_later_page():
    content = json.dumps(_prior_manifest()).encode()
    client, runner = _client(_response(200, _public_release()),
                             _response(200, [{"name": f"other-{i}"} for i in range(100)]),
                             _response(200, [_stable_asset(content)]), Download(content))
    assert client.verify_initial_release() == PREVIOUS
    assert len(runner.calls) == 4


@pytest.mark.parametrize("fields", [{"id": True}, {"id": 0}, {"size": 0}, {"size": True}, {"size": 1_048_577},
                                    {"digest": None}, {"digest": "sha256:bad"}, {"state": "pending"},
                                    {"browser_download_url": "https://example.invalid"}])
def test_bootstrap_rejects_invalid_stable_asset_identity_before_download(fields):
    content = json.dumps(_prior_manifest()).encode()
    client, runner = _client(_response(200, _public_release()), _response(200, [_stable_asset(content, **fields)]))
    with pytest.raises(baseline.BaselineError, match="asset identity"):
        client.verify_initial_release()
    assert len(runner.calls) == 2


@pytest.mark.parametrize("change", ["size", "digest", "failure", "absent", "timeout"])
def test_bootstrap_download_must_match_immutable_asset_identity(change):
    content = json.dumps(_prior_manifest()).encode()
    download = Download(content)
    if change == "size":
        download = Download(content + b" ")
    elif change == "digest":
        download = Download(content.replace(b"0.7.308", b"0.8.000"))
    elif change == "failure":
        download = Download(content, status=1)
    elif change == "absent":
        download = subprocess.CompletedProcess([], 0, "", "")
    elif change == "timeout":
        download = subprocess.TimeoutExpired("gh", 60)
    client, _ = _client(_response(200, _public_release()), _response(200, [_stable_asset(content)]), download)
    with pytest.raises(baseline.BaselineError):
        client.verify_initial_release()


def test_prior_publication_must_be_a_strict_ancestor(repository, metadata):
    root, revision = repository
    _retarget(metadata, revision)
    client = FakeClient(main=revision)
    client.predecessor = revision
    with pytest.raises(baseline.BaselineError, match="follow its published"):
        baseline.ensure_release_baseline(client, baseline.GitCheckout(root), revision=revision, **metadata)
    assert _writes(client) == []


@pytest.mark.parametrize("local_tag", [None, REVISION])
def test_existing_remote_baseline_accepts_absent_or_matching_local_tag(metadata, local_tag):
    client = FakeClient(tag=REVISION)
    assert "version=0.8.0" in _ensure(client, metadata, FakeCheckout(local_tag=local_tag))
    assert _writes(client) == []


@pytest.mark.parametrize("remote_tag", [None, OTHER])
def test_present_local_baseline_blocks_remote_deletion_or_mismatch(metadata, remote_tag):
    client = FakeClient(tag=remote_tag)
    with pytest.raises(baseline.BaselineError, match="checkout's existing baseline"):
        _ensure(client, metadata, FakeCheckout(local_tag=REVISION))
    assert _writes(client) == []
    assert ("predecessor",) not in client.events


@pytest.mark.parametrize("annotated", [False, True])
def test_local_baseline_resolves_both_tag_kinds_without_writing(repository, annotated):
    root, revision = repository
    assert baseline.GitCheckout(root).baseline_revision() is None
    _git(root, "tag", *(["-a", "v0.8.0", "-m", "release"] if annotated else ["v0.8.0"]))
    raw = _git(root, "rev-parse", "refs/tags/v0.8.0")
    assert baseline.GitCheckout(root).baseline_revision() == revision
    assert _git(root, "rev-parse", "refs/tags/v0.8.0") == raw


def test_local_noncommit_tag_fails_closed(repository):
    root, _ = repository
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    _git(root, "tag", "v0.8.0", tree)
    with pytest.raises(baseline.BaselineError, match="Git history"):
        baseline.GitCheckout(root).baseline_revision()


def test_local_tag_read_failure_is_not_absence():
    checkout = baseline.GitCheckout(ROOT, runner=lambda *args, **kwargs: subprocess.CompletedProcess([], 128, "", "failure"))
    with pytest.raises(baseline.BaselineError, match="baseline reference"):
        checkout.baseline_revision()


def test_equal_count_different_baseline_ancestors_cannot_hide_tag_drift(repository, metadata):
    root, first = repository
    _git(root, "checkout", "-b", "first_feature")
    _git(root, "commit", "--allow-empty", "-m", "first feature")
    first_feature = _git(root, "rev-parse", "HEAD")
    _git(root, "tag", "v0.8.0")
    _git(root, "checkout", "-b", "second_feature", first)
    _git(root, "commit", "--allow-empty", "-m", "second feature")
    second_feature = _git(root, "rev-parse", "HEAD")
    _git(root, "checkout", "main")
    _git(root, "merge", "--ff-only", "first_feature")
    _git(root, "merge", "--no-ff", "second_feature", "-m", "integrate")
    revision = _git(root, "rev-parse", "HEAD")
    checkout = baseline.GitCheckout(root)
    assert checkout.commits_since(first_feature, revision) == checkout.commits_since(second_feature, revision) == 2
    _retarget(metadata, revision)
    _change(metadata, version="0.8.2")
    client = FakeClient(tag=second_feature, main=revision)
    with pytest.raises(baseline.BaselineError, match="checkout's existing baseline"):
        baseline.ensure_release_baseline(client, checkout, revision=revision, **metadata)
    assert _writes(client) == []
    assert _git(root, "rev-parse", "v0.8.0") == first_feature
