import numpy as np

from laser_aligner.geometry.svg import parse_svg
from laser_aligner.geometry.transforms import apply_matrix, parse_transform


def test_svg_shapes_paths_and_physical_size() -> None:
    svg = """
    <svg xmlns="http://www.w3.org/2000/svg" width="100mm" height="50mm" viewBox="0 0 200 100">
      <g transform="translate(10,5) scale(0.9)">
        <rect x="0" y="0" width="100" height="40" rx="4"/>
        <path d="M 0 60 C 20 20, 60 100, 100 60 S 180 60, 190 30"/>
        <path d="M 120 20 A 20 12 20 1 1 160 50"/>
        <circle cx="170" cy="75" r="10"/>
      </g>
    </svg>
    """
    geometry = parse_svg(svg)
    assert len(geometry.polylines) == 4
    assert geometry.point_count > 40
    assert geometry.width > 100
    assert geometry.intrinsic_width_mm > 50
    assert geometry.intrinsic_height_mm > 20


def test_relative_path_and_close() -> None:
    geometry = parse_svg('<svg xmlns="http://www.w3.org/2000/svg"><path d="m 10 10 l 20 0 v 20 h -20 z"/></svg>')
    line = geometry.polylines[0]
    assert line.closed
    assert np.allclose(line.points[0], line.points[-1])
    assert np.allclose(line.points.min(axis=0), [10, 10])
    assert np.allclose(line.points.max(axis=0), [30, 30])


def test_transform_matrix_behavior() -> None:
    matrix = parse_transform("translate(10,20) scale(2)")
    point = apply_matrix(np.array([[1.0, 1.0]]), matrix)[0]
    assert np.allclose(point, [12.0, 22.0])
