from __future__ import annotations

import threading
from typing import Any, Protocol

from ..config import LaserSettings, MachineSettings, ZAxisSettings
from ..errors import MachineError, SafetyError
from .model import (
    ZReferenceMode,
    effective_safe_z_max,
    probe_to_laser_position,
)


class LaserXYService(Protocol):
    settings: MachineSettings
    laser_settings: LaserSettings

    def status(self) -> dict[str, Any]: ...
    def prepare_photo_position(self) -> dict[str, Any]: ...
    def jog(self, dx_mm: float, dy_mm: float, feed_mm_min: float) -> dict[str, Any]: ...


class ZService(Protocol):
    def status(self) -> dict[str, Any]: ...
    def ensure_connected(self) -> None: ...
    def close(self) -> None: ...
    def request_stop(self) -> None: ...
    def test_probe(self) -> dict[str, Any]: ...
    def prepare_home(
        self,
        *,
        confirmed_unknown: bool,
        effective_max_mm: float,
    ) -> dict[str, Any]: ...
    def complete_home(
        self,
        token: str,
        *,
        reference_mode: ZReferenceMode | str,
        surface_height_mm: float | None,
        effective_max_mm: float,
    ) -> dict[str, Any]: ...
    def abort_home(self, token: str, reason: str) -> None: ...


class ZHomingController:
    """UI-neutral state machine spanning the independent real XY and Z boards."""

    def __init__(
        self,
        settings: ZAxisSettings,
        xy_service: LaserXYService,
        z_service: ZService,
        *,
        hardware_enabled: bool,
    ) -> None:
        if type(hardware_enabled) is not bool:
            raise TypeError("hardware_enabled must be an exact boolean")
        self.settings = settings
        self.xy_service = xy_service
        self.z_service = z_service
        self.hardware_enabled = hardware_enabled
        self._operation_lock = threading.Lock()

    def status(self) -> dict[str, Any]:
        status = self.z_service.status()
        status.setdefault("enabled", bool(self.settings.enabled))
        status.setdefault("safe_max_mm", float(self.settings.safe_max_mm))
        status.setdefault("reference_mode", self.settings.reference_mode)
        status.setdefault(
            "effective_safe_max_mm",
            self.effective_maximum(
                self.settings.reference_mode,
                self.settings.work_surface_height_mm,
            ),
        )
        return status

    def close(self) -> None:
        self.z_service.close()

    def request_stop(self) -> None:
        self.z_service.request_stop()

    def _require_authority(self) -> None:
        if not self.settings.enabled:
            raise MachineError("S1 Pro Z / CR Touch support is disabled for this machine")
        if self.hardware_enabled is not True:
            raise SafetyError("Hardware control is not enabled for this E3 process")
        if self.xy_service.settings.allow_motion is not True:
            raise SafetyError("S1 Pro Z motion is blocked by machine.allow_motion")
        machine_status = self.xy_service.status()
        job = machine_status.get("job") or {}
        if job.get("running"):
            raise SafetyError("Z referencing is blocked while a laser-controller job is running")
        if machine_status.get("armed"):
            raise SafetyError("Disarm laser control before operating the Z reference system")

    def effective_maximum(
        self,
        mode: ZReferenceMode | str,
        surface_height_mm: float | None,
    ) -> float:
        return effective_safe_z_max(
            float(self.settings.safe_max_mm),
            mode,
            surface_height_mm,
            minimum_homed_z_mm=float(self.settings.expected_homed_z_mm),
        )

    def test_probe(self) -> dict[str, Any]:
        self._require_authority()
        if not self._operation_lock.acquire(blocking=False):
            raise MachineError("Another Z reference operation is already active")
        try:
            self.z_service.ensure_connected()
            return self.z_service.test_probe()
        finally:
            self._operation_lock.release()

    def home(
        self,
        *,
        reference_mode: ZReferenceMode | str,
        work_probe_x_mm: float | None = None,
        work_probe_y_mm: float | None = None,
        surface_height_mm: float | None = None,
        confirmed_unknown: bool = False,
    ) -> dict[str, Any]:
        self._require_authority()
        if type(confirmed_unknown) is not bool:
            raise TypeError("confirmed_unknown must be an exact boolean")
        if self.xy_service.status().get("connected") is not True:
            raise SafetyError(
                "Connect the authoritative E3 laser X/Y controller before Z homing"
            )
        try:
            mode = ZReferenceMode(reference_mode)
        except ValueError as exc:
            raise SafetyError("Unsupported Z reference mode") from exc
        effective_max = self.effective_maximum(mode, surface_height_mm)

        laser_target: tuple[float, float] | None = None
        if mode is ZReferenceMode.WORK_SURFACE:
            desired_x = (
                self.settings.work_probe_x_mm
                if work_probe_x_mm is None
                else work_probe_x_mm
            )
            desired_y = (
                self.settings.work_probe_y_mm
                if work_probe_y_mm is None
                else work_probe_y_mm
            )
            laser_target = probe_to_laser_position(
                desired_x,
                desired_y,
                self.settings.probe_offset_x_mm,
                self.settings.probe_offset_y_mm,
            )
            if not self.xy_service.settings.work_area.contains(*laser_target):
                raise SafetyError(
                    "The configured work-surface probe point and CR Touch offsets "
                    "place the real laser axis outside the machine work area"
                )

        if not self._operation_lock.acquire(blocking=False):
            raise MachineError("Another Z reference operation is already active")
        token: str | None = None
        try:
            self.z_service.ensure_connected()
            prepared = self.z_service.prepare_home(
                confirmed_unknown=confirmed_unknown,
                effective_max_mm=effective_max,
            )
            raw_token = prepared.get("token")
            if not isinstance(raw_token, str) or not raw_token:
                raise MachineError("S1 Pro Z service did not establish a homing session")
            token = raw_token

            # This is the sole real-X/Y path.  The Creality board may report or
            # logically home fictional X/Y, but those values are never read or
            # used.  E3's laser controller homes and parks first, using its
            # established motion-completion barrier.
            parked = self.xy_service.prepare_photo_position()
            xy_result: dict[str, Any] = {"fixed_edge_park": parked}
            if laser_target is not None:
                position = parked.get("position")
                if not isinstance(position, dict):
                    raise MachineError("Real X/Y Home / park did not return a position")
                current_x = float(position["x"])
                current_y = float(position["y"])
                jogged = self.xy_service.jog(
                    laser_target[0] - current_x,
                    laser_target[1] - current_y,
                    min(
                        float(self.xy_service.laser_settings.travel_feed_mm_min),
                        float(self.xy_service.settings.max_work_feed_mm_min),
                    ),
                )
                xy_result = {
                    "probe_laser_axis_target_mm": {
                        "x": laser_target[0],
                        "y": laser_target[1],
                    },
                    "park": parked,
                    "positioning": jogged,
                }

            completed = self.z_service.complete_home(
                token,
                reference_mode=mode,
                surface_height_mm=surface_height_mm,
                effective_max_mm=effective_max,
            )
            token = None
            return {
                **completed,
                "prehome": prepared,
                "real_xy": xy_result,
            }
        except Exception as exc:
            if token is not None:
                self.z_service.abort_home(token, f"Z homing aborted after failure: {exc}")
            raise
        finally:
            self._operation_lock.release()
