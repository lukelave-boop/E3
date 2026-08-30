from __future__ import annotations

import argparse
import logging
import time
from pathlib import Path
from threading import Thread

from .air_assist import (
    AirAssistCommands,
    AirAssistMode,
    coerce_air_assist_mode,
)
from .camera.bridge import CameraBridgeServer
from .camera.remote_protocol import camera_token_from_environment
from .camera.service import CameraService
from .config import Settings, load_settings
from .errors import MachineError
from .machine.controller_dialects import resolve_air_assist_commands
from .machine.network_transport import is_bridge_uri
from .machine.pi_job_service import PiJobService
from .machine.pi_job_store import PiJobStore
from .machine.pi_machine_server import PiMachineServer
from .machine.secondary_controller import (
    CrealityControllerOwner,
    SecondaryMarlinFanController,
)
from .machine.service import MachineService

LOGGER = logging.getLogger(__name__)
_DEFAULT_MACHINE_PORT = 8765
_DEFAULT_CAMERA_PORT = 8766


def _resolved_device(path: str) -> Path:
    return Path(path).expanduser().resolve(strict=False)


def _secondary_air_assist_for_settings(
    settings: Settings,
    *,
    protocol: str,
) -> SecondaryMarlinFanController | None:
    machine = settings.machine
    air_assist = getattr(machine, "air_assist", None)
    mode = coerce_air_assist_mode(
        getattr(air_assist, "mode", AirAssistMode.DISABLED)
    )
    if mode is not AirAssistMode.SECONDARY_MARLIN_FAN:
        return None
    binding = resolve_air_assist_commands(air_assist, protocol=protocol)
    secondary_port = binding.port
    secondary_baudrate = binding.baudrate
    if secondary_port is None or secondary_baudrate is None:
        raise MachineError("Secondary Marlin fan binding omitted its serial endpoint")
    if is_bridge_uri(secondary_port):
        raise MachineError(
            "The Pi-owned secondary Marlin fan port must be a Pi-local serial device"
        )
    if _resolved_device(machine.port) == _resolved_device(secondary_port):
        raise MachineError(
            "Primary and secondary controllers must resolve to different serial devices"
        )
    owner = CrealityControllerOwner(secondary_port, secondary_baudrate)
    return SecondaryMarlinFanController(owner, binding)


def _recover_pending_secondary_air_assist(
    store: PiJobStore,
    *,
    primary_port: str,
) -> None:
    """Attempt exact acknowledged OFF for every durable unresolved binding."""

    grouped: dict[AirAssistCommands, list[str]] = {}
    for recovery in store.pending_secondary_recoveries():
        grouped.setdefault(recovery.binding, []).append(recovery.job_id)
    for binding, job_ids in grouped.items():
        secondary_port = binding.port
        secondary_baudrate = binding.baudrate
        assert secondary_port is not None
        assert secondary_baudrate is not None
        if is_bridge_uri(secondary_port):
            LOGGER.error(
                "Refusing persisted secondary recovery through a bridge URI; "
                "pending jobs remain blocked"
            )
            continue
        if _resolved_device(primary_port) == _resolved_device(secondary_port):
            LOGGER.error(
                "Refusing persisted secondary recovery on the current primary "
                "controller device; pending jobs remain blocked"
            )
            continue
        owner: CrealityControllerOwner | None = None
        acknowledged = False
        try:
            owner = CrealityControllerOwner(secondary_port, secondary_baudrate)
            recovery_fan = SecondaryMarlinFanController(owner, binding)
            recovery_fan.initialize_off()
            acknowledged = True
            LOGGER.warning(
                "Acknowledged restart OFF on persisted secondary mapping %s",
                binding.mapping_digest[:12],
            )
        except Exception:
            LOGGER.exception(
                "Persisted secondary Air Assist restart OFF failed; pending jobs "
                "remain blocked"
            )
        finally:
            if owner is not None:
                owner.close()
        if not acknowledged:
            continue
        _clear_pending_secondary_binding(
            store,
            binding=binding,
            job_ids=job_ids,
        )


def _clear_pending_secondary_binding(
    store: PiJobStore,
    *,
    binding: AirAssistCommands,
    job_ids: list[str] | None = None,
) -> None:
    """CAS-clear records covered by one exact acknowledged OFF exchange."""

    if job_ids is None:
        job_ids = [
            recovery.job_id
            for recovery in store.pending_secondary_recoveries()
            if recovery.binding == binding
        ]
    for job_id in job_ids:
        try:
            store.clear_secondary_recovery(
                job_id,
                acknowledged_binding=binding,
            )
        except Exception:
            LOGGER.exception(
                "Acknowledged secondary restart OFF but could not clear Pi job %s",
                job_id[:8],
            )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="E3 Raspberry Pi hardware node for controller and camera"
    )
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="Explicitly enable access to the physical controller",
    )
    parser.add_argument("--config", required=True, help="Pi-local E3 JSON configuration")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind address; use 0.0.0.0 only on a trusted/firewalled machine network",
    )
    parser.add_argument("--machine-port", type=int, default=_DEFAULT_MACHINE_PORT)
    parser.add_argument("--camera-port", type=int, default=_DEFAULT_CAMERA_PORT)
    parser.add_argument(
        "--protocol",
        choices=("grbl", "marlin"),
        help="Controller protocol; required when the Pi-local config uses auto",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    if args.hardware is not True:
        parser.error("--hardware is required before the E3 node may open a controller")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = load_settings(args.config)
    if settings.machine.backend != "serial":
        raise MachineError("The Pi hardware-node configuration must use machine.backend='serial'")
    if is_bridge_uri(settings.machine.port):
        raise MachineError("The Pi hardware-node controller port must be a Pi-local serial device")
    protocol = args.protocol or settings.machine.protocol
    if protocol not in {"grbl", "marlin"}:
        raise MachineError(
            "Set machine.protocol to grbl/marlin in the Pi config or pass --protocol"
        )
    settings.machine.protocol = protocol
    settings.ensure_directories()
    job_store = PiJobStore(settings.app.data_dir / "pi_machine_jobs")
    _recover_pending_secondary_air_assist(
        job_store,
        primary_port=settings.machine.port,
    )
    secondary_air_assist = _secondary_air_assist_for_settings(
        settings,
        protocol=protocol,
    )
    try:
        token = camera_token_from_environment()
        machine = MachineService(
            settings.machine,
            settings.laser,
            hardware_enabled=True,
            laser_lockout=False,
            secondary_air_assist=secondary_air_assist,
        )
        job_service = PiJobService(
            machine,
            job_store,
        )
        machine_server: PiMachineServer | None = None
        camera_server: CameraBridgeServer | None = None
        workers: list[Thread] = []
        try:
            # PiJobStore construction already reconciled persisted active jobs
            # and restart recovery already attempted their exact accepted OFF.
            if secondary_air_assist is not None:
                try:
                    secondary_air_assist.initialize_off()
                    _clear_pending_secondary_binding(
                        job_store,
                        binding=secondary_air_assist.binding,
                    )
                except MachineError:
                    LOGGER.exception(
                        "Secondary Marlin fan startup OFF was not acknowledged; "
                        "air-assist job START remains degraded until initialization succeeds"
                    )
            machine_server = PiMachineServer(
                job_service,
                host=args.host,
                port=args.machine_port,
                token=token,
            )
            camera_server = CameraBridgeServer(
                CameraService(settings.camera),
                host=args.host,
                port=args.camera_port,
                token=token,
            )
            workers = [
                Thread(
                    target=machine_server.serve_forever,
                    name="e3-pi-machine-service",
                    daemon=True,
                ),
                Thread(
                    target=camera_server.serve_forever,
                    name="e3-camera-bridge",
                    daemon=True,
                ),
            ]
            for worker in workers:
                worker.start()
            try:
                while all(worker.is_alive() for worker in workers):
                    time.sleep(0.5)
                failed = next(
                    (worker.name for worker in workers if not worker.is_alive()),
                    "bridge",
                )
                raise RuntimeError(f"{failed} stopped unexpectedly")
            except KeyboardInterrupt:
                LOGGER.info("Stopping E3 hardware node")
        finally:
            try:
                if machine_server is not None:
                    machine_server.stop()
            finally:
                try:
                    job_service.shutdown(stop_machine=True)
                finally:
                    if camera_server is not None:
                        camera_server.stop()
                    for worker in workers:
                        worker.join(timeout=2.0)
    finally:
        if secondary_air_assist is not None:
            secondary_air_assist.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
