"""Operator-run Windows firmware identity diagnostic, separate from normal control.

Selects only one newly attached CH340, leaving existing adapters untouched.
Sends M115 once and, only after ERR UNSUPPORTED, INFO once. Never sends boot,
update, reset, motion, probe, heater or fan commands or operates Pi services.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

MAX_RX_BYTES = 65536


def select_new_port(before: set[str], ports):
    candidates = [p for p in ports if p.device not in before
                  and (p.vid, p.pid) == (0x1A86, 0x7523)]
    if len(candidates) != 1:
        raise ValueError(
            f"Expected exactly one newly attached Ender CH340; found {len(candidates)}. "
            "No port was opened. Start again with the Ender USB disconnected from Windows."
        )
    return candidates[0]


def collect(port, seconds, clock, remaining):
    data = bytearray()
    deadline = clock() + seconds
    while clock() < deadline:
        chunk = port.read(min(4096, remaining - len(data) + 1))
        data.extend(chunk)
        if len(data) > remaining:
            raise ValueError("Serial capture exceeded its 64 KiB limit; stopped without retry")
    return bytes(data)


def identify(port, *, clock=time.monotonic):
    phases = []
    remaining = MAX_RX_BYTES

    def capture(name, seconds):
        nonlocal remaining
        data = collect(port, seconds, clock, remaining)
        remaining -= len(data)
        text = data.decode("ascii", errors="backslashreplace")
        phases.append({"phase": name, "bytes": len(data), "text": text})
        return text.splitlines()

    def send(data):
        if port.write(data) != len(data):
            raise OSError("Incomplete identity query write; stopped without retry")

    # Receive rather than flush boot evidence. No traffic during updater/startup.
    capture("startup_listen_35_seconds", 35)
    send(b"M115\n")
    lines = capture("M115", 8)
    role = "unidentified"
    if any(line.startswith("FIRMWARE_NAME:Marlin ") for line in lines) and "ok" in lines:
        role = "marlin"
        required = ("Cap:E3_MAINBOARD_V1:1", "Cap:E3_MATERIAL_HEIGHT_V1:1",
                    "Cap:EMERGENCY_PARSER:1")
        if all(lines.count(cap) == 1 for cap in required):
            role = "e3_mainboard_v1_reported"
    elif "ERR UNSUPPORTED" in lines:
        send(b"INFO\n")
        info = capture("INFO", 3)
        if "E3AUX1 UPDATER 0.1.0 BOARD=0401E013" in info:
            role = "e3_updater_0_1_0"
    if not any(phase["bytes"] for phase in phases):
        role = "no_response"
    return {"reported_role": role, "phases": phases,
            "physical_functions_verified": False}


def inspect_adapter(factory, device, *, clock=time.monotonic):
    port = factory(port=None, baudrate=115200, timeout=0.2, write_timeout=2)
    try:
        # No deliberate DTR/RTS reset pulse. Opening USB serial can still have
        # electrical effects; this is an operator-run maintenance diagnostic.
        port.dtr = False
        port.rts = False
        port.port = device
        port.open()
        return identify(port, clock=clock)
    finally:
        port.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hardware-enabled", action="store_true", required=True)
    args = parser.parse_args(argv)
    if not args.hardware_enabled or os.name != "nt":
        parser.error("This explicit operator diagnostic requires Windows and --hardware-enabled")

    import serial
    from serial.tools import list_ports

    report = {"started_utc": datetime.now(timezone.utc).isoformat(), "baudrate": 115200}
    try:
        before = {p.device for p in list_ports.comports()}
        print("Existing Windows serial ports (will not be opened): " + ", ".join(sorted(before)))
        input("Keep the Ender powered, SD removed and laser unable to emit.\n"
              "Move ONLY the Ender USB cable from the Pi to this Windows PC now.\n"
              "Leave the webcam on the Pi. Once Windows detects the adapter, press Enter: ")
        selected = select_new_port(before, list_ports.comports())
        report["adapter"] = {key: getattr(selected, key, None)
                             for key in ("device", "vid", "pid", "location", "description")}
        print(f"Selected newly attached {selected.device}. Listening 35 seconds, then querying identity.", flush=True)
        report.update(inspect_adapter(serial.Serial, selected.device))
    except (OSError, ValueError, EOFError, KeyboardInterrupt) as exc:
        report["error"] = str(exc) or type(exc).__name__
    folder = Path(__file__).resolve().parents[1] / "dist"
    folder.mkdir(exist_ok=True)
    path = folder / f"ender-usb-identity-{uuid4().hex[:12]}.json"
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Report saved: {path}")
    return 0 if report.get("reported_role") in {"marlin", "e3_mainboard_v1_reported", "e3_updater_0_1_0"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
