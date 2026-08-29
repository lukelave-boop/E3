from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..storage import atomic_write_bytes_if_absent, atomic_write_json, read_json

PROFILE_SCHEMA_VERSION = 1

_LEGACY_FILES = (
    "lens_calibration.json",
    "lens_image_index.json",
    "bed_points.json",
    "bed_calibration.json",
    "honeycomb_support.json",
    "bed_reference.png",
    "bed_reference.jpg",
    "base_bed_mapping.json",
    "fine_registration.json",
    "accuracy_validation.json",
    "dense_calibration.json",
    "dense_validation.json",
    "dense_confirmation.json",
)


@dataclass(frozen=True, slots=True)
class CalibrationProfileSignature:
    width: int
    height: int
    autofocus: bool
    focus_absolute: int

    def __post_init__(self) -> None:
        if type(self.width) is not int or self.width <= 0:
            raise ValueError("Calibration profile width must be a positive integer")
        if type(self.height) is not int or self.height <= 0:
            raise ValueError("Calibration profile height must be a positive integer")
        if type(self.autofocus) is not bool:
            raise ValueError("Calibration profile autofocus must be a boolean")
        if type(self.focus_absolute) is not int or self.focus_absolute < 0:
            raise ValueError("Calibration profile focus must be a nonnegative integer")

    @property
    def key(self) -> str:
        focus = "autofocus" if self.autofocus else f"manual-focus-{self.focus_absolute:03d}"
        return f"{self.width}x{self.height}-{focus}"

    @property
    def label(self) -> str:
        focus = "autofocus" if self.autofocus else f"manual focus {self.focus_absolute}"
        return f"{self.width} x {self.height}, {focus}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_SCHEMA_VERSION,
            "key": self.key,
            **asdict(self),
        }


def signature_from_camera_settings(camera: Any) -> CalibrationProfileSignature:
    return signature_from_values(
        width=camera.width,
        height=camera.height,
        controls=camera.controls,
    )


def signature_from_values(
    *,
    width: int,
    height: int,
    controls: Mapping[str, Any],
) -> CalibrationProfileSignature:
    automatic = controls.get(
        "focus_automatic_continuous",
        controls.get("focus_auto", 0),
    )
    autofocus = automatic is True or automatic == 1
    focus = controls.get("focus_absolute", 0)
    if type(focus) is not int:
        raise ValueError("Camera focus_absolute must be an integer")
    return CalibrationProfileSignature(width, height, autofocus, focus)


class CalibrationProfileStore:
    """Select and preserve calibration stacks by configured optical state."""

    def __init__(self, data_dir: Path, current: CalibrationProfileSignature):
        self.data_dir = data_dir
        self.root = data_dir / "calibration_profiles"
        self.current = current
        self.root.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_stack()
        self.active_dir = self.root / current.key
        self.active_dir.mkdir(parents=True, exist_ok=True)
        self._write_profile_metadata(self.active_dir, current)

    def status(self) -> dict[str, Any]:
        profiles: list[dict[str, Any]] = []
        for directory in sorted(self.root.iterdir()):
            if not directory.is_dir() or directory.is_symlink():
                continue
            metadata = read_json(directory / "profile.json", None)
            if isinstance(metadata, dict):
                profiles.append(copy.deepcopy(metadata))
        return {
            "active_key": self.current.key,
            "active_label": self.current.label,
            "active_path": str(self.active_dir),
            "profiles": profiles,
        }

    def _migrate_legacy_stack(self) -> None:
        marker = self.root / "legacy_migration.json"
        if marker.exists():
            return
        signature = self._legacy_signature() or self.current
        destination = self.root / signature.key
        destination.mkdir(parents=True, exist_ok=True)
        copied: list[str] = []
        for name in _LEGACY_FILES:
            source = self.data_dir / name
            if self._copy_regular_file(source, destination / name):
                copied.append(name)
        source_images = self.data_dir / "lens_images"
        if source_images.is_dir() and not source_images.is_symlink():
            for source in sorted(source_images.iterdir()):
                if self._copy_regular_file(source, destination / "lens_images" / source.name):
                    copied.append(f"lens_images/{source.name}")
        self._write_profile_metadata(destination, signature)
        atomic_write_json(
            marker,
            {
                "schema_version": PROFILE_SCHEMA_VERSION,
                "legacy_profile_key": signature.key,
                "copied": copied,
            },
        )

    def _legacy_signature(self) -> CalibrationProfileSignature | None:
        raw = read_json(self.data_dir / "bed_calibration.json", None)
        if not isinstance(raw, dict):
            return None
        provenance = raw.get("provenance")
        camera = provenance.get("camera") if isinstance(provenance, dict) else None
        if not isinstance(camera, dict):
            return None
        width = camera.get("width")
        height = camera.get("height")
        controls = camera.get("controls")
        if type(width) is not int or type(height) is not int or not isinstance(controls, dict):
            return None
        try:
            return signature_from_values(width=width, height=height, controls=controls)
        except ValueError:
            return None

    @staticmethod
    def _copy_regular_file(source: Path, destination: Path) -> bool:
        if not source.is_file() or source.is_symlink():
            return False
        payload = source.read_bytes()
        destination.parent.mkdir(parents=True, exist_ok=True)
        return atomic_write_bytes_if_absent(destination, payload)

    @staticmethod
    def _write_profile_metadata(
        directory: Path,
        signature: CalibrationProfileSignature,
    ) -> None:
        path = directory / "profile.json"
        if not path.exists():
            atomic_write_json(path, signature.to_dict())
