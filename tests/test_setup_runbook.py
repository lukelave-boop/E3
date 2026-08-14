from __future__ import annotations

from importlib.resources import files
from pathlib import Path

from laser_aligner import __version__

ROOT = Path(__file__).resolve().parents[1]
RUNBOOK = (
    files("laser_aligner.operator_docs")
    .joinpath("PERMANENT_CAMERA_SETUP.md")
    .read_text(encoding="utf-8")
)


def test_runbook_matches_current_version_and_five_tab_sequence() -> None:
    assert f"Applies to E3 Positioning System `{__version__}`" in RUNBOOK
    headings = (
        "## 1. Camera",
        "## 2. Lens",
        "## 3. Bed Mapping",
        "## 4. Fine Registration",
        "## 5. Accuracy Validation",
    )
    positions = [RUNBOOK.index(heading) for heading in headings]
    assert positions == sorted(positions)


def test_runbook_tracks_exact_setup_and_job_handoff_labels() -> None:
    normalized = " ".join(RUNBOOK.split())
    required_labels = (
        "**Apply all configured controls**",
        "**Solve current-resolution calibration**",
        "**Prepare powered base-map job**",
        "**Home / park, capture and detect base grid**",
        "**Capture ruler overlay**",
        "**Detect honeycomb automatically**",
        "**Fallback: detect with 3 hints**",
        "**Prepare powered mark job**",
        "**Home / park, precision capture**",
        "**Prepare powered validation job**",
    )
    for label in required_labels:
        assert label in RUNBOOK
    assert "Preview's **Start/Play** controls animate the preview only" in normalized
    assert "Do not click the main **Generate** button" in normalized
    assert "dry run" not in RUNBOOK.lower()
    assert "dry frame" not in RUNBOOK.lower()
    assert "Continue directly to Step 4" in RUNBOOK
    assert "These three hints do not calibrate the camera or machine" in RUNBOOK
    assert "are not used as ruler coordinates" in normalized
    assert "keyed 25-point map remains the sole camera-to-machine calibration" in normalized


def test_runbook_is_packaged_and_linked_as_the_canonical_operator_sequence() -> None:
    package_config = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert '"operator_docs/*.md"' in package_config
    link = "laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md"
    for relative in ("README.md", "SAFETY.md", "CURRENT_STATE.md"):
        assert link in (ROOT / relative).read_text(encoding="utf-8")
    for relative in ("docs/MACHINE_SETUP.md", "docs/CALIBRATION.md", "docs/HARDWARE_BRINGUP.md"):
        assert "../laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md" in (
            ROOT / relative
        ).read_text(encoding="utf-8")
