from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (_ROOT / relative).read_text(encoding="utf-8")


def test_normal_browser_script_is_the_only_browser_behavior() -> None:
    normal = _source("run.sh")
    compatibility = _source("run-hardware.sh")

    assert "-m laser_aligner" in normal
    assert "--hardware" not in normal
    assert 'exec "$ROOT/run.sh" "$@"' in compatibility
    assert "-m laser_aligner" not in compatibility
    assert "--hardware" not in compatibility


def test_system_service_uses_the_same_normal_browser_entry_point() -> None:
    service = _source("systemd/laser-camera-aligner.service")

    assert "-m laser_aligner" in service
    assert "--hardware" not in service


def test_normal_desktop_script_is_the_only_desktop_behavior() -> None:
    normal = _source("run-desktop.sh")
    compatibility = _source("run-desktop-hardware.sh")

    assert "-m laser_aligner.desktop.main" in normal
    assert "--hardware" not in normal
    assert "--safe" not in normal
    assert 'exec "$ROOT/run-desktop.sh" "$@"' in compatibility
    assert "-m laser_aligner.desktop.main" not in compatibility
    assert "--hardware" not in compatibility
    assert "--safe" not in compatibility


def test_desktop_install_exposes_one_normal_full_capability_launcher() -> None:
    installer = _source("install-desktop.sh")
    launcher = _source("system/e3-positioning-system.desktop.in")

    assert "system/e3-positioning-system-safe.desktop.in" not in installer
    assert "E3 Positioning System (Safe)" not in installer
    assert 'rm -f "$APPLICATIONS/e3-positioning-system-safe.desktop"' in installer
    assert "Exec=@ROOT@/run-desktop.sh" in launcher
    assert not (_ROOT / "system/e3-positioning-system-safe.desktop.in").exists()


def test_desktop_product_startup_remains_hardware_capable() -> None:
    source = _source("laser_aligner/desktop/main.py")

    assert "hardware_enabled=True" in source
    assert "hardware_enabled=False" not in source
