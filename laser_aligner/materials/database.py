from __future__ import annotations

import logging
import math
import os
import sqlite3
import tempfile
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass, replace
from pathlib import Path

from ..project import LayerMode, OperationLayer
from ..storage import default_user_data_dir, legacy_user_data_dir

logger = logging.getLogger(__name__)


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
        ) as legacy, closing(sqlite3.connect(temporary)) as migrated:
            legacy.backup(migrated)
            migrated.commit()
        with temporary.open("rb") as handle:
            os.fsync(handle.fileno())
        try:
            os.link(temporary, destination)
        except FileExistsError:
            # A native-path database created during migration wins. Never
            # replace operator data with the legacy snapshot.
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
        self.vector_power_correction = _finite_number(
            self.vector_power_correction,
            "Preset vector power correction",
        )
        self.raster_power_correction = _finite_number(
            self.raster_power_correction,
            "Preset raster power correction",
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
        if not -100 <= self.vector_power_correction <= 100:
            raise ValueError("Preset vector power correction must be between -100 and 100")
        if not -100 <= self.raster_power_correction <= 100:
            raise ValueError("Preset raster power correction must be between -100 and 100")
        if self.id is not None:
            if type(self.id) is not int:
                raise ValueError("Preset ID must be an integer")

    def apply_to_layer(self, layer: OperationLayer) -> OperationLayer:
        payload = layer.to_dict()
        payload.update(
            {
                "mode": self.mode.value,
                "speed_mm_min": self.speed_mm_min,
                "power_percent": self.power_percent,
                "passes": self.passes,
                "line_interval_mm": self.line_interval_mm,
                "vector_power_correction": self.vector_power_correction,
                "raster_power_correction": self.raster_power_correction,
            }
        )
        return OperationLayer.from_dict(payload)


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
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS material_presets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    material TEXT NOT NULL,
                    name TEXT NOT NULL,
                    thickness_mm REAL,
                    mode TEXT NOT NULL,
                    speed_mm_min REAL NOT NULL,
                    power_percent REAL NOT NULL,
                    passes INTEGER NOT NULL,
                    line_interval_mm REAL NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    UNIQUE(material, name, thickness_mm)
                )
                """
            )
            columns = {
                row[1]
                for row in connection.execute("PRAGMA table_info(material_presets)")
            }
            for name in ("vector_power_correction", "raster_power_correction"):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE material_presets ADD COLUMN {name} REAL NOT NULL DEFAULT 0"
                    )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_material_presets_material "
                "ON material_presets(material, name)"
            )

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
            vector_power_correction=row["vector_power_correction"],
            raster_power_correction=row["raster_power_correction"],
            notes=row["notes"],
        )

    def list(self, search: str = "") -> list[MaterialPreset]:
        query = search.strip().lower()
        sql = "SELECT * FROM material_presets"
        parameters: tuple[str, ...] = ()
        if query:
            sql += " WHERE lower(material) LIKE ? OR lower(name) LIKE ? OR lower(notes) LIKE ?"
            like = f"%{query}%"
            parameters = (like, like, like)
        sql += " ORDER BY material COLLATE NOCASE, thickness_mm, name COLLATE NOCASE"
        with self._connect() as connection:
            return [self._from_row(row) for row in connection.execute(sql, parameters)]

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
            if preset.id is None:
                cursor = connection.execute(
                    """
                    INSERT INTO material_presets (
                        material, name, thickness_mm, mode, speed_mm_min,
                        power_percent, passes, line_interval_mm,
                        vector_power_correction, raster_power_correction, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        preset.vector_power_correction,
                        preset.raster_power_correction,
                        preset.notes,
                    ),
                )
                preset.id = int(cursor.lastrowid)
            else:
                cursor = connection.execute(
                    """
                    UPDATE material_presets SET
                        material = ?, name = ?, thickness_mm = ?, mode = ?,
                        speed_mm_min = ?, power_percent = ?, passes = ?,
                        line_interval_mm = ?, vector_power_correction = ?,
                        raster_power_correction = ?, notes = ?
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
                        preset.vector_power_correction,
                        preset.raster_power_correction,
                        preset.notes,
                        preset.id,
                    ),
                )
                if cursor.rowcount == 0:
                    raise KeyError(f"Unknown material preset: {preset.id}")
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
            try:
                self.save(preset)
            except sqlite3.IntegrityError:
                continue
            created += 1
        return created
