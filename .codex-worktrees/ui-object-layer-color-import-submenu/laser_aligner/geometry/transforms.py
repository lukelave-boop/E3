from __future__ import annotations

import math
import re

import numpy as np

_TRANSFORM_RE = re.compile(r"([a-zA-Z]+)\s*\(([^)]*)\)")
_NUMBER_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def identity() -> np.ndarray:
    return np.eye(3, dtype=np.float64)


def translate(tx: float, ty: float = 0.0) -> np.ndarray:
    return np.array([[1.0, 0.0, tx], [0.0, 1.0, ty], [0.0, 0.0, 1.0]], dtype=np.float64)


def scale(sx: float, sy: float | None = None) -> np.ndarray:
    sy = sx if sy is None else sy
    return np.array([[sx, 0.0, 0.0], [0.0, sy, 0.0], [0.0, 0.0, 1.0]], dtype=np.float64)


def rotate(angle_deg: float, cx: float = 0.0, cy: float = 0.0) -> np.ndarray:
    radians = math.radians(angle_deg)
    cosine, sine = math.cos(radians), math.sin(radians)
    core = np.array(
        [[cosine, -sine, 0.0], [sine, cosine, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )
    return translate(cx, cy) @ core @ translate(-cx, -cy)


def skew_x(angle_deg: float) -> np.ndarray:
    return np.array(
        [[1.0, math.tan(math.radians(angle_deg)), 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def skew_y(angle_deg: float) -> np.ndarray:
    return np.array(
        [[1.0, 0.0, 0.0], [math.tan(math.radians(angle_deg)), 1.0, 0.0], [0.0, 0.0, 1.0]],
        dtype=np.float64,
    )


def parse_transform(value: str | None) -> np.ndarray:
    """Parse an SVG transform list into a 3x3 column-vector matrix."""
    result = identity()
    if not value:
        return result
    if value.strip().lower() == "none":
        return result
    transform_end = 0
    found = False
    for match in _TRANSFORM_RE.finditer(value):
        if re.fullmatch(r"[\s,]*", value[transform_end : match.start()]) is None:
            raise ValueError(
                f"Invalid SVG transform token near character {transform_end}"
            )
        found = True
        name = match.group(1).lower()
        arguments = match.group(2)
        numbers: list[float] = []
        argument_end = 0
        for number_match in _NUMBER_RE.finditer(arguments):
            if re.fullmatch(
                r"[\s,]*", arguments[argument_end : number_match.start()]
            ) is None:
                raise ValueError(f"Invalid numeric value in SVG {name}() transform")
            number = float(number_match.group(0))
            if not math.isfinite(number):
                raise ValueError(f"SVG {name}() transform values must be finite")
            numbers.append(number)
            argument_end = number_match.end()
        if re.fullmatch(r"[\s,]*", arguments[argument_end:]) is None:
            raise ValueError(f"Invalid numeric value in SVG {name}() transform")
        if name == "matrix" and len(numbers) == 6:
            a, b, c, d, e, f = numbers
            operation = np.array([[a, c, e], [b, d, f], [0.0, 0.0, 1.0]], dtype=np.float64)
        elif name == "translate" and len(numbers) in {1, 2}:
            operation = translate(numbers[0], numbers[1] if len(numbers) > 1 else 0.0)
        elif name == "scale" and len(numbers) in {1, 2}:
            operation = scale(numbers[0], numbers[1] if len(numbers) > 1 else None)
        elif name == "rotate" and len(numbers) in {1, 3}:
            operation = rotate(
                numbers[0],
                numbers[1] if len(numbers) > 2 else 0.0,
                numbers[2] if len(numbers) > 2 else 0.0,
            )
        elif name == "skewx" and len(numbers) == 1:
            operation = skew_x(numbers[0])
        elif name == "skewy" and len(numbers) == 1:
            operation = skew_y(numbers[0])
        else:
            raise ValueError(f"Unsupported or malformed SVG transform: {name}()")
        result = result @ operation
        transform_end = match.end()
    if not found or re.fullmatch(r"[\s,]*", value[transform_end:]) is None:
        raise ValueError(f"Invalid SVG transform token near character {transform_end}")
    return result


def apply_matrix(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    if points.size == 0:
        return points.astype(np.float64)
    points = np.asarray(points, dtype=np.float64).reshape(-1, 2)
    homogeneous = np.column_stack([points, np.ones(len(points), dtype=np.float64)])
    transformed = (matrix @ homogeneous.T).T
    divisor = transformed[:, 2:3]
    divisor[np.abs(divisor) < 1e-15] = 1.0
    return transformed[:, :2] / divisor
