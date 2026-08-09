from pathlib import Path
from zipfile import ZipFile

import pytest

from tools.make_release import (
    build_release_archive,
    release_files,
    release_version,
)


def test_release_archive_contains_only_explicit_tracked_regular_files(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    package = root / "laser_aligner"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    (root / "README.md").write_text("tracked working-tree content\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=not-for-release\n", encoding="utf-8")
    output = tmp_path / "release.zip"

    build_release_archive(
        root,
        output,
        tracked_paths=(Path("laser_aligner/__init__.py"), Path("README.md")),
    )

    with ZipFile(output) as archive:
        assert archive.namelist() == [
            "laser-camera-aligner/README.md",
            "laser-camera-aligner/laser_aligner/__init__.py",
        ]
        assert archive.read("laser-camera-aligner/README.md") == (
            b"tracked working-tree content\n"
        )
        assert all(".env" not in name for name in archive.namelist())
    assert release_version(root) == "1.2.3"


def test_release_version_rejects_package_metadata_drift(tmp_path: Path) -> None:
    package = tmp_path / "laser_aligner"
    package.mkdir()
    (package / "__init__.py").write_text('__version__ = "1.2.3"\n', encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "example"\nversion = "1.2.4"\n',
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="version mismatch"):
        release_version(tmp_path)


def test_release_manifest_rejects_tracked_symbolic_links(tmp_path: Path) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    target = tmp_path / "operator-secret.txt"
    target.write_text("private\n", encoding="utf-8")
    link = root / "tracked-link.txt"
    try:
        link.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    with pytest.raises(RuntimeError, match="symbolic links"):
        release_files(root, (Path("tracked-link.txt"),))


def test_release_manifest_rejects_files_beneath_a_symbolic_link(
    tmp_path: Path,
) -> None:
    root = tmp_path / "checkout"
    root.mkdir()
    outside = tmp_path / "operator-data"
    outside.mkdir()
    (outside / "secret.txt").write_text("private\n", encoding="utf-8")
    try:
        (root / "package").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Symbolic links are unavailable: {exc}")

    with pytest.raises(RuntimeError, match="symbolic links"):
        release_files(root, (Path("package/secret.txt"),))


@pytest.mark.parametrize("relative", [Path("../secret.txt"), Path("/absolute.txt")])
def test_release_manifest_rejects_paths_outside_checkout(
    tmp_path: Path,
    relative: Path,
) -> None:
    with pytest.raises(RuntimeError, match="Invalid tracked release path"):
        release_files(tmp_path, (relative,))
