"""Source-neutral binary foreground cleanup and contour-tree decomposition."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


class ForegroundComponentLimitError(ValueError):
    """Raised when a binary foreground contains too many retained components."""

    def __init__(self, count: int, maximum: int) -> None:
        super().__init__(
            f"{count:,} connected foreground components exceed the "
            f"{maximum:,}-component limit"
        )
        self.count = int(count)
        self.maximum = int(maximum)


class ForegroundContourLimitError(ValueError):
    """Raised when a binary foreground contains too many retained contours."""

    def __init__(self, count: int, maximum: int) -> None:
        super().__init__(
            f"{count:,} foreground contours exceed the {maximum:,}-contour limit"
        )
        self.count = int(count)
        self.maximum = int(maximum)


@dataclass(frozen=True, slots=True)
class ForegroundContourTree:
    """One outer contour and its complete even-odd descendant hierarchy."""

    root_index: int
    contour_indices: tuple[int, ...]
    parents: tuple[int | None, ...]
    depths: tuple[int, ...]
    bounds_px: tuple[int, int, int, int]
    touches_image_edge: bool


def _binary_mask(mask: np.ndarray) -> np.ndarray:
    source = np.asarray(mask)
    if source.ndim != 2:
        raise TypeError("Foreground masks must be two-dimensional")
    return (source > 0).astype(np.uint8) * 255


def readonly_array(array: np.ndarray) -> np.ndarray:
    """Return a contiguous read-only array owned by the returned value."""

    result = np.ascontiguousarray(array).copy()
    result.setflags(write=False)
    return result


def clean_foreground_components(
    mask: np.ndarray,
    *,
    minimum_component_area_px: float,
    minimum_hole_area_px: float | None = None,
    maximum_components: int | None = None,
) -> tuple[np.ndarray, int]:
    """Remove small foreground components and optionally fill small holes."""

    binary = _binary_mask(mask)
    minimum_component_area_px = max(0.0, float(minimum_component_area_px))
    hole_area = (
        minimum_component_area_px
        if minimum_hole_area_px is None
        else max(0.0, float(minimum_hole_area_px))
    )
    count, labels, stats, _centroids = cv2.connectedComponentsWithStats(
        binary,
        connectivity=8,
        ltype=cv2.CV_32S,
    )
    retained_labels = [
        index
        for index in range(1, count)
        if int(stats[index, cv2.CC_STAT_AREA]) >= minimum_component_area_px
    ]
    if maximum_components is not None and len(retained_labels) > maximum_components:
        raise ForegroundComponentLimitError(len(retained_labels), maximum_components)
    retained = np.zeros(count, dtype=np.uint8)
    if retained_labels:
        retained[np.asarray(retained_labels, dtype=np.int64)] = 255
    cleaned = retained[labels]

    if hole_area > 0.0 and retained_labels:
        background_count, background_labels, background_stats, _centroids = (
            cv2.connectedComponentsWithStats(
                cv2.bitwise_not(cleaned),
                connectivity=8,
                ltype=cv2.CV_32S,
            )
        )
        border_labels = set(
            int(value)
            for value in np.unique(
                np.concatenate(
                    (
                        background_labels[0],
                        background_labels[-1],
                        background_labels[:, 0],
                        background_labels[:, -1],
                    )
                )
            )
        )
        fill_labels = [
            index
            for index in range(1, background_count)
            if index not in border_labels
            and int(background_stats[index, cv2.CC_STAT_AREA]) < hole_area
        ]
        if fill_labels:
            fill = np.zeros(background_count, dtype=bool)
            fill[np.asarray(fill_labels, dtype=np.int64)] = True
            cleaned = cleaned.copy()
            cleaned[fill[background_labels]] = 255
    return cleaned, len(retained_labels)


def extract_foreground_contours(
    mask: np.ndarray,
    *,
    approximation: int = cv2.CHAIN_APPROX_NONE,
    maximum_contours: int | None = None,
) -> tuple[tuple[np.ndarray, ...], np.ndarray]:
    """Extract one bounded OpenCV RETR_TREE hierarchy from a binary mask."""

    contours, hierarchy = cv2.findContours(
        _binary_mask(mask),
        cv2.RETR_TREE,
        approximation,
    )
    if hierarchy is None or not contours:
        raise ValueError("No closed foreground contours were produced")
    if maximum_contours is not None and len(contours) > maximum_contours:
        raise ForegroundContourLimitError(len(contours), maximum_contours)
    return (
        tuple(readonly_array(contour) for contour in contours),
        readonly_array(hierarchy),
    )


def contour_depth(index: int, hierarchy: np.ndarray) -> int:
    """Return one contour's depth while validating parent relationships."""

    tree = np.asarray(hierarchy)
    if tree.ndim == 3:
        tree = tree[0]
    if tree.ndim != 2 or tree.shape[1] != 4:
        raise ValueError("Contour hierarchy must have OpenCV RETR_TREE shape")
    if not 0 <= int(index) < len(tree):
        raise ValueError("Contour index is outside the hierarchy")
    depth = 0
    parent = int(tree[int(index), 3])
    while parent >= 0:
        if parent >= len(tree):
            raise ValueError("Contour hierarchy contains an out-of-range parent")
        depth += 1
        if depth > len(tree):
            raise ValueError("Contour hierarchy is cyclic")
        parent = int(tree[parent, 3])
    return depth


def foreground_contour_trees(
    contours: tuple[np.ndarray, ...],
    hierarchy: np.ndarray,
    image_shape: tuple[int, int] | tuple[int, int, int],
) -> tuple[ForegroundContourTree, ...]:
    """Partition a RETR_TREE forest into deterministic root contour trees."""

    tree = np.asarray(hierarchy)
    if tree.ndim == 3:
        tree = tree[0]
    if len(contours) != len(tree):
        raise ValueError("Contour hierarchy length does not match contours")
    height, width = int(image_shape[0]), int(image_shape[1])
    depths = tuple(contour_depth(index, tree) for index in range(len(contours)))
    roots = [index for index in range(len(contours)) if int(tree[index, 3]) < 0]
    output: list[ForegroundContourTree] = []
    for root in roots:
        descendants: list[int] = []
        for index in range(len(contours)):
            ancestor = index
            while ancestor >= 0 and ancestor != root:
                ancestor = int(tree[ancestor, 3])
            if ancestor == root:
                descendants.append(index)
        descendants.sort(key=lambda index: (depths[index] - depths[root], index))
        local_index = {original: index for index, original in enumerate(descendants)}
        parents = tuple(
            local_index.get(int(tree[original, 3])) for original in descendants
        )
        x, y, extent_width, extent_height = cv2.boundingRect(contours[root])
        output.append(
            ForegroundContourTree(
                root_index=root,
                contour_indices=tuple(descendants),
                parents=parents,
                depths=tuple(depths[index] - depths[root] for index in descendants),
                bounds_px=(int(x), int(y), int(extent_width), int(extent_height)),
                touches_image_edge=bool(
                    x <= 0
                    or y <= 0
                    or x + extent_width >= width
                    or y + extent_height >= height
                ),
            )
        )
    return tuple(
        sorted(
            output,
            key=lambda item: (
                item.bounds_px[1],
                item.bounds_px[0],
                item.bounds_px[3],
                item.bounds_px[2],
            ),
        )
    )


def contour_tree_at_point(
    contours: tuple[np.ndarray, ...],
    trees: tuple[ForegroundContourTree, ...],
    point_px: tuple[int, int],
) -> ForegroundContourTree | None:
    """Return the unique visible foreground tree containing a clicked pixel."""

    x, y = float(point_px[0]), float(point_px[1])
    matches: list[ForegroundContourTree] = []
    for candidate in trees:
        containing_depth = -1
        for relative_depth, contour_index in zip(
            candidate.depths, candidate.contour_indices, strict=True
        ):
            if cv2.pointPolygonTest(contours[contour_index], (x, y), False) >= 0:
                containing_depth = relative_depth
        if containing_depth >= 0 and containing_depth % 2 == 0:
            matches.append(candidate)
    return matches[0] if len(matches) == 1 else None


def render_contour_tree_mask(
    contours: tuple[np.ndarray, ...],
    candidate: ForegroundContourTree,
    image_shape: tuple[int, int] | tuple[int, int, int],
) -> np.ndarray:
    """Render exactly one root contour tree with even-odd foreground parity."""

    mask = np.zeros((int(image_shape[0]), int(image_shape[1])), dtype=np.uint8)
    for relative_depth, contour_index in zip(
        candidate.depths, candidate.contour_indices, strict=True
    ):
        cv2.drawContours(
            mask,
            contours,
            contour_index,
            255 if relative_depth % 2 == 0 else 0,
            thickness=cv2.FILLED,
        )
    return mask


__all__ = [
    "ForegroundComponentLimitError",
    "ForegroundContourLimitError",
    "ForegroundContourTree",
    "clean_foreground_components",
    "contour_depth",
    "contour_tree_at_point",
    "extract_foreground_contours",
    "foreground_contour_trees",
    "readonly_array",
    "render_contour_tree_mask",
]
