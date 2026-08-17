from pathlib import Path

from laser_aligner import __version__
from laser_aligner.identity import (
    APPLICATION_NAME,
    REVISION_ENVIRONMENT_VARIABLE,
    _source_revision,
    application_identity,
    application_window_title,
    build_revision,
)


def test_source_revision_changes_with_application_contents(tmp_path: Path) -> None:
    package_root = tmp_path / "laser_aligner"
    package_root.mkdir()
    source = package_root / "module.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    first = _source_revision(package_root)

    source.write_text("VALUE = 2\n", encoding="utf-8")
    second = _source_revision(package_root)

    assert len(first) == 8
    assert first != second


def test_build_revision_accepts_a_sanitized_packaging_override(monkeypatch) -> None:
    monkeypatch.setenv(REVISION_ENVIRONMENT_VARIABLE, " release candidate #7 ")
    build_revision.cache_clear()
    try:
        assert build_revision() == "release-candidate-7"
    finally:
        build_revision.cache_clear()


def test_application_identity_and_window_title_are_build_first() -> None:
    identity = application_identity()
    title = application_window_title("fixture.e3laser", dirty=True)

    assert identity.startswith(f"{APPLICATION_NAME} {__version__} · build ")
    assert title == f"{identity} — fixture.e3laser *"

