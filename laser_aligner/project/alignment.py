from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from .model import Bounds, ObjectKind, ProjectDocument, Transform


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
    target: str = "selection",
    boundary_id: str | None = None,
) -> dict[str, Transform]:
    ids = list(dict.fromkeys(object_ids))
    if target not in {"selection", "bed", "rectangle"}:
        raise ValueError("Choose selection span, full bed, or a rectangle")
    if target != "selection":
        area = document.work_area
        if target == "rectangle":
            if not boundary_id:
                raise ValueError("Choose a boundary rectangle")
            boundary = document.get_object(boundary_id)
            if (
                boundary.kind != ObjectKind.RECTANGLE
                or boundary.geometry.get("corner_radius_mm", 0.0) != 0.0
                or abs(boundary.transform.rotation_deg / 90 - round(boundary.transform.rotation_deg / 90)) > 1e-9
            ):
                raise ValueError("Use a square-cornered rectangle aligned with the horizontal and vertical axes")
            area = boundary.bounds()
            ids = [object_id for object_id in ids if object_id != boundary_id]
        return _distribute_in_area(document, ids, area, horizontal=horizontal)
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


def _distribute_in_area(
    document: ProjectDocument,
    ids: list[str],
    area: Bounds,
    *,
    horizontal: bool,
) -> dict[str, Transform]:
    """Equal edge gaps, including margins; contain without resizing objects."""
    if not ids:
        raise ValueError("Select at least one object besides the boundary rectangle")
    if area.width <= 0 or area.height <= 0:
        raise ValueError("The distribution area must have positive width and height")
    # Document order breaks center ties consistently, independent of Qt selection order.
    selected = set(ids)
    items = [document.get_object(object_id) for object_id in ids]
    if any(item.locked or item.is_stock_boundary for item in items):
        raise ValueError("Locked objects and stock boundaries cannot be distributed")
    items = [item for item in document.objects if item.id in selected]
    items.sort(key=lambda item: item.bounds().center[0 if horizontal else 1])
    boxes = [item.bounds() for item in items]
    sizes = [box.width if horizontal else box.height for box in boxes]
    span = area.width if horizontal else area.height
    if sum(sizes) > span or any(box.width > area.width or box.height > area.height for box in boxes):
        raise ValueError("Objects do not fit in this area. Enlarge the area, resize objects, or select fewer objects.")
    gap = (span - sum(sizes)) / (len(items) + 1)
    position = (area.x_min if horizontal else area.y_min) + gap
    transforms = {}
    for item, box, size in zip(items, boxes, sizes, strict=True):
        # Preserve the other axis when possible; otherwise move only enough to fit.
        x_min = position if horizontal else min(max(box.x_min, area.x_min), area.x_max - box.width)
        y_min = min(max(box.y_min, area.y_min), area.y_max - box.height) if horizontal else position
        transforms[item.id] = item.transform.copy(
            x_mm=item.transform.x_mm + x_min - box.x_min,
            y_mm=item.transform.y_mm + y_min - box.y_min,
        )
        position += size + gap
    return transforms
