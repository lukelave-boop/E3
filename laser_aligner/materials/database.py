from __future__ import annotations

import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

from ..project import LayerMode, OperationLayer


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
    notes: str = ""
    id: int | None = None

    def __post_init__(self) -> None:
        self.material = str(self.material or "Unspecified")[:120]
        self.name = str(self.name or "Preset")[:120]
        if self.thickness_mm is not None:
            self.thickness_mm = float(self.thickness_mm)
            if self.thickness_mm < 0:
                raise ValueError("Material thickness cannot be negative")
        self.mode = self.mode if isinstance(self.mode, LayerMode) else LayerMode(str(self.mode))
        self.speed_mm_min = float(self.speed_mm_min)
        self.power_percent = float(self.power_percent)
        self.passes = int(self.passes)
        self.line_interval_mm = float(self.line_interval_mm)
        self.notes = str(self.notes)[:2000]
        if self.speed_mm_min <= 0:
            raise ValueError("Preset speed must be positive")
        if not 0 <= self.power_percent <= 100:
            raise ValueError("Preset power must be between 0 and 100")
        if self.passes < 1:
            raise ValueError("Preset passes must be at least one")
        if self.line_interval_mm <= 0:
            raise ValueError("Preset line interval must be positive")
        if self.id is not None:
            self.id = int(self.id)

    def apply_to_layer(self, layer: OperationLayer) -> OperationLayer:
        payload = layer.to_dict()
        payload.update(
            {
                "mode": self.mode.value,
                "speed_mm_min": self.speed_mm_min,
                "power_percent": self.power_percent,
                "passes": self.passes,
                "line_interval_mm": self.line_interval_mm,
            }
        )
        return OperationLayer.from_dict(payload)


class MaterialDatabase:
    def __init__(self, path: str | Path | None = None) -> None:
        if path is None:
            path = (
                Path.home()
                / ".local"
                / "share"
                / "e3-positioning-system"
                / "materials.sqlite"
            )
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
                        power_percent, passes, line_interval_mm, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        line_interval_mm = ?, notes = ?
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
