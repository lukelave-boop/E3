from pathlib import Path

from laser_aligner.calibration.targets import write_default_targets


def test_target_generation(tmp_path: Path) -> None:
    paths = write_default_targets(tmp_path)
    assert len(paths) == 2
    checkerboard = paths[0].read_text(encoding="utf-8")
    assert 'width="220.0mm"' in checkerboard
    assert "9×6 inner corners" in checkerboard
