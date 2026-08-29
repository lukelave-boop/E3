from __future__ import annotations

import argparse
import logging
import time
from threading import Thread

from .camera.bridge import CameraBridgeServer
from .camera.remote_protocol import camera_token_from_environment
from .camera.service import CameraService
from .config import load_settings
from .errors import MachineError
from .machine.network_transport import is_bridge_uri
from .machine.pi_job_service import PiJobService
from .machine.pi_job_store import PiJobStore
from .machine.pi_machine_server import PiMachineServer
from .machine.service import MachineService

LOGGER = logging.getLogger(__name__)
_DEFAULT_MACHINE_PORT = 8765
_DEFAULT_CAMERA_PORT = 8766


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
    token = camera_token_from_environment()
    machine = MachineService(
        settings.machine,
        settings.laser,
        hardware_enabled=True,
        laser_lockout=False,
    )
    job_service = PiJobService(
        machine,
        PiJobStore(settings.app.data_dir / "pi_machine_jobs"),
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
        Thread(target=camera_server.serve_forever, name="e3-camera-bridge", daemon=True),
    ]
    for worker in workers:
        worker.start()
    try:
        while all(worker.is_alive() for worker in workers):
            time.sleep(0.5)
        failed = next((worker.name for worker in workers if not worker.is_alive()), "bridge")
        raise RuntimeError(f"{failed} stopped unexpectedly")
    except KeyboardInterrupt:
        LOGGER.info("Stopping E3 hardware node")
    finally:
        machine_server.stop()
        job_service.shutdown(stop_machine=True)
        camera_server.stop()
        for worker in workers:
            worker.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
