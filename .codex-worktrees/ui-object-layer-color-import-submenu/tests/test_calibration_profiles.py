from __future__ import annotations

import json

from laser_aligner.calibration.profiles import (
    CalibrationProfileSignature,
    CalibrationProfileStore,
    signature_from_values,
)


def test_calibration_profile_key_tracks_resolution_and_manual_focus() -> None:
    focus_five = signature_from_values(
        width=1920,
        height=1080,
        controls={"focus_auto": 0, "focus_absolute": 5},
    )
    focus_ten = signature_from_values(
        width=1920,
        height=1080,
        controls={"focus_automatic_continuous": 0, "focus_absolute": 10},
    )

    assert focus_five.key == "1920x1080-manual-focus-005"
    assert focus_ten.key == "1920x1080-manual-focus-010"
    assert focus_five != focus_ten


def test_legacy_stack_is_copied_to_provenance_focus_without_deletion(tmp_path) -> None:
    calibration = {
        "provenance": {
            "camera": {
                "width": 1920,
                "height": 1080,
                "controls": {"focus_auto": 0, "focus_absolute": 5},
            }
        }
    }
    source = tmp_path / "bed_calibration.json"
    source.write_text(json.dumps(calibration), encoding="utf-8")
    (tmp_path / "lens_calibration.json").write_bytes(b"focus-five-lens")
    (tmp_path / "lens_images").mkdir()
    (tmp_path / "lens_images" / "view.png").write_bytes(b"focus-five-view")

    current = CalibrationProfileSignature(1920, 1080, False, 10)
    store = CalibrationProfileStore(tmp_path, current)
    legacy = tmp_path / "calibration_profiles" / "1920x1080-manual-focus-005"

    assert store.active_dir.name == "1920x1080-manual-focus-010"
    assert (legacy / "lens_calibration.json").read_bytes() == b"focus-five-lens"
    assert (legacy / "lens_images" / "view.png").read_bytes() == b"focus-five-view"
    assert source.read_text(encoding="utf-8") == json.dumps(calibration)
    assert not (store.active_dir / "lens_calibration.json").exists()


def test_returning_to_saved_focus_reopens_same_profile(tmp_path) -> None:
    focus_five = CalibrationProfileSignature(1920, 1080, False, 5)
    first = CalibrationProfileStore(tmp_path, focus_five)
    (first.active_dir / "lens_calibration.json").write_bytes(b"saved-focus-five")

    focus_ten = CalibrationProfileSignature(1920, 1080, False, 10)
    second = CalibrationProfileStore(tmp_path, focus_ten)
    (second.active_dir / "lens_calibration.json").write_bytes(b"saved-focus-ten")

    restored = CalibrationProfileStore(tmp_path, focus_five)

    assert (restored.active_dir / "lens_calibration.json").read_bytes() == b"saved-focus-five"
    assert (second.active_dir / "lens_calibration.json").read_bytes() == b"saved-focus-ten"


def test_legacy_migration_never_overwrites_existing_profile_artifact(tmp_path) -> None:
    signature = CalibrationProfileSignature(1920, 1080, False, 5)
    destination = tmp_path / "calibration_profiles" / signature.key
    destination.mkdir(parents=True)
    (destination / "lens_calibration.json").write_bytes(b"existing-profile")
    (tmp_path / "lens_calibration.json").write_bytes(b"legacy-root")

    CalibrationProfileStore(tmp_path, signature)

    assert (destination / "lens_calibration.json").read_bytes() == b"existing-profile"
