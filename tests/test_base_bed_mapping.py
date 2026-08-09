import json
import time
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path

import cv2
import numpy as np
import pytest

from laser_aligner.app import AppContext
from laser_aligner.calibration.bed import BedPoint
from laser_aligner.calibration.registration import (
    base_bed_grid_mark_sizes,
    base_bed_grid_targets,
)
from laser_aligner.camera.controls import ControlResult
from laser_aligner.camera.service import FrameBurst
from laser_aligner.config import WorkArea, load_settings
from laser_aligner.errors import CalibrationError


def _run_prepared_job(context: AppContext, job: object) -> None:
    program = context.machine.preflight_program(job.program.text)
    context.machine.arm_program(context.machine.ARM_PHRASE, program)
    context.machine.start_validated_program(program, job.filename)
    deadline = time.monotonic() + 2.0
    while context.machine.status()["job"]["running"] and time.monotonic() < deadline:
        time.sleep(0.01)
    assert context.machine.status()["job"]["error"] is None


def _context(tmp_path: Path) -> AppContext:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "app": {"data_dir": "data", "simulation": True, "open_browser": False},
                "camera": {"width": 1200, "height": 900},
                "machine": {
                    "backend": "simulator",
                    "work_area": {"x_min": 10, "x_max": 210, "y_min": 10, "y_max": 210},
                },
                "laser": {"boundary_margin_mm": 5},
            }
        ),
        encoding="utf-8",
    )
    return AppContext(load_settings(config_path))


def _keyed_grid_image() -> np.ndarray:
    image = np.full((900, 1200, 3), 190, dtype=np.uint8)
    plate = np.full((700, 700, 3), 235, dtype=np.uint8)
    coordinates = [105, 227, 350, 472, 595]
    for row, y in enumerate(coordinates):
        for column, x in enumerate(coordinates):
            arm = 28 if (row, column) == (1, 1) else 21 if (row, column) == (1, 2) else 14
            cv2.line(plate, (x - arm, y), (x + arm, y), (25, 25, 25), 3)
            cv2.line(plate, (x, y - arm), (x, y + arm), (25, 25, 25), 3)
    source = np.float32([[0, 0], [699, 0], [699, 699], [0, 699]])
    destination = np.float32([[230, 90], [1000, 130], [940, 830], [160, 790]])
    transform = cv2.getPerspectiveTransform(source, destination)
    warped = cv2.warpPerspective(plate, transform, (1200, 900), borderValue=(190, 190, 190))
    mask = cv2.warpPerspective(np.full((700, 700), 255, np.uint8), transform, (1200, 900))
    image[mask > 0] = warped[mask > 0]
    return image


def test_base_grid_is_broad_keyed_and_inside_centered_honeycomb() -> None:
    area = WorkArea(x_min=10, x_max=210, y_min=10, y_max=210)

    targets = base_bed_grid_targets(area, mark_size_mm=4, boundary_margin_mm=5)
    keyed_sizes = base_bed_grid_mark_sizes(4)

    assert len(targets) == 25
    assert sorted({item.machine_x for item in targets}) == pytest.approx([40, 75, 110, 145, 180])
    assert sorted({item.machine_y for item in targets}) == pytest.approx([40, 75, 110, 145, 180])
    assert keyed_sizes == {7: 8.0, 8: 6.0}
    assert all(15 <= item.machine_x <= 205 and 15 <= item.machine_y <= 205 for item in targets)


def test_base_job_requires_no_existing_map_and_uses_guarded_dry_and_powered_programs(tmp_path: Path) -> None:
    context = _context(tmp_path)
    assert context.bed.calibration is None

    dry = context.prepare_base_bed_mapping_job(
        powered=False,
        power_percent=0,
        mark_size_mm=4,
        speed_mm_min=1200,
    )

    assert dry.powered is False
    assert len(dry.targets) == 25
    assert "M3 " not in dry.program.text
    assert "M4 " not in dry.program.text
    lines = dry.program.text.splitlines()
    g21 = next(index for index, line in enumerate(lines) if line.startswith("G21"))
    g90 = next(index for index, line in enumerate(lines) if line.startswith("G90"))
    assert g21 < g90 < lines.index("M5")
    assert dry.program.text.rstrip().endswith("M5\n; End of generated job")
    session = json.loads(context.base_bed_mapping_path.read_text(encoding="utf-8"))
    assert session["schema_version"] == 2
    assert len(session["program_digest"]) == 64
    assert session["kind"] == "base_bed_mapping"
    assert session["powered"] is False

    with pytest.raises(CalibrationError, match="verified visible-marking power"):
        context.prepare_base_bed_mapping_job(
            powered=True,
            power_percent=0,
            mark_size_mm=4,
            speed_mm_min=1200,
        )

    powered = context.prepare_base_bed_mapping_job(
        powered=True,
        power_percent=10,
        mark_size_mm=4,
        speed_mm_min=1200,
    )
    assert powered.program.text.count("M4 S100") == 50
    assert powered.program.bounds_mm == pytest.approx((38, 38, 182, 182))


def test_base_detection_rejects_dry_and_stale_sessions(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.prepare_base_bed_mapping_job(
        powered=False,
        power_percent=0,
        mark_size_mm=4,
        speed_mm_min=1200,
    )
    image = _keyed_grid_image()

    with pytest.raises(CalibrationError, match="dry motion only"):
        context.analyze_base_bed_mapping_image(image)

    context.prepare_base_bed_mapping_job(
        powered=True,
        power_percent=10,
        mark_size_mm=4,
        speed_mm_min=1200,
    )
    context.settings.machine.work_area.x_min = 11
    with pytest.raises(CalibrationError, match="work area.*changed"):
        context.analyze_base_bed_mapping_image(image)


def test_base_detection_rejects_altered_target_session(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.prepare_base_bed_mapping_job(
        powered=True,
        power_percent=10,
        mark_size_mm=4,
        speed_mm_min=1200,
    )
    session = json.loads(context.base_bed_mapping_path.read_text(encoding="utf-8"))
    session["targets"][0]["machine_x"] += 1
    context.base_bed_mapping_path.write_text(json.dumps(session), encoding="utf-8")

    with pytest.raises(CalibrationError, match="targets do not match"):
        context.analyze_base_bed_mapping_image(_keyed_grid_image())


def test_base_session_rejects_string_boolean_instead_of_treating_it_as_true(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    context.prepare_base_bed_mapping_job(
        powered=True,
        power_percent=10,
        mark_size_mm=4,
        speed_mm_min=1200,
    )
    session = json.loads(context.base_bed_mapping_path.read_text(encoding="utf-8"))
    session["powered"] = "false"
    context.base_bed_mapping_path.write_text(json.dumps(session), encoding="utf-8")

    with pytest.raises(CalibrationError, match="invalid 'powered' flag"):
        context._base_bed_mapping_session(require_powered=True)


def test_base_capture_requires_exact_completed_powered_job_receipt(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    context.start()
    try:
        context.prepare_base_bed_mapping_job(
            powered=True,
            power_percent=10,
            mark_size_mm=4,
            speed_mm_min=1200,
        )

        with pytest.raises(CalibrationError, match="has not completed successfully"):
            context.analyze_base_bed_mapping_image(_keyed_grid_image())
    finally:
        context.stop()


def test_saved_verified_capture_receipt_survives_app_restart(tmp_path: Path) -> None:
    image = _keyed_grid_image()
    first = _context(tmp_path)
    first.start()
    try:
        job = first.prepare_base_bed_mapping_job(
            powered=True,
            power_percent=10,
            mark_size_mm=4,
            speed_mm_min=1200,
        )
        _run_prepared_job(first, job)
        session = first._base_bed_mapping_session(require_powered=True)
        result = first.analyze_base_bed_mapping_image(image)
        first.base_bed_mapping_path.write_text(
            json.dumps(
                {
                    **session,
                    "captured_at": time.time(),
                    "detection": result,
                }
            ),
            encoding="utf-8",
        )
    finally:
        first.stop()

    restarted = _context(tmp_path)
    recovered = restarted.analyze_base_bed_mapping_image(image)

    assert recovered["detected"] is True
    assert recovered["candidate"]["can_apply"] is True


def test_saved_capture_receipt_must_be_exact_after_restart(tmp_path: Path) -> None:
    image = _keyed_grid_image()
    first = _context(tmp_path)
    first.start()
    try:
        job = first.prepare_base_bed_mapping_job(
            powered=True,
            power_percent=10,
            mark_size_mm=4,
            speed_mm_min=1200,
        )
        _run_prepared_job(first, job)
        session = first._base_bed_mapping_session(require_powered=True)
        session["captured_at"] = time.time()
        session["detection"] = {"detected": False}
        session["execution_receipt"]["completed_lines"] -= 1
        first.base_bed_mapping_path.write_text(json.dumps(session), encoding="utf-8")
    finally:
        first.stop()

    restarted = _context(tmp_path)
    with pytest.raises(CalibrationError, match="no matching verified capture receipt"):
        restarted.analyze_base_bed_mapping_image(image)


def test_keyed_detection_installs_fresh_map_and_clears_old_refinements(tmp_path: Path) -> None:
    context = _context(tmp_path)
    context.start()
    try:
        context.bed.apply_registration_translation(0.2, -0.1)
        nodes = np.asarray([40, 75, 110, 145, 180], dtype=np.float64)
        context.bed.apply_residual_mesh(
            nodes,
            nodes,
            np.zeros((5, 5, 2), dtype=np.float64),
            fit_rms_mm=0,
            fit_max_mm=0,
        )
        job = context.prepare_base_bed_mapping_job(
            powered=True,
            power_percent=10,
            mark_size_mm=4,
            speed_mm_min=1200,
        )
        _run_prepared_job(context, job)
        image = _keyed_grid_image()
        detection = context.analyze_base_bed_mapping_image(image)
        assert detection["detected"] is True
        assert detection["candidate"]["can_apply"] is True
        assert cv2.imwrite(str(context.bed_reference_path), image)

        applied = context.apply_base_bed_mapping(detection)

        assert applied["point_count"] == 25
        assert applied["inlier_count"] == 25
        assert applied["fine_registration"]["translation_x_mm"] == 0
        assert applied["fine_registration"]["translation_y_mm"] == 0
        assert applied["fine_registration"]["homography_refinement"] is None
        assert applied["residual_mesh"] is None
        assert applied["axis_mapping"] == {"reverse_x": False, "reverse_y": False}
        assert len(context.bed.points) == 25
    finally:
        context.stop()


def test_base_capture_holds_home_parks_and_releases_before_detection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    context.start()
    try:
        job = context.prepare_base_bed_mapping_job(
            powered=True,
            power_percent=10,
            mark_size_mm=4,
            speed_mm_min=1200,
        )
        _run_prepared_job(context, job)
        image = _keyed_grid_image()
        burst = FrameBurst(
            frames=(image,),
            sequence_numbers=(1,),
            discarded_frames=8,
            settle_seconds=1.5,
            elapsed_seconds=2.0,
            sharpness_scores=(1.0,),
            controls=ControlResult(requested={}, applied={}, skipped={}),
        )
        events: list[str] = []

        class LensModel:
            model_id = "test-model"
            quality = {"gate": "pass"}

            @staticmethod
            def undistort(frame: np.ndarray) -> np.ndarray:
                events.append("undistort")
                return frame.copy()

        context.lens._model = LensModel()

        @contextmanager
        def hold():
            events.append("hold")
            try:
                yield
            finally:
                events.append("release")

        monkeypatch.setattr(context.machine, "temporary_stepper_hold", hold)
        monkeypatch.setattr(context.machine, "prepare_photo_position", lambda: events.append("home/park"))

        def capture_burst(*, undistort: bool):
            assert undistort is False
            events.append("capture")
            return burst

        monkeypatch.setattr(context, "precision_camera_burst", capture_burst)
        previous_points = [asdict(point) for point in context.bed.points]

        captured, detection = context.capture_base_bed_mapping()

        assert events == ["hold", "home/park", "capture", "release", "undistort"]
        assert np.array_equal(captured, image)
        assert detection["candidate"]["can_apply"] is True
        assert [asdict(point) for point in context.bed.points] == previous_points
    finally:
        context.stop()


def test_fresh_base_mapping_rejects_legacy_lens_model_without_quality_gate(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)

    class LegacyLensModel:
        model_id = "legacy-model"
        quality: dict[str, object] = {}

    context.lens._model = LegacyLensModel()

    with pytest.raises(CalibrationError, match="pose-diversity"):
        context._require_accepted_lens_calibration()


def test_failed_transaction_restores_previous_points_and_model(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _context(tmp_path)
    context.start()
    try:
        old_points = [asdict(point) for point in context.bed.points]
        old_model = json.loads(context.bed.model_path.read_text(encoding="utf-8"))
        replacement = [
            BedPoint(
                image_x=float(column * 100 + 50),
                image_y=float(row * 100 + 50),
                machine_x=float(column * 35 + 40),
                machine_y=float(row * 35 + 40),
                label=f"replacement {row * 5 + column + 1}",
            )
            for row in range(5)
            for column in range(5)
        ]
        from laser_aligner.calibration import bed as bed_module

        real_atomic_write = bed_module.atomic_write_json
        failed = False

        def fail_model_once(path, payload):
            nonlocal failed
            if path == context.bed.model_path and not failed:
                failed = True
                raise OSError("simulated model write failure")
            return real_atomic_write(path, payload)

        monkeypatch.setattr(bed_module, "atomic_write_json", fail_model_once)
        with pytest.raises(OSError, match="simulated model write failure"):
            context.bed.replace_points_and_solve(replacement, 1200, 900)

        assert [asdict(point) for point in context.bed.points] == old_points
        assert json.loads(context.bed.points_path.read_text(encoding="utf-8")) == old_points
        assert json.loads(context.bed.model_path.read_text(encoding="utf-8")) == old_model
    finally:
        context.stop()
