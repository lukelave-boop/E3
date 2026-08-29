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
from .machine.bridge import BridgeServer as MachineBridgeServer
from .machine.network_transport import is_bridge_uri
from .z_axis.bridge import ZAxisBridgeServer
from .z_axis.remote import is_z_axis_uri
from .z_axis.service import ZAxisHardwareService

LOGGER = logging.getLogger(__name__)
_DEFAULT_MACHINE_PORT = 8765
_DEFAULT_CAMERA_PORT = 8766
_DEFAULT_Z_PORT = 8767


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
    parser.add_argument("--z-port", type=int, default=_DEFAULT_Z_PORT)
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
    token = camera_token_from_environment()
    machine_server = MachineBridgeServer(
        host=args.host,
        port=args.machine_port,
        serial_path=settings.machine.port,
        baudrate=settings.machine.baudrate,
        protocol=protocol,
        token=token,
    )
    camera_server = CameraBridgeServer(
        CameraService(settings.camera),
        host=args.host,
        port=args.camera_port,
        token=token,
    )
    z_server: ZAxisBridgeServer | None = None
    if settings.machine.z_axis.enabled:
        if is_z_axis_uri(settings.machine.z_axis.endpoint):
            raise MachineError(
                "The Pi hardware-node Z endpoint must be a Pi-local serial device or auto"
            )
        z_server = ZAxisBridgeServer(
            ZAxisHardwareService(
                settings.machine.z_axis,
                allow_motion=settings.machine.allow_motion,
            ),
            host=args.host,
            port=args.z_port,
            token=token,
        )
    workers = [
        Thread(target=machine_server.serve_forever, name="e3-machine-bridge", daemon=True),
        Thread(target=camera_server.serve_forever, name="e3-camera-bridge", daemon=True),
    ]
    if z_server is not None:
        workers.append(
            Thread(target=z_server.serve_forever, name="e3-z-bridge", daemon=True)
        )
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
        camera_server.stop()
        if z_server is not None:
            z_server.stop()
        for worker in workers:
            worker.join(timeout=2.0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
