"""Operator-run Pi identity capture. No firmware writes or actuator commands.

The hardware service must already be stopped by the operator. This standalone
maintenance tool opens one explicitly selected CH340 exclusively, preserves
received startup bytes, then sends M115 once (INFO only for an old updater).
It does not operate services, reset the controller or reconnect automatically.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

MAX_BYTES = 65536
UPDATERS = {
    "E3AUX1 UPDATER 0.3.0 BOARD=0401C013": "f401_compact_updater_0.3.0",
    "E3AUX1 UPDATER 0.1.0 BOARD=0401E013": "f401_updater_0.1.0",
    "E3AUX1 UPDATER 0.2.0 BOARD=0401E013": "f401_updater_0.2.0",
    "E3AUX1 UPDATER 0.2.0 BOARD=0103E013": "f103_updater_0.2.0",
}


def classify(phases):
    # Application identity and acknowledgment must occur in the query reply,
    # not be assembled from unrelated startup and query phases.
    for phase in phases:
        lines = phase["text"].splitlines()
        if phase["phase"] == "M115" and "ok" in lines:
            if any(line.startswith("FIRMWARE_NAME:Marlin ") for line in lines):
                caps = {"Cap:E3_MAINBOARD_V1:1", "Cap:E3_MATERIAL_HEIGHT_V1:1",
                        "Cap:EMERGENCY_PARSER:1"}
                return "e3_marlin" if caps <= set(lines) else "marlin"
    lines = [line for phase in phases for line in phase["text"].splitlines()]
    if "ERR MCU_MISMATCH" in lines:
        return "mcu_mismatch_reported"
    for identity, role in UPDATERS.items():
        if identity in lines:
            return role
    if any(line.startswith("E3AUX1 APP ") and "BOARD=0401E013" in line for line in lines):
        return "f401_aux_application"
    return "unidentified_bytes" if any(p["bytes"] for p in phases) else "no_response"


def capture(port, *, clock=time.monotonic):
    phases = []
    remaining = MAX_BYTES

    def listen(name, seconds):
        nonlocal remaining
        deadline = clock() + seconds
        data = bytearray()
        while clock() < deadline:
            data.extend(port.read(min(4096, remaining - len(data) + 1)))
            if len(data) > remaining:
                raise ValueError("Receive limit of 64 KiB exceeded; stopped without retry")
        remaining -= len(data)
        phases.append({"phase": name, "bytes": len(data),
                       "text": data.decode("ascii", errors="backslashreplace"),
                       "hex": data.hex()})

    def query(command):
        if port.write(command) != len(command):
            raise OSError("Incomplete query write; stopped without retry")

    # Never flush an early error or send traffic during this observation phase.
    listen("listen_35_seconds", 35)
    query(b"M115\n")
    listen("M115", 8)
    if "ERR UNSUPPORTED" in phases[-1]["text"].splitlines():
        query(b"INFO\n")
        listen("INFO", 3)
    return {"reported_role": classify(phases), "phases": phases,
            "physical_functions_verified": False,
            "note": "Silence cannot identify the chip or establish whether SD installation succeeded. "
                    "An error printed before this port opened may have been missed."}


def require_stopped_service():
    result = subprocess.run(
        ["systemctl", "show", "e3-hardware-node.service", "--property=ActiveState", "--value"],
        text=True, capture_output=True, timeout=5, check=False,
    )
    if result.returncode != 0 or result.stdout.strip() not in {"inactive", "failed"}:
        raise ValueError("Stop e3-hardware-node.service before this maintenance capture. "
                         "Its inactive state could not be confirmed; no port was opened.")


def inspect_adapter(factory, device, *, clock=time.monotonic):
    port = factory(port=None, baudrate=115200, timeout=0.2, write_timeout=2, exclusive=True)
    try:
        port.dtr = False
        port.rts = False
        port.port = device
        port.open()
        return capture(port, clock=clock)
    finally:
        port.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", required=True)
    parser.add_argument("--hardware-enabled", action="store_true", required=True)
    args = parser.parse_args(argv)
    if not args.hardware_enabled or not sys.platform.startswith("linux"):
        parser.error("Run this maintenance diagnostic on the Pi with --hardware-enabled")
    report = {"started_utc": datetime.now(timezone.utc).isoformat(),
              "port": args.port, "baudrate": 115200}
    try:
        require_stopped_service()
        import serial
        from serial.tools import list_ports

        selected = Path(args.port).resolve(strict=True)
        matches = [p for p in list_ports.comports()
                   if Path(p.device).resolve() == selected and (p.vid, p.pid) == (0x1A86, 0x7523)]
        if len(matches) != 1:
            raise ValueError("The selected port must resolve to exactly one CH340 (1A86:7523)")
        report["adapter"] = {key: getattr(matches[0], key, None)
                             for key in ("device", "vid", "pid", "location", "description")}
        print("Listening 35 seconds, then querying identity. Leave the board powered and USB connected.",
              flush=True)
        report.update(inspect_adapter(serial.Serial, str(selected)))
    except (OSError, ValueError, subprocess.SubprocessError, KeyboardInterrupt) as exc:
        report["error"] = str(exc) or type(exc).__name__
    text = json.dumps(report, indent=2) + "\n"
    with tempfile.NamedTemporaryFile(mode="w", prefix="e3-startup-", suffix=".json",
                                     encoding="utf-8", delete=False) as output:
        output.write(text)
    print(text)
    print(f"Report saved: {output.name}")
    return 1 if "error" in report or report.get("reported_role") in {"no_response", "unidentified_bytes"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
