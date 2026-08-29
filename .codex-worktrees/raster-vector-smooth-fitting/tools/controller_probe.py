#!/usr/bin/env python3
from __future__ import annotations

import argparse
import time

from laser_aligner.errors import MachineError
from laser_aligner.machine.serial_backend import (
    MachineTransport,
    create_serial_transport,
    list_serial_ports,
)


def collect(port: MachineTransport, seconds: float) -> list[str]:
    deadline = time.monotonic() + seconds
    lines: list[str] = []
    while time.monotonic() < deadline:
        line = port.read_line(timeout=min(0.2, deadline - time.monotonic()))
        if line:
            lines.append(line)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read controller startup/identity information without sending motion or laser commands"
    )
    parser.add_argument("--port", help="Serial device path")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--startup-wait", type=float, default=2.5)
    args = parser.parse_args()

    ports = list_serial_ports()
    if not args.port:
        if not ports:
            print("No supported serial devices found on this platform.")
            return 1
        print("Available serial ports:")
        for item in ports:
            print(f"  {item['path']} -> {item['resolved']}")
        print("\nRun again with --port PATH to query one device.")
        return 0

    try:
        port = create_serial_transport(args.port, args.baud)
    except MachineError as exc:
        parser.error(str(exc))
    port.open()
    try:
        print(f"Opened {args.port} at {args.baud} baud")
        startup = collect(port, args.startup_wait)
        print("\n--- startup ---")
        print("\n".join(startup) or "(no startup text)")
        for command, wait in (("$I", 1.5), ("$$", 2.5), ("M115", 1.5)):
            print(f"\n--- {command} ---")
            port.write_line(command)
            print("\n".join(collect(port, wait)) or "(no response)")
    finally:
        port.close()
    print("\nNo motion, homing, or laser-enable commands were sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
