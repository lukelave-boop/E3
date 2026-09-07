"""Operator-run Linux USB transfer metadata capture; never opens a serial port.

Requires usbmon already loaded. Uses its binary API with zero payload allocation.
No controller commands, USB reset, power changes, module loading or service calls.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import json
import os
import platform
import re
import struct
import sys
import tempfile
import time
from pathlib import Path

HEADER = struct.Struct("=QBBBBHBBqiiII8siiII")
MAX_BYTES = 8 * 1024 * 1024


def device_for_tty(tty: str, root: Path = Path("/sys/class/tty")) -> Path:
    if not re.fullmatch(r"ttyUSB\d+", tty):
        raise ValueError("Use the Ender's known ttyUSB name, for example ttyUSB0")
    resolved = (root / tty / "device").resolve(strict=True)
    for path in (resolved, *resolved.parents):
        if (path / "idVendor").exists():
            if (path / "idVendor").read_text().strip().lower() != "1a86":
                raise ValueError("Selected device is not the expected WCH USB serial adapter")
            if (path / "idProduct").read_text().strip().lower() != "7523":
                raise ValueError("Selected device does not match this rig's CH340 adapter")
            return path
    raise ValueError("Could not identify the USB device behind the selected tty")


def identity(device: Path) -> tuple[int, int]:
    return tuple(int((device / name).read_text().strip()) for name in ("busnum", "devnum"))


def snapshot(device: Path) -> dict:
    values = {"usb_path": device.name}
    for name in ("busnum", "devnum", "idVendor", "idProduct", "speed", "power/control", "power/runtime_status"):
        try:
            values[name] = (device / name).read_text().strip()
        except OSError:
            values[name] = "unavailable"
    return values


def metadata(raw: bytes, bus: int, device: int, ids: dict[int, int]) -> dict | None:
    if len(raw) != HEADER.size:
        raise ValueError("Incomplete usbmon header")
    tag, event, kind, endpoint, dev, busnum, _, _, sec, usec, status, length, _, _, _, _, _, _ = HEADER.unpack(raw)
    if (busnum, dev) != (bus, device):
        return None
    if event not in b"SCE" or kind not in range(4) or not 0 <= usec < 1_000_000:
        raise ValueError("Invalid usbmon metadata")
    if tag not in ids:
        if len(ids) >= 65536:
            raise ValueError("USB request ID limit reached")
        ids[tag] = len(ids) + 1
    # Raw kernel addresses, USB setup bytes and payloads are deliberately omitted.
    return {"request": ids[tag], "usb_timestamp_us": sec * 1_000_000 + usec,
            "event": chr(event), "type": ("iso", "interrupt", "control", "bulk")[kind],
            "direction": "in" if endpoint & 128 else "out", "endpoint": endpoint & 15,
            "status": status, "length": length}


class UsbMonitor:
    def __init__(self, bus: int):
        import fcntl

        self.ioctl = fcntl.ioctl
        self.fd = os.open(f"/dev/usbmon{bus}", os.O_RDONLY | os.O_NONBLOCK)
        self.header = ctypes.create_string_buffer(HEADER.size)
        self.args = bytearray(struct.pack("@PPP", ctypes.addressof(self.header), 0, 0))
        self.get_request = (1 << 30) | (len(self.args) << 16) | (0x92 << 8) | 10
        self.stats_request = (2 << 30) | (8 << 16) | (0x92 << 8) | 3

    def read(self) -> bytes | None:
        try:
            self.ioctl(self.fd, self.get_request, self.args, True)
        except OSError as exc:
            if exc.errno in (errno.EAGAIN, errno.EWOULDBLOCK):
                return None
            raise
        return self.header.raw

    def stats(self) -> dict:
        data = bytearray(8)
        self.ioctl(self.fd, self.stats_request, data, True)
        queued, dropped = struct.unpack("=II", data)
        return {"queued": queued, "dropped_since_previous_stats": dropped}

    def close(self):
        os.close(self.fd)


def collect(monitor, device: Path, duration: int, output: Path, *, clock=time.monotonic, sleep=time.sleep) -> dict:
    bus, dev = identity(device)
    start = clock()
    check_at = start
    counts = {"selected_events": 0, "other_device_headers_discarded": 0,
              "receive_bytes": 0, "usb_errors": 0, "written_bytes": 0}
    ids: dict[int, int] = {}
    dropped = monitor.stats()["dropped_since_previous_stats"]
    reason = "time limit"
    try:
        with output.open("x", encoding="utf-8") as trace:
            while clock() - start < duration:
                now = clock()
                if now >= check_at:
                    if identity(device) != (bus, dev):
                        reason = "device address changed; no new device captured"
                        break
                    check_at = now + 1
                raw = monitor.read()
                if raw is None:
                    sleep(.005)
                    continue
                entry = metadata(raw, bus, dev, ids)
                if entry is None:
                    counts["other_device_headers_discarded"] += 1
                    continue
                entry["observed_elapsed_seconds"] = round(now - start, 6)
                line = json.dumps(entry, separators=(",", ":")) + "\n"
                size = len(line.encode("utf-8"))
                if counts["written_bytes"] + size > MAX_BYTES:
                    reason = "8 MiB limit"
                    break
                trace.write(line)
                trace.flush()
                counts["written_bytes"] += size
                counts["selected_events"] += 1
                if entry["event"] == "C" and entry["direction"] == "in" and entry["status"] == 0:
                    counts["receive_bytes"] += entry["length"]
                if entry["event"] in ("C", "E") and entry["status"] < 0:
                    counts["usb_errors"] += 1
    except KeyboardInterrupt:
        reason = "operator stopped capture"
    except (OSError, ValueError) as exc:
        reason = f"capture stopped: {exc}"
    try:
        stats = monitor.stats()
        dropped += stats["dropped_since_previous_stats"]
    except OSError:
        stats = {"queued": None}
        dropped = None
    return {**counts, "reason": reason, "elapsed_seconds": round(clock() - start, 2),
            "capture_dropped_events": dropped, "queued_at_end": stats["queued"],
            "note": "Driver-level metadata, not a physical wire trace. Missing replies are inconclusive if events were dropped."}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tty", default="ttyUSB0")
    parser.add_argument("--seconds", type=int, default=1200)
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        parser.error("Run this capture on the Pi, not Windows")
    if not 1 <= args.seconds <= 1200:
        parser.error("Capture duration must be between 1 and 1200 seconds")
    if os.geteuid() != 0:
        parser.error("Run using sudo; usbmon access requires root")
    os.umask(0o077)
    try:
        device = device_for_tty(args.tty)
        monitor = UsbMonitor(identity(device)[0])
    except (OSError, ValueError) as exc:
        print(f"Capture did not start: {exc}. If usbmon is absent, run sudo modprobe usbmon first.")
        return 1
    directory = Path(tempfile.mkdtemp(prefix="e3-ender-usb-"))
    info = {"started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "started_unix_seconds": time.time(), "kernel": platform.release(),
            "tty": args.tty, "device": snapshot(device), "maximum_seconds": args.seconds,
            "payload_capture": False}
    (directory / "environment.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"CAPTURE READY: {directory}\nKeep this terminal open. No device commands are sent.\n"
          f"Capture ends after {args.seconds} seconds or Ctrl+C. Use your other terminal for the test.", flush=True)
    try:
        try:
            summary = collect(monitor, device, args.seconds, directory / "transfers.jsonl")
        except (OSError, ValueError) as exc:
            summary = {"reason": f"capture unavailable: {exc}", "capture_dropped_events": None}
        summary["finished_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        (directory / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(json.dumps(summary, indent=2), flush=True)
    finally:
        monitor.close()
        uid = int(os.environ.get("SUDO_UID", "0"))
        gid = int(os.environ.get("SUDO_GID", "0"))
        for path in (*directory.iterdir(), directory):
            os.chown(path, uid, gid)
    print(f"CAPTURE SAVED: {directory}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
