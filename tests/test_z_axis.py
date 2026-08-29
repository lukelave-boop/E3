from __future__ import annotations

from collections import defaultdict, deque

import pytest

from laser_aligner.config import LaserSettings, MachineSettings, WorkArea, ZAxisSettings
from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.z_axis.controller import ZHomingController
from laser_aligner.z_axis.model import (
    ZReferenceMode,
    ZState,
    effective_safe_z_max,
    parse_m114,
    parse_m119,
    probe_to_laser_position,
)
from laser_aligner.z_axis.service import ZAxisHardwareService, resolve_z_serial_device


class FakeMarlinSerial:
    def __init__(self, scripts: dict[str, list[list[str]]] | None = None) -> None:
        self.scripts: dict[str, deque[list[str]]] = defaultdict(deque)
        for command, exchanges in (scripts or {}).items():
            self.scripts[command].extend([list(lines) for lines in exchanges])
        self.writes: list[str] = []
        self.current: deque[str] = deque()
        self.opened = False
        self.fail_read = False

    def open(self) -> None:
        self.opened = True

    def close(self) -> None:
        self.opened = False

    def write_line(self, line: str) -> None:
        if not self.opened:
            raise MachineError("serial disconnected")
        self.writes.append(line)
        exchanges = self.scripts[line]
        self.current = deque(exchanges.popleft() if exchanges else [])

    def read_line(self, timeout: float = 1.0) -> str | None:
        if self.fail_read:
            raise MachineError("USB serial disconnected")
        return self.current.popleft() if self.current else None

    def drain(self) -> list[str]:
        self.current.clear()
        return []


def _settings(**overrides: object) -> ZAxisSettings:
    settings = ZAxisSettings(
        enabled=True,
        endpoint="/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0",
        startup_delay=0.0,
        read_timeout=0.01,
        homing_timeout=0.02,
    )
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _connected_service(
    scripts: dict[str, list[list[str]]] | None = None,
    **settings_overrides: object,
) -> tuple[ZAxisHardwareService, FakeMarlinSerial]:
    serial_scripts = {
        "M115": [["FIRMWARE_NAME:Marlin 2.0.8.26F4", "MACHINE_TYPE: Ender-3 S1 Pro", "ok"]],
        **(scripts or {}),
    }
    serial = FakeMarlinSerial(serial_scripts)
    service = ZAxisHardwareService(
        _settings(**settings_overrides),
        allow_motion=True,
        serial_factory=lambda _path, _baud: serial,
        sleep=lambda _seconds: None,
        start_health_monitor=False,
    )
    service.connect()
    serial.writes.clear()
    return service, serial


def _success_unknown_home_scripts(final_z: float = 5.0) -> dict[str, list[list[str]]]:
    return {
        "M280 P0 S90": [["ok"], ["ok"]],
        "M420 S0": [["ok"], ["ok"]],
        "G91": [["ok"]],
        "G1 Z10.000 F480.000": [["ok"]],
        "G90": [["ok"]],
        "M280 P0 S10": [["ok"]],
        "M119": [["Reporting endstop status", "z_min: open", "ok"]],
        "G28": [["echo:busy: processing", "echo:busy: processing", "ok"]],
        "M114": [[f"X:141.80 Y:150.50 Z:{final_z:.2f} E:0.00 Count X:11344 Y:12040 Z:2000", "ok"]],
    }


def test_parse_m114_uses_only_logical_coordinates_before_count() -> None:
    parsed = parse_m114(
        ["X:141.80 Y:150.50 Z:5.00 E:0.00 Count X:11344 Y:12040 Z:2000"]
    )
    assert parsed["Z"] == pytest.approx(5.0)
    assert parsed["X"] == pytest.approx(141.8)


@pytest.mark.parametrize("lines", [[], ["ok"], ["Count X:0 Y:0 Z:2000"]])
def test_parse_m114_rejects_missing_logical_z(lines: list[str]) -> None:
    with pytest.raises(MachineError, match="M114"):
        parse_m114(lines)


def test_parse_m119_requires_z_min() -> None:
    assert parse_m119(["Reporting endstop status", "z_min: open"])["z_min"] == "open"
    with pytest.raises(MachineError, match="z_min"):
        parse_m119(["x_min: open"])


def test_busy_messages_are_retained_until_terminal_ok() -> None:
    service, serial = _connected_service({"G28": [["echo:busy: processing", "echo:busy: processing", "ok"]]})
    responses = service._execute("G28", timeout=0.1)
    assert responses == ["echo:busy: processing", "echo:busy: processing"]
    assert serial.writes == ["G28"]


def test_serial_timeout_and_disconnect_invalidate_z() -> None:
    service, serial = _connected_service({"M114": [[]]})
    service._set_state(ZState.KNOWN, current_z_mm=5.0)
    with pytest.raises(MachineError, match="Timed out"):
        service._execute("M114", timeout=0.001)
    assert service.status()["z_known"] is False
    assert service.status()["connected"] is False

    service, serial = _connected_service({"M114": [["unused"]]})
    service._set_state(ZState.KNOWN, current_z_mm=5.0)
    serial.fail_read = True
    with pytest.raises(MachineError, match="USB serial disconnected"):
        service._execute("M114")
    assert service.status()["z_known"] is False
    assert service.status()["connected"] is False


def test_uncertain_motion_timeout_attempts_emergency_stop_before_disconnect() -> None:
    service, serial = _connected_service({"G28": [[]]})

    with pytest.raises(MachineError, match="Timed out"):
        service._execute("G28", timeout=0.001)

    assert serial.writes == ["G28", "M112"]
    assert service.status()["connected"] is False
    assert service.status()["z_known"] is False


def test_s1_board_reset_banner_invalidates_z_position() -> None:
    service, _serial = _connected_service(
        {"M114": [["start", "echo:Marlin 2.0.8.26F4", "ok"]]}
    )
    service._set_state(ZState.KNOWN, current_z_mm=5.0)
    with pytest.raises(MachineError, match="reset/startup"):
        service._execute("M114")
    assert service.status()["z_known"] is False
    assert service.status()["connected"] is False


def test_client_disconnect_invalidates_known_position_without_closing_pi_serial() -> None:
    service, serial = _connected_service()
    service._set_state(ZState.KNOWN, current_z_mm=5.0)
    service.client_disconnected()
    assert service.status()["state"] == "UNKNOWN"
    assert service.status()["z_known"] is False
    assert service.status()["connected"] is True
    assert serial.opened is True


def test_software_stop_attempts_m112_and_requires_serial_reconnect() -> None:
    service, serial = _connected_service()
    service._set_state(ZState.KNOWN, current_z_mm=5.0)

    service.request_stop()

    assert serial.writes == ["M112"]
    assert serial.opened is False
    assert service.status()["state"] == "FAULT"
    assert service.status()["z_known"] is False
    assert service.status()["connected"] is False


def test_unknown_home_requires_confirmation_before_any_motion() -> None:
    service, serial = _connected_service()
    with pytest.raises(SafetyError, match="explicit gantry-clear confirmation"):
        service.prepare_home(confirmed_unknown=False, effective_max_mm=80.0)
    assert serial.writes == []


def test_confirmed_unknown_home_uses_relative_lift_and_verifies_final_z() -> None:
    service, serial = _connected_service(_success_unknown_home_scripts())
    prepared = service.prepare_home(confirmed_unknown=True, effective_max_mm=80.0)
    assert prepared["clearance"] == {
        "kind": "confirmed_unknown_relative",
        "lift_mm": 10.0,
    }
    result = service.complete_home(
        prepared["token"],
        reference_mode="fixed_edge",
        surface_height_mm=None,
        effective_max_mm=80.0,
    )
    assert result["z_known"] is True
    assert service.status()["state"] == "KNOWN"
    assert serial.writes[:5] == [
        "M280 P0 S90",
        "M420 S0",
        "G91",
        "G1 Z10.000 F480.000",
        "G90",
    ]
    home_index = serial.writes.index("G28")
    mesh_indexes = [i for i, command in enumerate(serial.writes) if command == "M420 S0"]
    assert mesh_indexes[0] < home_index < mesh_indexes[-1]
    assert all(command not in serial.writes for command in ("M" + "401", "G" + "30"))


def test_failed_final_home_verification_clears_z_known() -> None:
    service, _serial = _connected_service(_success_unknown_home_scripts(final_z=5.5))
    prepared = service.prepare_home(confirmed_unknown=True, effective_max_mm=80.0)
    with pytest.raises(MachineError, match="G28 verification failed"):
        service.complete_home(
            prepared["token"],
            reference_mode="fixed_edge",
            surface_height_mm=None,
            effective_max_mm=80.0,
        )
    assert service.status()["z_known"] is False
    assert service.status()["state"] == "FAULT"


@pytest.mark.parametrize(
    ("current", "expected_target", "moves"),
    [(74.0, 80.0, True), (79.0, 80.0, True), (80.0, 80.0, False)],
)
def test_known_prehome_lift_clamps_at_80(
    current: float,
    expected_target: float,
    moves: bool,
) -> None:
    scripts = {
        "M280 P0 S90": [["ok"]],
        "M420 S0": [["ok"]],
        "M114": [
            [f"X:0 Y:0 Z:{current:.3f}", "ok"],
            *([[f"X:0 Y:0 Z:{expected_target:.3f}", "ok"]] if moves else []),
        ],
        "G90": [["ok"]],
        f"G1 Z{expected_target:.3f} F480.000": [["ok"]],
    }
    service, serial = _connected_service(scripts)
    service._set_state(ZState.KNOWN, current_z_mm=current)
    prepared = service.prepare_home(confirmed_unknown=False, effective_max_mm=80.0)
    assert prepared["clearance"]["target_z_mm"] == expected_target
    assert any(command.startswith("G1 Z") for command in serial.writes) is moves
    service.abort_home(prepared["token"], "test cleanup")


def test_switching_to_material_reference_uses_current_frame_ceiling_for_prehome() -> None:
    service, _serial = _connected_service(
        {
            "M280 P0 S90": [["ok"]],
            "M420 S0": [["ok"]],
            "M114": [
                ["X:0 Y:0 Z:74.000", "ok"],
                ["X:0 Y:0 Z:80.000", "ok"],
            ],
            "G90": [["ok"]],
            "G1 Z80.000 F480.000": [["ok"]],
        }
    )
    service._set_state(ZState.KNOWN, current_z_mm=74.0)
    prepared = service.prepare_home(confirmed_unknown=False, effective_max_mm=60.0)
    assert prepared["clearance"]["target_z_mm"] == 80.0
    service.abort_home(prepared["token"], "test cleanup")


def test_normal_absolute_move_accepts_80_and_rejects_above_ceiling() -> None:
    service, serial = _connected_service(
        {
            "M420 S0": [["ok"]],
            "G90": [["ok"]],
            "G1 Z80.000 F480.000": [["ok"]],
            "M114": [["X:0 Y:0 Z:80.000", "ok"]],
        }
    )
    service._set_state(ZState.KNOWN, current_z_mm=5.0)
    result = service.move_absolute(80.0, effective_max_mm=80.0)
    assert result["current_z_mm"] == pytest.approx(80.0)
    before = list(serial.writes)
    with pytest.raises(SafetyError, match="exceeds"):
        service.move_absolute(80.001, effective_max_mm=80.0)
    assert serial.writes == before


def test_probe_test_never_moves_z() -> None:
    service, serial = _connected_service(
        {
            "M280 P0 S10": [["ok"]],
            "M119": [["z_min: open", "ok"]],
            "M280 P0 S90": [["ok"]],
        }
    )
    assert service.test_probe()["passed"] is True
    assert serial.writes == ["M280 P0 S10", "M119", "M280 P0 S90"]


def test_failed_probe_test_invalidates_previously_known_z() -> None:
    service, _serial = _connected_service(
        {
            "M280 P0 S10": [["ok"]],
            "M119": [["z_min: TRIGGERED", "ok"]],
        }
    )
    service._set_state(ZState.KNOWN, current_z_mm=5.0)
    with pytest.raises(MachineError, match="expected z_min open"):
        service.test_probe()
    assert service.status()["z_known"] is False
    assert service.status()["state"] == "FAULT"


def test_explicit_serial_reconnect_reopens_but_never_restores_known_z() -> None:
    service, serial = _connected_service({"M114": [[]]})
    service._set_state(ZState.KNOWN, current_z_mm=5.0)
    with pytest.raises(MachineError):
        service._execute("M114", timeout=0.001)
    serial.scripts["M115"].append(
        ["FIRMWARE_NAME:Marlin 2.0.8.26F4", "MACHINE_TYPE: Ender-3 S1 Pro", "ok"]
    )
    service.connect()
    assert serial.opened is True
    assert service.status()["connected"] is True
    assert service.status()["state"] == "UNKNOWN"
    assert service.status()["z_known"] is False


def test_exclusive_home_session_blocks_second_operation() -> None:
    service, _serial = _connected_service(
        {
            "M280 P0 S90": [["ok"]],
            "M420 S0": [["ok"]],
            "G91": [["ok"]],
            "G1 Z10.000 F480.000": [["ok"]],
            "G90": [["ok"]],
        }
    )
    prepared = service.prepare_home(confirmed_unknown=True, effective_max_mm=80.0)
    with pytest.raises(MachineError, match="already active"):
        service.test_probe()
    service.abort_home(prepared["token"], "test cleanup")


def test_device_detection_prefers_unique_stable_1a86_path() -> None:
    path = "/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0"
    assert resolve_z_serial_device("auto", by_id_paths=[path]) == path
    assert resolve_z_serial_device("/dev/custom") == "/dev/custom"
    with pytest.raises(MachineError, match="Could not auto-detect"):
        resolve_z_serial_device("auto", by_id_paths=[])
    with pytest.raises(MachineError, match="More than one"):
        resolve_z_serial_device("auto", by_id_paths=[path, path + "-other"])


@pytest.mark.parametrize(
    ("height", "expected"),
    [(0.0, 80.0), (10.0, 70.0), (20.0, 60.0)],
)
def test_work_surface_effective_ceiling(height: float, expected: float) -> None:
    assert effective_safe_z_max(80.0, "work_surface", height) == expected


@pytest.mark.parametrize("height", [None, -0.1, float("nan")])
def test_invalid_work_surface_height_is_rejected(height: float | None) -> None:
    with pytest.raises(SafetyError, match="surface height"):
        effective_safe_z_max(80.0, "work_surface", height)


def test_probe_offset_transform_matches_measured_defaults() -> None:
    assert probe_to_laser_position(20.0, 20.0, -3.302, -38.608) == pytest.approx(
        (23.302, 58.608)
    )


class FakeXY:
    def __init__(self, *, connected: bool = True) -> None:
        self.settings = MachineSettings(
            allow_motion=True,
            work_area=WorkArea(0.0, 220.0, 0.0, 220.0),
            photo_x=110.0,
            photo_y=110.0,
        )
        self.laser_settings = LaserSettings(travel_feed_mm_min=2000.0)
        self.calls: list[tuple[object, ...]] = []
        self.connected = connected

    def status(self) -> dict[str, object]:
        return {
            "connected": self.connected,
            "job": {"running": False},
            "armed": False,
        }

    def prepare_photo_position(self) -> dict[str, object]:
        self.calls.append(("prepare_photo_position",))
        return {"position": {"x": 110.0, "y": 110.0}}

    def jog(self, dx_mm: float, dy_mm: float, feed_mm_min: float) -> dict[str, object]:
        self.calls.append(("jog", dx_mm, dy_mm, feed_mm_min))
        return {"position": {"x": 110.0 + dx_mm, "y": 110.0 + dy_mm}}


class FakeZ:
    def __init__(self, *, known: bool = False) -> None:
        self.known = known
        self.calls: list[tuple[object, ...]] = []

    def status(self) -> dict[str, object]:
        return {"z_known": self.known, "state": "KNOWN" if self.known else "UNKNOWN"}

    def ensure_connected(self) -> None:
        self.calls.append(("ensure_connected",))

    def close(self) -> None:
        self.calls.append(("close",))

    def request_stop(self) -> None:
        self.calls.append(("stop",))

    def test_probe(self) -> dict[str, object]:
        return {"passed": True, "z_min": "open"}

    def prepare_home(self, *, confirmed_unknown: bool, effective_max_mm: float) -> dict[str, object]:
        self.calls.append(("prepare_home", confirmed_unknown, effective_max_mm))
        if not self.known and not confirmed_unknown:
            raise SafetyError("confirmation required")
        return {"token": "token", "clearance": {}}

    def complete_home(self, token: str, **kwargs: object) -> dict[str, object]:
        self.calls.append(("complete_home", token, kwargs))
        self.known = True
        return {"current_z_mm": 5.0, "effective_safe_max_mm": kwargs["effective_max_mm"]}

    def abort_home(self, token: str, reason: str) -> None:
        self.calls.append(("abort_home", token, reason))


def test_work_surface_home_routes_real_xy_through_existing_machine_service() -> None:
    xy = FakeXY()
    z = FakeZ()
    settings = _settings()
    controller = ZHomingController(settings, xy, z, hardware_enabled=True)
    result = controller.home(
        reference_mode=ZReferenceMode.WORK_SURFACE,
        work_probe_x_mm=20.0,
        work_probe_y_mm=20.0,
        surface_height_mm=20.0,
        confirmed_unknown=True,
    )
    assert xy.calls[0] == ("prepare_photo_position",)
    assert xy.calls[1][0] == "jog"
    assert xy.calls[1][1:] == pytest.approx(
        (23.302 - 110.0, 58.608 - 110.0, 2000.0)
    )
    assert result["effective_safe_max_mm"] == 60.0


def test_invalid_surface_is_rejected_before_either_controller_moves() -> None:
    xy = FakeXY()
    z = FakeZ()
    controller = ZHomingController(_settings(), xy, z, hardware_enabled=True)
    with pytest.raises(SafetyError, match="surface height"):
        controller.home(
            reference_mode="work_surface",
            surface_height_mm=None,
            confirmed_unknown=True,
        )
    assert xy.calls == []
    assert z.calls == []


def test_disconnected_authoritative_xy_is_rejected_before_z_moves() -> None:
    xy = FakeXY(connected=False)
    z = FakeZ()
    controller = ZHomingController(_settings(), xy, z, hardware_enabled=True)

    with pytest.raises(SafetyError, match="authoritative E3 laser X/Y controller"):
        controller.home(reference_mode="fixed_edge", confirmed_unknown=True)

    assert xy.calls == []
    assert z.calls == []


def test_unknown_confirmation_rejection_causes_no_xy_movement() -> None:
    xy = FakeXY()
    z = FakeZ()
    controller = ZHomingController(_settings(), xy, z, hardware_enabled=True)
    with pytest.raises(SafetyError, match="confirmation"):
        controller.home(reference_mode="fixed_edge", confirmed_unknown=False)
    assert xy.calls == []
