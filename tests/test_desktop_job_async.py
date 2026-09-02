from __future__ import annotations

import json
import os
import threading
import time
from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtGui, QtTest, QtWidgets

from laser_aligner.air_assist import (
    AirAssistCommands,
    AirAssistMode,
    AirAssistSettings,
)
from laser_aligner.calibration.support import HoneycombSupportReference
from laser_aligner.config import WorkArea
from laser_aligner.core import CoreRuntime
from laser_aligner.desktop import controller as controller_module
from laser_aligner.desktop import main_window as main_window_module
from laser_aligner.desktop.controller import DesktopController
from laser_aligner.desktop.job_preview import JobPreviewCanvas
from laser_aligner.desktop.main_window import E3MainWindow
from laser_aligner.desktop.workspace import WorkspaceView
from laser_aligner.gcode.generator import GcodeProgram
from laser_aligner.gcode.job_plan import JobPlan, PlannedMove, build_job_plan
from laser_aligner.project import (
    LayerMode,
    ObjectKind,
    ProjectDocument,
    ProjectJob,
    SceneObject,
    Transform,
    capture_raster_asset_identity,
)
from laser_aligner.project.job_preflight import (
    JobPreflightReport,
    PreflightFinding,
    PreflightSeverity,
)
from laser_aligner.project.job_preflight import (
    build_job_preflight_report as _real_build_job_preflight_report,
)
from laser_aligner.vision.object_trace import CameraTraceRasterPreview


@pytest.fixture
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application
    application.processEvents()


def _runtime(tmp_path: Path) -> CoreRuntime:
    root = Path(__file__).resolve().parents[1]
    payload = json.loads((root / "config" / "default.json").read_text())
    payload["app"]["data_dir"] = str(tmp_path / "data")
    payload["app"]["open_browser"] = False
    payload["camera"]["autostart"] = False
    # Generic desktop behavior in this module exercises the direct-local
    # MachineService path. Pi-owned execution has dedicated protocol/UI tests.
    payload["machine"]["port"] = "COM_TEST"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return CoreRuntime.from_config(path, hardware_enabled=True)


def _job(move_count: int = 2) -> ProjectJob:
    lines = ["G21", "G90", "M5", "G0 X10 Y10 F2000"]
    x = 10
    for index in range(move_count):
        x = 11 if x == 10 else 10
        lines.append(f"G1 X{x} Y{10 + index % 20} F1000")
    lines.append("M5")
    text = "\n".join(lines)
    plan = build_job_plan(text, power_max=1000, start_position=(0.0, 0.0))
    return ProjectJob(
        text=text,
        bounds_mm=plan.bounds_mm,
        cut_length_mm=plan.cut_distance_mm,
        travel_length_mm=plan.travel_distance_mm,
        estimated_seconds=plan.total_seconds,
        path_count=len(plan.moves),
        point_count=len(plan.moves),
        plan=plan,
    )


def _air_assist_job() -> ProjectJob:
    commands = AirAssistCommands(
        mode=AirAssistMode.GRBL_COOLANT,
        protocol="grbl",
        fan_index=None,
        on_commands=("M8",),
        off_commands=("M9",),
    )
    text = "\n".join(
        (
            "G21",
            "G90",
            "M5",
            "M9",
            '; @E3_LAYER {"id":"air","name":"Air cut","color":"#E35D6A",'
            '"mode":"line","air_assist":true}',
            "G0 X10 Y10 F2000",
            "M8",
            "M4 S100",
            "G1 X20 Y10 F1000",
            "M5",
            "M9",
            "M5",
        )
    )
    plan = build_job_plan(
        text,
        power_max=1000,
        start_position=(0.0, 0.0),
        air_assist_commands=commands,
    )
    return ProjectJob(
        text=text,
        bounds_mm=plan.bounds_mm,
        cut_length_mm=plan.cut_distance_mm,
        travel_length_mm=plan.travel_distance_mm,
        estimated_seconds=plan.total_seconds,
        path_count=sum(1 for move in plan.moves if move.laser_on),
        point_count=len(plan.moves),
        plan=plan,
        air_assist_commands=commands,
    )


def _repeated_plan(move_count: int, *, powered: bool = False) -> JobPlan:
    move = PlannedMove(
        index=0,
        line_number=4,
        start_x=10.0,
        start_y=10.0,
        end_x=11.0,
        end_y=10.0,
        rapid=False,
        laser_on=powered,
        power=100.0 if powered else 0.0,
        feed_mm_min=1000.0,
        layer_id="layer-1",
        layer_name="Line 01",
        layer_color="#E35D6A",
        layer_mode="line",
        pass_index=1,
        pass_count=1,
        source_name="Stress path",
        distance_mm=1.0,
        duration_seconds=1.0,
        start_seconds=0.0,
        end_seconds=1.0,
    )
    return JobPlan(
        moves=(move,) * move_count,
        bounds_mm=(10.0, 10.0, 11.0, 10.0),
        cut_distance_mm=float(move_count if powered else 0),
        travel_distance_mm=float(0 if powered else move_count),
        cut_seconds=float(move_count if powered else 0),
        travel_seconds=float(0 if powered else move_count),
        total_seconds=float(move_count),
        maximum_power=100.0 if powered else 0.0,
        power_max=1000,
        warnings=(),
    )


def _large_job(move_count: int, *, powered: bool = False) -> ProjectJob:
    plan = _repeated_plan(move_count, powered=powered)
    text = "\n".join(("G21", "G90", "M5", "G1 X11 Y10 F1000", "M5"))
    return ProjectJob(
        text=text,
        bounds_mm=plan.bounds_mm,
        cut_length_mm=plan.cut_distance_mm,
        travel_length_mm=plan.travel_distance_mm,
        estimated_seconds=plan.total_seconds,
        path_count=move_count if powered else 0,
        point_count=move_count,
        plan=plan,
    )


def _registration_job(job: ProjectJob) -> SimpleNamespace:
    return SimpleNamespace(
        program=job,
        power_percent=0.0,
        powered=False,
        display_name="Deterministic registration",
        filename="registration.gcode",
        targets=(object(),),
    )


def _legacy_honeycomb_support() -> HoneycombSupportReference:
    return HoneycombSupportReference.from_observations(
        ruler_origin_machine_mm=(29.0, 37.0),
        ruler_x_mark_machine_mm=(219.0, 37.0),
        ruler_xy_mark_machine_mm=(219.0, 227.0),
        ruler_mark_mm=190.0,
        support_width_mm=190.0,
        support_height_mm=190.0,
        bed_calibration_created_at=123.0,
        created_at=456.0,
    )


def _running_identity(
    machine_profile_id: str = "ender-3-s1-pro",
    tool_head_profile_id: str = "generic-diode-10w",
) -> SimpleNamespace:
    return SimpleNamespace(
        machine_profile_id=machine_profile_id,
        tool_head_profile_id=tool_head_profile_id,
    )


def test_legacy_support_selects_local_empty_workspace_but_not_execution() -> None:
    support = _legacy_honeycomb_support()
    machine_area = WorkArea(10.0, 210.0, 10.0, 210.0)
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            _current_honeycomb_support=lambda: support,
            machine_identity=_running_identity(),
        ),
        settings=SimpleNamespace(
            machine=SimpleNamespace(
                work_area=machine_area,
                max_work_feed_mm_min=3000.0,
            )
        ),
    )
    harness = SimpleNamespace(runtime=runtime)

    document = E3MainWindow._new_document(harness)

    assert support.is_execution_verifiable is False
    assert document.name == "Untitled"
    assert document.objects == []
    assert (
        document.coordinate_space
        is main_window_module.CoordinateSpace.HONEYCOMB_LOCAL
    )
    assert document.work_area == main_window_module.Bounds(0.0, 0.0, 190.0, 190.0)
    assert len(document.layers) == 13
    assert document.layers[0].name == "Copy / Printer Paper — CUT"
    assert document.layers[7].name == "Basswood / Poplar Ply — RASTER"
    assert document.layers[12].name == "Copy / Printer Paper — RASTER"

    # The legacy support is visual placement evidence only. The execution gate
    # used before job generation must continue to require schema-2 evidence.
    harness.document = document
    with pytest.raises(ValueError, match="automatic four-corner"):
        E3MainWindow._project_coordinate_frame(harness)


def test_machine_frame_new_project_uses_default_e3_profiles() -> None:
    machine_area = WorkArea(10.0, 210.0, 10.0, 210.0)
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            _current_honeycomb_support=lambda: None,
            machine_identity=_running_identity(),
        ),
        settings=SimpleNamespace(
            machine=SimpleNamespace(
                work_area=machine_area,
                max_work_feed_mm_min=3000.0,
            )
        ),
    )

    document = E3MainWindow._new_document(SimpleNamespace(runtime=runtime))

    assert document.coordinate_space is main_window_module.CoordinateSpace.MACHINE
    assert document.work_area == main_window_module.Bounds(10.0, 10.0, 210.0, 210.0)
    assert len(document.layers) == 13
    assert document.layers[0].name == "Copy / Printer Paper — CUT"
    assert document.layers[12].name == "Copy / Printer Paper — RASTER"


def test_machine_frame_new_project_uses_safe_running_profile_fallback() -> None:
    machine_area = WorkArea(5.0, 405.0, 10.0, 310.0)
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            _current_honeycomb_support=lambda: None,
            machine_identity=_running_identity(
                "generic-marlin",
                "custom-laser-head",
            ),
        ),
        settings=SimpleNamespace(
            machine=SimpleNamespace(
                work_area=machine_area,
                max_work_feed_mm_min=425.0,
            )
        ),
        machine_registry=SimpleNamespace(active_machine_id="next-launch-only"),
    )
    harness = SimpleNamespace(runtime=runtime)

    document = E3MainWindow._new_document(harness)

    assert document.coordinate_space is main_window_module.CoordinateSpace.MACHINE
    assert document.work_area == main_window_module.Bounds(5.0, 10.0, 405.0, 310.0)
    assert len(document.layers) == 1
    layer = document.layers[0]
    assert layer.name == "Line — configure material"
    assert layer.speed_mm_min == 425.0
    assert layer.power_percent == 0.0
    assert layer.passes == 1
    assert layer.output_enabled is False
    assert layer.visible is True
    assert harness._new_project_defaults_source == "safe_neutral"
    assert "No curated material defaults" in harness._new_project_defaults_notice


def test_machine_calibration_preview_uses_active_honeycomb_display_frame() -> None:
    frame = object()
    harness = SimpleNamespace(
        document=ProjectDocument.new(
            work_area=main_window_module.Bounds(0.0, 0.0, 190.0, 190.0),
            coordinate_space=main_window_module.CoordinateSpace.HONEYCOMB_LOCAL,
        ),
        _project_coordinate_frame=lambda: frame,
    )

    assert E3MainWindow._job_preview_coordinate_frame(harness) is frame


def test_machine_workspace_preview_keeps_machine_coordinates() -> None:
    harness = SimpleNamespace(
        document=ProjectDocument.new(),
        _project_coordinate_frame=lambda: pytest.fail(
            "machine-coordinate previews must not request a honeycomb frame"
        ),
    )

    assert E3MainWindow._job_preview_coordinate_frame(harness) is None


def test_legacy_support_empty_workspace_drives_exact_local_camera_area(
    qt_application: QtWidgets.QApplication,
) -> None:
    support = _legacy_honeycomb_support()
    coordinate_frame = support.coordinate_frame
    machine_area = WorkArea(10.0, 210.0, 10.0, 210.0)
    calls: list[dict[str, object]] = []
    launches: list[dict[str, object]] = []
    calibration = object()

    def rectified_frame(**kwargs: object) -> np.ndarray:
        calls.append(dict(kwargs))
        area = kwargs.get("work_area")
        assert isinstance(area, WorkArea)
        return np.zeros(
            (
                int(round((area.y_max - area.y_min) * 2.0)),
                int(round((area.x_max - area.x_min) * 2.0)),
                3,
            ),
            dtype=np.uint8,
        )

    context = SimpleNamespace(
        _current_honeycomb_support=lambda: support,
        machine_identity=_running_identity(),
        current_honeycomb_coordinate_frame=lambda: coordinate_frame,
        trace_camera_work_area=lambda: machine_area,
        has_simulation_workspace_frame=False,
        bed=SimpleNamespace(calibration=calibration),
        lens=SimpleNamespace(model=None),
        bed_calibration_validity=lambda: {"state": "VALID", "reasons": []},
        rectified_frame=rectified_frame,
    )
    runtime = SimpleNamespace(
        running=False,
        context=context,
        settings=SimpleNamespace(
            machine=SimpleNamespace(
                work_area=machine_area,
                max_work_feed_mm_min=3000.0,
            )
        ),
    )
    document = E3MainWindow._new_document(SimpleNamespace(runtime=runtime))
    controller = DesktopController(runtime)
    workspace = WorkspaceView(main_window_module.Bounds(10.0, 10.0, 210.0, 210.0))

    class FinishedSignal:
        def connect(self, _callback: object, _connection: object) -> None:
            return

    def fake_run(operation: object, **kwargs: object) -> object:
        launches.append({"operation": operation, **kwargs})
        return SimpleNamespace(signals=SimpleNamespace(finished=FinishedSignal()))

    controller._run = fake_run  # type: ignore[method-assign]
    delivered: list[object] = []
    controller.cameraImageReady.connect(delivered.append)

    try:
        workspace.set_document(document)
        controller.set_workspace_coordinate_space(document.coordinate_space.value)
        runtime.running = True
        controller.refresh_camera_image()
        assert len(launches) == 1
        operation = launches[0]["operation"]
        assert callable(operation)
        image = operation()
        on_success = launches[0]["on_success"]
        assert callable(on_success)
        on_success(image)

        assert calls == [
            {
                "refresh": True,
                "work_area": WorkArea(0.0, 190.0, 0.0, 190.0),
                "coordinate_frame": coordinate_frame,
            }
        ]
        assert len(delivered) == 1
        payload = delivered[0]
        assert isinstance(payload, dict)
        camera_image = payload["image"]
        assert isinstance(camera_image, QtGui.QImage)
        assert camera_image.size() == QtCore.QSize(380, 380)
        assert payload["camera_image_area"] == {
            "x_min": 0.0,
            "x_max": 190.0,
            "y_min": 0.0,
            "y_max": 190.0,
        }
        local_area = main_window_module.Bounds(0.0, 0.0, 190.0, 190.0)
        workspace.set_camera_image(
            camera_image,
            pixels_per_mm=2.0,
            image_area=local_area,
        )
        assert workspace.workspace_scene.work_area == local_area
        assert workspace._camera_image_area == local_area
    finally:
        controller._camera_live_timer.stop()
        controller.deleteLater()
        workspace.close()
        workspace.deleteLater()
        qt_application.processEvents()


def test_machine_frame_calibration_job_does_not_inherit_local_project_pose() -> None:
    job = _job()
    fake = SimpleNamespace(
        last_job=job,
        last_job_coordinate_frame=None,
        runtime=SimpleNamespace(
            settings=SimpleNamespace(
                laser=SimpleNamespace(
                    spot_offset_x_mm=0.0,
                    spot_offset_y_mm=0.0,
                )
            )
        ),
        _project_execution_signature=lambda: (
            "honeycomb-coordinate-frame",
            1,
            "support-digest",
            "bed-map-digest",
        ),
    )

    assert E3MainWindow._prepared_frame_is_current(fake)


def test_prepared_job_uses_full_precision_spot_offset_authority() -> None:
    job = _job()
    job.spot_offset_mm = (0.0004, -0.0004)
    assert job.plan is not None
    assert (job.plan.spot_offset_x, job.plan.spot_offset_y) == (0.0, 0.0)
    fake = SimpleNamespace(
        last_job=job,
        last_job_coordinate_frame=None,
        runtime=SimpleNamespace(
            settings=SimpleNamespace(
                laser=SimpleNamespace(
                    spot_offset_x_mm=0.0004,
                    spot_offset_y_mm=-0.0004,
                )
            )
        ),
    )

    assert E3MainWindow._prepared_frame_is_current(fake)
    fake.runtime.settings.laser.spot_offset_x_mm = 0.0005
    assert not E3MainWindow._prepared_frame_is_current(fake)


def test_prepared_air_assist_job_requires_exact_runtime_mapping() -> None:
    job = _air_assist_job()
    fake = SimpleNamespace(
        last_job=job,
        last_job_coordinate_frame=None,
        runtime=SimpleNamespace(
            settings=SimpleNamespace(
                machine=SimpleNamespace(
                    protocol="grbl",
                    air_assist=AirAssistSettings(
                        mode=AirAssistMode.GRBL_COOLANT
                    ),
                ),
                laser=SimpleNamespace(
                    spot_offset_x_mm=0.0,
                    spot_offset_y_mm=0.0,
                ),
            )
        ),
    )

    assert E3MainWindow._prepared_frame_is_current(fake)
    fake.runtime.settings.machine.air_assist = AirAssistSettings(
        mode=AirAssistMode.DISABLED
    )
    assert not E3MainWindow._prepared_frame_is_current(fake)


def test_local_job_requires_its_exact_current_execution_signature() -> None:
    current = (
        "honeycomb-coordinate-frame",
        1,
        "support-digest",
        "bed-map-digest",
    )
    job = _job()
    job.coordinate_space = main_window_module.CoordinateSpace.HONEYCOMB_LOCAL
    job.coordinate_frame_signature = current[:3]
    job.execution_signature = current
    polygon = (
        (18.0, 30.0),
        (228.0, 30.0),
        (228.0, 240.0),
        (18.0, 240.0),
    )
    job.guarded_output_polygon_mm = polygon
    fake = SimpleNamespace(
        last_job=job,
        last_job_coordinate_frame=current,
        _project_execution_signature=lambda: current,
        runtime=SimpleNamespace(
            settings=SimpleNamespace(
                laser=SimpleNamespace(
                    guarded_output_polygon_mm=polygon,
                    spot_offset_x_mm=0.0,
                    spot_offset_y_mm=0.0,
                )
            )
        ),
    )

    assert E3MainWindow._prepared_frame_is_current(fake)
    fake.runtime.settings.laser.spot_offset_x_mm = 0.1
    assert not E3MainWindow._prepared_frame_is_current(fake)
    fake.runtime.settings.laser.spot_offset_x_mm = 0.0
    fake.runtime.settings.laser.guarded_output_polygon_mm = (
        polygon[0],
        polygon[1],
        (229.0, 240.0),
        polygon[3],
    )
    assert not E3MainWindow._prepared_frame_is_current(fake)
    fake.runtime.settings.laser.guarded_output_polygon_mm = polygon
    fake._project_execution_signature = lambda: (*current[:2], "moved", current[3])
    assert not E3MainWindow._prepared_frame_is_current(fake)


@pytest.mark.parametrize(
    "configured_polygon",
    [
        None,
        ((18.0, 30.0), (228.0, 30.0), (18.0, 30.0)),
    ],
)
def test_local_prepared_job_rejects_missing_or_malformed_current_authority(
    configured_polygon: object,
) -> None:
    polygon = (
        (18.0, 30.0),
        (228.0, 30.0),
        (228.0, 240.0),
        (18.0, 240.0),
    )
    job = _job()
    job.coordinate_space = main_window_module.CoordinateSpace.HONEYCOMB_LOCAL
    job.guarded_output_polygon_mm = polygon
    fake = SimpleNamespace(
        runtime=SimpleNamespace(
            settings=SimpleNamespace(
                laser=SimpleNamespace(
                    guarded_output_polygon_mm=configured_polygon
                )
            )
        )
    )

    assert not E3MainWindow._prepared_output_authority_is_current(fake, job)


def test_local_start_here_preserves_prepared_output_and_air_assist_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = (
        "honeycomb-coordinate-frame",
        1,
        "support-digest",
        "bed-map-digest",
    )
    polygon = (
        (18.0, 30.0),
        (228.0, 30.0),
        (228.0, 240.0),
        (18.0, 240.0),
    )
    source = _air_assist_job()
    source.spot_offset_mm = (0.0004, -0.0004)
    commands = source.air_assist_commands
    assert commands is not None
    source.coordinate_space = main_window_module.CoordinateSpace.HONEYCOMB_LOCAL
    source.coordinate_frame_signature = current[:3]
    source.execution_signature = current
    source.guarded_output_polygon_mm = polygon
    payloads: list[dict[str, object]] = []

    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Ok,
    )

    def run_background(operation: object, **_kwargs: object) -> None:
        assert callable(operation)
        payload = operation()
        assert isinstance(payload, dict)
        payloads.append(payload)

    def capture_start_here_context(
        plan: JobPlan,
        source_job: ProjectJob,
    ) -> dict[str, object]:
        return E3MainWindow._capture_start_here_request_context(
            harness,
            plan,
            source_job,
        )

    harness = SimpleNamespace(
        document=SimpleNamespace(revision=7),
        last_job=source,
        last_job_revision=7,
        last_job_coordinate_frame=current,
        _current_job_plan=lambda: source.plan,
        _prepared_frame_is_current=lambda: True,
        _invalidate_generated_job=lambda **_kwargs: None,
        _work_area_signature=lambda area: (
            area.x_min,
            area.x_max,
            area.y_min,
            area.y_max,
        ),
        _planned_job_start_position=lambda: (110.0, 110.0),
        _job_request_id=3,
        _job_worker_requests={},
        _job_worker_phases={},
        _job_cancel_reason="",
        _claim_job_preparation=lambda *_args: None,
        show_error=lambda message: pytest.fail(message),
        runtime=SimpleNamespace(
            settings=SimpleNamespace(
                machine=SimpleNamespace(
                    work_area=WorkArea(10.0, 210.0, 10.0, 210.0),
                    protocol="grbl",
                    air_assist=AirAssistSettings(
                        mode=AirAssistMode.GRBL_COOLANT
                    ),
                ),
                laser=SimpleNamespace(
                    power_mode="M4",
                    spot_offset_x_mm=0.0004,
                    spot_offset_y_mm=-0.0004,
                ),
            )
        ),
        _capture_start_here_request_context=capture_start_here_context,
        controller=SimpleNamespace(run_background=run_background),
    )

    E3MainWindow._prepare_start_here(harness, 0)

    assert len(payloads) == 1
    restarted = payloads[0]["job"]
    assert isinstance(restarted, ProjectJob)
    assert restarted.guarded_output_polygon_mm == polygon
    assert restarted.execution_signature == current
    assert restarted.air_assist_commands is commands
    assert restarted.plan is not None
    assert restarted.plan.air_assist_commands is commands
    assert tuple(
        (event.command, event.enabled)
        for event in restarted.plan.air_assist_events
    ) == (("M9", False), ("M8", True), ("M9", False))
    assert "\nM8\n" in restarted.text
    assert restarted.text.count("\nM9\n") == 2
    request_context = payloads[0]["start_here_request_context"]
    assert isinstance(request_context, dict)
    assert request_context["air_assist_commands"] == commands
    assert request_context["spot_offset_mm"] == (0.0004, -0.0004)
    harness.runtime.settings.laser.spot_offset_y_mm = 0.1
    assert not E3MainWindow._start_here_request_context_is_current(
        harness,
        request_context,
    )


def test_local_run_passes_exact_prepared_output_authority() -> None:
    current = (
        "honeycomb-coordinate-frame",
        1,
        "support-digest",
        "bed-map-digest",
    )
    polygon = (
        (18.0, 30.0),
        (228.0, 30.0),
        (228.0, 240.0),
        (18.0, 240.0),
    )
    job = _job()
    job.coordinate_space = main_window_module.CoordinateSpace.HONEYCOMB_LOCAL
    job.coordinate_frame_signature = current[:3]
    job.execution_signature = current
    job.guarded_output_polygon_mm = polygon
    calls: list[dict[str, object]] = []
    harness = SimpleNamespace(
        last_job=job,
        last_job_name="local.gcode",
        last_job_revision=3,
        last_job_powered=False,
        last_job_work_area=(10.0, 210.0, 10.0, 210.0),
        last_job_coordinate_frame=current,
        document=SimpleNamespace(revision=3),
        _job_preparation_busy=False,
        _prepared_frame_is_current=lambda: True,
        _verify_prepared_job_assets=lambda _action: True,
        _work_area_signature=lambda _area: (10.0, 210.0, 10.0, 210.0),
        _pending_calibration_capture=None,
        _invalidate_generated_job=lambda: pytest.fail("job must remain current"),
        show_error=lambda message: pytest.fail(message),
        show_notice=lambda _message: None,
        runtime=SimpleNamespace(
            settings=SimpleNamespace(
                machine=SimpleNamespace(
                    work_area=WorkArea(10.0, 210.0, 10.0, 210.0),
                    allow_motion=True,
                    backend="serial",
                )
            ),
            context=SimpleNamespace(
                machine=SimpleNamespace(status=lambda: {}),
            ),
        ),
        controller=SimpleNamespace(
            run_job=lambda text, name, **kwargs: calls.append(
                {"text": text, "name": name, **kwargs}
            )
        ),
    )

    E3MainWindow.run_current_job(harness)

    assert calls == [
        {
            "text": job.text,
            "name": "local.gcode",
            "arm_phrase": None,
            "honeycomb_signature": current,
            "guarded_output_polygon_mm": polygon,
        }
    ]


def test_pristine_machine_project_switches_to_new_honeycomb_frame() -> None:
    old_document = ProjectDocument.new()
    new_document = ProjectDocument.new(
        work_area=main_window_module.Bounds(0.0, 0.0, 190.0, 190.0),
        coordinate_space=main_window_module.CoordinateSpace.HONEYCOMB_LOCAL,
    )
    notices: list[str] = []
    refreshed: list[bool] = []
    fake = SimpleNamespace(
        project_path=None,
        document=old_document,
        history=SimpleNamespace(
            is_clean=True,
            clear=lambda: None,
            mark_clean=lambda: None,
        ),
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                current_honeycomb_coordinate_frame=lambda: object()
            )
        ),
        _new_document=lambda: new_document,
        _invalidate_generated_job=lambda: None,
        _clear_trace_preview=lambda: None,
        _clear_template_preview=lambda **_kwargs: None,
        _refresh_document=lambda: refreshed.append(True),
        show_notice=notices.append,
        _work_area_signature=E3MainWindow._work_area_signature,
    )
    fake._reconcile_pristine_project_frame = lambda: (
        E3MainWindow._reconcile_pristine_project_frame(fake)
    )

    E3MainWindow._calibration_project_frame_changed(fake)

    assert fake.document is new_document
    assert fake.active_layer_id == new_document.active_layer_id
    assert refreshed == [True]
    assert notices == [
        "Updated the empty project to the detected honeycomb X0 Y0 frame "
        "(190 × 190 mm)"
    ]


def test_pristine_local_project_switches_to_remeasured_honeycomb_dimensions() -> None:
    old_document = ProjectDocument.new(
        work_area=main_window_module.Bounds(0.0, 0.0, 192.0, 192.0),
        coordinate_space=main_window_module.CoordinateSpace.HONEYCOMB_LOCAL,
    )
    new_document = ProjectDocument.new(
        work_area=main_window_module.Bounds(0.0, 0.0, 190.0, 190.0),
        coordinate_space=main_window_module.CoordinateSpace.HONEYCOMB_LOCAL,
    )
    notices: list[str] = []
    refreshed: list[bool] = []
    fake = SimpleNamespace(
        project_path=None,
        document=old_document,
        history=SimpleNamespace(
            is_clean=True,
            clear=lambda: None,
            mark_clean=lambda: None,
        ),
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                current_honeycomb_coordinate_frame=lambda: object()
            )
        ),
        _new_document=lambda: new_document,
        _invalidate_generated_job=lambda: None,
        _clear_trace_preview=lambda: None,
        _clear_template_preview=lambda **_kwargs: None,
        _refresh_document=lambda: refreshed.append(True),
        show_notice=notices.append,
        _work_area_signature=E3MainWindow._work_area_signature,
    )
    fake._reconcile_pristine_project_frame = lambda: (
        E3MainWindow._reconcile_pristine_project_frame(fake)
    )

    E3MainWindow._calibration_project_frame_changed(fake)

    assert fake.document is new_document
    assert fake.active_layer_id == new_document.active_layer_id
    assert refreshed == [True]
    assert notices == [
        "Updated the empty project to the detected honeycomb X0 Y0 frame "
        "(190 × 190 mm)"
    ]


def test_trace_self_heals_pristine_stale_local_project_before_strict_check() -> None:
    old_document = ProjectDocument.new(
        work_area=main_window_module.Bounds(0.0, 0.0, 192.0, 192.0),
        coordinate_space=main_window_module.CoordinateSpace.HONEYCOMB_LOCAL,
    )
    new_document = ProjectDocument.new(
        work_area=main_window_module.Bounds(0.0, 0.0, 190.0, 190.0),
        coordinate_space=main_window_module.CoordinateSpace.HONEYCOMB_LOCAL,
    )
    support = SimpleNamespace(
        is_execution_verifiable=True,
        support_width_mm=190.0,
        support_height_mm=190.0,
        coordinate_frame=object(),
    )
    requests: list[dict[str, object]] = []
    errors: list[str] = []
    fake = SimpleNamespace(
        project_path=None,
        document=old_document,
        history=SimpleNamespace(
            is_clean=True,
            clear=lambda: None,
            mark_clean=lambda: None,
        ),
        runtime=SimpleNamespace(
            context=SimpleNamespace(_current_honeycomb_support=lambda: support)
        ),
        controller=SimpleNamespace(detect_trace_objects=requests.append),
        _new_document=lambda: new_document,
        _invalidate_generated_job=lambda: None,
        _clear_trace_preview=lambda: None,
        _clear_template_preview=lambda **_kwargs: None,
        _refresh_document=lambda: None,
        show_notice=lambda _message: None,
        show_error=errors.append,
        _work_area_signature=E3MainWindow._work_area_signature,
    )
    fake._reconcile_pristine_project_frame = lambda: (
        E3MainWindow._reconcile_pristine_project_frame(fake)
    )
    fake._require_project_machine_work_area_match = lambda: (
        E3MainWindow._require_project_machine_work_area_match(fake)
    )
    fake._project_coordinate_frame = lambda: E3MainWindow._project_coordinate_frame(
        fake
    )

    options = {"detection_mode": "auto"}
    E3MainWindow._detect_trace_objects(fake, options)

    assert fake.document is new_document
    assert requests == [options]
    assert errors == []


def test_new_trace_request_retires_candidates_before_detect_and_keeps_project() -> None:
    document = ProjectDocument.new()
    project_object = SceneObject.rectangle(
        document.active_layer_id,
        name="Existing artwork",
    )
    document.add_object(project_object)
    events: list[str] = []

    def detect(_options: dict[str, object]) -> int:
        events.append("detect")
        assert document.objects == [project_object]
        return 42

    fake = SimpleNamespace(
        document=document,
        controller=SimpleNamespace(
            detect_trace_objects=detect,
            review_signature_is_current=lambda _signature: False,
        ),
        workspace=SimpleNamespace(
            clear_trace_preview=lambda: events.append("clear candidates")
        ),
        trace_panel=SimpleNamespace(
            clear_result=lambda: events.append("clear panel"),
            begin_detection=lambda: events.append("begin detection"),
        ),
        _trace_result={"detections": [{"id": "old-candidate"}]},
        _active_trace_request_id=41,
        _trace_raster_preview_images={},
        _trace_raster_preview_area=None,
        _trace_raster_preview_signature=None,
        _reconcile_pristine_project_frame=lambda: None,
        _require_project_machine_work_area_match=lambda: None,
        _clear_template_preview=lambda **_kwargs: events.append("clear template"),
    )

    E3MainWindow._detect_trace_objects(fake, {"detection_mode": "auto"})

    assert events == [
        "clear candidates",
        "clear panel",
        "clear template",
        "begin detection",
        "detect",
    ]
    assert fake._active_trace_request_id == 42
    assert fake._trace_result is None
    assert document.objects == [project_object]


def test_trace_raster_preview_uses_exact_arrays_and_ignores_late_request(
    qt_application: QtWidgets.QApplication,
) -> None:
    displayed: list[tuple[QtGui.QImage, object, float | None, int | None]] = []
    strategies: list[tuple[str, bool, bool]] = []
    failures: list[tuple[str, bool]] = []
    candidate_clears: list[bool] = []
    selected_panels: list[str] = []
    cancellations: list[bool] = []

    def preview_available(
        strategy: str,
        *,
        selected_strategy: bool,
        native_fitting_completed: bool,
    ) -> None:
        strategies.append(
            (strategy, selected_strategy, native_fitting_completed)
        )

    panel = SimpleNamespace(
        set_raster_preview_available=preview_available,
        raster_preview_mode=lambda: "mask",
        set_detection_failed=lambda message, *, retain_preview: failures.append(
            (message, retain_preview)
        ),
    )
    fake = SimpleNamespace(
        controller=SimpleNamespace(
            review_signature_is_current=lambda signature: signature == ("current",),
            cancel_trace_detection=lambda: cancellations.append(True),
        ),
        trace_panel=panel,
        workspace=SimpleNamespace(
            clear_trace_preview=lambda: candidate_clears.append(True)
        ),
        inspector_tabs=SimpleNamespace(select_panel=selected_panels.append),
        _trace_result=None,
        _active_trace_request_id=8,
        _trace_raster_preview_images={},
        _trace_raster_preview_area=None,
        _trace_raster_preview_signature=None,
        _trace_raster_preview_value=E3MainWindow._trace_raster_preview_value,
        _camera_image_area=E3MainWindow._camera_image_area,
        _camera_image_ready=lambda image, **kwargs: displayed.append(
            (
                image,
                kwargs.get("image_area"),
                kwargs.get("pixels_per_mm"),
                kwargs.get("source_resolution_multiplier"),
            )
        ),
    )
    fake._trace_raster_preview_mode_changed = lambda mode: (
        E3MainWindow._trace_raster_preview_mode_changed(fake, mode)
    )
    preview = SimpleNamespace(
        camera_bgr=np.array([[[10, 20, 30], [40, 50, 60]]], dtype=np.uint8),
        exposed_bed_mask=np.array([[0, 255]], dtype=np.uint8),
        eligible_mask=np.array([[255, 0]], dtype=np.uint8),
        normalized_grayscale=np.array([[42, 84]], dtype=np.uint8),
        foreground_mask=np.array([[0, 255]], dtype=np.uint8),
        contour_mask=np.pad(
            np.full((4, 4), 255, dtype=np.uint8),
            ((0, 0), (4, 0)),
        ),
        strategy="raster_dark",
        selected_strategy=False,
    )
    payload = {
        "preview": preview,
        "review_signature": ("current",),
        "camera_image_area": {
            "x_min": 0.0,
            "x_max": 2.0,
            "y_min": 0.0,
            "y_max": 1.0,
        },
    }

    E3MainWindow._trace_raster_preview_ready(fake, 7, payload)
    assert displayed == []
    E3MainWindow._trace_raster_preview_ready(fake, 8, payload)

    assert strategies == [("raster_dark", False, False)]
    assert set(fake._trace_raster_preview_images) == {
        "camera",
        "exposed_bed",
        "eligible",
        "normalized",
        "mask",
    }
    assert fake._trace_raster_preview_images["camera"].pixelColor(0, 0) == (
        QtGui.QColor(30, 20, 10)
    )
    assert (
        fake._trace_raster_preview_images["exposed_bed"].pixelColor(1, 0).red()
        == 255
    )
    assert fake._trace_raster_preview_images["eligible"].pixelColor(0, 0).red() == 255
    assert (
        fake._trace_raster_preview_images["normalized"].pixelColor(0, 0).red()
        == 42
    )
    assert fake._trace_raster_preview_images["mask"].size() == QtCore.QSize(8, 4)
    assert fake._trace_raster_preview_images["mask"].pixelColor(7, 3).red() == 255
    assert displayed[-1][0].pixelColor(7, 3).red() == 255
    assert displayed[-1][1] == main_window_module.Bounds(0.0, 0.0, 2.0, 1.0)
    assert displayed[-1][2] == 4.0
    assert displayed[-1][3] == 4

    before = dict(fake._trace_raster_preview_images)
    fake.controller._shutdown_started = True
    E3MainWindow._trace_raster_preview_ready(fake, 8, payload)
    assert fake._trace_raster_preview_images == before
    assert len(displayed) == 1
    fake.controller._shutdown_started = False

    E3MainWindow._trace_detection_failed(
        fake,
        8,
        "native fit did not converge",
        True,
    )
    assert fake._active_trace_request_id is None
    assert fake._trace_raster_preview_images == before
    assert failures == [("native fit did not converge", True)]
    assert candidate_clears == [True]
    assert selected_panels == ["trace"]
    assert cancellations == []

    E3MainWindow._trace_raster_preview_ready(fake, 8, payload)
    assert fake._trace_raster_preview_images == before
    assert len(displayed) == 1

    fake._active_trace_request_id = 9
    E3MainWindow._trace_detection_failed(fake, 9, "capture failed", False)
    assert fake._active_trace_request_id is None
    assert fake._trace_raster_preview_images == {}
    assert failures[-1] == ("capture failed", False)
    assert cancellations == []
    qt_application.processEvents()


def test_trace_raster_preview_switches_real_workspace_at_each_physical_scale(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    window, _errors, notices = _window(tmp_path, monkeypatch)
    try:
        caplog.set_level("INFO", logger=main_window_module.__name__)
        window._active_trace_request_id = 41
        monkeypatch.setattr(
            window.controller,
            "review_signature_is_current",
            lambda _signature: True,
        )

        def immutable(values: np.ndarray) -> np.ndarray:
            result = np.ascontiguousarray(values).copy()
            result.setflags(write=False)
            return result

        def qimage_rgba(image: QtGui.QImage) -> np.ndarray:
            converted = image.convertToFormat(
                QtGui.QImage.Format.Format_RGBA8888
            )
            rows = np.frombuffer(
                converted.constBits(),
                dtype=np.uint8,
                count=converted.sizeInBytes(),
            ).reshape(converted.height(), converted.bytesPerLine())
            return rows[:, : converted.width() * 4].reshape(
                converted.height(), converted.width(), 4
            ).copy()

        camera_values = np.empty((6, 8, 3), dtype=np.uint8)
        camera_values[:3, :4] = (5, 25, 245)
        camera_values[:3, 4:] = (15, 235, 45)
        camera_values[3:, :4] = (225, 35, 65)
        camera_values[3:, 4:] = (105, 75, 155)
        camera = immutable(camera_values)
        exposed_values = np.zeros((6, 8), dtype=np.uint8)
        exposed_values[1:3, 4:7] = 255
        exposed = immutable(exposed_values)
        eligible_values = np.zeros((6, 8), dtype=np.uint8)
        eligible_values[1:5, 2:4] = 255
        eligible_values[4:, 5:8] = 255
        eligible = immutable(eligible_values)
        normalized = immutable(
            (np.arange(48, dtype=np.uint16).reshape(6, 8) * 5 + 7).astype(
                np.uint8
            )
        )
        foreground = immutable(np.zeros((6, 8), dtype=np.uint8))
        production_mask_values = np.zeros((24, 32), dtype=np.uint8)
        production_mask_values[2:9, 3:14] = 255
        production_mask_values[14:22, 21:30] = 255
        production_mask_values[18:23, 6:9] = 255
        production_mask = immutable(production_mask_values)
        original_mask = production_mask.tobytes(order="C")
        preview = CameraTraceRasterPreview(
            strategy="raster_dark",
            polarity="dark",
            camera_bgr=camera,
            exposed_bed_mask=exposed,
            eligible_mask=eligible,
            normalized_grayscale=normalized,
            foreground_mask=foreground,
            contour_mask=production_mask,
            threshold_used=127,
            connected_component_count=3,
            selected_strategy=True,
        )
        expected_pixels = {
            "camera": np.concatenate(
                (
                    camera[..., ::-1],
                    np.full((*camera.shape[:2], 1), 255, dtype=np.uint8),
                ),
                axis=2,
            ),
            "exposed_bed": np.concatenate(
                (
                    np.repeat(exposed[..., None], 3, axis=2),
                    np.full((*exposed.shape, 1), 255, dtype=np.uint8),
                ),
                axis=2,
            ),
            "eligible": np.concatenate(
                (
                    np.repeat(eligible[..., None], 3, axis=2),
                    np.full((*eligible.shape, 1), 255, dtype=np.uint8),
                ),
                axis=2,
            ),
            "normalized": np.concatenate(
                (
                    np.repeat(normalized[..., None], 3, axis=2),
                    np.full((*normalized.shape, 1), 255, dtype=np.uint8),
                ),
                axis=2,
            ),
            "mask": np.concatenate(
                (
                    np.repeat(production_mask[..., None], 3, axis=2),
                    np.full((*production_mask.shape, 1), 255, dtype=np.uint8),
                ),
                axis=2,
            ),
        }
        base_ppm = float(window.runtime.settings.calibration.bed.pixels_per_mm)
        # The source raster rounds 7.6 x 5.6 pixels to 8 x 6. Its exact 4x mask
        # is 32 x 24, while the old independent high-resolution rounding asked
        # the workspace for 30 x 22 and left the prior Camera pixmap visible.
        camera_area_width = (camera.shape[1] - 0.4) / base_ppm
        camera_area_height = (camera.shape[0] - 0.4) / base_ppm
        E3MainWindow._trace_raster_preview_ready(
            window,
            41,
            {
                "preview": preview,
                "review_signature": ("current",),
                "camera_image_area": {
                    "x_min": 0.0,
                    "x_max": camera_area_width,
                    "y_min": 0.0,
                    "y_max": camera_area_height,
                },
            },
        )

        assert window.trace_panel.raster_preview_mode() == "mask"
        assert window.workspace._camera_item.pixmap().size() == QtCore.QSize(32, 24)
        assert np.array_equal(
            qimage_rgba(window.workspace._camera_item.pixmap().toImage()),
            expected_pixels["mask"],
        )

        expected_sizes = {
            "camera": QtCore.QSize(8, 6),
            "exposed_bed": QtCore.QSize(8, 6),
            "eligible": QtCore.QSize(8, 6),
            "normalized": QtCore.QSize(8, 6),
            "mask": QtCore.QSize(32, 24),
        }
        slot_messages = [
            record.getMessage()
            for record in caplog.records
            if record.name == main_window_module.__name__
            and record.getMessage().startswith("Camera Trace preview slot ")
        ]
        assert len(slot_messages) == 5
        for mode, expected_size in expected_sizes.items():
            assert any(
                f"slot {mode}: {expected_size.width()} x "
                f"{expected_size.height()}," in message
                for message in slot_messages
            )
        slot_hashes = {
            message.split("slot ", 1)[1].split(":", 1)[0]: message.rsplit(
                "pixel_sha256=", 1
            )[1]
            for message in slot_messages
        }
        assert len(set(slot_hashes.values())) == 5
        assert slot_hashes["mask"] != slot_hashes["camera"]
        displayed_pixels: dict[str, bytes] = {}
        for mode, expected_size in expected_sizes.items():
            assert np.array_equal(
                qimage_rgba(window._trace_raster_preview_images[mode]),
                expected_pixels[mode],
            )
            index = window.trace_panel.raster_preview_combo.findData(mode)
            window.trace_panel.raster_preview_combo.setCurrentIndex(index)
            qt_application.processEvents()
            item = window.workspace._camera_item
            assert item.pixmap().size() == expected_size
            workspace_pixels = qimage_rgba(item.pixmap().toImage())
            assert np.array_equal(workspace_pixels, expected_pixels[mode])
            displayed_pixels[mode] = workspace_pixels.tobytes(order="C")
            mapped_pixels = item.sceneTransform().mapRect(
                QtCore.QRectF(
                    0.0,
                    0.0,
                    float(expected_size.width()),
                    float(expected_size.height()),
                )
            )
            assert mapped_pixels.width() == pytest.approx(camera.shape[1] / base_ppm)
            assert mapped_pixels.height() == pytest.approx(camera.shape[0] / base_ppm)
        assert len(set(displayed_pixels.values())) == 5
        assert displayed_pixels["mask"] != displayed_pixels["camera"]
        assert production_mask.tobytes(order="C") == original_mask
        assert not production_mask.flags.writeable

        window._trace_raster_preview_area = main_window_module.Bounds(
            0.0, 0.0, 1.0, 1.0
        )
        E3MainWindow._trace_raster_preview_mode_changed(window, "mask")
        assert not window.workspace._camera_item.isVisible()
        assert notices[-1].startswith(
            "Could not display Camera Trace Mask preview: Corrected camera raster "
            "dimensions do not match"
        )
    finally:
        _dispose(qt_application, window)


def test_trace_completion_reports_mapping_change_as_request_failure() -> None:
    failures: list[tuple[int, str, bool]] = []
    errors: list[str] = []
    results: list[object] = []
    resumed: list[bool] = []
    fake = SimpleNamespace(
        _trace_request_id=12,
        _trace_review_active=True,
        _trace_sample_image=np.zeros((1, 1, 3), dtype=np.uint8),
        _trace_sample_area=WorkArea(0.0, 1.0, 0.0, 1.0),
        _trace_sample_signature=("old",),
        _current_review_signature=lambda: ("current",),
        _camera_review_active=lambda: True,
        _resume_live_camera_after_review=resumed.append,
        traceDetectionFailed=SimpleNamespace(
            emit=lambda request_id, message, retain: failures.append(
                (request_id, message, retain)
            )
        ),
        errorOccurred=SimpleNamespace(emit=errors.append),
        traceResultReady=SimpleNamespace(emit=results.append),
    )
    payload = {
        "_trace_sample_image": np.zeros((1, 1, 3), dtype=np.uint8),
        "_trace_sample_area": WorkArea(0.0, 1.0, 0.0, 1.0),
        "_trace_sample_signature": ("old",),
    }

    DesktopController._trace_detection_complete(fake, 12, payload)

    assert failures == [
        (
            12,
            "the honeycomb or bed mapping changed during capture; run detection again",
            False,
        )
    ]
    assert errors == [
        "Detect and trace objects failed: the honeycomb or bed mapping changed "
        "during capture; run detection again"
    ]
    assert results == []
    assert resumed == [True]
    assert fake._trace_review_active is False
    assert fake._trace_sample_image is None
    assert fake._trace_sample_area is None
    assert fake._trace_sample_signature is None


def test_controller_publishes_request_scoped_raster_preview_and_timings(
    qt_application: QtWidgets.QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    camera_area = WorkArea(0.0, 2.0, 0.0, 1.0)
    captured_options: list[dict[str, object]] = []

    class Context:
        bed = SimpleNamespace(calibration=object())
        honeycomb_support = SimpleNamespace(reference=None)

        @staticmethod
        def bed_mapping_digest() -> str:
            return "bed-map"

        @staticmethod
        def trace_camera_work_area() -> WorkArea:
            return camera_area

        @staticmethod
        def capture_parked_trace_frame(**options: object) -> np.ndarray:
            captured_options.append(options)
            timing = options["timing"]
            assert isinstance(timing, dict)
            timing.update(
                {
                    "capture_seconds": 0.10,
                    "rectification_seconds": 0.20,
                    "capture_rectification_total_seconds": 0.30,
                }
            )
            return np.array([[[1, 2, 3], [4, 5, 6]]], dtype=np.uint8)

    runtime = SimpleNamespace(
        running=False,
        context=Context(),
        settings=SimpleNamespace(
            calibration=SimpleNamespace(
                bed=SimpleNamespace(pixels_per_mm=1.0)
            ),
            machine=SimpleNamespace(work_area=camera_area),
            laser=SimpleNamespace(
                boundary_margin_mm=0.0,
                spot_offset_x_mm=0.0,
                spot_offset_y_mm=0.0,
                guarded_output_polygon_mm=None,
            ),
        ),
    )
    preview = SimpleNamespace(
        camera_bgr=np.zeros((1, 2, 3), dtype=np.uint8),
        normalized_grayscale=np.zeros((1, 2), dtype=np.uint8),
        foreground_mask=np.zeros((1, 2), dtype=np.uint8),
        contour_mask=np.zeros((4, 8), dtype=np.uint8),
        strategy="raster_dark",
    )

    class Result:
        detections: list[object] = []
        message = "Detection complete"

        @staticmethod
        def to_dict() -> dict[str, object]:
            return {
                "message": "Detection complete",
                "detections": [],
                "diagnostics": {"kept": True},
            }

    def detect_objects(
        _image: np.ndarray,
        _options: object,
        _camera_area: WorkArea,
        _pixels_per_mm: float,
        **kwargs: object,
    ) -> Result:
        callback = kwargs["raster_preview_callback"]
        assert callable(callback)
        callback(preview)
        return Result()

    monkeypatch.setattr(controller_module, "detect_objects", detect_objects)
    controller = DesktopController(runtime)
    previews: list[tuple[int, object]] = []
    results: list[dict[str, object]] = []
    failures: list[tuple[int, str, bool]] = []
    controller.traceRasterPreviewReady.connect(
        lambda request_id, payload: previews.append((request_id, payload))
    )
    controller.traceResultReady.connect(results.append)
    controller.traceDetectionFailed.connect(
        lambda request_id, message, retain_preview: failures.append(
            (request_id, message, retain_preview)
        )
    )

    def run_now(
        operation,
        *,
        on_success,
        on_failure,
        **_kwargs: object,
    ) -> None:
        try:
            on_success(operation())
        except RuntimeError as exc:
            on_failure(str(exc))

    controller._run = run_now
    request_id = controller.detect_trace_objects({})

    assert request_id == 1
    assert len(previews) == 1
    assert previews[0][0] == request_id
    preview_payload = previews[0][1]
    assert isinstance(preview_payload, dict)
    assert preview_payload["preview"] is preview
    assert preview_payload["review_signature"] == ("machine", None, "bed-map")
    assert captured_options[0]["timing"] is not None
    timing = results[0]["diagnostics"]["timing"]
    assert timing["capture_seconds"] == pytest.approx(0.10)
    assert timing["rectification_seconds"] == pytest.approx(0.20)
    assert timing["capture_rectification_total_seconds"] == pytest.approx(0.30)
    assert timing["detect_objects_seconds"] >= 0.0
    assert timing["request_total_seconds"] >= timing["detect_objects_seconds"]

    controller.cancel_trace_detection()

    def fail_after_preview(
        _image: np.ndarray,
        _options: object,
        _camera_area: WorkArea,
        _pixels_per_mm: float,
        **kwargs: object,
    ) -> Result:
        callback = kwargs["raster_preview_callback"]
        assert callable(callback)
        callback(preview)
        raise RuntimeError("native fit did not converge")

    monkeypatch.setattr(controller_module, "detect_objects", fail_after_preview)
    failed_request_id = controller.detect_trace_objects({})

    assert failures[-1] == (
        failed_request_id,
        "native fit did not converge",
        True,
    )
    assert controller._trace_review_active is True
    assert previews[-1][0] == failed_request_id

    controller.cancel_trace_detection()
    assert controller._trace_review_active is False

    def fail_before_preview(*_args: object, **_kwargs: object) -> Result:
        raise RuntimeError("normalization failed")

    monkeypatch.setattr(controller_module, "detect_objects", fail_before_preview)
    failed_request_id = controller.detect_trace_objects({})

    assert failures[-1] == (failed_request_id, "normalization failed", False)
    assert controller._trace_review_active is False
    controller.deleteLater()
    qt_application.processEvents()


def test_nonempty_machine_project_is_not_reinterpreted_after_detection() -> None:
    document = ProjectDocument.new()
    document.add_object(
        SceneObject.line(
            document.layers[0].id,
            center=(20.0, 20.0),
            length_mm=10.0,
        )
    )
    fake = SimpleNamespace(
        project_path=None,
        document=document,
        history=SimpleNamespace(is_clean=False),
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                current_honeycomb_coordinate_frame=lambda: object()
            )
        ),
        _invalidate_generated_job=lambda: None,
        _new_document=lambda: pytest.fail("must not reinterpret existing geometry"),
    )

    changed = E3MainWindow._reconcile_pristine_project_frame(fake)

    assert changed is False
    assert fake.document is document


def test_nonempty_local_project_is_not_reinterpreted_after_redetection() -> None:
    document = ProjectDocument.new(
        work_area=main_window_module.Bounds(0.0, 0.0, 192.0, 192.0),
        coordinate_space=main_window_module.CoordinateSpace.HONEYCOMB_LOCAL,
    )
    document.add_object(
        SceneObject.line(
            document.layers[0].id,
            center=(20.0, 20.0),
            length_mm=10.0,
        )
    )
    fake = SimpleNamespace(
        project_path=None,
        document=document,
        history=SimpleNamespace(is_clean=False),
        runtime=SimpleNamespace(
            context=SimpleNamespace(
                current_honeycomb_coordinate_frame=lambda: object()
            )
        ),
        _invalidate_generated_job=lambda: None,
        _new_document=lambda: pytest.fail("must not reinterpret existing geometry"),
    )

    changed = E3MainWindow._reconcile_pristine_project_frame(fake)

    assert changed is False
    assert fake.document is document


@pytest.mark.parametrize(
    ("project_path", "history_clean"),
    ((Path("saved.e3laser"), True), (None, False)),
    ids=("saved", "dirty"),
)
def test_empty_stale_local_project_is_not_silently_reinterpreted(
    project_path: Path | None,
    history_clean: bool,
) -> None:
    document = ProjectDocument.new(
        work_area=main_window_module.Bounds(0.0, 0.0, 192.0, 192.0),
        coordinate_space=main_window_module.CoordinateSpace.HONEYCOMB_LOCAL,
    )
    fake = SimpleNamespace(
        project_path=project_path,
        document=document,
        history=SimpleNamespace(is_clean=history_clean),
        _new_document=lambda: pytest.fail(
            "must not reinterpret a saved or dirty project"
        ),
    )

    changed = E3MainWindow._reconcile_pristine_project_frame(fake)

    assert changed is False
    assert fake.document is document


def _core_registration_job(job: ProjectJob) -> SimpleNamespace:
    return SimpleNamespace(
        program=GcodeProgram(
            text=job.text,
            bounds_mm=job.bounds_mm,
            cut_length_mm=job.cut_length_mm,
            travel_length_mm=job.travel_length_mm,
            path_count=job.path_count,
            point_count=job.point_count,
        ),
        power_percent=0.0,
        powered=False,
        display_name="Base bed mapping",
        filename="base-bed-mapping.gcode",
        targets=(object(),),
    )


def _add_raster_source(window: E3MainWindow, path: Path) -> None:
    layer = window.document.layers[0]
    layer.mode = LayerMode.RASTER
    layer.output_enabled = True
    window.document.add_object(
        SceneObject(
            name=path.stem,
            kind=ObjectKind.IMAGE,
            layer_id=layer.id,
            transform=Transform(50.0, 50.0, 10.0, 10.0),
            geometry={"asset": str(path)},
        )
    )
    window._refresh_document()


def _wait_until(
    application: QtWidgets.QApplication,
    predicate,
    *,
    timeout: float = 5.0,
) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        application.processEvents()
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for desktop preparation")
        time.sleep(0.002)


def _window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[E3MainWindow, list[str], list[str]]:
    user_data_root = tmp_path / "user-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(user_data_root))
    monkeypatch.setenv("LOCALAPPDATA", str(user_data_root))
    monkeypatch.setenv("APPDATA", str(user_data_root))
    monkeypatch.setattr(
        main_window_module,
        "build_job_preflight_report",
        lambda _document, _context, **_kwargs: JobPreflightReport(),
    )
    window = E3MainWindow(_runtime(tmp_path))
    errors: list[str] = []
    notices: list[str] = []
    window.show_error = errors.append  # type: ignore[method-assign]
    window.show_notice = notices.append  # type: ignore[method-assign]
    window.show()
    return window, errors, notices


def _restore_real_job_preflight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        main_window_module,
        "build_job_preflight_report",
        _real_build_job_preflight_report,
    )


def _add_line_output(window: E3MainWindow) -> SceneObject:
    window.document.layers[0].output_enabled = True
    scene_object = SceneObject.line(
        window.document.layers[0].id,
        center=(20.0, 20.0),
        length_mm=10.0,
    )
    window.document.add_object(scene_object)
    window._refresh_document()
    return scene_object


def _dispose(
    application: QtWidgets.QApplication,
    window: E3MainWindow,
) -> None:
    window.history.mark_clean()
    window._cancel_job_preparation("Test cleanup")
    window._cancel_job_render()
    window.controller.stop()
    window._closing = True
    window.close()
    window.deleteLater()
    application.processEvents()


def test_sidebar_generate_buttons_share_the_generate_action(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[E3MainWindow] = []
    monkeypatch.setattr(
        E3MainWindow,
        "generate_toolpath",
        lambda window: calls.append(window),
    )
    window, errors, _notices = _window(tmp_path, monkeypatch)
    try:
        window.template_panel.generate_button.click()
        window.trace_panel.generate_button.click()
        qt_application.processEvents()

        assert calls == [window, window]
        assert errors == []

        window.actions["generate"].setEnabled(False)
        qt_application.processEvents()
        assert not window.template_panel.generate_button.isEnabled()
        assert not window.trace_panel.generate_button.isEnabled()

        window.template_panel.generate_button.click()
        window.trace_panel.generate_button.click()
        assert calls == [window, window]

        window.actions["generate"].setEnabled(True)
        qt_application.processEvents()
        assert window.template_panel.generate_button.isEnabled()
        assert window.trace_panel.generate_button.isEnabled()
    finally:
        _dispose(qt_application, window)


def test_narrow_layout_reset_keeps_stop_and_global_progress_in_bounds(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    try:
        window.resize(900, 680)
        qt_application.processEvents()
        qt_application.processEvents()
        assert window._safety_on_own_row

        removed_keys: list[str] = []
        settings = SimpleNamespace(remove=removed_keys.append)
        with monkeypatch.context() as reset_patch:
            reset_patch.setattr(
                main_window_module.QtCore,
                "QSettings",
                lambda *_args, **_kwargs: settings,
            )
            window._reset_workspace_layout()
        qt_application.processEvents()
        qt_application.processEvents()

        window.job_progress.set_prepared_job(
            "fixture.gcode · 100 commands",
            power_percent=20.0,
            controller_power=200.0,
        )
        window.job_progress.set_job_status(
            {
                "running": True,
                "name": "fixture.gcode",
                "total_lines": 100,
                "completed_lines": 25,
            }
        )
        qt_application.processEvents()
        stop_top_left = window.runtime_strip.stop_button.mapTo(
            window,
            QtCore.QPoint(0, 0),
        )
        stop_rect = QtCore.QRect(
            stop_top_left,
            window.runtime_strip.stop_button.size(),
        )
        status = window.statusBar().geometry()
        progress_top_left = window.job_progress.mapTo(
            window,
            QtCore.QPoint(0, 0),
        )
        progress = QtCore.QRect(progress_top_left, window.job_progress.size())

        assert window._safety_on_own_row
        assert window.contentsRect().contains(stop_rect)
        assert status.contains(progress)
        assert progress.width() >= window.width() * 0.25
        visible_progress_text = window.job_progress.progress.format()
        assert window.job_progress.progress.width() >= (
            window.job_progress.progress.fontMetrics().horizontalAdvance(
                visible_progress_text
            )
            + 16
        )
        assert "max power 20.0% / S200" in window.job_progress.toolTip()
        assert not window.direct_edit_label.isVisible()
        assert not window.cursor_label.isVisible()
        assert not window.selection_label.isVisible()
        assert "mainWindow/state-v7" in removed_keys
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_next_launch_ender_selection_preserves_running_machine_authority(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, notices = _window(tmp_path, monkeypatch)
    try:
        _wait_until(
            qt_application,
            lambda: window.runtime.running and not window.controller.has_active_tasks,
        )
        notices.clear()
        running_machine_id = window.runtime.running_machine_id
        running_area = window.runtime.settings.machine.work_area
        running_bounds = main_window_module.Bounds(
            running_area.x_min,
            running_area.y_min,
            running_area.x_max,
            running_area.y_max,
        )
        assert window.runtime.context.machine_identity.machine_profile_id == "custom-machine"
        assert (
            window.runtime.context.machine_identity.tool_head_profile_id
            == "custom-laser-head"
        )
        assert window._new_project_defaults_source == "safe_neutral"
        assert len(window.document.layers) == 1
        layer = window.document.layers[0]
        assert layer.name == "Line — configure material"
        assert layer.power_percent == 0.0
        assert layer.output_enabled is False

        registry = window.runtime.machine_registry
        running_saved = registry.get_machine(running_machine_id)
        immutable_running_name = (
            window.runtime.context.machine_identity.machine_name
        )
        running_saved.name = "Renamed machine for next launch"
        registry.update_machine(running_saved)
        window._refresh_machine_selector(running_machine_id)
        running_index = window.machine_selector.findData(running_machine_id)
        running_text = window.machine_selector.itemText(running_index)
        assert "Renamed machine for next launch" in running_text
        assert f"running as {immutable_running_name}" in running_text
        assert (
            window.runtime.context.machine_identity.machine_name
            == immutable_running_name
        )

        next_launch = registry.create_machine(
            "Next-launch Ender + 10 W",
            "ender-3-s1-pro",
            "generic-diode-10w",
        )
        next_launch.machine.work_area = WorkArea(5.0, 405.0, 10.0, 310.0)
        registry.update_machine(next_launch)
        window._refresh_machine_selector(next_launch.id)
        next_launch_index = window.machine_selector.findData(next_launch.id)
        assert next_launch_index >= 0
        window._machine_selector_activated(next_launch_index)

        assert registry.active_machine_id == next_launch.id
        assert window.runtime.running_machine_id == running_machine_id
        assert window.runtime.context.machine_identity.machine_profile_id == "custom-machine"
        assert (
            window.runtime.context.machine_identity.tool_head_profile_id
            == "custom-laser-head"
        )

        window.new_project()

        assert window.document.work_area == running_bounds
        assert window.document.work_area != main_window_module.Bounds(
            5.0,
            10.0,
            405.0,
            310.0,
        )
        assert window._new_project_defaults_source == "safe_neutral"
        assert len(window.document.layers) == 1
        layer = window.document.layers[0]
        assert layer.name == "Line — configure material"
        assert layer.power_percent == 0.0
        assert layer.output_enabled is False

        window.material_panel.search.setText("Copy / Printer Paper")
        qt_application.processEvents()
        assert window.material_panel.list.count() == 2
        ender_recipe = window.material_panel.list.item(0)
        assert "Incompatible" in ender_recipe.text()
        assert (
            ender_recipe.data(QtCore.Qt.ItemDataRole.UserRole + 1)
            == "incompatible"
        )
        window.material_panel.list.setCurrentItem(ender_recipe)
        qt_application.processEvents()
        assert window.material_panel.apply_button.isEnabled() is False

        assert notices == [window._new_project_defaults_notice]
        assert "No curated material defaults" in notices[0]
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_object_panel_visible_toggle_is_applied_after_item_signal_returns(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _, _ = _window(tmp_path, monkeypatch)
    try:
        layer = window.document.layers[0]
        scene_object = SceneObject(
            name="Traced object 01",
            kind=ObjectKind.RECTANGLE,
            layer_id=layer.id,
            transform=Transform(50.0, 50.0, 20.0, 10.0),
        )
        window.document.add_object(scene_object)
        window._refresh_document()
        row = window.object_panel.tree.topLevelItem(0)
        assert row.text(0) == "Traced object 01"

        row.setCheckState(2, QtCore.Qt.CheckState.Unchecked)

        # The native itemChanged callback must finish before the history-driven
        # document refresh destroys and rebuilds the tree items.
        assert window.document.get_object(scene_object.id).visible is True
        qt_application.processEvents()
        assert window.document.get_object(scene_object.id).visible is False
        assert window.object_panel.tree.topLevelItemCount() == 1
    finally:
        _dispose(qt_application, window)


def test_completed_calibration_job_reopens_setup_and_starts_capture(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _, _ = _window(tmp_path, monkeypatch)
    opened: list[tuple[int, str | None]] = []
    monkeypatch.setattr(
        window,
        "open_machine_setup",
        lambda tab_index=0, *, automatic_capture=None: opened.append(
            (tab_index, automatic_capture)
        ),
    )
    window._pending_calibration_capture = {
        "filename": "dense-validation.gcode",
        "tab_index": 4,
        "capture_action": "capture_dense_validation",
        "submitted": True,
        "baseline_job": (100.0, 101.0, "old-digest", "previous.gcode"),
        "started_at": 122.0,
        "program_digest": "dense-digest",
    }
    try:
        window._maybe_start_calibration_capture(
            {
                "job": {
                    "running": False,
                    "started_at": 122.0,
                    "finished_at": 123.0,
                    "name": "dense-validation.gcode",
                    "phase": "complete",
                    "error": None,
                    "program_digest": "dense-digest",
                }
            }
        )
        qt_application.processEvents()

        assert opened == [(4, "capture_dense_validation")]
        assert window._pending_calibration_capture is None
    finally:
        _dispose(qt_application, window)


def test_failed_or_replaced_calibration_job_never_starts_capture(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _, _ = _window(tmp_path, monkeypatch)
    opened: list[tuple[int, str | None]] = []
    monkeypatch.setattr(
        window,
        "open_machine_setup",
        lambda tab_index=0, *, automatic_capture=None: opened.append(
            (tab_index, automatic_capture)
        ),
    )
    pending = {
        "filename": "fine-registration.gcode",
        "tab_index": 3,
        "capture_action": "capture_fine_registration",
        "submitted": True,
        "baseline_job": (100.0, 101.0, "old-digest", "previous.gcode"),
        "started_at": 122.0,
        "program_digest": "fine-digest",
    }
    try:
        window._pending_calibration_capture = dict(pending)
        window._maybe_start_calibration_capture(
            {
                "job": {
                    "running": False,
                    "started_at": 122.0,
                    "finished_at": 123.0,
                    "name": "fine-registration.gcode",
                    "phase": "failed",
                    "error": "Controller error",
                    "program_digest": "fine-digest",
                }
            }
        )
        assert window._pending_calibration_capture is None

        window._pending_calibration_capture = dict(pending)
        window._invalidate_generated_job()
        qt_application.processEvents()

        assert opened == []
        assert window._pending_calibration_capture is None
    finally:
        _dispose(qt_application, window)


def test_stale_same_name_terminal_job_cannot_trigger_automatic_capture(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _, _ = _window(tmp_path, monkeypatch)
    opened: list[tuple[int, str | None]] = []
    monkeypatch.setattr(
        window,
        "open_machine_setup",
        lambda tab_index=0, *, automatic_capture=None: opened.append(
            (tab_index, automatic_capture)
        ),
    )
    stale = {
        "running": False,
        "started_at": 100.0,
        "finished_at": 101.0,
        "name": "same-second.gcode",
        "phase": "complete",
        "error": None,
        "program_digest": "stale-digest",
    }
    window._pending_calibration_capture = {
        "filename": "same-second.gcode",
        "tab_index": 4,
        "capture_action": "capture_dense_validation",
        "submitted": False,
        "baseline_job": None,
    }
    try:
        window._maybe_start_calibration_capture({"job": stale})
        assert opened == []

        pending = window._pending_calibration_capture
        assert pending is not None
        pending["submitted"] = True
        pending["baseline_job"] = window._machine_job_identity(stale)
        window._maybe_start_calibration_capture({"job": stale})
        qt_application.processEvents()

        assert opened == []
        assert window._pending_calibration_capture is pending
    finally:
        _dispose(qt_application, window)


def test_rapid_completed_job_binds_start_identity_before_automatic_capture(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _, _ = _window(tmp_path, monkeypatch)
    opened: list[tuple[int, str | None]] = []
    monkeypatch.setattr(
        window,
        "open_machine_setup",
        lambda tab_index=0, *, automatic_capture=None: opened.append(
            (tab_index, automatic_capture)
        ),
    )
    window._pending_calibration_capture = {
        "filename": "instant.gcode",
        "tab_index": 4,
        "capture_action": "capture_dense_confirmation",
        "submitted": True,
        "baseline_job": (100.0, 101.0, "old-digest", "instant.gcode"),
    }
    terminal = {
        "running": False,
        "started_at": 200.0,
        "finished_at": 200.001,
        "name": "instant.gcode",
        "phase": "complete",
        "error": None,
        "program_digest": "new-digest",
    }
    monkeypatch.setattr(
        window.controller,
        "poll_status",
        lambda: window._maybe_start_calibration_capture({"job": terminal}),
    )
    try:
        window._maybe_start_calibration_capture({"job": terminal})
        assert opened == []

        window._job_started(
            {
                **terminal,
                "running": True,
                "finished_at": None,
                "phase": "streaming",
            }
        )
        qt_application.processEvents()

        assert opened == [(4, "capture_dense_confirmation")]
        assert window._pending_calibration_capture is None
    finally:
        _dispose(qt_application, window)


def test_automatic_capture_reuses_an_open_machine_setup_dialog(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _, _ = _window(tmp_path, monkeypatch)
    calls: list[object] = []

    class ExistingDialog:
        tabs = SimpleNamespace(setCurrentIndex=lambda index: calls.append(("tab", index)))

        def capture_dense_validation(self) -> None:
            calls.append("capture")

        def show(self) -> None:
            calls.append("show")

        def raise_(self) -> None:
            calls.append("raise")

        def activateWindow(self) -> None:
            calls.append("activate")

    existing = ExistingDialog()
    window._machine_setup_dialog = existing  # type: ignore[assignment]
    monkeypatch.setattr(
        main_window_module,
        "MachineSetupDialog",
        lambda *_args, **_kwargs: pytest.fail("opened a nested Machine Setup dialog"),
    )
    try:
        window.open_machine_setup(
            4,
            automatic_capture="capture_dense_validation",
        )
        qt_application.processEvents()

        assert calls == [("tab", 4), "show", "raise", "activate", "capture"]
    finally:
        window._machine_setup_dialog = None
        _dispose(qt_application, window)


def test_generation_keeps_gui_and_stop_live_and_rejects_result(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, notices = _window(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def blocked_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        entered.set()
        assert release.wait(3.0)
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        blocked_generation,
    )
    heartbeat = 0
    timer = QtCore.QTimer()
    timer.setInterval(1)

    def beat() -> None:
        nonlocal heartbeat
        heartbeat += 1

    timer.timeout.connect(beat)
    timer.start()
    try:
        window.generate_toolpath()
        assert window.job_progress.currentWidget() is window.job_progress.preparation_progress
        _wait_until(qt_application, entered.is_set)
        _wait_until(qt_application, lambda: heartbeat >= 5)

        assert window.runtime_strip.stop_button.isEnabled()
        window.runtime_strip.stop_button.click()
        qt_application.processEvents()
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and not window._job_preparation_busy,
        )

        assert window.last_job is None
        assert window.job_progress.currentWidget() is window.job_progress.progress
        assert not list((tmp_path / "data").rglob("*.gcode"))
        assert errors == []
        assert any("Stop cancelled" in notice for notice in notices)
    finally:
        release.set()
        timer.stop()
        _dispose(qt_application, window)


def test_structured_preflight_runs_on_cloned_snapshot_and_reaches_exact_preview(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    _restore_real_job_preflight(monkeypatch)
    source_document = window.document
    source_object = _add_line_output(window)
    gui_thread = threading.get_ident()
    preflight_calls: list[
        tuple[int, ProjectDocument, JobPreflightReport]
    ] = []
    exact_snapshots: list[ProjectDocument] = []
    job = _job()

    def inspected_preflight(
        document, context, *, cancel_check=None
    ) -> JobPreflightReport:
        report = _real_build_job_preflight_report(
            document,
            context,
            cancel_check=cancel_check,
        )
        preflight_calls.append((threading.get_ident(), document, report))
        return report

    def exact_generation(
        document: ProjectDocument,
        *_args,
        **_kwargs,
    ) -> ProjectJob:
        exact_snapshots.append(document)
        return job

    monkeypatch.setattr(
        main_window_module,
        "build_job_preflight_report",
        inspected_preflight,
    )
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        exact_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window._job_preview_dialog is not None
            and not window._job_preparation_busy,
        )

        assert len(preflight_calls) == 1
        worker_thread, snapshot, report = preflight_calls[0]
        assert worker_thread != gui_thread
        assert snapshot is not source_document
        assert snapshot.revision == source_document.revision
        assert snapshot.objects[0] is not source_object
        assert snapshot.objects[0].id == source_object.id
        assert exact_snapshots == [snapshot]
        assert report.ready
        assert report.warning_count >= 1
        assert any(
            finding.code == "execution.not_ready" for finding in report.findings
        )
        assert window.last_job is job
        assert window.last_job_preflight_report is report
        assert window._job_preflight_dialog is None
        preview = window._job_preview_dialog
        assert preview is not None
        assert preview.preflight_report is report
        assert preview.preflight_view is not None
        assert preview.preflight_view.report is report
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_job_generation_propagates_resolved_air_assist_mapping(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    _restore_real_job_preflight(monkeypatch)
    _add_line_output(window)
    window.document.layers[0].air_assist = True
    window.document.layers[0].power_percent = 20.0
    machine = window.runtime.settings.machine
    machine.protocol = "grbl"
    machine.air_assist = AirAssistSettings(mode=AirAssistMode.GRBL_COOLANT)
    laser = window.runtime.settings.laser
    laser.spot_offset_x_mm = 0.0004
    laser.spot_offset_y_mm = -0.0004
    preflight_mappings: list[AirAssistCommands | None] = []
    preflight_spot_offsets: list[tuple[float, float]] = []
    generator_mappings: list[AirAssistCommands | None] = []
    generator_spot_offsets: list[tuple[float, float]] = []
    job = _job()

    def inspected_preflight(
        document, context, *, cancel_check=None
    ) -> JobPreflightReport:
        preflight_mappings.append(context.air_assist_commands)
        preflight_spot_offsets.append(
            (context.spot_offset_x_mm, context.spot_offset_y_mm)
        )
        return _real_build_job_preflight_report(
            document,
            context,
            cancel_check=cancel_check,
        )

    def exact_generation(*_args, **kwargs) -> ProjectJob:
        generator_mappings.append(kwargs["air_assist_commands"])
        generator_laser = _args[1]
        generator_spot_offsets.append(
            (
                generator_laser.spot_offset_x_mm,
                generator_laser.spot_offset_y_mm,
            )
        )
        job.spot_offset_mm = generator_spot_offsets[-1]
        return job

    monkeypatch.setattr(
        main_window_module,
        "build_job_preflight_report",
        inspected_preflight,
    )
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        exact_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window._job_preview_dialog is not None
            and not window._job_preparation_busy,
        )

        assert len(preflight_mappings) == 1
        commands = preflight_mappings[0]
        assert commands is not None
        assert commands is generator_mappings[0]
        assert commands.on_commands == ("M8",)
        assert commands.off_commands == ("M9",)
        assert commands is not machine.air_assist
        assert preflight_spot_offsets == [(0.0004, -0.0004)]
        assert generator_spot_offsets == preflight_spot_offsets
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_air_assist_mapping_change_cancels_stale_preflight_request(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, notices = _window(tmp_path, monkeypatch)
    _restore_real_job_preflight(monkeypatch)
    _add_line_output(window)
    window.document.layers[0].air_assist = True
    machine = window.runtime.settings.machine
    machine.protocol = "grbl"
    machine.air_assist = AirAssistSettings(mode=AirAssistMode.GRBL_COOLANT)
    entered = threading.Event()
    release = threading.Event()
    exact_calls = 0

    def blocked_preflight(
        document, context, *, cancel_check=None
    ) -> JobPreflightReport:
        report = _real_build_job_preflight_report(
            document,
            context,
            cancel_check=cancel_check,
        )
        assert report.ready
        entered.set()
        assert release.wait(5.0)
        return report

    def exact_generation(*_args, **_kwargs) -> ProjectJob:
        nonlocal exact_calls
        exact_calls += 1
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "build_job_preflight_report",
        blocked_preflight,
    )
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        exact_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(qt_application, entered.is_set)
        machine.air_assist = AirAssistSettings(mode=AirAssistMode.DISABLED)
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
        )

        assert exact_calls == 0
        assert window.last_job is None
        assert errors == []
        assert any("Job preparation cancelled" in notice for notice in notices)
    finally:
        release.set()
        _dispose(qt_application, window)


def test_blocked_preflight_is_modeless_and_never_invokes_exact_or_machine_actions(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    _restore_real_job_preflight(monkeypatch)
    _wait_until(
        qt_application,
        lambda: window.runtime.running and not window.controller.has_active_tasks,
    )
    exact_calls: list[ProjectDocument] = []
    action_calls: list[str] = []

    def exact_generation(
        document: ProjectDocument,
        *_args,
        **_kwargs,
    ) -> ProjectJob:
        exact_calls.append(document)
        return _job()

    def record_action(name: str):
        def operation(*_args, **_kwargs) -> None:
            action_calls.append(name)

        return operation

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        exact_generation,
    )
    try:
        with monkeypatch.context() as action_patch:
            machine = window.runtime.context.machine
            for name in (
                "connect",
                "disconnect",
                "replace_connection",
                "arm",
                "arm_program",
                "disarm",
                "send_command",
                "prepare_photo_position",
                "prepare_job_start",
                "jog",
                "preflight_program",
                "start_validated_program",
                "start_job",
                "request_stop",
                "stop_job",
            ):
                action_patch.setattr(machine, name, record_action(f"machine.{name}"))
            for name in (
                "connect_machine",
                "reconnect_machine",
                "disconnect_machine",
                "park_at_camera_pose",
                "run_job",
                "pause_resume",
                "emergency_stop",
                "send_diagnostic",
                "jog",
            ):
                action_patch.setattr(
                    window.controller,
                    name,
                    record_action(f"controller.{name}"),
                )

            window.generate_toolpath()
            _wait_until(
                qt_application,
                lambda: window._job_preflight_dialog is not None,
            )

            dialog = window._job_preflight_dialog
            assert dialog is not None
            assert dialog.isVisible()
            assert dialog.windowModality() is QtCore.Qt.WindowModality.NonModal
            assert window.isEnabled()
            assert dialog.report.has_blockers
            assert any(
                finding.code == "project.objects_missing"
                for finding in dialog.report.findings
            )
            assert not dialog.continue_button.isEnabled()
            assert exact_calls == []
            assert action_calls == []
            assert window.last_job is None
            assert window.last_job_preflight_report is dialog.report
            assert window._job_preparation_owner is None
            assert not window._job_preparation_busy
            assert window._job_worker_requests == {}
            assert window._job_worker_phases == {}
            assert errors == []
            dialog.close()
            qt_application.processEvents()
    finally:
        _dispose(qt_application, window)


def test_blocked_preflight_navigation_opens_exact_bed_mapping_target_only(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    opened: list[tuple[int, str | None, str | None]] = []
    side_effects: list[str] = []
    report = JobPreflightReport(
        findings=(
            PreflightFinding(
                code="honeycomb.support_not_current",
                severity=PreflightSeverity.BLOCKER,
                title="Honeycomb frame is not current",
                message=(
                    "E3 does not have a current saved honeycomb frame for this "
                    "camera-to-machine map."
                ),
                detail="No honeycomb support reference is recorded.",
                resolution_steps=(
                    "Open Tools → Machine Setup.",
                    "Select 3. Bed Mapping.",
                ),
                navigation_target="machine_setup.bed_mapping",
                navigation_label="Open Bed Mapping",
            ),
        )
    )

    def open_setup(
        tab_index: int = 0,
        *,
        automatic_capture: str | None = None,
        navigation_target: str | None = None,
    ) -> None:
        opened.append((tab_index, automatic_capture, navigation_target))

    monkeypatch.setattr(window, "open_machine_setup", open_setup)
    machine = window.runtime.context.machine
    monkeypatch.setattr(
        machine,
        "prepare_photo_position",
        lambda *_args, **_kwargs: side_effects.append("motion"),
    )
    monkeypatch.setattr(
        window.runtime.context,
        "capture_parked_work_area_reference",
        lambda *_args, **_kwargs: side_effects.append("capture"),
    )
    try:
        window._show_blocked_job_preflight(report)
        dialog = window._job_preflight_dialog
        assert dialog is not None

        dialog.preflight_view.navigation_button.click()
        qt_application.processEvents()

        assert opened == [
            (2, None, "machine_setup.bed_mapping"),
        ]
        assert side_effects == []
        assert errors == []
    finally:
        _dispose(qt_application, window)


@pytest.mark.parametrize(
    ("finding_code", "focus_target"),
    (
        ("honeycomb.frame_missing", "honeycomb_span"),
        ("honeycomb.machine_work_area_missing", "work_area"),
        ("honeycomb.output_polygon_invalid", "guarded_output_polygon"),
    ),
)
def test_machine_manager_preflight_navigation_focuses_finding_section(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    finding_code: str,
    focus_target: str,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    opened: list[dict[str, object]] = []
    report = JobPreflightReport(
        findings=(
            PreflightFinding(
                code=finding_code,
                severity=PreflightSeverity.BLOCKER,
                title="Saved machine configuration is incomplete",
                message="Review the relevant saved-machine configuration.",
                resolution_steps=("Open Machine Manager.",),
                navigation_target="machine_manager",
                navigation_label="Open Machine Manager",
            ),
        )
    )

    monkeypatch.setattr(
        window,
        "open_machine_manager",
        lambda **kwargs: opened.append(dict(kwargs)),
    )
    try:
        window._show_blocked_job_preflight(report)
        dialog = window._job_preflight_dialog
        assert dialog is not None

        dialog.preflight_view.navigation_button.click()
        qt_application.processEvents()

        assert opened == [{"focus_target": focus_target}]
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_unconfigured_air_assist_blocks_before_exact_generation(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    _restore_real_job_preflight(monkeypatch)
    _add_line_output(window)
    window.document.layers[0].air_assist = True
    window.document.layers[0].power_percent = 20.0
    exact_calls = 0

    def exact_generation(*_args, **_kwargs) -> ProjectJob:
        nonlocal exact_calls
        exact_calls += 1
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        exact_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window._job_preflight_dialog is not None
            or bool(errors)
            or exact_calls > 0,
        )

        assert errors == []
        assert exact_calls == 0
        report = window.last_job_preflight_report
        assert report is not None
        assert "air_assist.output_unconfigured" in {
            finding.code for finding in report.findings
        }
        assert window.last_job is None
        assert window._job_preview_dialog is None
    finally:
        _dispose(qt_application, window)


def test_layer_work_feed_above_machine_ceiling_blocks_exact_generation(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    _restore_real_job_preflight(monkeypatch)
    _add_line_output(window)
    limit = window.runtime.settings.machine.max_work_feed_mm_min
    window.document.layers[0].speed_mm_min = limit + 1.0
    exact_calls = 0

    def exact_generation(*_args, **_kwargs) -> ProjectJob:
        nonlocal exact_calls
        exact_calls += 1
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        exact_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window._job_preflight_dialog is not None,
        )

        dialog = window._job_preflight_dialog
        assert dialog is not None
        findings = {
            finding.code: finding for finding in dialog.report.findings
        }
        blocker = findings["layer.work_feed_exceeds_machine_limit"]
        assert blocker.severity.value == "blocker"
        assert blocker.context["layer_id"] == window.document.layers[0].id
        assert blocker.context["requested_mm_min"] == limit + 1.0
        assert blocker.context["limit_mm_min"] == limit
        assert exact_calls == 0
        assert window.last_job is None
        assert window._job_preparation_owner is None
        assert not window._job_preparation_busy
        assert errors == []
        dialog.close()
        qt_application.processEvents()
    finally:
        _dispose(qt_application, window)


@pytest.mark.parametrize(
    "changed_authority",
    ("readiness", "expected_calibration_profile"),
)
def test_local_authority_change_discards_preflight_before_exact_generation(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    changed_authority: str,
) -> None:
    window, errors, notices = _window(tmp_path, monkeypatch)
    _restore_real_job_preflight(monkeypatch)
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *_args, **_kwargs: QtWidgets.QMessageBox.StandardButton.Ok,
    )
    frame = _legacy_honeycomb_support().coordinate_frame
    document = ProjectDocument.new(
        work_area=main_window_module.Bounds(
            0.0,
            0.0,
            frame.width_mm,
            frame.height_mm,
        ),
        coordinate_space=main_window_module.CoordinateSpace.HONEYCOMB_LOCAL,
    )
    window.document = document
    window.active_layer_id = document.active_layer_id
    window.history.clear()
    window.history.mark_clean()
    window._refresh_document()
    _add_line_output(window)

    app_context = window.runtime.context
    active_profile_id = app_context.calibration_profiles.current.key
    monkeypatch.setattr(
        app_context,
        "machine_identity",
        replace(
            app_context.machine_identity,
            expected_calibration_profile_id=active_profile_id,
        ),
    )
    execution_signature = (*frame.provenance_signature, "a" * 64)
    readiness = [("VALID", (), "CURRENT", ())]
    monkeypatch.setattr(
        window,
        "_capture_job_coordinate_authority",
        lambda: (frame, execution_signature),
    )
    monkeypatch.setattr(
        window,
        "_capture_job_coordinate_readiness",
        lambda: readiness[0],
    )

    entered = threading.Event()
    release = threading.Event()
    reports: list[JobPreflightReport] = []
    exact_calls = 0

    def blocked_preflight(
        document, context, *, cancel_check=None
    ) -> JobPreflightReport:
        report = _real_build_job_preflight_report(
            document,
            context,
            cancel_check=cancel_check,
        )
        reports.append(report)
        entered.set()
        assert release.wait(5.0)
        return report

    def exact_generation(*_args, **_kwargs) -> ProjectJob:
        nonlocal exact_calls
        exact_calls += 1
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "build_job_preflight_report",
        blocked_preflight,
    )
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        exact_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(qt_application, entered.is_set)
        assert len(reports) == 1
        assert reports[0].ready

        if changed_authority == "readiness":
            readiness[0] = (
                "STALE",
                ("Bed mapping provenance changed.",),
                "STALE",
                ("Honeycomb support evidence changed.",),
            )
        else:
            monkeypatch.setattr(
                app_context,
                "machine_identity",
                replace(
                    app_context.machine_identity,
                    expected_calibration_profile_id="changed-profile",
                ),
            )
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None
            and not window._job_preparation_busy,
        )

        assert exact_calls == 0
        assert window.last_job is None
        assert window._job_preflight_dialog is None
        assert window._job_preview_dialog is None
        assert errors == []
        assert any("Job preparation cancelled" in notice for notice in notices)
    finally:
        release.set()
        _dispose(qt_application, window)


def test_warning_preflight_does_not_override_authoritative_exact_failure(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    _restore_real_job_preflight(monkeypatch)
    _add_line_output(window)
    reports: list[JobPreflightReport] = []
    exact_calls = 0

    def inspected_preflight(
        document, context, *, cancel_check=None
    ) -> JobPreflightReport:
        report = _real_build_job_preflight_report(
            document,
            context,
            cancel_check=cancel_check,
        )
        reports.append(report)
        return report

    def fail_generation(*_args, **_kwargs) -> ProjectJob:
        nonlocal exact_calls
        exact_calls += 1
        raise ValueError("authoritative exact planning failure")

    monkeypatch.setattr(
        main_window_module,
        "build_job_preflight_report",
        inspected_preflight,
    )
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        fail_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and not window._job_preparation_busy,
        )

        assert len(reports) == 1
        assert reports[0].ready
        assert reports[0].warning_count >= 1
        assert exact_calls == 1
        assert window.last_job is None
        assert window.last_job_preflight_report is None
        assert window._job_preflight_dialog is None
        assert window._job_preview_dialog is None
        assert window.actions["generate"].isEnabled()
        assert errors == [
            "Toolpath generation failed: authoritative exact planning failure"
        ]
    finally:
        _dispose(qt_application, window)


def test_software_stop_cancels_blocked_preflight_worker_before_exact_generation(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, notices = _window(tmp_path, monkeypatch)
    _restore_real_job_preflight(monkeypatch)
    _add_line_output(window)
    entered = threading.Event()
    release = threading.Event()
    exact_calls = 0

    def blocked_preflight(
        document, context, *, cancel_check=None
    ) -> JobPreflightReport:
        entered.set()
        assert release.wait(5.0)
        return _real_build_job_preflight_report(
            document,
            context,
            cancel_check=cancel_check,
        )

    def exact_generation(*_args, **_kwargs) -> ProjectJob:
        nonlocal exact_calls
        exact_calls += 1
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "build_job_preflight_report",
        blocked_preflight,
    )
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        exact_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(qt_application, entered.is_set)

        assert "preflight" in window._job_worker_phases.values()
        assert window.runtime_strip.stop_button.isEnabled()
        window.runtime_strip.stop_button.click()
        qt_application.processEvents()
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None
            and not window._job_preparation_busy,
        )

        assert exact_calls == 0
        assert window.last_job is None
        assert window._job_preflight_dialog is None
        assert window._job_preview_dialog is None
        assert errors == []
        assert any("Stop cancelled" in notice for notice in notices)
    finally:
        release.set()
        _dispose(qt_application, window)


def test_queued_generation_cancelled_by_stop_skips_project_clone(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    queued: list[object] = []
    clone_calls = 0
    original_clone = ProjectDocument.clone

    def capture_task(operation, **_kwargs) -> None:
        queued.append(operation)

    def counted_clone(document: ProjectDocument) -> ProjectDocument:
        nonlocal clone_calls
        clone_calls += 1
        return original_clone(document)

    monkeypatch.setattr(window.controller, "run_background", capture_task)
    monkeypatch.setattr(ProjectDocument, "clone", counted_clone)
    try:
        window.generate_toolpath()
        assert len(queued) == 1
        assert clone_calls == 0

        window.runtime_strip.stop_button.click()
        qt_application.processEvents()

        operation = queued[0]
        assert callable(operation)
        assert operation() is None
        assert clone_calls == 0
        assert window.last_job is None
        assert not window.actions["preview_job"].isEnabled()
        assert not list((tmp_path / "data").rglob("*.gcode"))
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_background_results_are_dispatched_on_gui_thread(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _errors, _notices = _window(tmp_path, monkeypatch)
    gui_thread = threading.get_ident()
    worker_threads: list[int] = []
    callback_threads: list[int] = []
    try:
        window.controller.run_background(
            lambda: worker_threads.append(threading.get_ident()),
            on_success=lambda _result: callback_threads.append(
                threading.get_ident()
            ),
            label="Thread-affinity probe",
        )
        _wait_until(qt_application, lambda: bool(callback_threads))

        assert worker_threads and worker_threads[0] != gui_thread
        assert callback_threads == [gui_thread]
    finally:
        _dispose(qt_application, window)


def test_large_job_text_workspace_and_dialog_render_in_event_loop_slices(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    job = _job(18_000)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: job,
    )
    heartbeat = 0
    timer = QtCore.QTimer()
    timer.setInterval(0)

    def beat() -> None:
        nonlocal heartbeat
        heartbeat += 1

    timer.timeout.connect(beat)
    timer.start()
    try:
        window.generate_toolpath()
        assert window.job_progress.currentWidget() is window.job_progress.preparation_progress
        _wait_until(qt_application, lambda: window.last_job is job)
        _wait_until(qt_application, lambda: not window._job_preparation_busy)

        assert heartbeat >= 5
        assert not window.last_job_powered
        assert 1 <= len(window.workspace._toolpath_items) <= 3
        assert window._job_preview_dialog is not None
        assert 1 <= len(window._job_preview_dialog.canvas._items) <= 3
        assert window.job_progress.currentWidget() is window.job_progress.progress
        assert errors == []
    finally:
        timer.stop()
        _dispose(qt_application, window)


def test_preview_start_job_uses_guarded_run_path_and_releases_modal_stop(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    job = _job()
    calls: list[tuple[str, str, str | None]] = []
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: job,
    )
    try:
        window.runtime.settings.machine.allow_motion = True
        window.controller.run_job = (  # type: ignore[method-assign]
            lambda text, name, *, arm_phrase=None: calls.append(
                (text, name, arm_phrase)
            )
        )
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window._job_preview_dialog is not None
            and not window._job_preparation_busy,
        )
        dialog = window._job_preview_dialog
        assert dialog is not None
        assert qt_application.activeModalWidget() is dialog

        dialog.run_button.click()

        assert calls == [(job.text, window.last_job_name, None)]
        assert not dialog.isVisible()
        assert qt_application.activeModalWidget() is not dialog
        assert window.runtime_strip.stop_button.isEnabled()
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_preview_start_job_still_rejects_stale_revision(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    job = _job()
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: job,
    )
    window.controller.run_job = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: pytest.fail("stale job reached controller")
    )
    try:
        window.runtime.settings.machine.allow_motion = True
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window._job_preview_dialog is not None
            and not window._job_preparation_busy,
        )
        dialog = window._job_preview_dialog
        assert dialog is not None
        window.last_job_revision = window.document.revision - 1

        dialog.run_button.click()

        assert not dialog.isVisible()
        assert window.last_job is None
        assert errors == [
            "The project changed; regenerate the toolpath before running"
        ]
    finally:
        _dispose(qt_application, window)


def test_preview_action_reopens_preview_instead_of_running(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    job = _job()
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: job,
    )
    window.controller.run_job = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: pytest.fail("main panel bypassed Preview")
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window._job_preview_dialog is not None
            and not window._job_preparation_busy,
        )
        first = window._job_preview_dialog
        assert first is not None
        first.close()
        qt_application.processEvents()

        assert "run" not in window.actions
        assert window.actions["preview_job"].isEnabled()
        window.actions["preview_job"].trigger()
        _wait_until(
            qt_application,
            lambda: window._job_preview_dialog is not None
            and window._job_preview_dialog is not first
            and not window._job_preparation_busy,
        )

        assert window._job_preview_dialog is not None
        assert window._job_preview_dialog.isVisible()
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_modal_preview_blocks_actual_main_window_project_edit(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: _job(),
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window._job_preview_dialog is not None
            and not window._job_preparation_busy,
        )
        dialog = window._job_preview_dialog
        assert dialog is not None
        assert qt_application.activeModalWidget() is dialog
        initial_layer_count = len(window.document.layers)
        add_layer = window.layer_panel.add_button
        click_position = add_layer.mapTo(window, add_layer.rect().center())

        QtTest.QTest.mouseClick(
            window.windowHandle(),
            QtCore.Qt.MouseButton.LeftButton,
            QtCore.Qt.KeyboardModifier.NoModifier,
            click_position,
        )
        qt_application.processEvents()
        assert len(window.document.layers) == initial_layer_count

        dialog.close()
        _wait_until(
            qt_application,
            lambda: qt_application.activeModalWidget() is not dialog
            and not dialog.isVisible(),
        )
        assert add_layer.isEnabled()
        add_layer.click()
        qt_application.processEvents()
        assert len(window.document.layers) == initial_layer_count + 1
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_start_here_confirmation_is_owned_by_preview_and_never_runs_job(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    job = _job()
    parents: list[QtWidgets.QWidget] = []
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: job,
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda parent, *_args, **_kwargs: parents.append(parent)
        or QtWidgets.QMessageBox.StandardButton.Cancel,
    )
    window.controller.run_job = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: pytest.fail("Start Here executed the job")
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window._job_preview_dialog is not None
            and not window._job_preparation_busy,
        )
        dialog = window._job_preview_dialog
        assert dialog is not None
        dialog.set_elapsed(job.plan.moves[0].start_seconds + 0.01)

        dialog.start_here_button.click()

        assert parents == [dialog]
        assert dialog.isVisible()
        assert window.last_job is job
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_project_revision_rejects_inflight_generation_result(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, notices = _window(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def blocked_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        entered.set()
        assert release.wait(3.0)
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        blocked_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(qt_application, entered.is_set)
        window.document.touch()
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and not window._job_preparation_busy,
        )

        assert window.last_job is None
        assert errors == []
        assert any("stale generated result" in notice for notice in notices)
    finally:
        release.set()
        _dispose(qt_application, window)


def test_generation_failure_clears_busy_state_without_partial_job(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)

    def fail_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        raise ValueError("deterministic planning failure")

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        fail_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and not window._job_preparation_busy,
        )

        assert window.last_job is None
        assert window.actions["generate"].isEnabled()
        assert window.job_progress.currentWidget() is window.job_progress.progress
        assert errors == [
            "Toolpath generation failed: deterministic planning failure"
        ]
    finally:
        _dispose(qt_application, window)


@pytest.mark.parametrize("move_count", [1_000, 100_000])
def test_closing_unfinished_preview_invalidates_exact_job(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    move_count: int,
) -> None:
    window, errors, notices = _window(tmp_path, monkeypatch)
    job = _large_job(move_count)

    def stalled_slice(self: JobPreviewCanvas) -> None:
        self.buildProgress.emit(0, max(1, self._build_target))

    monkeypatch.setattr(JobPreviewCanvas, "_build_slice", stalled_slice)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: job,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window._job_preview_dialog is not None
            and window._job_preview_dialog.canvas._building,
            timeout=10.0,
        )
        dialog = window._job_preview_dialog
        assert dialog is not None
        assert not dialog.close_button.isEnabled()

        dialog.close()
        _wait_until(
            qt_application,
            lambda: window._job_preparation_owner is None
            and window.last_job is None,
        )

        assert not window.actions["preview_job"].isEnabled()
        assert not window.actions["export_gcode"].isEnabled()
        assert not window.actions["preview_job"].isEnabled()
        assert not any("ready for review" in notice for notice in notices)
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_core_gcode_calibration_job_is_adapted_for_desktop_preview(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    payloads: list[dict[str, object]] = []

    def capture_install(request_id: int, payload: dict[str, object]) -> None:
        del request_id
        payloads.append(payload)

    monkeypatch.setattr(window, "_install_generated_job", capture_install)
    source = _job()
    window.runtime.settings.laser.spot_offset_x_mm = 0.0004
    window.runtime.settings.laser.spot_offset_y_mm = -0.0004
    try:
        window._load_fine_registration_job(_core_registration_job(source))

        assert len(payloads) == 1
        adapted = payloads[0]["job"]
        assert isinstance(adapted, ProjectJob)
        assert adapted.text == source.text
        assert adapted.bounds_mm == source.bounds_mm
        assert adapted.plan is not None
        assert tuple(
            (move.end_x, move.end_y, move.laser_on)
            for move in adapted.plan.moves
        ) == tuple(
            (move.end_x, move.end_y, move.laser_on)
            for move in source.plan.moves
        )
        assert adapted.plan.moves[0].start_x == 110.0
        assert adapted.plan.moves[0].start_y == 110.0
        assert adapted.spot_offset_mm == (0.0004, -0.0004)
        assert adapted.raster_assets == ()
        assert errors == []
    finally:
        _dispose(qt_application, window)


@pytest.mark.parametrize("stale_failure", [False, True])
def test_registration_render_owns_busy_state_against_late_worker(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stale_failure: bool,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    callbacks: dict[str, object] = {}

    def blocked_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        entered.set()
        assert release.wait(5.0)
        if stale_failure:
            raise ValueError("late stale failure")
        return _job()

    def held_workspace(
        plan,
        *,
        on_progress=None,
        on_finished=None,
        on_failed=None,
    ) -> None:
        del plan, on_progress, on_failed
        callbacks["finished"] = on_finished

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        blocked_generation,
    )
    monkeypatch.setattr(window.workspace, "start_toolpath_preview", held_workspace)
    window.runtime.settings.laser.spot_offset_x_mm = 0.0004
    window.runtime.settings.laser.spot_offset_y_mm = -0.0004
    registration = _large_job(10)
    try:
        window.generate_toolpath()
        _wait_until(qt_application, entered.is_set)
        stale_request = next(
            request_id
            for request_id, phase in window._job_worker_phases.items()
            if phase == "planning"
        )

        window._load_fine_registration_job(_registration_job(registration))
        current_request = window._job_render_request_id
        assert current_request is not None
        assert window._job_preparation_owner == ("render", current_request)
        release.set()
        _wait_until(
            qt_application,
            lambda: stale_request not in window._job_worker_requests,
        )

        assert window.last_job is registration
        assert registration.spot_offset_mm == (0.0004, -0.0004)
        assert window._job_preparation_owner == ("render", current_request)
        assert window._job_preparation_busy
        assert not window.actions["preview_job"].isEnabled()
        finished = callbacks["finished"]
        assert callable(finished)
        finished(True)
        _wait_until(
            qt_application,
            lambda: window._job_preparation_owner is None,
        )
        assert window.last_job is registration
        assert window.actions["preview_job"].isEnabled()
        assert errors == []
    finally:
        release.set()
        _dispose(qt_application, window)


@pytest.mark.parametrize("failure_site", ["workspace", "dialog"])
def test_render_construction_errors_fail_closed(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_site: str,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: _job(),
    )
    if failure_site == "workspace":
        monkeypatch.setattr(
            window.workspace,
            "start_toolpath_preview",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                MemoryError("deterministic workspace allocation failure")
            ),
        )
    else:
        monkeypatch.setattr(
            main_window_module,
            "JobPreviewDialog",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("deterministic dialog construction failure")
            ),
        )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
        )

        assert window.last_job is None
        assert not window._job_preparation_busy
        assert not window.actions["preview_job"].isEnabled()
        assert len(errors) == 1
        assert "failed" in errors[0]
        assert "deterministic" in errors[0]
    finally:
        _dispose(qt_application, window)


@pytest.mark.parametrize("replacement", ["new", "open"])
def test_project_replacement_cancels_worker_without_late_install(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()

    def blocked_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        entered.set()
        assert release.wait(5.0)
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        blocked_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(qt_application, entered.is_set)
        if replacement == "new":
            window.new_project()
            replacement_document = window.document
        else:
            replacement_document = ProjectDocument.new("Opened replacement")
            monkeypatch.setattr(
                QtWidgets.QFileDialog,
                "getOpenFileName",
                lambda *args, **kwargs: (str(tmp_path / "replacement.e3laser"), ""),
            )
            monkeypatch.setattr(
                main_window_module,
                "load_project",
                lambda _path: replacement_document,
            )
            monkeypatch.setattr(
                main_window_module,
                "autosave_is_newer",
                lambda *args, **kwargs: False,
            )
            window.open_project()
        assert window.document is replacement_document

        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
        )

        assert window.document is replacement_document
        assert window.last_job is None
        assert not list((tmp_path / "data").rglob("*.gcode"))
        assert errors == []
    finally:
        release.set()
        _dispose(qt_application, window)


def test_close_is_bounded_while_cancelled_worker_remains_owned(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, _errors, _notices = _window(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    setup_shutdown: list[bool] = []

    class OpenMachineSetup:
        def begin_shutdown(self) -> None:
            setup_shutdown.append(True)

    window._machine_setup_dialog = OpenMachineSetup()  # type: ignore[assignment]

    def blocked_generation(*args, **kwargs) -> ProjectJob:
        del args, kwargs
        entered.set()
        assert release.wait(5.0)
        return _job()

    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        blocked_generation,
    )
    try:
        window.generate_toolpath()
        _wait_until(qt_application, entered.is_set)

        started = time.monotonic()
        window.close()
        qt_application.processEvents()
        elapsed = time.monotonic() - started

        assert window._close_requested
        assert window._closing
        assert window.controller.has_active_tasks
        assert not window.isVisible()
        assert elapsed < 3.0
        assert not window.runtime.running
        assert window.last_job is None
        assert setup_shutdown == [True]

        release.set()
        _wait_until(
            qt_application,
            lambda: not window.controller.has_active_tasks,
            timeout=10.0,
        )
        assert not window.controller.has_active_tasks
    finally:
        release.set()
        window._machine_setup_dialog = None
        window.deleteLater()
        qt_application.processEvents()


def test_start_here_preserves_exact_raster_identities(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    image_path = tmp_path / "source.png"
    image = QtGui.QImage(2, 2, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("black"))
    assert image.save(str(image_path), "PNG")
    _add_raster_source(window, image_path)
    source = _job(8)
    source.raster_assets = (capture_raster_asset_identity(image_path),)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: source,
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Ok,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window.last_job is source and not window._job_preparation_busy,
        )

        window._prepare_start_here(0)
        _wait_until(
            qt_application,
            lambda: window.last_job is not None
            and window.last_job is not source
            and not window._job_preparation_busy,
        )

        assert window.last_job.raster_assets == source.raster_assets
        assert window.last_job_name == "start-here-move-1.gcode"
        assert '; @E3_JOB {"start_x":110.0,"start_y":110.0}' in window.last_job.text
        approach = window.last_job.plan.moves[0]
        assert approach.rapid and not approach.laser_on
        assert (approach.start_x, approach.start_y) == pytest.approx((110.0, 110.0))
        assert (approach.end_x, approach.end_y) == pytest.approx((0.0, 0.0))
        window.runtime.context.machine.settings.allow_motion = True
        preflight = window.runtime.context.machine.preflight_program(
            window.last_job.text
        )
        assert preflight.requires_motion
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_start_here_discards_worker_result_after_air_assist_mapping_change(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, notices = _window(tmp_path, monkeypatch)
    machine = window.runtime.settings.machine
    machine.protocol = "grbl"
    machine.air_assist = AirAssistSettings(mode=AirAssistMode.GRBL_COOLANT)
    source = _air_assist_job()
    entered = threading.Event()
    release = threading.Event()
    original_restart = main_window_module.restart_program_from_move
    installed: list[dict[str, object]] = []
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: source,
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Ok,
    )

    def blocked_restart(*args, **kwargs):
        entered.set()
        assert release.wait(5.0)
        return original_restart(*args, **kwargs)

    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window.last_job is source
            and not window._job_preparation_busy,
        )
        monkeypatch.setattr(
            main_window_module,
            "restart_program_from_move",
            blocked_restart,
        )
        window._install_generated_job = (  # type: ignore[method-assign]
            lambda _request_id, payload: installed.append(payload)
        )

        window._prepare_start_here(0)
        _wait_until(qt_application, entered.is_set)
        machine.air_assist = AirAssistSettings(mode=AirAssistMode.DISABLED)
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
        )

        assert installed == []
        assert window.last_job is None
        assert window._job_preview_dialog is None
        assert errors == []
        assert any("stale generated result" in notice for notice in notices)
    finally:
        release.set()
        _dispose(qt_application, window)


def test_software_stop_cancels_start_here_worker(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    source = _job(8)
    entered = threading.Event()
    release = threading.Event()
    original_restart = main_window_module.restart_program_from_move
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: source,
    )
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: QtWidgets.QMessageBox.StandardButton.Ok,
    )

    def blocked_restart(*args, **kwargs):
        entered.set()
        assert release.wait(5.0)
        return original_restart(*args, **kwargs)

    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window.last_job is source and not window._job_preparation_busy,
        )
        monkeypatch.setattr(
            main_window_module,
            "restart_program_from_move",
            blocked_restart,
        )

        window._prepare_start_here(0)
        _wait_until(qt_application, entered.is_set)
        window.runtime_strip.stop_button.click()
        qt_application.processEvents()
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
        )

        assert window.last_job is None
        assert not window.actions["preview_job"].isEnabled()
        assert errors == []
    finally:
        release.set()
        _dispose(qt_application, window)


@pytest.mark.parametrize("action", ["preview", "export", "run"])
def test_changed_raster_asset_blocks_prepared_job_actions(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    image_path = tmp_path / "mutable.bmp"
    image = QtGui.QImage(2, 1, QtGui.QImage.Format.Format_RGB32)
    image.fill(QtGui.QColor("black"))
    assert image.save(str(image_path), "BMP")
    _add_raster_source(window, image_path)
    job = _job()
    job.raster_assets = (capture_raster_asset_identity(image_path),)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: job,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window.last_job is job and not window._job_preparation_busy,
        )
        image.fill(QtGui.QColor("white"))
        assert image.save(str(image_path), "BMP")

        if action == "preview":
            window.show_job_preview()
        elif action == "export":
            window.export_gcode()
        else:
            window.run_current_job()

        assert window.last_job is None
        assert not window.actions["preview_job"].isEnabled()
        assert len(errors) == 1
        assert "changed on disk" in errors[0]
    finally:
        _dispose(qt_application, window)


def test_same_path_raster_change_refreshes_canvas_and_rejects_first_generation(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    image_path = tmp_path / "same-path.bmp"
    black = QtGui.QImage(2, 1, QtGui.QImage.Format.Format_RGB32)
    black.fill(QtGui.QColor("black"))
    assert black.save(str(image_path), "BMP")
    original_stat = image_path.stat()
    _add_raster_source(window, image_path)
    displayed_before = next(iter(window.workspace._items_by_id.values()))
    identity_before = displayed_before.raster_preview_identity
    assert identity_before is not None

    white = QtGui.QImage(2, 1, QtGui.QImage.Format.Format_RGB32)
    white.fill(QtGui.QColor("white"))
    assert white.save(str(image_path), "BMP")
    assert image_path.stat().st_size == original_stat.st_size
    os.utime(
        image_path,
        ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns),
    )
    changed_job = _job()
    changed_job.raster_assets = (capture_raster_asset_identity(image_path),)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: changed_job,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
        )

        displayed_after = next(iter(window.workspace._items_by_id.values()))
        assert displayed_after is displayed_before
        assert displayed_after.raster_preview_identity == (
            changed_job.raster_assets[0].path,
            changed_job.raster_assets[0].sha256,
        )
        assert displayed_after.raster_preview_identity != identity_before
        assert window.last_job is None
        assert not window.actions["preview_job"].isEnabled()
        assert not window.actions["export_gcode"].isEnabled()
        assert errors and "canvas has been refreshed" in errors[-1]

        errors.clear()
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window.last_job is changed_job
            and not window._job_preparation_busy,
        )

        assert window.actions["preview_job"].isEnabled()
        assert window.actions["export_gcode"].isEnabled()
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_exact_plan_controls_zero_effective_power_state_and_run_gate(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    layer = window.document.layers[0]
    layer.output_enabled = True
    layer.power_percent = 0.04
    window.document.add_object(
        SceneObject.line(
            layer.id,
            name="Sub-quantized line",
            center=(55.0, 50.0),
            length_mm=10.0,
        )
    )
    warnings: list[str] = []
    monkeypatch.setattr(
        QtWidgets.QMessageBox,
        "warning",
        lambda *args, **kwargs: warnings.append("powered warning")
        or QtWidgets.QMessageBox.StandardButton.Cancel,
    )
    try:
        window.generate_toolpath()
        _wait_until(
            qt_application,
            lambda: window.last_job is not None and not window._job_preparation_busy,
        )

        assert window.last_job.plan is not None
        assert not window.last_job.plan.powered
        assert not window.last_job_powered
        window.run_current_job()
        assert warnings == []
        assert errors == ["Motion is blocked in the local configuration"]
    finally:
        _dispose(qt_application, window)


def test_offline_prepared_job_reaches_controller_auto_connect_path(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    job = _job()
    calls: list[tuple[str, str, str | None]] = []
    try:
        window.runtime.settings.machine.allow_motion = True
        window.last_job = job
        window.last_job_name = "offline-prepared.gcode"
        window.last_job_revision = window.document.revision
        window.last_job_work_area = window._work_area_signature(
            window.runtime.settings.machine.work_area
        )
        window.last_job_powered = False
        window.controller.run_job = (  # type: ignore[method-assign]
            lambda text, name, *, arm_phrase=None: calls.append(
                (text, name, arm_phrase)
            )
        )
        assert window.runtime.context.machine.status()["connected"] is False

        window.run_current_job()

        assert calls == [(job.text, "offline-prepared.gcode", None)]
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_powered_start_has_no_confirmation_and_uses_one_time_arm(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    job = _large_job(2, powered=True)
    calls: list[tuple[str, str, str | None]] = []
    try:
        window.runtime.settings.machine.allow_motion = True
        window.last_job = job
        window.last_job_name = "powered-calibration.gcode"
        window.last_job_revision = window.document.revision
        window.last_job_work_area = window._work_area_signature(
            window.runtime.settings.machine.work_area
        )
        window.last_job_powered = True
        monkeypatch.setattr(
            QtWidgets.QMessageBox,
            "warning",
            lambda *args, **kwargs: pytest.fail("powered warning was displayed"),
        )
        monkeypatch.setattr(
            QtWidgets.QInputDialog,
            "getText",
            lambda *args, **kwargs: pytest.fail("arming phrase was requested"),
        )
        window.controller.run_job = (  # type: ignore[method-assign]
            lambda text, name, *, arm_phrase=None: calls.append(
                (text, name, arm_phrase)
            )
        )

        window.run_current_job()

        assert calls == [
            (job.text, "powered-calibration.gcode", "ENABLE LASER CONTROL")
        ]
        assert errors == []
    finally:
        _dispose(qt_application, window)


def test_near_cap_snapshot_clone_keeps_gui_and_stop_live(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    window, errors, _notices = _window(tmp_path, monkeypatch)
    window.document.metadata["near_cap_snapshot"] = [0] * 250_000
    entered = threading.Event()
    release = threading.Event()
    original_clone = ProjectDocument.clone

    def controlled_clone(document: ProjectDocument) -> ProjectDocument:
        snapshot = original_clone(document)
        entered.set()
        assert release.wait(5.0)
        return snapshot

    monkeypatch.setattr(ProjectDocument, "clone", controlled_clone)
    monkeypatch.setattr(
        main_window_module,
        "generate_project_gcode",
        lambda *args, **kwargs: _job(),
    )
    heartbeat = 0
    timer = QtCore.QTimer()
    timer.setInterval(1)

    def beat() -> None:
        nonlocal heartbeat
        heartbeat += 1

    timer.timeout.connect(beat)
    timer.start()
    try:
        window.generate_toolpath()
        assert not window.workspace.isEnabled()
        assert not window.actions["new"].isEnabled()
        _wait_until(qt_application, entered.is_set, timeout=10.0)
        _wait_until(qt_application, lambda: heartbeat >= 5)
        assert window.runtime_strip.stop_button.isEnabled()

        window.runtime_strip.stop_button.click()
        qt_application.processEvents()
        release.set()
        _wait_until(
            qt_application,
            lambda: not window._job_worker_requests
            and window._job_preparation_owner is None,
            timeout=10.0,
        )

        assert window.workspace.isEnabled()
        assert window.actions["new"].isEnabled()
        assert window.last_job is None
        assert errors == []
    finally:
        release.set()
        timer.stop()
        _dispose(qt_application, window)


def test_near_cap_backward_timeline_scrub_is_time_sliced(
    qt_application: QtWidgets.QApplication,
) -> None:
    plan = _repeated_plan(250_000)
    move_ends = tuple(float(index + 1) for index in range(len(plan.moves)))
    canvas = JobPreviewCanvas(
        plan,
        (0.0, 220.0, 0.0, 220.0),
        move_ends=move_ends,
        defer_render=True,
    )
    heartbeat = 0
    timer = QtCore.QTimer()
    timer.setInterval(1)

    def beat() -> None:
        nonlocal heartbeat
        heartbeat += 1

    timer.timeout.connect(beat)
    timer.start()
    try:
        canvas.start_deferred_render()
        _wait_until(
            qt_application,
            lambda: not canvas._building,
            timeout=20.0,
        )
        before = heartbeat
        started = time.perf_counter()
        canvas.set_elapsed(plan.total_seconds / 2.0)
        call_seconds = time.perf_counter() - started

        assert call_seconds < 0.08
        assert canvas._building
        _wait_until(
            qt_application,
            lambda: not canvas._building and heartbeat >= before + 5,
            timeout=20.0,
        )
        assert 124_999 <= canvas._rendered_count <= 125_001
    finally:
        timer.stop()
        canvas.cancel_deferred_render()
        canvas.deleteLater()
        qt_application.processEvents()
