from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6", reason="PySide6 is required for desktop tests")

from PySide6 import QtCore, QtGui, QtWidgets

from laser_aligner.desktop.workspace import ObjectGraphicsItem
from laser_aligner.project import (
    LayerMode,
    NativePathGeometry,
    OperationLayer,
    PathCubicSegment,
    PathFillRule,
    PathLineSegment,
    PathSubpath,
    SceneObject,
    Transform,
)


@pytest.fixture(scope="module")
def qt_application() -> Iterator[QtWidgets.QApplication]:
    application = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    yield application


def _square(half_size: float) -> PathSubpath:
    return PathSubpath(
        start=(-half_size, -half_size),
        segments=(
            PathLineSegment((half_size, -half_size)),
            PathLineSegment((half_size, half_size)),
            PathLineSegment((-half_size, half_size)),
            PathLineSegment((-half_size, -half_size)),
        ),
        closed=True,
    )


def test_workspace_path_retains_cubic_elements_and_object_transform(
    qt_application: QtWidgets.QApplication,
) -> None:
    del qt_application
    layer = OperationLayer(mode=LayerMode.LINE)
    geometry = NativePathGeometry(
        subpaths=(
            PathSubpath(
                start=(-0.5, 0.0),
                segments=(
                    PathCubicSegment(
                        control_1=(-0.25, 0.75),
                        control_2=(0.25, 0.75),
                        to=(0.5, 0.0),
                    ),
                ),
                closed=False,
            ),
        ),
        fill_rule=PathFillRule.EVENODD,
    )
    scene_object = SceneObject.native_path(
        layer.id,
        geometry,
        name="Native cubic",
        transform=Transform(
            x_mm=12.0,
            y_mm=34.0,
            width_mm=20.0,
            height_mm=10.0,
            rotation_deg=30.0,
            mirror_x=True,
            mirror_y=True,
        ),
    )

    item = ObjectGraphicsItem(scene_object, layer)
    path = item.path()

    assert path.elementCount() == 4
    elements = [path.elementAt(index) for index in range(path.elementCount())]
    assert [element.type for element in elements] == [
        QtGui.QPainterPath.ElementType.MoveToElement,
        QtGui.QPainterPath.ElementType.CurveToElement,
        QtGui.QPainterPath.ElementType.CurveToDataElement,
        QtGui.QPainterPath.ElementType.CurveToDataElement,
    ]
    assert [(element.x, element.y) for element in elements] == pytest.approx(
        [(-10.0, 0.0), (-5.0, -7.5), (5.0, -7.5), (10.0, 0.0)]
    )
    assert path.fillRule() is QtCore.Qt.FillRule.OddEvenFill
    assert item.pos() == QtCore.QPointF(12.0, -34.0)
    assert item.rotation() == pytest.approx(-30.0)
    assert item.transform().m11() == pytest.approx(-1.0)
    assert item.transform().m22() == pytest.approx(-1.0)
    item.setSelected(True)
    assert item.isSelected()


@pytest.mark.parametrize(
    ("fill_rule", "center_is_filled"),
    [
        (PathFillRule.EVENODD, False),
        (PathFillRule.NONZERO, True),
    ],
)
def test_workspace_compound_path_honors_native_fill_rule(
    qt_application: QtWidgets.QApplication,
    fill_rule: PathFillRule,
    center_is_filled: bool,
) -> None:
    del qt_application
    layer = OperationLayer(mode=LayerMode.FILL)
    scene_object = SceneObject.native_path(
        layer.id,
        NativePathGeometry(
            subpaths=(_square(0.5), _square(0.2)),
            fill_rule=fill_rule,
        ),
        transform=Transform(width_mm=20.0, height_mm=20.0),
    )

    path = ObjectGraphicsItem._path_for_object(scene_object)

    expected = (
        QtCore.Qt.FillRule.OddEvenFill
        if fill_rule is PathFillRule.EVENODD
        else QtCore.Qt.FillRule.WindingFill
    )
    assert path.fillRule() is expected
    assert path.contains(QtCore.QPointF(0.0, 0.0)) is center_is_filled
