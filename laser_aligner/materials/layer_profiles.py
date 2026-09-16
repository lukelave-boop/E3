"""Reusable layer sets, separate from project files and material recipes."""

from __future__ import annotations

import copy
from pathlib import Path

from ..project import FunctionalCommand, OperationLayer, ProjectDocument
from ..storage import atomic_write_json, default_user_data_dir, strict_json_loads


def validated_layers(raw: object) -> list[OperationLayer]:
    if not isinstance(raw, list) or not 1 <= len(raw) <= 256:
        raise ValueError("A layer profile must contain 1–256 layers.")
    layers = [OperationLayer.from_dict(item) for item in raw]
    if len({layer.id for layer in layers}) != len(layers):
        raise ValueError("Layer profile IDs must be unique.")
    return layers


class LayerProfileStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or default_user_data_dir() / "layer-profiles.json"

    def read(self) -> dict:
        if not self.path.exists():
            return {}
        raw = strict_json_loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict) or raw.get("schema_version") != 1:
            raise ValueError("Unsupported layer profile library.")
        profiles = raw.get("profiles")
        if not isinstance(profiles, dict):
            raise ValueError("Invalid layer profile library.")
        for name, profile in profiles.items():
            if not isinstance(name, str) or not name.strip() or len(name) > 80:
                raise ValueError("Invalid layer profile name.")
            if not isinstance(profile, dict):
                raise ValueError("Invalid layer profile.")
            scope = profile.get("scope")
            if not isinstance(scope, list) or len(scope) != 2 or not all(
                isinstance(value, str) and value for value in scope
            ):
                raise ValueError("Invalid layer profile machine/tool scope.")
            validated_layers(profile.get("layers"))
        return profiles

    def save(
        self, name: str, scope: tuple[str, str], layers: list[OperationLayer],
        *, overwrite: bool = False,
    ) -> None:
        name = name.strip()
        if not name or len(name) > 80:
            raise ValueError("Use a profile name of 1–80 characters.")
        if not all(isinstance(value, str) and value for value in scope):
            raise ValueError("A running machine and tool profile are required.")
        profiles = self.read()
        if overwrite:
            if name not in profiles:
                raise ValueError("That profile no longer exists. Use Save As to create it.")
            if profiles[name]["scope"] != list(scope):
                raise ValueError("This profile belongs to a different machine or tool head.")
        elif name in profiles:
            raise ValueError("That profile name already exists. Use a new name.")
        payload = [layer.to_dict() for layer in layers]
        validated_layers(payload)
        profiles[name] = {"scope": list(scope), "layers": payload}
        atomic_write_json(self.path, {"schema_version": 1, "profiles": profiles})

    def delete(self, name: str) -> None:
        profiles = self.read()
        del profiles[name]
        atomic_write_json(self.path, {"schema_version": 1, "profiles": profiles})


def load_profile_command(
    document: ProjectDocument, profile: dict, scope: tuple[str, str],
) -> FunctionalCommand:
    if profile.get("scope") != list(scope):
        raise ValueError("This profile belongs to a different machine or tool head.")
    layers = validated_layers(profile.get("layers"))
    by_id = {layer.id: layer for layer in layers}
    mapping = {}
    used = {item.layer_id for item in document.objects}
    for current in document.layers:
        if current.id not in used:
            continue
        candidates = [layer for layer in layers if layer.color == current.color]
        if current.id in by_id:
            mapping[current.id] = current.id
        elif len(candidates) == 1:
            mapping[current.id] = candidates[0].id
        else:
            raise ValueError(
                f"Profile has no unique match for used layer '{current.name}'. "
                "Assign its objects to a matching layer before loading this profile."
            )
    if len(set(mapping.values())) != len(mapping):
        raise ValueError("Profile would combine used layers. Reassign their objects first.")
    before = copy.deepcopy(document.layers)
    assignments = {item.id: item.layer_id for item in document.objects}

    def redo() -> None:
        document.layers = copy.deepcopy(layers)
        for item in document.objects:
            item.layer_id = mapping[assignments[item.id]]
        document.touch()

    def undo() -> None:
        document.layers = copy.deepcopy(before)
        for item in document.objects:
            item.layer_id = assignments[item.id]
        document.touch()

    return FunctionalCommand("Load layer profile", redo, undo)
