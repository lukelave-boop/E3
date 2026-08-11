from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_browser_measurement_fields_accept_unit_suffix_text() -> None:
    html = (ROOT / "laser_aligner/web/index.html").read_text(encoding="utf-8")
    for field in (
        "bedMachineX", "bedMachineY", "designX", "designY",
        "designWidth", "designHeight", "designFeed", "travelFeed",
    ):
        assert f'id="{field}" type="text" inputmode="decimal"' in html


def test_browser_converts_all_measurement_payload_fields_to_mm() -> None:
    source = (ROOT / "laser_aligner/web/app.js").read_text(encoding="utf-8")
    for field in (
        "bedMachineX", "bedMachineY", "designX", "designY",
        "designWidth", "designHeight", "designFeed", "travelFeed",
    ):
        assert f"measurementMm('{field}')" in source
    assert "25.4 mm" in source
    assert "1 in" in source
