"""Frozen browser placement photographs bound to the shared material context."""
from __future__ import annotations

import hashlib
import uuid
from contextlib import contextmanager
from typing import Any

from .errors import CalibrationError


class BrowserPlacementMixin:
    def material_placement_status(self) -> dict[str, Any]:
        try:
            selection = self.selected_material_surface()
            return {"enabled": selection is not None, "ready": True,
                    "surface": None if selection is None else selection.to_dict(),
                    "reason": "", "physical_qualification_required": True}
        except Exception as exc:
            return {"enabled": True, "ready": False, "surface": None,
                    "reason": str(exc), "physical_qualification_required": True}

    def capture_browser_placement(self) -> dict[str, Any]:
        with self._workspace_lock:
            self._browser_placement_programs = {}
            self._browser_placement_capture = None
        before = self.material_surface_metadata()
        ppm = self.precision_pixels_per_mm() if before is not None else None
        image = self.capture_parked_trace_frame(pixels_per_mm=ppm)
        if before != self.material_surface_metadata():
            raise CalibrationError("Surface changed during browser capture; capture again")
        identifier = str(uuid.uuid4())
        with self._workspace_lock:
            self._browser_placement_capture = (identifier, before, image.copy(), ppm)
        return {"capture_id": identifier, "material_surface": before,
                "width": image.shape[1], "height": image.shape[0]}

    def record_browser_placement_program(self, text: str, capture_id: Any) -> None:
        """Record only an exact server-generated program reviewed on this photograph."""
        with self._workspace_lock:
            surface = self.validate_browser_placement(capture_id)
            if surface is None:
                return
            programs = getattr(self, "_browser_placement_programs", {})
            if len(programs) >= 256:
                programs = {}
            programs[hashlib.sha256(text.encode("utf-8")).hexdigest()] = capture_id
            self._browser_placement_programs = programs

    def validate_browser_placement_program(self, text: str) -> None:
        if self.material_surface_metadata() is None:
            return
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._workspace_lock:
            capture_id = getattr(self, "_browser_placement_programs", {}).get(digest)
            if capture_id is None:
                raise CalibrationError("Capture the measured surface and generate this exact browser job again before Arm or Run")
            self.validate_browser_placement(capture_id)

    @contextmanager
    def browser_placement_program_review(self, text: str):
        # A recapture must revoke old review before positioning. Hold that same
        # lock until Arm or Start accepts so recapture cannot race acceptance.
        with self._workspace_lock:
            self.validate_browser_placement_program(text)
            yield

    def validate_browser_placement(self, capture_id: Any) -> dict[str, Any] | None:
        current = self.material_surface_metadata()
        if current is None and capture_id is None:
            return None
        with self._workspace_lock:
            capture = getattr(self, "_browser_placement_capture", None)
        if capture is None or capture[0] != capture_id or capture[1] != current:
            raise CalibrationError("Capture the measured surface and review browser placement again")
        return current

    def browser_placement_image(self, capture_id: str):
        self.validate_browser_placement(capture_id)
        with self._workspace_lock:
            capture = getattr(self, "_browser_placement_capture", None)
            if capture is None or capture[0] != capture_id:
                raise CalibrationError("Placement photograph expired; capture again")
            return capture[2].copy()

    def browser_placement_pixels_per_mm(self, capture_id: str) -> float:
        self.validate_browser_placement(capture_id)
        with self._workspace_lock:
            capture = getattr(self, "_browser_placement_capture", None)
            if capture is None or capture[0] != capture_id or capture[3] is None:
                raise CalibrationError("Native-detail placement photograph is unavailable")
            return capture[3]
