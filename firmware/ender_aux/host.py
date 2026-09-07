"""Offline image validation and explicit operator-run E3 auxiliary firmware upload.

This is a maintenance tool for the disconnected spare, not a MachineService
controller backend. Importing it or validating an image never opens hardware.
"""

from __future__ import annotations

import argparse
import struct
import sys
import time
import zlib
from pathlib import Path

MAGIC = 0x55413345
FORMAT = 1
BOARD = 0x0401E013
VERSION = 1
HEADER_SIZE = 512
APP_BASE = 0x08020000
VECTOR_BASE = APP_BASE + HEADER_SIZE
VECTOR_TABLE_SIZE = 101 * 4
MIN_PAYLOAD = VECTOR_TABLE_SIZE + 4
FLASH_END = 0x08080000
RAM_BASE = 0x20000000
RAM_END = 0x20018000
MAX_PAYLOAD = FLASH_END - VECTOR_BASE
PREFIX = "E3AUX1"
IDENTITIES = {
    "E3AUX1 UPDATER 0.1.0 BOARD=0401E013": "UPDATER",
    "E3AUX1 APP 0.1.0 BOARD=0401E013": "APP",
    "E3AUX1 APP 0.2.0 BOARD=0401E013 MODE=BENCH OUTPUTS=DISABLED": "BENCH",
}


class FirmwareError(ValueError):
    """An image or protocol operation was rejected."""


def crc32(data: bytes) -> int:
    return zlib.crc32(data) & 0xFFFFFFFF


def validate_payload(payload: bytes) -> None:
    if not MIN_PAYLOAD <= len(payload) <= MAX_PAYLOAD or len(payload) % 4:
        raise FirmwareError("Payload must be word aligned and fit the application region")
    stack, reset = struct.unpack_from("<II", payload)
    if not RAM_BASE < stack <= RAM_END or stack % 8:
        raise FirmwareError("Invalid initial stack pointer")
    if not reset & 1 or not VECTOR_BASE + VECTOR_TABLE_SIZE <= reset & ~1 < VECTOR_BASE + len(payload):
        raise FirmwareError("Reset vector must point inside this application's payload")


def pack_image(payload: bytes) -> bytes:
    """Called by the builder; the loader's magic word is committed last on hardware."""
    payload += b"\xff" * (-len(payload) % 4)
    validate_payload(payload)
    metadata = struct.pack("<IIIII", FORMAT, BOARD, len(payload), crc32(payload), VERSION)
    header = struct.pack("<I", MAGIC) + metadata + struct.pack("<I", crc32(metadata))
    return header.ljust(HEADER_SIZE, b"\xff") + payload


def validate_image(image: bytes) -> bytes:
    if not HEADER_SIZE + MIN_PAYLOAD <= len(image) <= HEADER_SIZE + MAX_PAYLOAD:
        raise FirmwareError("Image length outside supported range")
    magic, fmt, board, length, checksum, version, header_crc = struct.unpack_from("<7I", image)
    if (magic, fmt, board, version) != (MAGIC, FORMAT, BOARD, VERSION):
        raise FirmwareError("Wrong image format, board, or image version")
    if crc32(image[4:24]) != header_crc or image[28:HEADER_SIZE] != b"\xff" * (HEADER_SIZE - 28):
        raise FirmwareError("Header integrity check failed")
    payload = image[HEADER_SIZE:]
    if length != len(payload) or crc32(payload) != checksum:
        raise FirmwareError("Payload length or CRC mismatch")
    validate_payload(payload)
    return payload


def read_image(path: Path) -> bytes:
    # Bound read allocation, including files supplied by an operator.
    if path.stat().st_size > HEADER_SIZE + MAX_PAYLOAD:
        raise FirmwareError("Image is too large")
    with path.open("rb") as handle:
        data = handle.read(HEADER_SIZE + MAX_PAYLOAD + 1)
    if len(data) > HEADER_SIZE + MAX_PAYLOAD:
        raise FirmwareError("Image is too large")
    return data


class Link:
    def __init__(self, serial_port):
        self.port = serial_port

    def send(self, command: str) -> None:
        data = (command + "\n").encode("ascii")
        if len(data) > 256 or self.port.write(data) != len(data):
            raise FirmwareError("Serial write was incomplete; do not retry this transaction")
        self.port.flush()

    def receive(self, timeout: float = 3.0) -> str:
        deadline = time.monotonic() + timeout
        line = bytearray()
        while time.monotonic() < deadline:
            chunk = self.port.read(1)
            if not chunk:
                continue
            if chunk == b"\n":
                if not line:
                    continue
                try:
                    return line.rstrip(b"\r").decode("ascii", "strict")
                except UnicodeDecodeError as exc:
                    raise FirmwareError("Non-ASCII serial response") from exc
            line.extend(chunk)
            if len(line) > 256:
                raise FirmwareError("Oversized serial response")
        raise FirmwareError("No complete firmware response before timeout")

    def expect(self, expected: str, timeout: float = 3.0) -> None:
        response = self.receive(timeout)
        if response != expected:
            raise FirmwareError(f"Expected {expected!r}; received {response!r}")

    def identity(self) -> str:
        self.send("INFO")
        response = self.receive()
        if response not in IDENTITIES:
            raise FirmwareError(f"Unsupported firmware identity: {response!r}")
        return IDENTITIES[response]

    def hold_updater(self) -> None:
        role = self.identity()
        if role in ("APP", "BENCH"):
            self.send("UPDATE")
            self.expect("OK UPDATE")
            # No port reopen or DTR pulse: the CH340 stays enumerated across reset.
            time.sleep(0.5)
            self.port.reset_input_buffer()
        self.send("HOLD")
        # A new startup identity can precede the HOLD response after reset.
        response = self.receive()
        if response == "E3AUX1 UPDATER 0.1.0 BOARD=0401E013":
            response = self.receive()
        if response != "OK HOLD":
            raise FirmwareError(f"Updater did not hold: {response!r}")
        if self.identity() != "UPDATER":
            raise FirmwareError("Expected the updater before any erase")


def upload(link: Link, image: bytes) -> None:
    payload = validate_image(image)
    link.hold_updater()
    link.send(f"BEGIN {BOARD:08X} {len(payload):08X} {crc32(payload):08X}")
    # Three 128 KiB sector erases may stall the single-bank MCU; stop-and-wait.
    link.expect("OK BEGIN", timeout=30.0)
    for offset in range(0, len(payload), 64):
        block = payload[offset:offset + 64]
        link.send(f"DATA {offset:08X} {block.hex().upper()}")
        link.expect(f"OK DATA {offset + len(block):08X}")
    link.send("END")
    link.expect("OK END", timeout=10.0)
    # Do not run the new application automatically. Inspect/boot is separate.


def interrupt_upload(link: Link, image: bytes) -> None:
    """Leave one acknowledged block uncommitted for an operator recovery test.

    This erases the previous application. It stops between completed writes;
    it does not emulate power loss while the flash controller is busy.
    """
    payload = validate_image(image)
    link.hold_updater()
    link.send(f"BEGIN {BOARD:08X} {len(payload):08X} {crc32(payload):08X}")
    link.expect("OK BEGIN", timeout=30.0)
    # Every accepted image is larger than this one block. Never send END/BOOT.
    link.send(f"DATA 00000000 {payload[:64].hex().upper()}")
    link.expect("OK DATA 00000040")


def open_port(name: str):
    try:
        import serial
    except ImportError as exc:
        raise FirmwareError("Install pyserial==3.5 before using hardware commands") from exc
    port = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=3)
    port.dtr = False
    port.rts = False
    port.port = name
    port.open()
    # Only received startup text is discarded; no control-line toggles requested.
    port.reset_input_buffer()
    return port


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="action", required=True)
    verify = commands.add_parser("verify", help="Validate an image offline")
    verify.add_argument("image", type=Path)
    for name in ("inspect", "upload", "boot", "interrupt-upload"):
        command = commands.add_parser(
            name, help=("Erase application, write one block, stop without commit (spare recovery test)"
                        if name == "interrupt-upload" else None),
        )
        command.add_argument("--port", required=True, help="Explicit spare-board COM port")
        command.add_argument("--hardware-enabled", action="store_true")
        if name in ("upload", "interrupt-upload"):
            command.add_argument("image", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        image = None
        if args.action in ("verify", "upload", "interrupt-upload"):
            image = read_image(args.image)
            payload = validate_image(image)
        if args.action == "verify":
            print(f"Valid E3 spare-board image: {len(payload)} payload bytes, CRC32={crc32(payload):08X}")
            return 0
        if not args.hardware_enabled:
            raise FirmwareError("Hardware access requires --hardware-enabled; no port was opened")
        with open_port(args.port) as port:
            link = Link(port)
            if args.action == "inspect":
                print(link.identity())
            elif args.action == "upload":
                upload(link, image)
                print("Application verified and committed. Updater remains active; use boot when ready.")
            elif args.action == "interrupt-upload":
                interrupt_upload(link, image)
                print("Application deliberately left incomplete after 64 bytes. No commit or boot was sent.")
            else:
                link.hold_updater()
                link.send("BOOT")
                link.expect("OK BOOT")
                print("Boot requested; confirm the application with inspect.")
        return 0
    except (FirmwareError, OSError) as exc:
        print(f"Stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
