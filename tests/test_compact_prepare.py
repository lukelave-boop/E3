"""Exercise real Git patching across Windows source/patch line endings."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from firmware.marlin_mainboard_compact import prepare as compact

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="Git is required for source preparation")


def git(source: Path, *args: str, data: bytes | None = None) -> bytes:
    return subprocess.check_output(["git", "-C", str(source), *args], input=data, stderr=subprocess.PIPE)


def fixture_tree(tmp_path, monkeypatch, patch_eol=b"\n", source_eol=None, blob_eol=b"\n"):
    source = tmp_path / "source"
    source.mkdir()
    git(source, "init")
    git(source, "config", "core.autocrlf", "true")
    git(source, "config", "user.name", "Preparation test")
    git(source, "config", "user.email", "prepare@example.invalid")
    # Match the real vendor: C++ is LF, INI follows Windows checkout settings.
    attributes = (b"*.cpp text eol=lf\n*.ini text eol=crlf\n" if blob_eol == b"\n"
                  else b"*.cpp -text\n*.ini -text\n")
    (source / ".gitattributes").write_bytes(attributes)
    git(source, "add", ".gitattributes")
    old = b"// keep context\nint value = 1;\n// keep trailing context\n"
    for relative in ("sample.cpp", "settings.ini"):
        blob = git(source, "hash-object", "-w", "--stdin", data=old.replace(b"\n", blob_eol)).decode().strip()
        git(source, "update-index", "--add", "--cacheinfo", "100644", blob, relative)
    git(source, "commit", "-m", "Pinned fixture")
    revision = git(source, "rev-parse", "HEAD").decode().strip()
    git(source, "checkout-index", "--all", "--force")
    # An additional plain tracked file checks that preparation does not rewrite
    # unrelated source or normalize its bytes.
    (source / "untouched.dat").write_bytes(b"untouched\r\n")
    git(source, "add", "untouched.dat")
    git(source, "commit", "-m", "Unrelated source")
    revision = git(source, "rev-parse", "HEAD").decode().strip()
    if source_eol is not None:
        # Git's clean filter sees these as the same source, as on Windows.
        for relative in ("sample.cpp", "settings.ini"):
            (source / relative).write_bytes(old.replace(b"\n", source_eol))
    package = tmp_path / "package"
    package.mkdir()
    patch = "".join(
        f"diff --git a/{name} b/{name}\n--- a/{name}\n+++ b/{name}\n"
        "@@ -1,3 +1,3 @@\n // keep context\n-int value = 1;\n+int value = 2;\n // keep trailing context\n"
        for name in ("sample.cpp", "settings.ini")
    ).encode()
    (package / "compact.patch").write_bytes(patch.replace(b"\n", patch_eol))
    (package / "overlay.inc").write_bytes(b"// reviewed overlay\r\n")
    expected = {name: hashlib.sha256(old.replace(b"value = 1", b"value = 2")).hexdigest()
                for name in ("sample.cpp", "settings.ini")}
    expected["overlay.inc"] = hashlib.sha256(b"// reviewed overlay\n").hexdigest()
    (package / "source-files.json").write_text(json.dumps(expected))
    monkeypatch.setattr(compact, "HERE", package)
    monkeypatch.setattr(compact, "BASELINE", {"revision": revision})
    monkeypatch.setattr(compact, "OVERLAYS", {"overlay.inc": package / "overlay.inc"})
    return source


@pytest.mark.parametrize("patch_eol", [b"\n", b"\r\n"], ids=["patch-lf", "patch-crlf"])
@pytest.mark.parametrize("source_eol", [None, b"\n", b"\r\n"], ids=["source-native", "source-lf", "source-crlf"])
def test_prepare_mixed_windows_endings(tmp_path, monkeypatch, patch_eol, source_eol):
    source = fixture_tree(tmp_path, monkeypatch, patch_eol, source_eol)
    unrelated = (source / "untouched.dat").read_bytes()
    compact.prepare(source)
    compact.verify(source)
    compact.prepare(source)
    assert (source / "untouched.dat").read_bytes() == unrelated
    assert git(source, "config", "core.autocrlf").strip() == b"true"


@pytest.mark.parametrize("patch_eol", [b"\n", b"\r\n"], ids=["patch-lf", "patch-crlf"])
def test_prepare_raw_crlf_git_blobs(tmp_path, monkeypatch, patch_eol):
    source = fixture_tree(tmp_path, monkeypatch, patch_eol=patch_eol, blob_eol=b"\r\n")
    assert b"\r\n" in git(source, "show", "HEAD:sample.cpp")
    assert not git(source, "diff", "HEAD", "--name-only").strip()
    compact.prepare(source)
    compact.verify(source)


def test_changed_source_is_preserved(tmp_path, monkeypatch):
    source = fixture_tree(tmp_path, monkeypatch)
    changed = b"// operator change\n"
    (source / "sample.cpp").write_bytes(changed)
    with pytest.raises(ValueError, match="hash mismatch"):
        compact.prepare(source)
    assert (source / "sample.cpp").read_bytes() == changed
    assert b"value = 1" in (source / "settings.ini").read_bytes()


def test_untracked_source_is_preserved(tmp_path, monkeypatch):
    source = fixture_tree(tmp_path, monkeypatch)
    (source / "unrelated.txt").write_bytes(b"keep this")
    with pytest.raises(ValueError, match="untracked"):
        compact.prepare(source)
    assert (source / "unrelated.txt").read_bytes() == b"keep this"
    assert b"value = 1" in (source / "sample.cpp").read_bytes()


def test_prepared_modified_overlay_is_preserved(tmp_path, monkeypatch):
    source = fixture_tree(tmp_path, monkeypatch)
    compact.prepare(source)
    (source / "overlay.inc").write_bytes(b"operator overlay\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        compact.prepare(source)
    assert (source / "overlay.inc").read_bytes() == b"operator overlay\n"
