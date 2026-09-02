from pathlib import Path

from laser_aligner import __version__
from laser_aligner.identity import (
    APPLICATION_NAME,
    DEV_TEST_APP_USER_MODEL_ID,
    DEV_TEST_APPLICATION_NAME,
    DEV_TEST_ENVIRONMENT_VARIABLE,
    DEV_TEST_FEATURE_ENVIRONMENT_VARIABLE,
    DEV_TEST_VERSION_ENVIRONMENT_VARIABLE,
    REVISION_ENVIRONMENT_VARIABLE,
    DevTestIdentity,
    _source_revision,
    application_icon_filename,
    application_identity,
    application_window_title,
    build_revision,
    configure_windows_app_user_model_id,
    dev_test_identity,
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


def test_production_identity_window_icon_and_app_id_remain_unchanged(
    monkeypatch,
) -> None:
    monkeypatch.delenv(DEV_TEST_ENVIRONMENT_VARIABLE, raising=False)
    monkeypatch.setenv(DEV_TEST_FEATURE_ENVIRONMENT_VARIABLE, "ignored feature")
    monkeypatch.setenv(DEV_TEST_VERSION_ENVIRONMENT_VARIABLE, "99.0")
    app_id_calls: list[str] = []

    identity = application_identity()
    title = application_window_title("fixture.e3laser", dirty=True)

    assert dev_test_identity() is None
    assert identity.startswith(f"{APPLICATION_NAME} {__version__} · build ")
    assert title == f"{identity} — fixture.e3laser *"
    assert application_icon_filename() == "e3-positioning-system.svg"
    assert not configure_windows_app_user_model_id(
        platform="win32", setter=lambda app_id: app_id_calls.append(app_id) or 0
    )
    assert app_id_calls == []


def test_dev_environment_selects_exact_title_icon_and_windows_app_id(
    monkeypatch,
) -> None:
    monkeypatch.setenv(DEV_TEST_ENVIRONMENT_VARIABLE, "1")
    monkeypatch.setenv(
        DEV_TEST_FEATURE_ENVIRONMENT_VARIABLE,
        "  Outer\n silhouette  ",
    )
    monkeypatch.setenv(DEV_TEST_VERSION_ENVIRONMENT_VARIABLE, " v0.6.161 ")
    app_id_calls: list[str] = []

    assert dev_test_identity() == DevTestIdentity(
        feature="Outer silhouette",
        version="0.6.161",
    )
    assert application_identity() == (
        f"{DEV_TEST_APPLICATION_NAME} — Outer silhouette — v0.6.161"
    )
    assert application_window_title("fixture.e3laser", dirty=True) == (
        f"{DEV_TEST_APPLICATION_NAME} — Outer silhouette — "
        "v0.6.161 — fixture.e3laser *"
    )
    assert application_icon_filename() == "e3-dev-test.svg"
    assert configure_windows_app_user_model_id(
        platform="win32", setter=lambda app_id: app_id_calls.append(app_id) or 0
    )
    assert app_id_calls == [DEV_TEST_APP_USER_MODEL_ID]


def test_dev_identity_gate_requires_the_exact_enabled_value(monkeypatch) -> None:
    monkeypatch.setenv(DEV_TEST_ENVIRONMENT_VARIABLE, "true")
    monkeypatch.setenv(DEV_TEST_FEATURE_ENVIRONMENT_VARIABLE, "Outer silhouette")
    monkeypatch.setenv(DEV_TEST_VERSION_ENVIRONMENT_VARIABLE, "0.6.161")

    assert dev_test_identity() is None
    assert application_icon_filename() == "e3-positioning-system.svg"

