"""Frozen cutting outlines with an explicit, transform-only image link."""
from __future__ import annotations

import copy
import math
from collections.abc import Iterable, Mapping

from .history import AddLayerCommand, AddObjectCommand, Command
from .model import LayerMode, ObjectKind, OperationLayer, ProjectDocument, SceneObject, Transform
from .path_geometry import NativePathGeometry

OUTLINE_METADATA_KEY = "image_outline"


def validate_outline_metadata(item: SceneObject) -> dict | None:
    """Validate authoring provenance without assigning output authority."""
    if OUTLINE_METADATA_KEY not in item.metadata:
        return None
    value = item.metadata[OUTLINE_METADATA_KEY]
    if item.kind is not ObjectKind.PATH:
        raise ValueError("Image outline metadata belongs only to a native path")
    if not isinstance(value, dict) or set(value) != {
        "source_id", "linked", "offset_mm", "include_holes",
    }:
        raise ValueError("Image outline metadata requires source_id, linked, offset_mm and include_holes")
    if not isinstance(value["source_id"], str) or not value["source_id"].strip():
        raise ValueError("Image outline source_id must be a nonempty string")
    if type(value["linked"]) is not bool or type(value["include_holes"]) is not bool:
        raise ValueError("Image outline linked and include_holes must be booleans")
    offset = value["offset_mm"]
    if type(offset) not in (int, float) or not math.isfinite(offset):
        raise ValueError("Image outline offset must be a finite number of millimetres")
    return value


def validate_outline_links(objects: Iterable[SceneObject]) -> None:
    """Reject missing or misaligned linked sources on complete project reads."""
    by_id = {item.id: item for item in objects}
    for item in by_id.values():
        link = validate_outline_metadata(item)
        if link is None or not link["linked"]:
            continue
        source = by_id.get(link["source_id"])
        if source is None or source.kind is not ObjectKind.IMAGE:
            raise ValueError(f"Linked outline {item.name} requires its source image")
        if item.transform.to_dict() != source.transform.to_dict():
            raise ValueError(f"Linked outline {item.name} is misaligned with its source image")


def _linked_families(document: ProjectDocument) -> list[list[SceneObject]]:
    families: dict[str, list[SceneObject]] = {}
    by_id = {item.id: item for item in document.objects}
    for item in document.objects:
        link = validate_outline_metadata(item)
        if link is not None and link["linked"]:
            source_id = link["source_id"]
            if source_id not in families:
                families[source_id] = [by_id[source_id]]
            families[source_id].append(item)
    return list(families.values())


def linked_transform_locked_ids(document: ProjectDocument) -> set[str]:
    """An explicit position lock on any linked member anchors the whole pair."""
    return {
        item.id for family in _linked_families(document)
        if any(member.locked for member in family) for item in family
    }


def linked_transform_updates(
    document: ProjectDocument, transforms: Mapping[str, Transform],
    *, allow_linked_driver: bool = False,
) -> dict[str, Transform]:
    """Expand aligned transforms; only pure transforms may start at an outline.

    Shape replacement retains the source-only default: changing a path's local
    frame while editing its geometry must not silently reshape its source image.
    """
    validate_outline_links(document.objects)
    after = {object_id: transform.copy() for object_id, transform in transforms.items()}
    for object_id in after:
        document.get_object(object_id)
    if allow_linked_driver:
        for family in _linked_families(document):
            requested = [after[item.id] for item in family if item.id in after]
            if not requested:
                continue
            transform = requested[0]
            if any(other.to_dict() != transform.to_dict() for other in requested[1:]):
                raise ValueError("A linked outline must stay aligned; detach it before transforming separately")
            after[family[0].id] = transform.copy()
    for item in document.objects:
        link = validate_outline_metadata(item)
        if link is None or not link["linked"]:
            continue
        source_id = link["source_id"]
        if source_id in after:
            requested = after.get(item.id)
            if requested is not None and requested.to_dict() != after[source_id].to_dict():
                raise ValueError("A linked outline must stay aligned; detach it before transforming separately")
            after[item.id] = after[source_id].copy()
        elif item.id in after and after[item.id].to_dict() != item.transform.to_dict():
            raise ValueError("This outline is linked to its image; detach it before transforming separately")
    for object_id in linked_transform_locked_ids(document).intersection(after):
        if after[object_id].to_dict() != document.get_object(object_id).transform.to_dict():
            raise ValueError("Unlock the image and its linked outlines before transforming them")
    return after


def source_removal_detachments(
    document: ProjectDocument, removed_ids: Iterable[str],
) -> dict[str, dict]:
    """Return metadata snapshots for retained paths whose source is removed."""
    removed = set(removed_ids)
    return {
        item.id: copy.deepcopy(item.metadata)
        for item in document.objects
        if item.id not in removed
        and (link := validate_outline_metadata(item)) is not None
        and link["linked"] and link["source_id"] in removed
    }


def remap_duplicated_outline_links(
    originals: Iterable[SceneObject], duplicates: Iterable[SceneObject],
) -> None:
    """Pair copies with their copied image; path-only copies are detached."""
    pairs = list(zip(originals, duplicates, strict=True))
    copied_ids = {original.id: duplicate.id for original, duplicate in pairs}
    for _original, duplicate in pairs:
        link = validate_outline_metadata(duplicate)
        if link is None:
            continue
        source_id = link["source_id"]
        if source_id in copied_ids:
            link["source_id"] = copied_ids[source_id]
        elif link["linked"]:
            link["linked"] = False


class AddOutlineCommand(Command):
    """Create a reviewed geometry snapshot on a new, disabled zero-power layer."""

    description = "Create image outline path"

    def __init__(
        self, document: ProjectDocument, source_id: str, geometry: NativePathGeometry,
        *, linked: bool = True, offset_mm: float = 0.0, include_holes: bool = True,
    ) -> None:
        source = document.get_object(source_id)
        if source.kind is not ObjectKind.IMAGE:
            raise ValueError("An outline requires a source image")
        if not isinstance(geometry, NativePathGeometry):
            raise TypeError("An outline requires validated native path geometry")
        if any(not subpath.closed for subpath in geometry.subpaths):
            raise ValueError("An image cutting outline cannot contain open contours")
        from .image_edit import validate_outline

        errors = validate_outline(
            geometry, width_mm=source.transform.width_mm,
            height_mm=source.transform.height_mm,
        )
        if errors:
            raise ValueError("; ".join(errors))
        self.document = document
        self.layer = OperationLayer(
            name=f"{source.name} outline", color=document.next_layer_color(),
            mode=LayerMode.LINE, power_percent=0.0, output_enabled=False,
            visible=True, priority=len(document.layers),
        )
        self.item = SceneObject.native_path(
            self.layer.id, geometry, name=f"{source.name} outline",
            transform=source.transform.copy(),
        )
        self.item.metadata[OUTLINE_METADATA_KEY] = {
            "source_id": source.id, "linked": linked,
            "offset_mm": offset_mm, "include_holes": include_holes,
        }
        validate_outline_metadata(self.item)
        self._layer_command = AddLayerCommand(document, self.layer)
        self._object_command = AddObjectCommand(document, self.item)

    def redo(self) -> None:
        validate_outline_links((*self.document.objects, self.item))
        self._layer_command.redo()
        try:
            self._object_command.redo()
        except Exception:
            self._layer_command.undo()
            raise

    def undo(self) -> None:
        self._object_command.undo()
        self._layer_command.undo()


class DetachOutlineCommand(Command):
    """Detach transform synchronization without altering the frozen path."""

    description = "Detach outline from image"

    def __init__(self, document: ProjectDocument, object_id: str) -> None:
        item = document.get_object(object_id)
        link = validate_outline_metadata(item)
        if link is None or not link["linked"]:
            raise ValueError("Select an outline linked to an image")
        self.document = document
        self.object_id = object_id
        self.before = copy.deepcopy(item.metadata)
        self.after = copy.deepcopy(item.metadata)
        self.after[OUTLINE_METADATA_KEY]["linked"] = False

    def _apply(self, metadata: dict) -> None:
        item = self.document.get_object(self.object_id)
        if item.metadata != metadata:
            item.metadata = copy.deepcopy(metadata)
            self.document.touch()

    def redo(self) -> None:
        self._apply(self.after)

    def undo(self) -> None:
        self._apply(self.before)
