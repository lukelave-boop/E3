from __future__ import annotations

import copy
import uuid
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from .model import OperationLayer, ProjectDocument, SceneObject, Transform


class Command(ABC):
    """A reversible document operation."""

    description: str = "Edit"

    @abstractmethod
    def redo(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def undo(self) -> None:
        raise NotImplementedError


class FunctionalCommand(Command):
    def __init__(
        self,
        description: str,
        redo_callback: Callable[[], None],
        undo_callback: Callable[[], None],
    ) -> None:
        self.description = description
        self.redo_callback = redo_callback
        self.undo_callback = undo_callback

    def redo(self) -> None:
        self.redo_callback()

    def undo(self) -> None:
        self.undo_callback()


class CommandStack:
    """Framework-independent undo/redo stack used by both web and desktop UIs."""

    def __init__(self, max_depth: int = 250) -> None:
        if max_depth < 1:
            raise ValueError("max_depth must be positive")
        self.max_depth = int(max_depth)
        self._commands: list[Command] = []
        self._index = 0
        self._clean_index = 0
        self._listeners: list[Callable[["CommandStack"], None]] = []

    @property
    def can_undo(self) -> bool:
        return self._index > 0

    @property
    def can_redo(self) -> bool:
        return self._index < len(self._commands)

    @property
    def undo_text(self) -> str:
        return "" if not self.can_undo else self._commands[self._index - 1].description

    @property
    def redo_text(self) -> str:
        return "" if not self.can_redo else self._commands[self._index].description

    @property
    def is_clean(self) -> bool:
        return self._index == self._clean_index

    @property
    def depth(self) -> int:
        return len(self._commands)

    def add_listener(self, callback: Callable[["CommandStack"], None]) -> None:
        if callback not in self._listeners:
            self._listeners.append(callback)

    def remove_listener(self, callback: Callable[["CommandStack"], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _changed(self) -> None:
        for callback in tuple(self._listeners):
            callback(self)

    def clear(self) -> None:
        self._commands.clear()
        self._index = 0
        self._clean_index = 0
        self._changed()

    def mark_clean(self) -> None:
        self._clean_index = self._index
        self._changed()

    def mark_dirty(self) -> None:
        """Mark a non-command edit as unsaved without inventing an undo entry."""
        self._clean_index = -1
        self._changed()

    def execute(self, command: Command) -> None:
        if self.can_redo:
            del self._commands[self._index :]
            if self._clean_index > self._index:
                self._clean_index = -1
        command.redo()
        self._commands.append(command)
        self._index += 1
        if len(self._commands) > self.max_depth:
            excess = len(self._commands) - self.max_depth
            del self._commands[:excess]
            self._index -= excess
            self._clean_index = max(-1, self._clean_index - excess)
        self._changed()

    def undo(self) -> bool:
        if not self.can_undo:
            return False
        self._index -= 1
        self._commands[self._index].undo()
        self._changed()
        return True

    def redo(self) -> bool:
        if not self.can_redo:
            return False
        self._commands[self._index].redo()
        self._index += 1
        self._changed()
        return True


class AddLayerCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        layer: OperationLayer,
        index: int | None = None,
        description: str = "Add layer",
    ) -> None:
        self.document = document
        self.layer = OperationLayer.from_dict(layer.to_dict())
        self.index = len(document.layers) if index is None else int(index)
        self.description = description

    def redo(self) -> None:
        if any(layer.id == self.layer.id for layer in self.document.layers):
            raise ValueError(f"Duplicate layer ID: {self.layer.id}")
        insertion = max(0, min(self.index, len(self.document.layers)))
        self.document.layers.insert(insertion, self.layer)
        self.index = insertion
        self.document.touch()

    def undo(self) -> None:
        layer = self.document.get_layer(self.layer.id)
        if any(item.layer_id == layer.id for item in self.document.objects):
            raise ValueError("Cannot undo layer creation while objects still use the layer")
        self.index = self.document.layers.index(layer)
        self.document.layers.pop(self.index)
        self.document.touch()


class RemoveLayerCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        layer_id: str,
        reassign_to: str | None = None,
        description: str = "Remove layer",
    ) -> None:
        if len(document.layers) <= 1:
            raise ValueError("A project must contain at least one layer")
        layer = document.get_layer(layer_id)
        self.document = document
        self.layer = OperationLayer.from_dict(layer.to_dict())
        self.index = document.layers.index(layer)
        self.fallback_id = reassign_to or next(
            candidate.id for candidate in document.layers if candidate.id != layer_id
        )
        document.get_layer(self.fallback_id)
        self.object_ids = [item.id for item in document.objects if item.layer_id == layer_id]
        self.description = description

    def redo(self) -> None:
        layer = self.document.get_layer(self.layer.id)
        for object_id in self.object_ids:
            self.document.get_object(object_id).layer_id = self.fallback_id
        self.index = self.document.layers.index(layer)
        self.document.layers.pop(self.index)
        self.document.touch()

    def undo(self) -> None:
        if any(layer.id == self.layer.id for layer in self.document.layers):
            raise ValueError(f"Duplicate layer ID: {self.layer.id}")
        self.document.layers.insert(
            max(0, min(self.index, len(self.document.layers))),
            self.layer,
        )
        for object_id in self.object_ids:
            self.document.get_object(object_id).layer_id = self.layer.id
        self.document.touch()


class UpdateLayerCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        layer_id: str,
        replacement: OperationLayer,
        description: str = "Edit layer",
    ) -> None:
        before = document.get_layer(layer_id)
        if replacement.id != layer_id:
            raise ValueError("Layer replacement must preserve the layer ID")
        self.document = document
        self.layer_id = layer_id
        self.before = OperationLayer.from_dict(before.to_dict())
        self.after = OperationLayer.from_dict(replacement.to_dict())
        self.description = description

    def _apply(self, replacement: OperationLayer) -> None:
        current = self.document.get_layer(self.layer_id)
        index = self.document.layers.index(current)
        self.document.layers[index] = OperationLayer.from_dict(replacement.to_dict())
        self.document.touch()

    def redo(self) -> None:
        self._apply(self.after)

    def undo(self) -> None:
        self._apply(self.before)


class AddObjectCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        item: SceneObject,
        index: int | None = None,
        description: str = "Add object",
    ) -> None:
        self.document = document
        self.item = item
        self.index = index
        self.description = description
        self._actual_index: int | None = None

    def redo(self) -> None:
        insertion = self.index if self._actual_index is None else self._actual_index
        self.document.add_object(self.item, insertion)
        self._actual_index = self.document.objects.index(self.item)

    def undo(self) -> None:
        _, self._actual_index = self.document.remove_object(self.item.id)


class AddObjectsCommand(Command):
    """Add a collection of objects as one undoable operation."""

    def __init__(
        self,
        document: ProjectDocument,
        items: Iterable[SceneObject],
        index: int | None = None,
        description: str = "Add objects",
    ) -> None:
        self.document = document
        self.items = list(items)
        if not self.items:
            raise ValueError("AddObjectsCommand requires at least one object")
        self.index = len(document.objects) if index is None else int(index)
        self.description = description
        self._indices: list[int] = []

    def redo(self) -> None:
        if self._indices:
            records = list(zip(self.items, self._indices, strict=True))
        else:
            start = max(0, min(self.index, len(self.document.objects)))
            records = [(item, start + offset) for offset, item in enumerate(self.items)]
        self._indices = []
        for item, index in records:
            self.document.add_object(item, index)
            self._indices.append(self.document.objects.index(item))

    def undo(self) -> None:
        records = sorted(
            zip(self.items, self._indices, strict=True),
            key=lambda record: record[1],
            reverse=True,
        )
        for item, _ in records:
            self.document.remove_object(item.id)


class RemoveObjectsCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        object_ids: Iterable[str],
        description: str = "Delete objects",
    ) -> None:
        self.document = document
        self.object_ids = list(dict.fromkeys(object_ids))
        self.description = description
        self._removed: list[tuple[SceneObject, int]] = []

    def redo(self) -> None:
        if not self._removed:
            records = [
                (self.document.get_object(object_id), self.document.objects.index(self.document.get_object(object_id)))
                for object_id in self.object_ids
            ]
            for item, index in sorted(records, key=lambda record: record[1], reverse=True):
                self.document.objects.pop(index)
                self._removed.append((item, index))
            self._removed.sort(key=lambda record: record[1])
            if self._removed:
                self.document.touch()
            return
        for item, _ in sorted(self._removed, key=lambda record: record[1], reverse=True):
            self.document.remove_object(item.id)

    def undo(self) -> None:
        for item, index in self._removed:
            self.document.add_object(item, index)


class UpdateObjectPropertiesCommand(Command):
    _ALLOWED = {"name", "visible", "locked"}

    def __init__(
        self,
        document: ProjectDocument,
        object_id: str,
        changes: dict[str, Any],
        description: str = "Edit object",
    ) -> None:
        unsupported = set(changes) - self._ALLOWED
        if unsupported:
            raise ValueError(f"Unsupported object properties: {sorted(unsupported)}")
        self.document = document
        self.object_id = object_id
        item = document.get_object(object_id)
        self.before = {name: getattr(item, name) for name in changes}
        self.after = dict(changes)
        if "name" in self.after:
            self.after["name"] = str(self.after["name"] or "Object")[:160]
        if "visible" in self.after:
            self.after["visible"] = bool(self.after["visible"])
        if "locked" in self.after:
            self.after["locked"] = bool(self.after["locked"])
        self.description = description

    def _apply(self, values: dict[str, Any]) -> None:
        item = self.document.get_object(self.object_id)
        changed = False
        for name, value in values.items():
            if getattr(item, name) != value:
                setattr(item, name, value)
                changed = True
        if changed:
            self.document.touch()

    def redo(self) -> None:
        self._apply(self.after)

    def undo(self) -> None:
        self._apply(self.before)


class UpdateObjectShapeCommand(Command):
    """Atomically replace one object's transform and validated geometry."""

    def __init__(
        self,
        document: ProjectDocument,
        object_id: str,
        new_transform: Transform,
        new_geometry: Mapping[str, Any],
        description: str = "Edit object shape",
    ) -> None:
        self.document = document
        self.object_id = str(object_id)
        item = document.get_object(self.object_id)
        self.before_transform = item.transform.copy()
        self.before_geometry = copy.deepcopy(item.geometry)

        payload = item.to_dict()
        payload["transform"] = new_transform.to_dict()
        payload["geometry"] = copy.deepcopy(dict(new_geometry))
        validated = SceneObject.from_dict(payload)
        if validated.id != item.id or validated.layer_id != item.layer_id:
            raise ValueError("Shape replacement must preserve object identity and layer")

        self.after_transform = validated.transform.copy()
        self.after_geometry = copy.deepcopy(validated.geometry)
        self.description = description

    def _apply(self, transform: Transform, geometry: Mapping[str, Any]) -> None:
        item = self.document.get_object(self.object_id)
        transform_changed = item.transform.to_dict() != transform.to_dict()
        geometry_changed = item.geometry != geometry
        if not transform_changed and not geometry_changed:
            return
        item.transform = transform.copy()
        item.geometry = copy.deepcopy(dict(geometry))
        self.document.touch()

    def redo(self) -> None:
        self._apply(self.after_transform, self.after_geometry)

    def undo(self) -> None:
        self._apply(self.before_transform, self.before_geometry)


class UpdateTransformCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        object_id: str,
        new_transform: Transform,
        description: str = "Transform object",
    ) -> None:
        self.document = document
        self.object_id = object_id
        self.before = document.get_object(object_id).transform.copy()
        self.after = new_transform.copy()
        self.description = description

    def redo(self) -> None:
        self.document.update_transform(self.object_id, self.after.copy())

    def undo(self) -> None:
        self.document.update_transform(self.object_id, self.before.copy())


class UpdateTransformsCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        transforms: dict[str, Transform],
        description: str = "Transform objects",
    ) -> None:
        self.document = document
        self.after = {object_id: transform.copy() for object_id, transform in transforms.items()}
        self.before = {
            object_id: document.get_object(object_id).transform.copy()
            for object_id in self.after
        }
        self.description = description

    def _apply(self, transforms: dict[str, Transform]) -> None:
        changed = False
        for object_id, transform in transforms.items():
            item = self.document.get_object(object_id)
            if item.transform.to_dict() != transform.to_dict():
                item.transform = transform.copy()
                changed = True
        if changed:
            self.document.touch()

    def redo(self) -> None:
        self._apply(self.after)

    def undo(self) -> None:
        self._apply(self.before)


class GroupObjectsCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        object_ids: Iterable[str],
        description: str = "Group objects",
    ) -> None:
        self.document = document
        self.object_ids = list(dict.fromkeys(object_ids))
        if len(self.object_ids) < 2:
            raise ValueError("At least two objects are required to create a group")
        self.before = {
            object_id: document.get_object(object_id).group_id
            for object_id in self.object_ids
        }
        self.group_id = f"group-{uuid.uuid4().hex}"
        self.description = description

    def redo(self) -> None:
        for object_id in self.object_ids:
            self.document.get_object(object_id).group_id = self.group_id
        self.document.touch()

    def undo(self) -> None:
        for object_id, group_id in self.before.items():
            self.document.get_object(object_id).group_id = group_id
        self.document.touch()


class UngroupObjectsCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        object_ids: Iterable[str],
        description: str = "Ungroup objects",
    ) -> None:
        self.document = document
        selected = [document.get_object(object_id) for object_id in dict.fromkeys(object_ids)]
        group_ids = {item.group_id for item in selected if item.group_id is not None}
        self.before = {
            item.id: item.group_id
            for item in document.objects
            if item.group_id in group_ids
        }
        if not self.before:
            raise ValueError("The selection does not contain a group")
        self.description = description

    def redo(self) -> None:
        for object_id in self.before:
            self.document.get_object(object_id).group_id = None
        self.document.touch()

    def undo(self) -> None:
        for object_id, group_id in self.before.items():
            self.document.get_object(object_id).group_id = group_id
        self.document.touch()


class ReorderLayersCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        ordered_ids: Iterable[str],
        description: str = "Reorder layers",
    ) -> None:
        self.document = document
        self.before = [layer.id for layer in document.layers]
        self.after = list(ordered_ids)
        if len(self.after) != len(self.before) or set(self.after) != set(self.before):
            raise ValueError("Layer order must contain every project layer exactly once")
        self.before_priority = {layer.id: layer.priority for layer in document.layers}
        self.after_priority = {layer_id: index for index, layer_id in enumerate(self.after)}
        self.description = description

    def _apply(self, order: list[str], priorities: dict[str, int]) -> None:
        by_id = {layer.id: layer for layer in self.document.layers}
        if set(by_id) != set(order):
            raise ValueError("Project layers changed while applying a layer-order command")
        self.document.layers[:] = [by_id[layer_id] for layer_id in order]
        for layer in self.document.layers:
            layer.priority = priorities[layer.id]
        self.document.touch()

    def redo(self) -> None:
        self._apply(self.after, self.after_priority)

    def undo(self) -> None:
        self._apply(self.before, self.before_priority)


class ReorderObjectsCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        ordered_ids: Iterable[str],
        description: str = "Reorder objects",
    ) -> None:
        self.document = document
        before = [item.id for item in document.objects]
        after = list(ordered_ids)
        if len(after) != len(before) or set(after) != set(before):
            raise ValueError("Object order must contain every project object exactly once")
        self.before = before
        self.after = after
        self.description = description

    def _apply(self, order: list[str]) -> None:
        by_id = {item.id: item for item in self.document.objects}
        if set(by_id) != set(order):
            raise ValueError("Project objects changed while applying an order command")
        self.document.objects[:] = [by_id[object_id] for object_id in order]
        self.document.touch()

    def redo(self) -> None:
        self._apply(self.after)

    def undo(self) -> None:
        self._apply(self.before)


class AssignLayerCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        object_ids: Iterable[str],
        layer_id: str,
        description: str = "Assign layer",
    ) -> None:
        self.document = document
        self.object_ids = list(dict.fromkeys(object_ids))
        self.layer_id = layer_id
        self.before = {
            object_id: document.get_object(object_id).layer_id for object_id in self.object_ids
        }
        self.description = description

    def redo(self) -> None:
        self.document.assign_layer(self.object_ids, self.layer_id)

    def undo(self) -> None:
        changed = False
        for object_id, layer_id in self.before.items():
            item = self.document.get_object(object_id)
            if item.layer_id != layer_id:
                item.layer_id = layer_id
                changed = True
        if changed:
            self.document.touch()


class DuplicateObjectsCommand(Command):
    def __init__(
        self,
        document: ProjectDocument,
        object_ids: Iterable[str],
        offset_mm: tuple[float, float] = (5.0, -5.0),
        description: str = "Duplicate objects",
    ) -> None:
        self.document = document
        self.object_ids = list(dict.fromkeys(object_ids))
        self.offset_mm = offset_mm
        self.description = description
        self.duplicates: list[SceneObject] = []

    def redo(self) -> None:
        if not self.duplicates:
            self.duplicates = self.document.duplicate_objects(self.object_ids, self.offset_mm)
        else:
            for item in self.duplicates:
                self.document.add_object(item)

    def undo(self) -> None:
        for item in reversed(self.duplicates):
            self.document.remove_object(item.id)
