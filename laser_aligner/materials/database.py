from __future__ import annotations

import logging
import math
import os
import re
import sqlite3
import tempfile
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from ..project import LayerMode, OperationLayer
from ..project.model import DEFAULT_OPERATION_PROFILES
from ..storage import (
    _publish_temp_if_absent,
    default_user_data_dir,
    legacy_user_data_dir,
)

logger = logging.getLogger(__name__)

MATERIAL_DATABASE_SCHEMA_VERSION = 2
_PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,79}$")
_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")


class MaterialCompatibility(str, Enum):
    """How a recipe scope relates to the running machine and tool head."""

    EXACT_MACHINE_TOOL = "exact_machine_tool"
    TOOL_ONLY = "tool_only"
    UNIVERSAL = "universal"
    INCOMPATIBLE = "incompatible"

    @property
    def can_apply(self) -> bool:
        return self is not MaterialCompatibility.INCOMPATIBLE

    @property
    def label(self) -> str:
        return {
            MaterialCompatibility.EXACT_MACHINE_TOOL: "Exact machine + tool",
            MaterialCompatibility.TOOL_ONLY: "Tool match",
            MaterialCompatibility.UNIVERSAL: "Universal",
            MaterialCompatibility.INCOMPATIBLE: "Incompatible",
        }[self]


def _finite_number(value: object, name: str) -> float:
    if type(value) not in {int, float}:
        raise ValueError(f"{name} must be a finite number")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


def _string(value: object, name: str) -> str:
    if type(value) is not str:
        raise ValueError(f"{name} must be a string")
    return value


def _boolean(value: object, name: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{name} must be a boolean")
    return value


def _optional_profile_id(value: object, name: str) -> str | None:
    if value is None:
        return None
    text = _string(value, name)
    if _PROFILE_ID_RE.fullmatch(text) is None:
        raise ValueError(
            f"{name} must be a stable canonical profile ID using 1-80 lowercase "
            "letters, digits, dots, underscores, or hyphens"
        )
    return text


def _optional_color(value: object, name: str) -> str | None:
    if value is None:
        return None
    color = _string(value, name).upper()
    if _COLOR_RE.fullmatch(color) is None:
        raise ValueError(f"{name} must be a #RRGGBB color")
    return color


def _stored_boolean(value: object, name: str) -> bool:
    if type(value) is not int or value not in {0, 1}:
        raise ValueError(f"{name} must be stored as 0 or 1")
    return bool(value)


def _migrate_database(source: Path, destination: Path) -> bool:
    """Copy one complete SQLite snapshot without mutating the legacy database."""

    temporary: Path | None = None
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        temporary.unlink(missing_ok=True)
        with closing(
            sqlite3.connect(f"{source.as_uri()}?mode=ro", uri=True)
        ) as legacy:
            with closing(sqlite3.connect(temporary)) as migrated:
                legacy.backup(migrated)
                migrated.commit()
        with temporary.open("r+b") as handle:
            os.fsync(handle.fileno())
        # A native-path database created during migration wins. Never replace
        # operator data with the legacy snapshot.
        if not _publish_temp_if_absent(temporary, destination):
            return True
    except (OSError, sqlite3.Error) as exc:
        logger.warning("Could not migrate legacy material presets: %s", exc)
        return False
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return True


def _default_database_path() -> Path:
    preferred = (default_user_data_dir() / "materials.sqlite").expanduser().resolve()
    legacy = (legacy_user_data_dir() / "materials.sqlite").expanduser().resolve()
    if preferred == legacy or preferred.exists() or not legacy.is_file():
        return preferred
    return preferred if _migrate_database(legacy, preferred) else legacy


@dataclass(slots=True)
class MaterialPreset:
    material: str
    name: str
    thickness_mm: float | None = None
    mode: LayerMode = LayerMode.LINE
    speed_mm_min: float = 2000.0
    power_percent: float = 10.0
    passes: int = 1
    line_interval_mm: float = 0.10
    vector_power_correction: float = 0.0
    raster_power_correction: float = 0.0
    notes: str = ""
    id: int | None = None
    scan_angle_deg: float = 0.0
    overscan_percent: float = 2.5
    air_assist: bool = False
    recommended_color: str | None = None
    machine_profile_id: str | None = None
    tool_head_profile_id: str | None = None
    builtin_key: str | None = None

    def __post_init__(self) -> None:
        self.material = (_string(self.material, "Preset material") or "Unspecified")[:120]
        self.name = (_string(self.name, "Preset name") or "Preset")[:120]
        if self.thickness_mm is not None:
            self.thickness_mm = _finite_number(
                self.thickness_mm,
                "Material thickness",
            )
            if self.thickness_mm < 0:
                raise ValueError("Material thickness cannot be negative")
        if not isinstance(self.mode, LayerMode):
            self.mode = LayerMode(_string(self.mode, "Preset mode"))
        self.speed_mm_min = _finite_number(self.speed_mm_min, "Preset speed")
        self.power_percent = _finite_number(self.power_percent, "Preset power")
        if type(self.passes) is not int:
            raise ValueError("Preset passes must be an integer")
        self.line_interval_mm = _finite_number(
            self.line_interval_mm,
            "Preset line interval",
        )
        self.scan_angle_deg = _finite_number(
            self.scan_angle_deg,
            "Preset scan angle",
        )
        self.scan_angle_deg = (self.scan_angle_deg + 180.0) % 360.0 - 180.0
        self.overscan_percent = _finite_number(
            self.overscan_percent,
            "Preset overscan",
        )
        self.vector_power_correction = _finite_number(
            self.vector_power_correction,
            "Preset vector power correction",
        )
        self.raster_power_correction = _finite_number(
            self.raster_power_correction,
            "Preset raster power correction",
        )
        self.air_assist = _boolean(self.air_assist, "Preset air assist")
        self.recommended_color = _optional_color(
            self.recommended_color,
            "Preset recommended color",
        )
        self.machine_profile_id = _optional_profile_id(
            self.machine_profile_id,
            "Preset machine profile ID",
        )
        self.tool_head_profile_id = _optional_profile_id(
            self.tool_head_profile_id,
            "Preset tool-head profile ID",
        )
        if self.machine_profile_id is not None and self.tool_head_profile_id is None:
            raise ValueError(
                "A machine-scoped preset must also specify a tool-head profile ID"
            )
        self.notes = _string(self.notes, "Preset notes")[:2000]
        if self.speed_mm_min <= 0:
            raise ValueError("Preset speed must be positive")
        if not 0 <= self.power_percent <= 100:
            raise ValueError("Preset power must be between 0 and 100")
        if self.passes < 1:
            raise ValueError("Preset passes must be at least one")
        if self.line_interval_mm <= 0:
            raise ValueError("Preset line interval must be positive")
        if not 0 <= self.overscan_percent <= 100:
            raise ValueError("Preset overscan must be between 0 and 100")
        if not -100 <= self.vector_power_correction <= 100:
            raise ValueError("Preset vector power correction must be between -100 and 100")
        if not -100 <= self.raster_power_correction <= 100:
            raise ValueError("Preset raster power correction must be between -100 and 100")
        if self.id is not None:
            if type(self.id) is not int:
                raise ValueError("Preset ID must be an integer")
        self.builtin_key = _optional_profile_id(
            self.builtin_key,
            "Preset built-in key",
        )

    def compatibility(
        self,
        machine_profile_id: str | None,
        tool_head_profile_id: str | None,
    ) -> MaterialCompatibility:
        machine_profile_id = _optional_profile_id(
            machine_profile_id,
            "Running machine profile ID",
        )
        tool_head_profile_id = _optional_profile_id(
            tool_head_profile_id,
            "Running tool-head profile ID",
        )
        if self.machine_profile_id is None and self.tool_head_profile_id is None:
            return MaterialCompatibility.UNIVERSAL
        if self.machine_profile_id is None:
            if self.tool_head_profile_id == tool_head_profile_id:
                return MaterialCompatibility.TOOL_ONLY
            return MaterialCompatibility.INCOMPATIBLE
        if (
            self.machine_profile_id == machine_profile_id
            and self.tool_head_profile_id == tool_head_profile_id
        ):
            return MaterialCompatibility.EXACT_MACHINE_TOOL
        return MaterialCompatibility.INCOMPATIBLE

    @property
    def scope_label(self) -> str:
        if self.machine_profile_id is not None:
            return f"{self.machine_profile_id} / {self.tool_head_profile_id}"
        if self.tool_head_profile_id is not None:
            return f"Tool: {self.tool_head_profile_id}"
        return "Universal"

    def apply_to_layer(
        self,
        layer: OperationLayer,
        *,
        machine_profile_id: str | None = None,
        tool_head_profile_id: str | None = None,
    ) -> OperationLayer:
        compatibility = self.compatibility(
            machine_profile_id,
            tool_head_profile_id,
        )
        if not compatibility.can_apply:
            raise ValueError(
                "Material preset is incompatible with the running machine and tool head"
            )
        payload = layer.to_dict()
        payload.update(
            {
                "mode": self.mode.value,
                "speed_mm_min": self.speed_mm_min,
                "power_percent": self.power_percent,
                "passes": self.passes,
                "line_interval_mm": self.line_interval_mm,
                "scan_angle_deg": self.scan_angle_deg,
                "overscan_percent": self.overscan_percent,
                "vector_power_correction": self.vector_power_correction,
                "raster_power_correction": self.raster_power_correction,
                "air_assist": self.air_assist,
            }
        )
        if self.recommended_color is not None:
            payload["color"] = self.recommended_color
        return OperationLayer.from_dict(payload)


def builtin_material_presets() -> tuple[MaterialPreset, ...]:
    """Return fresh recipes derived from the curated default layer source."""

    presets: list[MaterialPreset] = []
    for profile in DEFAULT_OPERATION_PROFILES:
        layer = profile["layer"]
        presets.append(
            MaterialPreset(
                builtin_key=profile["builtin_key"],
                material=profile["material"],
                name="Cut" if layer["mode"] is LayerMode.LINE else "Raster",
                thickness_mm=profile["thickness_mm"],
                mode=layer["mode"],
                speed_mm_min=layer["speed_mm_min"],
                power_percent=layer["power_percent"],
                passes=layer["passes"],
                line_interval_mm=layer["line_interval_mm"],
                scan_angle_deg=layer["scan_angle_deg"],
                overscan_percent=layer["overscan_percent"],
                vector_power_correction=layer["vector_power_correction"],
                raster_power_correction=layer["raster_power_correction"],
                air_assist=layer["air_assist"],
                recommended_color=layer["color"],
                machine_profile_id=profile["machine_profile_id"],
                tool_head_profile_id=profile["tool_head_profile_id"],
                notes=(
                    "Unverified operator-supplied E3 10 W starting value; "
                    "no air assist."
                ),
            )
        )
    return tuple(presets)


class MaterialDatabase:
    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = _default_database_path()
        self.path = Path(path).expanduser().resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > MATERIAL_DATABASE_SCHEMA_VERSION:
                raise ValueError(
                    "Material database schema is newer than this E3 version: "
                    f"{version} > {MATERIAL_DATABASE_SCHEMA_VERSION}"
                )
            connection.execute("BEGIN IMMEDIATE")
            table_exists = connection.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'material_presets'
                """
            ).fetchone()
            if table_exists is None:
                self._create_table(connection)
            elif version < MATERIAL_DATABASE_SCHEMA_VERSION:
                self._migrate_to_v2(connection)
            else:
                self._validate_v2_schema(connection)
            self._create_seed_history(connection)
            self._create_indexes(connection)
            connection.execute(
                f"PRAGMA user_version = {MATERIAL_DATABASE_SCHEMA_VERSION}"
            )

    @staticmethod
    def _create_table(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE material_presets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                builtin_key TEXT,
                material TEXT NOT NULL,
                name TEXT NOT NULL,
                thickness_mm REAL,
                mode TEXT NOT NULL,
                speed_mm_min REAL NOT NULL,
                power_percent REAL NOT NULL,
                passes INTEGER NOT NULL,
                line_interval_mm REAL NOT NULL,
                scan_angle_deg REAL NOT NULL DEFAULT 0,
                overscan_percent REAL NOT NULL DEFAULT 2.5,
                vector_power_correction REAL NOT NULL DEFAULT 0,
                raster_power_correction REAL NOT NULL DEFAULT 0,
                air_assist INTEGER NOT NULL DEFAULT 0,
                recommended_color TEXT,
                machine_profile_id TEXT,
                tool_head_profile_id TEXT,
                notes TEXT NOT NULL DEFAULT '',
                CHECK (air_assist IN (0, 1)),
                CHECK (
                    machine_profile_id IS NULL
                    OR tool_head_profile_id IS NOT NULL
                )
            )
            """
        )

    @staticmethod
    def _create_seed_history(connection: sqlite3.Connection) -> None:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS material_preset_seed_history (
                builtin_key TEXT PRIMARY KEY
            )
            """
        )

    @staticmethod
    def _create_indexes(connection: sqlite3.Connection) -> None:
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_material_presets_material "
            "ON material_presets(material, name)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_material_presets_scope "
            "ON material_presets(machine_profile_id, tool_head_profile_id, "
            "material, name, thickness_mm)"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_material_presets_builtin_key "
            "ON material_presets(builtin_key) WHERE builtin_key IS NOT NULL"
        )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_material_presets_scoped_identity "
            "ON material_presets(material, name, thickness_mm, "
            "coalesce(machine_profile_id, ''), "
            "coalesce(tool_head_profile_id, ''))"
        )

    @staticmethod
    def _validate_v2_schema(connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(material_presets)")
        }
        required = {
            "id",
            "builtin_key",
            "material",
            "name",
            "thickness_mm",
            "mode",
            "speed_mm_min",
            "power_percent",
            "passes",
            "line_interval_mm",
            "scan_angle_deg",
            "overscan_percent",
            "vector_power_correction",
            "raster_power_correction",
            "air_assist",
            "recommended_color",
            "machine_profile_id",
            "tool_head_profile_id",
            "notes",
        }
        missing = sorted(required - columns)
        if missing:
            raise ValueError(
                "Material database schema 2 is incomplete; missing columns: "
                + ", ".join(missing)
            )

    @classmethod
    def _migrate_to_v2(cls, connection: sqlite3.Connection) -> None:
        columns = {
            str(row[1])
            for row in connection.execute("PRAGMA table_info(material_presets)")
        }
        mandatory = {
            "id",
            "material",
            "name",
            "thickness_mm",
            "mode",
            "speed_mm_min",
            "power_percent",
            "passes",
            "line_interval_mm",
            "notes",
        }
        known = mandatory | {
            "builtin_key",
            "scan_angle_deg",
            "overscan_percent",
            "vector_power_correction",
            "raster_power_correction",
            "air_assist",
            "recommended_color",
            "machine_profile_id",
            "tool_head_profile_id",
        }
        missing = sorted(mandatory - columns)
        unknown = sorted(columns - known)
        if missing or unknown:
            details: list[str] = []
            if missing:
                details.append("missing " + ", ".join(missing))
            if unknown:
                details.append("unknown " + ", ".join(unknown))
            raise ValueError(
                "Cannot safely migrate material database schema: "
                + "; ".join(details)
            )

        def source(column: str, default: str) -> str:
            return f'"{column}"' if column in columns else default

        connection.execute(
            "ALTER TABLE material_presets RENAME TO material_presets_legacy_v1"
        )
        cls._create_table(connection)
        destination_columns = (
            "id",
            "builtin_key",
            "material",
            "name",
            "thickness_mm",
            "mode",
            "speed_mm_min",
            "power_percent",
            "passes",
            "line_interval_mm",
            "scan_angle_deg",
            "overscan_percent",
            "vector_power_correction",
            "raster_power_correction",
            "air_assist",
            "recommended_color",
            "machine_profile_id",
            "tool_head_profile_id",
            "notes",
        )
        source_expressions = (
            source("id", "NULL"),
            source("builtin_key", "NULL"),
            source("material", "NULL"),
            source("name", "NULL"),
            source("thickness_mm", "NULL"),
            source("mode", "NULL"),
            source("speed_mm_min", "NULL"),
            source("power_percent", "NULL"),
            source("passes", "NULL"),
            source("line_interval_mm", "NULL"),
            source("scan_angle_deg", "0"),
            source("overscan_percent", "2.5"),
            source("vector_power_correction", "0"),
            source("raster_power_correction", "0"),
            source("air_assist", "0"),
            source("recommended_color", "NULL"),
            source("machine_profile_id", "NULL"),
            source("tool_head_profile_id", "NULL"),
            source("notes", "NULL"),
        )
        connection.execute(
            "INSERT INTO material_presets ("
            + ", ".join(destination_columns)
            + ") SELECT "
            + ", ".join(source_expressions)
            + " FROM material_presets_legacy_v1"
        )
        connection.execute("DROP TABLE material_presets_legacy_v1")

    @staticmethod
    def _from_row(row: sqlite3.Row) -> MaterialPreset:
        return MaterialPreset(
            id=row["id"],
            material=row["material"],
            name=row["name"],
            thickness_mm=row["thickness_mm"],
            mode=LayerMode(row["mode"]),
            speed_mm_min=row["speed_mm_min"],
            power_percent=row["power_percent"],
            passes=row["passes"],
            line_interval_mm=row["line_interval_mm"],
            scan_angle_deg=row["scan_angle_deg"],
            overscan_percent=row["overscan_percent"],
            vector_power_correction=row["vector_power_correction"],
            raster_power_correction=row["raster_power_correction"],
            air_assist=_stored_boolean(row["air_assist"], "Preset air assist"),
            recommended_color=row["recommended_color"],
            machine_profile_id=row["machine_profile_id"],
            tool_head_profile_id=row["tool_head_profile_id"],
            notes=row["notes"],
            builtin_key=row["builtin_key"],
        )

    def list(self, search: str = "") -> list[MaterialPreset]:
        query = search.strip().lower()
        sql = "SELECT * FROM material_presets"
        parameters: tuple[str, ...] = ()
        if query:
            sql += " WHERE lower(material) LIKE ? OR lower(name) LIKE ? OR lower(notes) LIKE ?"
            like = f"%{query}%"
            parameters = (like, like, like)
        sql += (
            " ORDER BY material COLLATE NOCASE, thickness_mm, "
            "name COLLATE NOCASE, id"
        )
        with self._connect() as connection:
            return [self._from_row(row) for row in connection.execute(sql, parameters)]

    def list_for_profiles(
        self,
        *,
        machine_profile_id: str | None,
        tool_head_profile_id: str | None,
        search: str = "",
    ) -> list[MaterialPreset]:
        presets = self.list(search)
        rank = {
            MaterialCompatibility.EXACT_MACHINE_TOOL: 0,
            MaterialCompatibility.TOOL_ONLY: 1,
            MaterialCompatibility.UNIVERSAL: 2,
            MaterialCompatibility.INCOMPATIBLE: 3,
        }
        return sorted(
            presets,
            key=lambda preset: (
                rank[preset.compatibility(machine_profile_id, tool_head_profile_id)],
                preset.material.casefold(),
                preset.thickness_mm is not None,
                preset.thickness_mm if preset.thickness_mm is not None else 0.0,
                preset.name.casefold(),
                preset.machine_profile_id or "",
                preset.tool_head_profile_id or "",
                preset.id if preset.id is not None else -1,
            ),
        )

    def get(self, preset_id: int) -> MaterialPreset:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM material_presets WHERE id = ?",
                (int(preset_id),),
            ).fetchone()
        if row is None:
            raise KeyError(f"Unknown material preset: {preset_id}")
        return self._from_row(row)

    def save(self, preset: MaterialPreset) -> MaterialPreset:
        # Re-run dataclass validation so callers cannot persist values that were
        # mutated into an invalid state after construction.
        preset = replace(preset)
        with self._connect() as connection:
            identity_unchanged = False
            if preset.id is not None:
                current = connection.execute(
                    """
                    SELECT material, name, thickness_mm, machine_profile_id,
                           tool_head_profile_id
                    FROM material_presets WHERE id = ?
                    """,
                    (preset.id,),
                ).fetchone()
                if current is None:
                    raise KeyError(f"Unknown material preset: {preset.id}")
                identity_unchanged = (
                    current["material"] == preset.material
                    and current["name"] == preset.name
                    and current["thickness_mm"] == preset.thickness_mm
                    and current["machine_profile_id"] == preset.machine_profile_id
                    and current["tool_head_profile_id"] == preset.tool_head_profile_id
                )
            if not identity_unchanged:
                duplicate = connection.execute(
                    """
                    SELECT id FROM material_presets
                    WHERE material = ? AND name = ?
                      AND thickness_mm IS ?
                      AND machine_profile_id IS ?
                      AND tool_head_profile_id IS ?
                      AND (? IS NULL OR id != ?)
                    LIMIT 1
                    """,
                    (
                        preset.material,
                        preset.name,
                        preset.thickness_mm,
                        preset.machine_profile_id,
                        preset.tool_head_profile_id,
                        preset.id,
                        preset.id,
                    ),
                ).fetchone()
                if duplicate is not None:
                    raise sqlite3.IntegrityError(
                        "A material preset with this material, name, thickness, "
                        "and scope already exists"
                    )
            if preset.id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO material_presets (
                        builtin_key, material, name, thickness_mm, mode,
                        speed_mm_min, power_percent, passes, line_interval_mm,
                        scan_angle_deg, overscan_percent,
                        vector_power_correction, raster_power_correction,
                        air_assist, recommended_color, machine_profile_id,
                        tool_head_profile_id, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        preset.builtin_key,
                        preset.material,
                        preset.name,
                        preset.thickness_mm,
                        preset.mode.value,
                        preset.speed_mm_min,
                        preset.power_percent,
                        preset.passes,
                        preset.line_interval_mm,
                        preset.scan_angle_deg,
                        preset.overscan_percent,
                        preset.vector_power_correction,
                        preset.raster_power_correction,
                        int(preset.air_assist),
                        preset.recommended_color,
                        preset.machine_profile_id,
                        preset.tool_head_profile_id,
                        preset.notes,
                    ),
                )
                preset.id = int(cursor.lastrowid)
            else:
                cursor = connection.execute(
                    """
                    UPDATE material_presets SET
                        material = ?, name = ?, thickness_mm = ?, mode = ?,
                        speed_mm_min = ?, power_percent = ?, passes = ?, line_interval_mm = ?,
                        scan_angle_deg = ?, overscan_percent = ?,
                        vector_power_correction = ?,
                        raster_power_correction = ?, air_assist = ?,
                        recommended_color = ?, machine_profile_id = ?,
                        tool_head_profile_id = ?, notes = ?
                    WHERE id = ?
                    """,
                    (
                        preset.material,
                        preset.name,
                        preset.thickness_mm,
                        preset.mode.value,
                        preset.speed_mm_min,
                        preset.power_percent,
                        preset.passes,
                        preset.line_interval_mm,
                        preset.scan_angle_deg,
                        preset.overscan_percent,
                        preset.vector_power_correction,
                        preset.raster_power_correction,
                        int(preset.air_assist),
                        preset.recommended_color,
                        preset.machine_profile_id,
                        preset.tool_head_profile_id,
                        preset.notes,
                        preset.id,
                    ),
                )
        return self.get(preset.id)

    def delete(self, preset_id: int) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "DELETE FROM material_presets WHERE id = ?",
                (int(preset_id),),
            )
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown material preset: {preset_id}")

    def seed(self, presets: Iterable[MaterialPreset]) -> int:
        created = 0
        for preset in presets:
            preset = replace(preset, id=None)
            with self._connect() as connection:
                if preset.builtin_key is not None:
                    seeded = connection.execute(
                        """
                        SELECT 1 FROM material_preset_seed_history
                        WHERE builtin_key = ?
                        """,
                        (preset.builtin_key,),
                    ).fetchone()
                    if seeded is not None:
                        continue
                    existing = connection.execute(
                        "SELECT 1 FROM material_presets WHERE builtin_key = ?",
                        (preset.builtin_key,),
                    ).fetchone()
                    if existing is None:
                        existing = connection.execute(
                            """
                            SELECT 1 FROM material_presets
                            WHERE material = ? AND name = ?
                              AND thickness_mm IS ?
                              AND machine_profile_id IS ?
                              AND tool_head_profile_id IS ?
                            LIMIT 1
                            """,
                            (
                                preset.material,
                                preset.name,
                                preset.thickness_mm,
                                preset.machine_profile_id,
                                preset.tool_head_profile_id,
                            ),
                        ).fetchone()
                else:
                    existing = connection.execute(
                        """
                        SELECT 1 FROM material_presets
                        WHERE material = ? AND name = ?
                          AND thickness_mm IS ?
                          AND machine_profile_id IS ?
                          AND tool_head_profile_id IS ?
                        LIMIT 1
                        """,
                        (
                            preset.material,
                            preset.name,
                            preset.thickness_mm,
                            preset.machine_profile_id,
                            preset.tool_head_profile_id,
                        ),
                    ).fetchone()
            if existing is not None:
                if preset.builtin_key is not None:
                    with self._connect() as connection:
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO material_preset_seed_history (
                                builtin_key
                            ) VALUES (?)
                            """,
                            (preset.builtin_key,),
                        )
                continue
            self.save(preset)
            if preset.builtin_key is not None:
                with self._connect() as connection:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO material_preset_seed_history (
                            builtin_key
                        ) VALUES (?)
                        """,
                        (preset.builtin_key,),
                    )
            created += 1
        return created
