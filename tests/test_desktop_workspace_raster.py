from __future__ import annotations

# The Qt platform must be selected before importing PySide6-backed modules.
# ruff: noqa: E402, I001

import os
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from laser_aligner.desktop import workspace as workspace_module
from laser_aligner.desktop.qt import require_qt
from laser_aligner.desktop.workspace import ObjectGraphicsItem, WorkspaceView
from laser_aligner.project import (
    LayerMode,
    ObjectKind,
    OperationLayer,
    ProjectDocument,
    SceneObject,
    Transform,
    probe_raster_asset,
    read_raster_asset_payload,
)

QtCore, QtGui, QtWidgets = require_qt()


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application


def _raster_object(path: Path) -> tuple[SceneObject, OperationLayer]:
    layer = OperationLayer(mode=LayerMode.RASTER, color="#E35D6A")
    scene_object = SceneObject(
        name="Artwork",
        kind=ObjectKind.IMAGE,
        layer_id=layer.id,
        transform=Transform(width_mm=10.0, height_mm=10.0),
        geometry={"asset": str(path)},
    )
    return scene_object, layer


def test_raster_object_renders_real_pixels_instead_of_an_empty_rectangle(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    del qt_application
    source = QtGui.QImage(2, 1, QtGui.QImage.Format.Format_RGB32)
    source.setPixelColor(0, 0, QtGui.QColor("#E02020"))
    source.setPixelColor(1, 0, QtGui.QColor("#2050E0"))
    path = tmp_path / "artwork.png"
    assert source.save(str(path), "PNG")
    scene_object, layer = _raster_object(path)
    item = ObjectGraphicsItem(scene_object, layer)
    scene = QtWidgets.QGraphicsScene(QtCore.QRectF(-5.0, -5.0, 10.0, 10.0))
    scene.addItem(item)
    output = QtGui.QImage(100, 100, QtGui.QImage.Format.Format_ARGB32)
    output.fill(QtGui.QColor("white"))
    painter = QtGui.QPainter(output)
    scene.render(painter)
    painter.end()

    left = output.pixelColor(25, 50)
    right = output.pixelColor(75, 50)
    assert left.red() > left.blue() * 2
    assert right.blue() > right.red() * 2
    assert str(path.resolve()) in item.toolTip()


def test_missing_raster_source_is_visible_in_item_status(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    del qt_application
    scene_object, layer = _raster_object(tmp_path / "missing.png")

    item = ObjectGraphicsItem(scene_object, layer)

    assert item._raster_image is None
    assert "missing or unreadable" in item.toolTip()


def test_raster_import_dimensions_apply_exif_orientation(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    del qt_application
    source = QtGui.QImage(3, 2, QtGui.QImage.Format.Format_RGB32)
    source.fill(QtGui.QColor("white"))
    encoded_path = tmp_path / "raw.jpg"
    assert source.save(str(encoded_path), "JPEG")
    jpeg = encoded_path.read_bytes()
    assert jpeg.startswith(b"\xff\xd8")
    exif = (
        b"Exif\x00\x00II*\x00\x08\x00\x00\x00\x01\x00"
        b"\x12\x01\x03\x00\x01\x00\x00\x00\x06\x00\x00\x00"
        b"\x00\x00\x00\x00"
    )
    marker = b"\xff\xe1" + (len(exif) + 2).to_bytes(2, "big") + exif
    oriented_path = tmp_path / "oriented.jpg"
    oriented_path.write_bytes(jpeg[:2] + marker + jpeg[2:])

    oriented = probe_raster_asset(oriented_path)

    assert (oriented.width, oriented.height) == (2, 3)


def test_raster_workspace_preview_decode_and_cache_are_memory_bounded(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qt_application
    ObjectGraphicsItem._image_cache.clear()
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_PREVIEW_MAX_PIXELS", 100)
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_CACHE_MAX_BYTES", 450)
    first = QtGui.QImage(40, 20, QtGui.QImage.Format.Format_RGB32)
    first.fill(QtGui.QColor("red"))
    second = QtGui.QImage(40, 20, QtGui.QImage.Format.Format_RGB32)
    second.fill(QtGui.QColor("blue"))
    first_path = tmp_path / "first.png"
    second_path = tmp_path / "second.png"
    assert first.save(str(first_path), "PNG")
    assert second.save(str(second_path), "PNG")

    _first_source, first_preview = ObjectGraphicsItem._load_raster_image(first_path)
    _second_source, second_preview = ObjectGraphicsItem._load_raster_image(second_path)

    assert first_preview is not None and second_preview is not None
    assert first_preview.width() * first_preview.height() <= 100
    assert second_preview.width() * second_preview.height() <= 100
    assert sum(
        image.sizeInBytes() for image in ObjectGraphicsItem._image_cache.values()
    ) <= 450


def test_raster_workspace_cache_uses_exact_content_identity(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
) -> None:
    del qt_application
    ObjectGraphicsItem._image_cache.clear()
    path = tmp_path / "mutable.bmp"
    black = QtGui.QImage(2, 1, QtGui.QImage.Format.Format_RGB32)
    black.fill(QtGui.QColor("black"))
    assert black.save(str(path), "BMP")
    original = path.stat()

    _source, first = ObjectGraphicsItem._load_raster_image(path)
    assert first is not None

    white = QtGui.QImage(2, 1, QtGui.QImage.Format.Format_RGB32)
    white.fill(QtGui.QColor("white"))
    assert white.save(str(path), "BMP")
    assert path.stat().st_size == original.st_size
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))

    _source, second = ObjectGraphicsItem._load_raster_image(path)

    assert second is not None
    assert first.pixelColor(0, 0).value() < 10
    assert second.pixelColor(0, 0).value() > 245
    assert len(ObjectGraphicsItem._image_cache) == 2


def test_raster_workspace_decodes_the_same_payload_that_was_hashed(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qt_application
    ObjectGraphicsItem._image_cache.clear()
    path = tmp_path / "raced.bmp"
    black = QtGui.QImage(2, 1, QtGui.QImage.Format.Format_RGB32)
    black.fill(QtGui.QColor("black"))
    assert black.save(str(path), "BMP")
    captured = read_raster_asset_payload(path)
    white = QtGui.QImage(2, 1, QtGui.QImage.Format.Format_RGB32)
    white.fill(QtGui.QColor("white"))

    def captured_then_mutated(_path: object):
        assert white.save(str(path), "BMP")
        return captured

    monkeypatch.setattr(
        workspace_module,
        "read_raster_asset_payload",
        captured_then_mutated,
    )

    _source, preview = ObjectGraphicsItem._load_raster_image(path)

    assert preview is not None
    assert preview.pixelColor(0, 0).value() < 10
    assert QtGui.QImage(str(path)).pixelColor(0, 0).value() > 245


def test_project_raster_budget_keeps_all_current_previews_resident(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    del qt_application
    ObjectGraphicsItem._image_cache.clear()
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_PREVIEW_MAX_PIXELS", 1_000)
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_CACHE_MAX_BYTES", 12_000)
    document = ProjectDocument.new("Raster cache budget")
    layer = document.layers[0]
    for index in range(5):
        image = QtGui.QImage(40, 20, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtGui.QColor.fromHsv(index * 45, 255, 220))
        path = tmp_path / f"source-{index}.png"
        assert image.save(str(path), "PNG")
        document.add_object(
            SceneObject(
                name=f"Source {index}",
                kind=ObjectKind.IMAGE,
                layer_id=layer.id,
                transform=Transform(
                    20.0 + index * 20.0,
                    20.0,
                    10.0,
                    5.0,
                ),
                geometry={"asset": str(path)},
            )
        )
    view = WorkspaceView(document.work_area)
    try:
        view.set_document(document)
        first_keys = set(ObjectGraphicsItem._image_cache)

        assert ObjectGraphicsItem._project_raster_source_count == 5
        assert len(first_keys) == 5
        assert sum(
            image.sizeInBytes()
            for image in ObjectGraphicsItem._image_cache.values()
        ) <= ObjectGraphicsItem._IMAGE_CACHE_MAX_BYTES

        document.touch()
        view.set_document(document)

        assert set(ObjectGraphicsItem._image_cache) == first_keys
        assert len(ObjectGraphicsItem._image_cache) == 5
    finally:
        view.deleteLater()
        ObjectGraphicsItem._image_cache.clear()
        ObjectGraphicsItem.set_project_raster_source_count(1)


def test_unrelated_edits_reuse_exact_raster_items_without_gui_rereads(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ObjectGraphicsItem._image_cache.clear()
    document = ProjectDocument.new("Raster refresh reuse")
    layer = document.layers[0]
    for index in range(5):
        image = QtGui.QImage(40, 20, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtGui.QColor.fromHsv(index * 45, 220, 220))
        path = tmp_path / f"retained-{index}.png"
        assert image.save(str(path), "PNG")
        document.add_object(
            SceneObject(
                name=f"Retained {index}",
                kind=ObjectKind.IMAGE,
                layer_id=layer.id,
                transform=Transform(
                    20.0 + index * 20.0,
                    20.0,
                    10.0,
                    5.0,
                ),
                geometry={"asset": str(path)},
            )
        )
    reads: list[str] = []
    original_read = workspace_module.read_raster_asset_payload

    def slow_read(path: object):
        reads.append(str(path))
        time.sleep(0.02)
        return original_read(path)

    monkeypatch.setattr(
        workspace_module,
        "read_raster_asset_payload",
        slow_read,
    )
    view = WorkspaceView(document.work_area)
    heartbeat = 0
    timer = QtCore.QTimer()
    timer.setInterval(1)

    def beat() -> None:
        nonlocal heartbeat
        heartbeat += 1

    timer.timeout.connect(beat)
    try:
        view.set_document(document)
        retained_ids = {
            object_id: id(item)
            for object_id, item in view._items_by_id.items()
        }
        assert len(reads) == 5
        timer.start()
        qt_application.processEvents()
        before = heartbeat
        durations: list[float] = []
        for _ in range(3):
            document.touch()
            started = time.perf_counter()
            view.set_document(document)
            durations.append(time.perf_counter() - started)
            qt_application.processEvents()
        deadline = time.monotonic() + 0.1
        while heartbeat <= before and time.monotonic() < deadline:
            qt_application.processEvents()
            time.sleep(0.001)

        assert len(reads) == 5
        assert max(durations) < 0.05
        assert heartbeat > before
        assert {
            object_id: id(item)
            for object_id, item in view._items_by_id.items()
        } == retained_ids
    finally:
        timer.stop()
        view.deleteLater()
        ObjectGraphicsItem._image_cache.clear()
        ObjectGraphicsItem.set_project_raster_source_count(1)


def test_sequential_raster_add_remove_rebudgets_live_items_and_cache(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ObjectGraphicsItem._image_cache.clear()
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_PREVIEW_MAX_PIXELS", 1_000)
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_CACHE_MAX_BYTES", 12_000)
    document = ProjectDocument.new("Sequential raster budget")
    layer = document.layers[0]
    objects: list[SceneObject] = []
    view = WorkspaceView(document.work_area)
    quality_pixels: dict[int, int] = {}

    def assert_bounded() -> None:
        live: dict[tuple[str, str], QtGui.QImage] = {}
        for item in view._items_by_id.values():
            identity = item.raster_preview_identity
            if identity is not None and item._raster_image is not None:
                live[identity] = item._raster_image
        assert sum(image.sizeInBytes() for image in live.values()) <= 12_000
        assert sum(
            image.sizeInBytes()
            for image in ObjectGraphicsItem._image_cache.values()
        ) <= 12_000
        assert set(ObjectGraphicsItem._image_cache) == set(live)

    try:
        view.set_document(document)
        for index in range(20):
            image = QtGui.QImage(40, 20, QtGui.QImage.Format.Format_RGB32)
            image.fill(QtGui.QColor.fromHsv(index * 17, 230, 220))
            path = tmp_path / f"sequential-{index}.png"
            assert image.save(str(path), "PNG")
            scene_object = SceneObject(
                name=f"Sequential {index}",
                kind=ObjectKind.IMAGE,
                layer_id=layer.id,
                transform=Transform(
                    10.0 + index * 5.0,
                    20.0,
                    4.0,
                    2.0,
                ),
                geometry={"asset": str(path)},
            )
            objects.append(scene_object)
            document.add_object(scene_object)
            view.set_document(document)
            assert_bounded()

        quality_pixels[20] = next(
            item._raster_image.width() * item._raster_image.height()
            for item in view._items_by_id.values()
            if item._raster_image is not None
        )

        for scene_object in reversed(objects):
            document.remove_object(scene_object.id)
            view.set_document(document)
            assert_bounded()
            remaining = len(document.objects)
            if remaining in {16, 8, 4, 1}:
                deadline = time.monotonic() + 2.0
                while view._raster_restore_timer.isActive():
                    qt_application.processEvents()
                    if time.monotonic() >= deadline:
                        raise AssertionError(
                            "Timed out restoring raster preview quality"
                        )
                quality_pixels[remaining] = next(
                    item._raster_image.width() * item._raster_image.height()
                    for item in view._items_by_id.values()
                    if item._raster_image is not None
                )

        assert quality_pixels[20] < quality_pixels[16]
        assert quality_pixels[16] < quality_pixels[8]
        assert quality_pixels[8] < quality_pixels[4]
        assert quality_pixels[4] < quality_pixels[1]
        assert quality_pixels[1] == 40 * 20
    finally:
        view.deleteLater()
        ObjectGraphicsItem._image_cache.clear()
        ObjectGraphicsItem.set_project_raster_source_count(1)


def test_raster_quality_restore_is_time_sliced_and_keeps_heartbeat_live(
    qt_application: QtWidgets.QApplication,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ObjectGraphicsItem._image_cache.clear()
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_PREVIEW_MAX_PIXELS", 1_000)
    monkeypatch.setattr(ObjectGraphicsItem, "_IMAGE_CACHE_MAX_BYTES", 12_000)
    document = ProjectDocument.new("Async raster quality")
    layer = document.layers[0]
    objects: list[SceneObject] = []
    for index in range(17):
        image = QtGui.QImage(40, 20, QtGui.QImage.Format.Format_RGB32)
        image.fill(QtGui.QColor.fromHsv(index * 17, 230, 220))
        path = tmp_path / f"async-quality-{index}.png"
        assert image.save(str(path), "PNG")
        scene_object = SceneObject(
            name=f"Async quality {index}",
            kind=ObjectKind.IMAGE,
            layer_id=layer.id,
            transform=Transform(20.0 + index, 20.0, 4.0, 2.0),
            geometry={"asset": str(path)},
        )
        objects.append(scene_object)
        document.add_object(scene_object)
    view = WorkspaceView(document.work_area)
    reads: list[str] = []
    heartbeat = 0
    heartbeat_timer = QtCore.QTimer()
    heartbeat_timer.setInterval(1)

    def beat() -> None:
        nonlocal heartbeat
        heartbeat += 1

    heartbeat_timer.timeout.connect(beat)
    try:
        view.set_document(document)
        initial_pixels = next(
            item._raster_image.width() * item._raster_image.height()
            for item in view._items_by_id.values()
            if item._raster_image is not None
        )
        original_read = workspace_module.read_raster_asset_payload

        def slow_read(path: object):
            reads.append(str(path))
            time.sleep(0.01)
            return original_read(path)

        monkeypatch.setattr(
            workspace_module,
            "read_raster_asset_payload",
            slow_read,
        )
        document.remove_object(objects[-1].id)
        heartbeat_timer.start()
        started = time.perf_counter()
        view.set_document(document)
        call_seconds = time.perf_counter() - started

        assert call_seconds < 0.05
        assert view._raster_restore_timer.isActive()
        assert reads == []
        deadline = time.monotonic() + 3.0
        while view._raster_restore_timer.isActive():
            qt_application.processEvents()
            if time.monotonic() >= deadline:
                raise AssertionError("Timed out restoring raster preview quality")
            time.sleep(0.001)

        assert len(reads) == 16
        assert heartbeat >= 2
        assert all(
            item._raster_image is not None
            and item._raster_image.width() * item._raster_image.height()
            > initial_pixels
            for item in view._items_by_id.values()
        )

        document.add_object(objects[-1])
        view.set_document(document)
        document.remove_object(objects[-1].id)
        view.set_document(document)
        assert view._raster_restore_timer.isActive()
        document.add_object(objects[-1])
        view.set_document(document)
        assert not view._raster_restore_timer.isActive()
        assert view._raster_restore_queue == []
    finally:
        heartbeat_timer.stop()
        view.deleteLater()
        ObjectGraphicsItem._image_cache.clear()
        ObjectGraphicsItem.set_project_raster_source_count(1)
