import math
import random

import pytest

from laser_aligner.geometry.polygon import (
    convex_polygon_contains_normalized,
    normalize_convex_polygon,
    prepared_convex_polygon_contains,
)


@pytest.mark.parametrize("angle", [0, .03, .3, 1.1])
@pytest.mark.parametrize("offset", [0, 100, -100])
def test_prepared_predicate_matches_reference_inside_outside_and_at_tolerance(angle, offset):
    def rotate(x, y):
        return (offset + x * math.cos(angle) - y * math.sin(angle),
                offset + x * math.sin(angle) + y * math.cos(angle))
    polygon = normalize_convex_polygon(tuple(rotate(x, y) for x, y in ((0, 0), (73, 0), (70, 91), (0, 80))))
    contains = prepared_convex_polygon_contains(polygon)
    randomizer = random.Random(42)
    points = [rotate(randomizer.uniform(-10, 100), randomizer.uniform(-10, 100)) for _ in range(1000)]
    points += [rotate(x, y) for x in (0, 1, 30, 73)
               for y in (-1.01e-6, -1e-6, -0.99e-6, 0, 1e-6)]
    points += list(polygon) + [(math.inf, 0), (0, math.nan), (-math.inf, 0)]
    for point in points:
        assert contains(point) == convex_polygon_contains_normalized(point, polygon)


def test_prepared_polygon_rejects_invalid_boundary():
    with pytest.raises(ValueError):
        prepared_convex_polygon_contains(((0, 0), (1, 0), (0, 0)))
