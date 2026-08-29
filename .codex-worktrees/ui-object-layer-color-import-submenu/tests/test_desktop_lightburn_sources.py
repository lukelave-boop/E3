from __future__ import annotations

from pathlib import Path


def test_lightburn_import_is_exposed_by_the_desktop_file_menu() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "laser_aligner"
        / "desktop"
        / "main_window.py"
    ).read_text(encoding="utf-8")

    assert 'action("import_lightburn", "Import LightBurn project…")' in source
    assert 'self.actions["import_lightburn"].triggered.connect(self.import_lightburn)' in source
    assert '"import_lightburn",\n            "import_image",' in source
    assert "def import_lightburn(self) -> None:" in source
    assert "scan_lightburn_file(" in source
    assert "review_import_manifest(manifest, self)" in source
    assert "load_lightburn_project(" in source
    assert 'FunctionalCommand(\n                    "Import LightBurn project"' in source
    assert "output-disabled layer" in source


def test_lightburn_import_is_documented_as_output_disabled() -> None:
    root = Path(__file__).resolve().parents[1]
    documentation = (root / "docs" / "LIGHTBURN_IMPORT.md").read_text(encoding="utf-8")

    assert "Output disabled" in documentation
    assert "does not arm the laser" in documentation
    assert "embedded LightBurn bitmap" in documentation
    assert "vector `BackupPath`" in documentation
