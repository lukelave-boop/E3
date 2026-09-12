"""Real monitor preview metadata maps through source calibration without motion."""

from dataclasses import asdict, replace
from types import SimpleNamespace

import numpy as np
import pytest

from laser_aligner.app import AppContext
from laser_aligner.calibration.bed import BedPoint
from laser_aligner.calibration.lens import LensModel
from laser_aligner.camera.bridge import _monitor_frame
from laser_aligner.camera.service import CameraStatus
from laser_aligner.errors import CalibrationError
from tests.test_app_simulation import _settings


@pytest.fixture
def source_context(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    settings.camera.width, settings.camera.height = 1920, 1080
    context = AppContext(settings)
    context.lens._model = LensModel(
        np.array([[1400., 0., 960.], [0., 1400., 540.], [0., 0., 1.]]),
        np.zeros(5), 1920, 1080, 0.1, 0.1, 12, 1.,
    )
    context.bed.replace_points_and_solve(
        [BedPoint(100., 980., 0., 0.), BedPoint(1820., 980., 200., 0.),
         BedPoint(1820., 100., 200., 200.), BedPoint(100., 100., 0., 200.)],
        1920, 1080, provenance=context._bed_provenance(),
    )
    status = CameraStatus(True, "test", 1920, 1080, 15., 10, None, frame_age_seconds=0.1)
    monkeypatch.setattr(context.camera, "status", lambda: status)
    monkeypatch.setattr(context.camera, "snapshot", lambda: pytest.fail("No camera capture allowed"))
    monkeypatch.setattr(context.machine, "connect", lambda: pytest.fail("No hardware connection allowed"))
    return context


def preview_metadata(context, size=(1280, 720), **changes):
    return {
        "width": size[0], "height": size[1],
        "source_width": 1920, "source_height": 1080, "source_mode": "transcoded",
        "camera_settings": asdict(context.camera.settings), **changes,
    }


def map_preview(context, xy=(640., 360.), *, size=(1280, 720), metadata=None, **changes):
    return context.focus_probe_target(
        *xy, source_image_size=list(size), frame_age_seconds=changes.pop("frame_age_seconds", 0.1),
        frame_metadata=preview_metadata(context, size) if metadata is None else metadata,
        **changes,
    )


def monitor_payload(context):
    """Exercise the Pi bridge's actual native-request/transcoded-fallback path."""
    camera = SimpleNamespace(
        settings=context.camera.settings,
        snapshot_after=lambda *_args, **_kwargs: np.zeros((1080, 1920, 3), dtype=np.uint8),
        frame_sequence=lambda: 11,
        status=context.camera.status,
    )
    header, jpeg = _monitor_frame(
        camera, sequence=10, width=1920, height=1080, quality=80, timeout=1.,
    )
    return dict(header, camera_settings=asdict(context.camera.settings)), jpeg


def test_actual_pi_transcoded_preview_matches_native_raw_camera_point(source_context):
    context = source_context
    metadata, jpeg = monitor_payload(context)
    assert jpeg and metadata["source_mode"] == "transcoded"
    assert (metadata["width"], metadata["height"]) == (1280, 720)
    assert (metadata["source_width"], metadata["source_height"]) == (1920, 1080)
    preview = map_preview(context, (800., 300.), metadata=metadata)
    native = map_preview(
        context, (1200., 450.), size=(1920, 1080),
        metadata=preview_metadata(context, (1920, 1080), source_mode="direct_mjpeg"),
    )
    assert preview["raw_image_xy"] == [1200., 450.]
    assert preview["source_image_size"] == [1920, 1080]
    assert preview["preview_image_size"] == [1280, 720]
    assert preview["preview_image_xy"] == [800., 300.]
    assert preview["corrected_image_xy"] == pytest.approx(native["corrected_image_xy"])
    assert preview["target_machine_xy_mm"] == pytest.approx(native["target_machine_xy_mm"])
    assert preview["mapping_plane"] == "bed" and preview["height_corrected"] is False
    assert not context.machine.status()["connected"]


def test_preview_is_scaled_to_native_pixels_before_lens_correction(source_context):
    context = source_context
    context.lens._model = replace(
        context.lens.model, distortion=np.array([0.1, -0.02, 0.001, -0.001, 0.]), model_id="",
    )
    context.bed.calibration.provenance = context._bed_provenance()
    corrected = np.array([[1300., 700.]])
    raw = context.lens.model.distort_points(corrected)[0]
    mapped = map_preview(context, tuple(raw / 1.5))
    assert mapped["raw_image_xy"] == pytest.approx(raw)
    assert mapped["corrected_image_xy"] == pytest.approx(corrected[0], abs=1e-5)
    assert mapped["target_machine_xy_mm"] == pytest.approx(context.bed.image_to_mm(1300., 700.), abs=1e-5)


@pytest.mark.parametrize("size,mode", [((1920, 1080), "transcoded"), ((1920, 1080), "direct_mjpeg")])
def test_preview_dimensions_or_mode_change_invalidates_pending_mapping(source_context, size, mode):
    selected = map_preview(source_context)
    xy = (size[0] / 2, size[1] / 2)
    metadata = preview_metadata(source_context, size, source_mode=mode)
    with pytest.raises(CalibrationError, match="changed after selection"):
        map_preview(source_context, xy, size=size, metadata=metadata,
                    mapping_signature=selected["mapping_signature"])
    replacement = map_preview(source_context, xy, size=size, metadata=metadata)
    assert replacement["target_machine_xy_mm"] == pytest.approx(selected["target_machine_xy_mm"])


def test_native_preview_mode_change_alone_invalidates_mapping(source_context):
    size = (1920, 1080)
    selected = map_preview(source_context, (960., 540.), size=size)
    metadata = preview_metadata(source_context, size, source_mode="direct_mjpeg")
    with pytest.raises(CalibrationError, match="changed after selection"):
        map_preview(source_context, (960., 540.), size=size, metadata=metadata,
                    mapping_signature=selected["mapping_signature"])


def test_fresh_matching_frames_keep_selection_mapping_valid(source_context):
    metadata = preview_metadata(source_context, sequence=4, captured_monotonic=1.)
    selected = map_preview(source_context, metadata=metadata)
    metadata.update(sequence=5, captured_monotonic=2.)
    assert map_preview(source_context, metadata=metadata,
                       mapping_signature=selected["mapping_signature"]) == selected


@pytest.mark.parametrize("field,value", [
    ("source_mode", None), ("source_mode", "direct_mjpeg"), ("source_mode", "cropped"),
    ("source_width", 1280), ("source_height", 720), ("source_width", True),
    ("source_height", 1080.), ("source_width", "1920"), ("source_height", None),
    ("width", 640), ("height", 360), ("width", True), ("height", "720"),
    ("camera_settings", {}),
])
def test_scaled_preview_rejects_missing_malformed_or_inconsistent_metadata(source_context, field, value):
    metadata = preview_metadata(source_context, **{field: value})
    with pytest.raises(CalibrationError):
        map_preview(source_context, metadata=metadata)


@pytest.mark.parametrize("field", ["source_width", "source_height", "source_mode", "camera_settings"])
def test_scaled_preview_requires_source_provenance(source_context, field):
    metadata = preview_metadata(source_context)
    metadata.pop(field)
    with pytest.raises(CalibrationError):
        map_preview(source_context, metadata=metadata)


@pytest.mark.parametrize("metadata", [None, [], {}])
def test_scaled_preview_rejects_absent_frame_metadata(source_context, metadata):
    with pytest.raises(CalibrationError):
        source_context.focus_probe_target(
            640., 360., source_image_size=[1280, 720], frame_age_seconds=0.1,
            frame_metadata=metadata,
        )


@pytest.mark.parametrize("xy", [(-1., 360.), (1280., 360.), (640., 720.), (50., 360.)])
def test_scaled_preview_preserves_image_and_final_machine_bounds(source_context, xy):
    with pytest.raises(CalibrationError):
        map_preview(source_context, xy)


@pytest.mark.parametrize("size", [(1280, 800), (1000, 720)])
def test_nonuniform_preview_scaling_does_not_invent_a_mapping(source_context, size):
    with pytest.raises(CalibrationError):
        map_preview(source_context, size=size)


@pytest.mark.parametrize("size", [(960, 540), (640, 360)])
def test_unrecognized_monitor_dimensions_cannot_claim_resize_provenance(source_context, size):
    with pytest.raises(CalibrationError):
        map_preview(source_context, (size[0] / 2, size[1] / 2), size=size)


@pytest.mark.parametrize("age", [2.01, -0.1, None, True, float("nan")])
def test_scaled_preview_still_requires_a_fresh_frame(source_context, age):
    with pytest.raises(CalibrationError):
        map_preview(source_context, frame_age_seconds=age)


@pytest.mark.parametrize("dpr", [1., 2.])
def test_real_focus_view_resize_and_dpr_preserve_native_mapping(source_context, monkeypatch, dpr):
    """Offscreen integration uses the bridge, preview preparer, view and mapper."""
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6 import QtCore, QtWidgets

    from laser_aligner.desktop import focus_bed_view
    from laser_aligner.desktop.live_monitor import _prepare_monitor_payload
    from tests.test_desktop_focus_bed_view import Worker

    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    monkeypatch.setattr(focus_bed_view, "_MonitorThread", Worker)
    context = source_context
    camera = SimpleNamespace(settings=context.camera.settings, monitor_frames=lambda **_kwargs: iter(()))
    view = focus_bed_view.FocusBedView(camera)
    view.show()
    view.begin()
    view.set_selection_enabled(True)
    metadata, jpeg = monitor_payload(context)
    prepared = _prepare_monitor_payload(dict(metadata, jpeg=jpeg), (640, 360))
    prepared["prepared_image"].setDevicePixelRatio(dpr)
    view._worker.payload = prepared
    view._worker.frameAvailable.emit()
    results = []
    try:
        for width, height in [(420, 650), (820, 520)]:
            view.resize(width, height)
            application.processEvents()
            rect = view._display_rect
            view._select_point(QtCore.QPointF(rect.left() + rect.width() * 0.625,
                                            rect.top() + rect.height() * (5 / 12)))
            selection = view.selection_snapshot()
            assert selection is not None and selection["fresh"]
            assert selection["image_x"] == pytest.approx(800.)
            assert selection["image_y"] == pytest.approx(300.)
            results.append(map_preview(
                context, (selection["image_x"], selection["image_y"]),
                size=(selection["width"], selection["height"]), metadata=selection,
                frame_age_seconds=selection["frame_age_seconds"],
            ))
        assert results[0]["raw_image_xy"] == pytest.approx([1200., 450.])
        assert results[0]["target_machine_xy_mm"] == pytest.approx(results[1]["target_machine_xy_mm"])
        assert results[0]["mapping_signature"] == results[1]["mapping_signature"]
        assert not context.machine.status()["connected"]
    finally:
        view.end()
        view.close()
        view.deleteLater()
        application.processEvents()
