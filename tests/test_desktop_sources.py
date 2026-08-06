import ast
from pathlib import Path

from laser_aligner.desktop.main import build_parser
from laser_aligner.desktop.qt import PYSIDE6_IMPORT_ERROR


def test_desktop_python_sources_parse():
    desktop = Path(__file__).resolve().parents[1] / "laser_aligner" / "desktop"
    for path in desktop.glob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_desktop_cli_exposes_hardware_and_safe_modes():
    parser = build_parser()
    hardware = parser.parse_args(["--hardware"])
    safe = parser.parse_args(["--safe"])

    assert hardware.hardware is True
    assert safe.safe is True


def test_qt_import_guard_is_explicit():
    # CI does not need to install the large desktop dependency to validate core logic.
    assert PYSIDE6_IMPORT_ERROR is None or isinstance(PYSIDE6_IMPORT_ERROR, ImportError)
