from __future__ import annotations

import importlib.util
import json
import marshal
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "packaging"))
    spec = importlib.util.spec_from_file_location("frozen_profile_verifier", ROOT / "packaging/verify_frozen_profile.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def bundle(verifier, tmp_path, monkeypatch):
    import verify_release_metadata

    monkeypatch.setattr(verify_release_metadata, "build_version", lambda: "0.8.0")
    revision = "a" * 40
    root, directory = tmp_path / "source", tmp_path / "bundle"
    directory.mkdir()
    exe = directory / "E3.exe"
    exe.write_bytes(b"inert archive fixture, never executed")
    metadata = {"schema_version": 1, "version": "0.8.0", "revision": revision,
                "channel": "development", "platform_key": "windows-x86_64", "packaged": True}
    (directory / "build-info.json").write_text(json.dumps(metadata))
    sources = {name.replace(".", "/") + ".py": b"value = 1\n" for name in verifier.REQUIRED_MODULES}
    sources["packaging/e3_entry.py"] = b"entry = True\n"
    resources = ("config/default.json", "laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md",
                 "laser_aligner/web/app.js", "laser_aligner/web/index.html", "laser_aligner/web/style.css")
    for path in resources:
        sources[path] = b"fixture resource\n"
        destination = directory / path if path.startswith("config/") else directory / "_internal" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(sources[path])
    for path, data in sources.items():
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    codes = {name: compile(sources[name.replace(".", "/") + ".py"], name, "exec", dont_inherit=True) for name in verifier.REQUIRED_MODULES}
    entry = marshal.dumps(compile(sources["packaging/e3_entry.py"], "entry", "exec", dont_inherit=True))
    pyz = SimpleNamespace(toc={name: (0,) for name in codes}, extract=codes.__getitem__)
    archive = SimpleNamespace(extract=lambda name: entry)
    monkeypatch.setattr(verifier, "archive_readers", lambda _exe: (archive, pyz))

    def git(_root, *arguments):
        if arguments[0] == "rev-parse":
            return revision.encode()
        if arguments[0] == "ls-tree":
            return b""
        return sources[arguments[1].split(":", 1)[1]]

    monkeypatch.setattr(verifier, "git", git)
    return SimpleNamespace(root=root, exe=exe, revision=revision, sources=sources, codes=codes,
                           archive=archive, pyz=pyz, metadata=metadata)


def test_exact_archive_reports_identity_modules_resources_and_hash(verifier, bundle):
    result = verifier.verify_archive(bundle.root, bundle.exe, bundle.revision)
    assert result["verified_module_count"] == result["required_module_count"] == len(verifier.REQUIRED_MODULES)
    assert len(result["exe_sha256"]) == 64
    assert result["build_info"]["revision"] == bundle.revision
    assert "config/default.json" in result["verified_resources"]


@pytest.mark.parametrize("change", ["module", "entry", "source", "resource", "missing", "metadata", "revision"])
def test_mixed_or_incomplete_bundle_is_rejected(verifier, bundle, change):
    name = "laser_aligner.state_scope"
    if change == "module":
        bundle.codes[name] = compile("value = 2", name, "exec")
    elif change == "entry":
        bundle.archive.extract = lambda _name: marshal.dumps(compile("entry = False", "entry", "exec"))
    elif change == "source":
        (bundle.root / "laser_aligner/state_scope.py").write_text("value = 2")
    elif change == "resource":
        (bundle.exe.parent / "config/default.json").write_text("different")
    elif change == "missing":
        del bundle.pyz.toc[name]
    elif change == "metadata":
        bundle.metadata["revision"] = "b" * 40
        (bundle.exe.parent / "build-info.json").write_text(json.dumps(bundle.metadata))
    else:
        bundle.revision = "b" * 40
    with pytest.raises(ValueError):
        verifier.verify_archive(bundle.root, bundle.exe, bundle.revision)


def test_profile_comparison_uses_fresh_children_and_private_paths(verifier, tmp_path, monkeypatch):
    calls = []
    metadata = {"version": "0.8.0", "revision": "a" * 40, "channel": "development", "manifest_url": "https://example.invalid"}
    (tmp_path / "build-info.json").write_text(json.dumps(metadata))

    def run(arguments, **options):
        environment = options["env"]
        calls.append((arguments, options))
        private = Path(environment["E3_USER_STATE_DIR"])
        assert Path(environment["E3_CONFIG_PATH"]).is_relative_to(private)
        assert Path(environment["E3_BRIDGE_TOKEN_FILE"]).is_relative_to(private)
        assert json.loads(Path(environment["E3_CONFIG_PATH"]).read_text())["app"]["data_dir"] == str(private / "data")
        assert options["timeout"] == 60 and options["check"] is True
        assert arguments[0] == sys.executable and "--child" in arguments
        assert Path(environment["E3_BUILD_INFO"]).is_relative_to(private)
        return SimpleNamespace(stdout=json.dumps({"home": "normal home", "folders": {"DesktopLocation": "real Desktop"},
            "build_identity": json.loads(Path(environment["E3_BUILD_INFO"]).read_text())}))

    monkeypatch.setattr(verifier.subprocess, "run", run)
    verifier.probe_profiles(tmp_path / "never-started.exe", tmp_path)
    assert len(calls) == 2
    normal, isolated = (call[1]["env"] for call in calls)
    assert "E3_DEV_TEST" not in normal and isolated["E3_DEV_TEST"] == "1"
    original = json.loads(isolated["E3_STATE_ORIGINAL_ENV"])
    assert original == {key: normal.get(key) for key in verifier.PROFILE_KEYS}
    assert all(isolated[key] != normal.get(key) for key in verifier.PROFILE_KEYS)


def test_original_profile_bug_is_a_failing_probe(verifier, tmp_path, monkeypatch):
    metadata = {"version": "0.8.0", "revision": "a" * 40, "channel": "development", "manifest_url": ""}
    (tmp_path / "build-info.json").write_text(json.dumps(metadata))
    monkeypatch.setattr(verifier.subprocess, "run", lambda *_args, **options: SimpleNamespace(stdout=json.dumps({
        "home": "private" if options["env"].get("E3_DEV_TEST") else "normal", "folders": {},
        "build_identity": json.loads(Path(options["env"]["E3_BUILD_INFO"]).read_text()),
    })))
    with pytest.raises(ValueError, match="browsing folders differ"):
        verifier.probe_profiles(tmp_path / "never-started.exe", tmp_path)


@pytest.mark.parametrize("event,args", [
    ("open", ("real-config", "w", os.O_WRONLY)), ("open", ("real-config", None, os.O_CREAT)),
    ("socket.connect", ()), ("subprocess.Popen", ()), ("os.mkdir", ()),
    ("winreg.SetValue", ()), ("sqlite3.connect", ()),
])
def test_probe_denies_side_effects(verifier, event, args):
    with pytest.raises(RuntimeError):
        verifier.deny_side_effects(event, args)
    verifier.deny_side_effects("open", ("read-only-code", "r", os.O_RDONLY))


def test_workflow_verifies_canonical_bundle_before_uploading_it():
    workflow = (ROOT / ".github/workflows/windows-packaging.yml").read_text()
    build = workflow.index("- name: Build complete Windows installer")
    verify = workflow.index("packaging/verify_frozen_profile.py")
    upload = workflow.index("- uses: actions/upload-artifact")
    assert build < verify < upload
    assert "steps.package.outputs.bundle" in workflow
    assert "steps.profile.outputs.report" in workflow
    assert "Frozen code and Windows profile verification failed" in workflow
