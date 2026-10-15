"""Bind an unchanged calibrated camera to authenticated network aliases."""
from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path

from ..storage import atomic_write_bytes_if_absent, strict_json_loads


def remote_camera_identity(device: str, token: str) -> str | None:
    # Numbered video devices are not stable camera identities across reboots.
    if not device.startswith("/dev/v4l/by-id/") or len(token) < 24:
        return None
    return hmac.new(
        token.encode(), ("E3 calibrated camera v1\0" + device).encode(), hashlib.sha256
    ).hexdigest()


class CameraEndpointBindings:
    def __init__(self, profile_dir: Path) -> None:
        self.root = profile_dir / "camera_endpoint_bindings"

    def _path(self, mapping_digest: str) -> Path:
        if len(mapping_digest) != 64 or any(c not in "0123456789abcdef" for c in mapping_digest):
            raise ValueError("Invalid calibration mapping digest")
        return self.root / (mapping_digest + ".json")

    def identity_matches(self, mapping_digest: str, identity: str | None) -> bool:
        path = self._path(mapping_digest)
        try:
            if not path.exists():
                return True
            if path.stat().st_size > 1024:
                return False
            record = strict_json_loads(path.read_text(encoding="utf-8"))
            return (
                isinstance(record, dict) and record.get("schema_version") == 1
                and identity is not None and record.get("identity") == identity
            )
        except (OSError, ValueError, UnicodeError):
            return False

    def canonical_camera(
        self, mapping_digest: str, saved: dict, current: dict, identity: str | None
    ) -> dict:
        saved_device = saved.get("device")
        current_device = current.get("device")
        if not all(isinstance(value, str) and value.startswith("e3camera://")
                   for value in (saved_device, current_device)):
            return current
        comparable = {**current, "device": saved_device}
        if comparable != saved or identity is None:
            return current
        path = self._path(mapping_digest)
        if saved_device == current_device and not path.exists():
            # Enroll only at the address already accepted by the saved map.
            # First writer wins; a changed camera cannot replace this binding.
            data = json.dumps({"schema_version": 1, "identity": identity}).encode()
            try:
                atomic_write_bytes_if_absent(path, data)
            except OSError:
                return current
        if path.exists() and self.identity_matches(mapping_digest, identity):
            return comparable
        return current
