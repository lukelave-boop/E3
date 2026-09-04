from __future__ import annotations

import importlib.util
import os
from pathlib import Path

_GUARD_PATH = (
    Path(__file__).resolve().parents[1] / "packaging" / "windows_build_guard.py"
)
_GUARD_SPEC = importlib.util.spec_from_file_location("windows_build_guard", _GUARD_PATH)
assert _GUARD_SPEC is not None and _GUARD_SPEC.loader is not None
windows_build_guard = importlib.util.module_from_spec(_GUARD_SPEC)
_GUARD_SPEC.loader.exec_module(windows_build_guard)


def test_sanitize_build_path_removes_foreign_dll_collisions(
    tmp_path: Path,
) -> None:
    system_root = tmp_path / "Windows"
    system32 = system_root / "System32"
    safe = tmp_path / "safe"
    foreign_icu = tmp_path / "foreign-icu"
    foreign_ssl = tmp_path / "foreign-ssl"
    for directory in (system32, safe, foreign_icu, foreign_ssl):
        directory.mkdir(parents=True)
    (system32 / "icuuc.dll").write_bytes(b"system")
    (foreign_icu / "icuuc.dll").write_bytes(b"foreign")
    (foreign_ssl / "libcrypto-3-x64.dll").write_bytes(b"foreign")
    original = os.pathsep.join(map(str, (foreign_icu, safe, system32, foreign_ssl)))

    sanitized, removed = windows_build_guard.sanitize_build_path(
        original,
        system_root=system_root,
    )

    assert sanitized.split(os.pathsep) == [str(safe), str(system32)]
    assert removed == (str(foreign_icu), str(foreign_ssl))


def test_bundle_guard_rejects_only_foreign_root_runtime_dlls(
    tmp_path: Path,
) -> None:
    internal = tmp_path / "_internal"
    pyside = internal / "PySide6"
    pyside.mkdir(parents=True)
    legitimate_qt_dll = pyside / "Qt6Core.dll"
    legitimate_qt_dll.write_bytes(b"qt")
    foreign_icu = internal / "icuuc.dll"
    foreign_ssl = internal / "libssl-3-x64.dll"
    bundled_ssl = internal / "libssl-3.dll"
    foreign_icu.write_bytes(b"icu")
    foreign_ssl.write_bytes(b"ssl")
    bundled_ssl.write_bytes(b"packaged dependency")

    assert windows_build_guard.forbidden_bundle_dlls(internal) == (
        foreign_icu,
        foreign_ssl,
    )


def test_windows_builder_sanitizes_before_and_validates_after_pyinstaller() -> None:
    source = (
        Path(__file__).resolve().parents[1] / "packaging" / "build_windows.ps1"
    ).read_text(encoding="utf-8")

    sanitize = source.index("sanitize-path")
    pyinstaller = source.index("-m PyInstaller")
    validate = source.index("validate-bundle")
    restore = source.index("$env:PATH = $originalPath")

    assert sanitize < pyinstaller < validate < restore
    assert "from PySide6 import QtCore, QtGui, QtWidgets" in source
