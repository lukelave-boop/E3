from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "packaging" / "verify_release_metadata.py"
REVISION = "abcdef0123456789" * 2 + "abcdef01"
VERSION = "0.8.0"


@pytest.fixture
def verifier(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("e3_verify_release_metadata", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _metadata(platform: str = "windows-x86_64") -> dict[str, object]:
    return {
        "schema_version": 1, "version": VERSION, "revision": REVISION,
        "channel": "development", "platform_key": platform, "packaged": True,
        "repository": "lukelave-boop/E3",
    }


def _write(path: Path, payload: object) -> Path:
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _cli(tmp_path: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
    )


def test_cli_verifies_source_bundle_both_platforms_manifest_and_installer(tmp_path: Path) -> None:
    windows = _write(tmp_path / "windows-source.json", _metadata())
    bundle = _write(tmp_path / "windows-bundle.json", _metadata())
    linux = _write(tmp_path / "linux-bundle.json", _metadata("linux-x86_64"))
    manifest = _write(tmp_path / "manifest.json", {
        key: value for key, value in _metadata().items()
        if key in {"schema_version", "version", "revision", "channel"}
    })
    before = {path.name: path.read_bytes() for path in tmp_path.iterdir()}
    result = _cli(
        tmp_path, "--revision", REVISION, "--version", VERSION,
        "--build-info", "windows-x86_64", str(windows),
        "--build-info", "windows-x86_64", str(bundle),
        "--build-info", "linux-x86_64", str(linux),
        "--manifest", str(manifest), "--installer-version", VERSION,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        f"verified version={VERSION} revision={REVISION} channel=development "
        "build_info=3 manifest=yes installer=yes"
    )
    assert {path.name: path.read_bytes() for path in tmp_path.iterdir()} == before


def test_cli_rejects_one_stale_platform_even_when_other_metadata_matches(tmp_path: Path) -> None:
    windows = _write(tmp_path / "windows.json", _metadata())
    linux = _write(tmp_path / "linux.json", {**_metadata("linux-x86_64"), "revision": "0" * 40})
    result = _cli(
        tmp_path, "--revision", REVISION, "--version", VERSION,
        "--build-info", "windows-x86_64", str(windows),
        "--build-info", "linux-x86_64", str(linux),
    )
    assert result.returncode != 0
    assert str(linux) in result.stderr and "revision must be" in result.stderr
    assert "verified" not in result.stdout


@pytest.mark.parametrize(("field", "value"), [
    ("schema_version", True), ("schema_version", "1"), ("schema_version", 1.0), ("schema_version", 2),
    ("packaged", False), ("packaged", 1), ("packaged", "true"),
    ("platform_key", "linux-x86_64"), ("platform_key", ["windows-x86_64"]),
    ("version", "0.7.999"), ("version", " 0.8.0"), ("version", 8),
    ("revision", REVISION.upper()), ("revision", REVISION[:8]), ("revision", REVISION + " "),
    ("channel", "stable"), ("channel", None),
])
def test_build_metadata_rejects_wrong_values_and_types(verifier, tmp_path: Path, field: str, value: object) -> None:
    path = _write(tmp_path / "build-info.json", {**_metadata(), field: value})
    with pytest.raises(verifier.MetadataError, match=field):
        verifier.verify_release_metadata([("windows-x86_64", path)], revision=REVISION, version=VERSION)


@pytest.mark.parametrize("field", ["schema_version", "version", "revision", "channel", "platform_key", "packaged"])
def test_build_metadata_requires_every_identity_field(verifier, tmp_path: Path, field: str) -> None:
    payload = _metadata()
    del payload[field]
    path = _write(tmp_path / "build-info.json", payload)
    with pytest.raises(verifier.MetadataError, match=f"missing {field}"):
        verifier.verify_release_metadata([("windows-x86_64", path)], revision=REVISION, version=VERSION)


@pytest.mark.parametrize("text", [
    "{", "[]", "null", '{"schema_version":1,"schema_version":1}',
    '{"schema_version":1,"extra":NaN}', '{"schema_version":1,"extra":Infinity}',
])
def test_invalid_json_is_rejected(verifier, tmp_path: Path, text: str) -> None:
    path = tmp_path / "build-info.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(verifier.MetadataError, match="metadata"):
        verifier.verify_release_metadata([("windows-x86_64", path)], revision=REVISION, version=VERSION)


def test_nonfinite_number_in_other_metadata_is_rejected(verifier, tmp_path: Path) -> None:
    path = tmp_path / "build-info.json"
    path.write_text(json.dumps(_metadata())[:-1] + ', "extra": {"number": 1e999}}', encoding="utf-8")
    with pytest.raises(verifier.MetadataError, match="nonfinite JSON number"):
        verifier.verify_release_metadata([("windows-x86_64", path)], revision=REVISION, version=VERSION)


@pytest.mark.parametrize("contents", [None, b"\xff\xfe"])
def test_missing_or_non_utf8_file_is_rejected(verifier, tmp_path: Path, contents: bytes | None) -> None:
    path = tmp_path / "build-info.json"
    if contents is not None:
        path.write_bytes(contents)
    with pytest.raises(verifier.MetadataError, match="cannot read valid metadata"):
        verifier.verify_release_metadata([("windows-x86_64", path)], revision=REVISION, version=VERSION)


@pytest.mark.parametrize(("field", "value"), [
    ("schema_version", True), ("version", "0.7.999"),
    ("revision", "0" * 40), ("channel", "stable"),
])
def test_manifest_must_match_the_same_identity(verifier, tmp_path: Path, field: str, value: object) -> None:
    path = _write(tmp_path / "build-info.json", _metadata())
    manifest = _write(tmp_path / "manifest.json", {**_metadata(), field: value})
    with pytest.raises(verifier.MetadataError, match=field):
        verifier.verify_release_metadata(
            [("windows-x86_64", path)], revision=REVISION, version=VERSION, manifest=manifest,
        )


@pytest.mark.parametrize("field", ["schema_version", "version", "revision", "channel"])
def test_manifest_requires_every_identity_field(verifier, tmp_path: Path, field: str) -> None:
    path = _write(tmp_path / "build-info.json", _metadata())
    payload = _metadata()
    del payload[field]
    manifest = _write(tmp_path / "manifest.json", payload)
    with pytest.raises(verifier.MetadataError, match=f"missing {field}"):
        verifier.verify_release_metadata(
            [("windows-x86_64", path)], revision=REVISION, version=VERSION, manifest=manifest,
        )


@pytest.mark.parametrize("installer_version", ["0.7.999", "0.8.0.0", "0.8.0 ", "", 8])
def test_installer_product_version_is_exact(verifier, tmp_path: Path, installer_version: object) -> None:
    path = _write(tmp_path / "build-info.json", _metadata())
    with pytest.raises(verifier.MetadataError, match="installer ProductVersion"):
        verifier.verify_release_metadata(
            [("windows-x86_64", path)], revision=REVISION, version=VERSION,
            installer_version=installer_version,
        )


@pytest.mark.parametrize("revision", ["", REVISION[:8], "g" * 40, REVISION + " ", " " + REVISION, 10])
def test_expected_revision_must_be_exact_full_hex(verifier, tmp_path: Path, revision: object) -> None:
    path = _write(tmp_path / "build-info.json", _metadata())
    with pytest.raises(verifier.MetadataError, match="exactly 40 hexadecimal"):
        verifier.verify_release_metadata([("windows-x86_64", path)], revision=revision, version=VERSION)


def test_cli_uses_shared_build_version_by_default(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("E3_BUILD_VERSION", "0.8.19")
    path = _write(tmp_path / "build-info.json", {**_metadata(), "version": "0.8.19"})
    result = _cli(tmp_path, "--revision", REVISION, "--build-info", "windows-x86_64", str(path))
    assert result.returncode == 0, result.stderr
    assert "version=0.8.19" in result.stdout


@pytest.mark.parametrize("arguments", [[], ["--revision", REVISION], ["--revision", "abc"]])
def test_cli_rejects_missing_metadata_or_revision(tmp_path: Path, arguments: list[str]) -> None:
    result = _cli(tmp_path, *arguments)
    assert result.returncode != 0
    assert "error:" in result.stderr
    assert "verified" not in result.stdout
