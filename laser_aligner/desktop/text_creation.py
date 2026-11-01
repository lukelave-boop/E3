"""Stage text geometry and operation layers as one undoable desktop edit."""

from __future__ import annotations

import uuid

from ..project import (
    DEFAULT_LAYER_COLORS,
    AddLayerCommand,
    AddObjectsCommand,
    Command,
    LayerMode,
    OperationLayer,
    ProjectDocument,
)
from .text_geometry import TextVectorOptions, create_vector_text_object


class CreateTextCommand(Command):
    description = "Add vector text"

    def __init__(
        self,
        document: ProjectDocument,
        active_layer_id: str,
        options: TextVectorOptions,
        *,
        center: tuple[float, float],
    ) -> None:
        active = document.get_layer(active_layer_id)
        # Resolve all glyphs before creating any document or history state.
        prototype = create_vector_text_object(active.id, options, center=center)
        combined = options.mode == "outline_filled"
        modes = (
            (LayerMode.RASTER, LayerMode.LINE) if combined else
            (LayerMode.RASTER,) if options.mode == "filled" else (LayerMode.LINE,)
        )
        self.layers: list[OperationLayer] = []
        self.items = []
        self._commands: list[Command] = []
        priority = max((layer.priority for layer in document.layers), default=-1) + 1
        group_id = f"group_{uuid.uuid4().hex}" if combined else None
        for mode in modes:
            if not combined and active.mode is mode:
                layer = active
            else:
                index = len(document.layers) + len(self.layers)
                layer = OperationLayer(
                    name=f"{prototype.name} {'fill' if mode is LayerMode.RASTER else 'line'}",
                    color=DEFAULT_LAYER_COLORS[index % len(DEFAULT_LAYER_COLORS)],
                    mode=mode,
                    power_percent=0.0,
                    output_enabled=False,
                    priority=priority + len(self.layers),
                )
                self.layers.append(layer)
                self._commands.append(AddLayerCommand(document, layer, index=index))
            item = prototype.duplicate(offset_mm=(0.0, 0.0), group_id=group_id)
            item.layer_id = layer.id
            item.name = prototype.name
            if combined:
                item.name = f"{prototype.name} {'fill' if mode is LayerMode.RASTER else 'outline'}"
            self.items.append(item)
        self._commands.append(AddObjectsCommand(document, self.items))

    def redo(self) -> None:
        completed = []
        try:
            for command in self._commands:
                command.redo()
                completed.append(command)
        except Exception:
            for command in reversed(completed):
                command.undo()
            raise

    def undo(self) -> None:
        for command in reversed(self._commands):
            command.undo()
