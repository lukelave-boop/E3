from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from .model import ProjectDocument, Transform


class Alignment(str, Enum):
    LEFT = "left"
    CENTER_X = "center_x"
    RIGHT = "right"
    BOTTOM = "bottom"
    CENTER_Y = "center_y"
    TOP = "top"


def aligned_transforms(
    document: ProjectDocument,
    object_ids: Iterable[str],
    alignment: Alignment,
) -> dict[str, Transform]:
    ids = list(dict.fromkeys(object_ids))
    if len(ids) < 2:
        return {}
    items = [document.get_object(object_id) for object_id in ids]
    bounds = [item.bounds() for item in items]

    if alignment == Alignment.LEFT:
        target = min(box.x_min for box in bounds)
        deltas = [target - box.x_min for box in bounds]
        return {
            item.id: item.transform.copy(x_mm=item.transform.x_mm + delta)
            for item, delta in zip(items, deltas, strict=True)
        }
    if alignment == Alignment.RIGHT:
        target = max(box.x_max for box in bounds)
        deltas = [target - box.x_max for box in bounds]
        return {
            item.id: item.transform.copy(x_mm=item.transform.x_mm + delta)
            for item, delta in zip(items, deltas, strict=True)
        }
    if alignment == Alignment.CENTER_X:
        target = sum(box.center[0] for box in bounds) / len(bounds)
        return {
            item.id: item.transform.copy(x_mm=item.transform.x_mm + target - box.center[0])
            for item, box in zip(items, bounds, strict=True)
        }
    if alignment == Alignment.BOTTOM:
        target = min(box.y_min for box in bounds)
        deltas = [target - box.y_min for box in bounds]
        return {
            item.id: item.transform.copy(y_mm=item.transform.y_mm + delta)
            for item, delta in zip(items, deltas, strict=True)
        }
    if alignment == Alignment.TOP:
        target = max(box.y_max for box in bounds)
        deltas = [target - box.y_max for box in bounds]
        return {
            item.id: item.transform.copy(y_mm=item.transform.y_mm + delta)
            for item, delta in zip(items, deltas, strict=True)
        }
    if alignment == Alignment.CENTER_Y:
        target = sum(box.center[1] for box in bounds) / len(bounds)
        return {
            item.id: item.transform.copy(y_mm=item.transform.y_mm + target - box.center[1])
            for item, box in zip(items, bounds, strict=True)
        }
    raise ValueError(f"Unsupported alignment: {alignment}")


def distributed_transforms(
    document: ProjectDocument,
    object_ids: Iterable[str],
    *,
    horizontal: bool,
) -> dict[str, Transform]:
    ids = list(dict.fromkeys(object_ids))
    if len(ids) < 3:
        return {}
    items = [document.get_object(object_id) for object_id in ids]
    if horizontal:
        items.sort(key=lambda item: item.bounds().center[0])
        first = items[0].bounds().center[0]
        last = items[-1].bounds().center[0]
        spacing = (last - first) / (len(items) - 1)
        return {
            item.id: item.transform.copy(x_mm=item.transform.x_mm + (first + index * spacing) - item.bounds().center[0])
            for index, item in enumerate(items)
        }
    items.sort(key=lambda item: item.bounds().center[1])
    first = items[0].bounds().center[1]
    last = items[-1].bounds().center[1]
    spacing = (last - first) / (len(items) - 1)
    return {
        item.id: item.transform.copy(y_mm=item.transform.y_mm + (first + index * spacing) - item.bounds().center[1])
        for index, item in enumerate(items)
    }
