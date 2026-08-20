"""Qt-neutral importer discovery and pre-parse manifest contracts.

This module establishes the shared contract used before a foreign file is
committed into an E3 project. It deliberately does not parse or execute source
content. Existing importers remain responsible for strict format parsing until
their dedicated scan stages are migrated onto this registry.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .gcode_import import (
    GCODE_FILE_DIALOG_FILTER,
    MAX_GCODE_FILE_BYTES,
    SUPPORTED_GCODE_SUFFIXES,
)
from .lightburn import (
    LIGHTBURN_FILE_DIALOG_FILTER,
    MAX_LIGHTBURN_FILE_BYTES,
    SUPPORTED_LIGHTBURN_SUFFIXES,
)

_IMPORTER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
_SOURCE_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class ImportCapability(str, Enum):
    """Source features an importer can preserve or deliberately reconstruct."""

    VECTOR_GEOMETRY = "vector_geometry"
    SOURCE_LAYERS = "source_layers"
    OPERATION_SETTINGS = "operation_settings"
    GROUPING = "grouping"
    ARC_GEOMETRY = "arc_geometry"


def _importer_id(value: str) -> str:
    identifier = str(value).strip().casefold()
    if not _IMPORTER_ID_RE.fullmatch(identifier):
        raise ValueError(
            "importer_id must use lowercase letters, digits, underscores, or hyphens"
        )
    return identifier


def _suffix(value: str) -> str:
    suffix = str(value).strip().casefold()
    if not suffix:
        raise ValueError("import suffix must not be empty")
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    if suffix == "." or "/" in suffix or "\\" in suffix:
        raise ValueError(f"Invalid import suffix {value!r}")
    return suffix


def _nonnegative_int(value: int, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")
    return value


def _optional_nonnegative_float(value: float | None, label: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise ValueError(f"{label} must be a finite non-negative number")
    return number


def _capabilities(
    values: Iterable[ImportCapability | str],
) -> frozenset[ImportCapability]:
    return frozenset(ImportCapability(value) for value in values)


def _text_tuple(values: Iterable[str], label: str) -> tuple[str, ...]:
    output: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise ValueError(f"{label} entries must be strings")
        output.append(value)
    return tuple(output)


def _source_sha256(value: str) -> str:
    digest = str(value).strip().casefold()
    if digest and not _SOURCE_SHA256_RE.fullmatch(digest):
        raise ValueError(
            "source_sha256 must be an empty string or 64 hexadecimal characters"
        )
    return digest


@dataclass(slots=True, frozen=True)
class ImporterSpec:
    """Static discovery metadata for one foreign-file importer."""

    importer_id: str
    display_name: str
    suffixes: tuple[str, ...]
    capabilities: frozenset[ImportCapability]
    max_file_bytes: int
    file_dialog_filter: str

    def __post_init__(self) -> None:
        identifier = _importer_id(self.importer_id)
        display_name = str(self.display_name).strip()
        if not display_name:
            raise ValueError("display_name must not be empty")
        suffixes = tuple(dict.fromkeys(_suffix(value) for value in self.suffixes))
        if not suffixes:
            raise ValueError("ImporterSpec requires at least one suffix")
        max_file_bytes = _nonnegative_int(self.max_file_bytes, "max_file_bytes")
        if max_file_bytes < 1:
            raise ValueError("max_file_bytes must be positive")
        file_dialog_filter = str(self.file_dialog_filter).strip()
        if not file_dialog_filter:
            raise ValueError("file_dialog_filter must not be empty")

        object.__setattr__(self, "importer_id", identifier)
        object.__setattr__(self, "display_name", display_name)
        object.__setattr__(self, "suffixes", suffixes)
        object.__setattr__(
            self,
            "capabilities",
            _capabilities(self.capabilities),
        )
        object.__setattr__(self, "max_file_bytes", max_file_bytes)
        object.__setattr__(self, "file_dialog_filter", file_dialog_filter)

    def accepts_suffix(self, value: str) -> bool:
        return _suffix(value) in self.suffixes


@dataclass(slots=True, frozen=True)
class ImportLayerManifest:
    """One layer/operation-like unit discovered during an import scan."""

    source_key: str
    name: str
    mode_hint: str = ""
    object_count: int | None = None

    def __post_init__(self) -> None:
        source_key = str(self.source_key).strip()
        name = str(self.name).strip()
        if not source_key:
            raise ValueError("source_key must not be empty")
        if not name:
            raise ValueError("layer manifest name must not be empty")
        object_count = self.object_count
        if object_count is not None:
            object_count = _nonnegative_int(object_count, "object_count")

        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "mode_hint", str(self.mode_hint).strip())
        object.__setattr__(self, "object_count", object_count)


@dataclass(slots=True, frozen=True)
class ImportScanManifest:
    """Bounded facts discovered before a foreign file is parsed into the project."""

    importer_id: str
    source_name: str
    source_suffix: str
    source_size_bytes: int
    capabilities: frozenset[ImportCapability] = frozenset()
    format_version: str = ""
    natural_width_mm: float | None = None
    natural_height_mm: float | None = None
    layers: tuple[ImportLayerManifest, ...] = ()
    source_facts: tuple[str, ...] = ()
    coordinate_facts: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    approximations: tuple[str, ...] = ()
    unsupported_features: tuple[str, ...] = ()
    errors: tuple[str, ...] = ()
    source_sha256: str = ""

    def __post_init__(self) -> None:
        source_name = str(self.source_name).strip()
        if not source_name:
            raise ValueError("source_name must not be empty")

        width = _optional_nonnegative_float(
            self.natural_width_mm,
            "natural_width_mm",
        )
        height = _optional_nonnegative_float(
            self.natural_height_mm,
            "natural_height_mm",
        )
        if (width is None) != (height is None):
            raise ValueError(
                "natural_width_mm and natural_height_mm must be supplied together"
            )

        layers = tuple(self.layers)
        if not all(isinstance(layer, ImportLayerManifest) for layer in layers):
            raise ValueError("layers must contain ImportLayerManifest values")

        object.__setattr__(self, "importer_id", _importer_id(self.importer_id))
        object.__setattr__(self, "source_name", source_name)
        object.__setattr__(self, "source_suffix", _suffix(self.source_suffix))
        object.__setattr__(
            self,
            "source_size_bytes",
            _nonnegative_int(self.source_size_bytes, "source_size_bytes"),
        )
        object.__setattr__(
            self,
            "capabilities",
            _capabilities(self.capabilities),
        )
        object.__setattr__(self, "format_version", str(self.format_version).strip())
        object.__setattr__(self, "natural_width_mm", width)
        object.__setattr__(self, "natural_height_mm", height)
        object.__setattr__(self, "layers", layers)
        object.__setattr__(self, "source_sha256", _source_sha256(self.source_sha256))
        for field_name in (
            "source_facts",
            "coordinate_facts",
            "warnings",
            "approximations",
            "unsupported_features",
            "errors",
        ):
            object.__setattr__(
                self,
                field_name,
                _text_tuple(getattr(self, field_name), field_name),
            )

    @property
    def natural_size_mm(self) -> tuple[float, float] | None:
        if self.natural_width_mm is None or self.natural_height_mm is None:
            return None
        return self.natural_width_mm, self.natural_height_mm

    @property
    def ready_for_parse(self) -> bool:
        """Return whether strict parsing may proceed without known blockers."""

        return not self.errors and not self.unsupported_features


class ImporterRegistry:
    """Immutable lookup table for the importers available in this E3 build."""

    def __init__(self, specs: Iterable[ImporterSpec] = ()) -> None:
        ordered = tuple(specs)
        by_id: dict[str, ImporterSpec] = {}
        by_suffix: dict[str, ImporterSpec] = {}
        for spec in ordered:
            if not isinstance(spec, ImporterSpec):
                raise TypeError("ImporterRegistry entries must be ImporterSpec values")
            if spec.importer_id in by_id:
                raise ValueError(f"Duplicate importer_id {spec.importer_id!r}")
            by_id[spec.importer_id] = spec
            for suffix in spec.suffixes:
                owner = by_suffix.get(suffix)
                if owner is not None:
                    raise ValueError(
                        f"Import suffix {suffix!r} is claimed by both "
                        f"{owner.importer_id!r} and {spec.importer_id!r}"
                    )
                by_suffix[suffix] = spec

        self._specs = ordered
        self._by_id = by_id
        self._by_suffix = by_suffix
        self._suffixes_longest_first = tuple(
            sorted(by_suffix, key=len, reverse=True)
        )

    @property
    def specs(self) -> tuple[ImporterSpec, ...]:
        return self._specs

    def get(self, importer_id: str) -> ImporterSpec | None:
        return self._by_id.get(_importer_id(importer_id))

    def for_suffix(self, suffix: str) -> ImporterSpec | None:
        return self._by_suffix.get(_suffix(suffix))

    def for_path(self, path: str | Path) -> ImporterSpec | None:
        name = str(path).casefold()
        for suffix in self._suffixes_longest_first:
            if name.endswith(suffix):
                return self._by_suffix[suffix]
        return None


GCODE_IMPORTER_SPEC = ImporterSpec(
    importer_id="gcode",
    display_name="Foreign G-code",
    suffixes=tuple(sorted(SUPPORTED_GCODE_SUFFIXES)),
    capabilities=frozenset(
        {
            ImportCapability.VECTOR_GEOMETRY,
            ImportCapability.OPERATION_SETTINGS,
            ImportCapability.ARC_GEOMETRY,
        }
    ),
    max_file_bytes=MAX_GCODE_FILE_BYTES,
    file_dialog_filter=GCODE_FILE_DIALOG_FILTER,
)

LIGHTBURN_IMPORTER_SPEC = ImporterSpec(
    importer_id="lightburn",
    display_name="LightBurn Project",
    suffixes=tuple(sorted(SUPPORTED_LIGHTBURN_SUFFIXES)),
    capabilities=frozenset(
        {
            ImportCapability.VECTOR_GEOMETRY,
            ImportCapability.SOURCE_LAYERS,
            ImportCapability.OPERATION_SETTINGS,
            ImportCapability.GROUPING,
        }
    ),
    max_file_bytes=MAX_LIGHTBURN_FILE_BYTES,
    file_dialog_filter=LIGHTBURN_FILE_DIALOG_FILTER,
)

DEFAULT_IMPORTER_REGISTRY = ImporterRegistry(
    (
        LIGHTBURN_IMPORTER_SPEC,
        GCODE_IMPORTER_SPEC,
    )
)
