"""Offline image validation and explicit operator-run F401 compact USB firmware update.

This maintenance tool requires exclusive serial ownership with the E3 service stopped.
It is not a MachineService controller backend. Importing it or validating an image never opens hardware.
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
BOARD = 0x0401C013
VERSION = 1
HEADER_SIZE = 512
APP_BASE = 0x08020000
VECTOR_BASE = APP_BASE + HEADER_SIZE
VECTOR_TABLE_SIZE = 101 * 4
MIN_PAYLOAD = VECTOR_TABLE_SIZE + 4
FLASH_END = 0x08040000
RAM_BASE = 0x20000000
RAM_END = 0x20010000
MAX_PAYLOAD = FLASH_END - VECTOR_BASE
PREFIX = "E3AUX1"
UPDATER_IDENTITIES = {"E3AUX1 UPDATER 0.3.0 BOARD=0401C013"}


class FirmwareError(ValueError):
    """An image or protocol operation was rejected."""


class ResponseTimeout(FirmwareError):
    def __init__(self, partial: bytes):
        self.partial = partial
        detail = f"; partial response: {partial!r}" if partial else ""
        super().__init__("No complete firmware response before timeout" + detail)


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
    def __init__(self, serial_port, *, progress=None):
        self.port = serial_port
        self.progress = progress
        self.stage = "opening serial connection"

    def mark(self, stage: str, *, announce: bool = True) -> None:
        self.stage = stage
        if announce and self.progress is not None:
            self.progress(stage)

    def settle(self) -> None:
        """Observe startup, then synchronize once on this same open connection.

        No reset, HOLD, BOOT or flash command is issued here. Startup bytes
        cannot satisfy the subsequent fresh M115 identity/acknowledgement.
        """
        def collect(seconds: float, *, synchronizing: bool = False) -> None:
            deadline = time.monotonic() + seconds
            data = bytearray()
            while time.monotonic() < deadline:
                data.extend(self.port.read(1))
                if len(data) > 65536:
                    raise FirmwareError("Startup response exceeded 64 KiB; no retry")
            if data and self.progress is not None:
                # repr keeps partial lines and escapes terminal control bytes.
                self.progress(f"Received {len(data)} startup/sync bytes: {bytes(data[:2048])!r}")
            for line in data.decode("ascii", errors="backslashreplace").splitlines():
                if synchronizing and line == "ERR UNSUPPORTED":
                    continue  # The compact updater rejects an empty line.
                if line.casefold().startswith(("err", "alarm", "!!")):
                    raise FirmwareError(f"Startup reported {line[:160]!r}")

        self.mark("waiting 35 seconds for startup (no commands sent)")
        collect(35.0)
        self.send("")
        collect(1.0, synchronizing=True)

    def send(self, command: str) -> None:
        stages = {
            "": "synchronizing serial line",
            "M115": "querying firmware identity (M115)",
            "M997": "requesting updater entry (M997)",
            "HOLD": "holding updater before erase",
            "BEGIN": "erasing application sector (BEGIN)",
            "END": "verifying and committing application (END)",
            "BOOT": "requesting application boot",
        }
        verb = command.split(" ", 1)[0]
        if verb == "DATA":
            offset = int(command.split(" ")[1], 16)
            self.mark(f"writing application block at 0x{offset:08X}", announce=offset % 4096 == 0)
        else:
            self.mark(stages.get(verb, f"sending {verb}"))
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
            if len(line) > 1024:
                raise FirmwareError("Oversized serial response")
        raise ResponseTimeout(bytes(line))

    def expect(self, expected: str, timeout: float = 3.0) -> None:
        response = self.receive(timeout)
        if response != expected:
            raise FirmwareError(f"Expected {expected!r}; received {response!r}")

    def identity(self) -> str:
        self.send("M115")
        deadline = time.monotonic() + 3.0
        marlin = capability = False
        for _ in range(128):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            response = self.receive(remaining)
            if response == "E3AUX1 UPDATER 0.3.0 BOARD=0401C013":
                return "UPDATER"
            if response.startswith("FIRMWARE_NAME:"):
                marlin = response.startswith("FIRMWARE_NAME:Marlin ")
                capability = False
                if not marlin:
                    raise FirmwareError(f"Unexpected firmware identity: {response}")
            elif response == "start" or response.casefold().startswith("echo:marlin"):
                marlin = capability = False
            elif response == "Cap:E3_USB_UPDATER_F401_V1:1":
                capability = True
            elif response == "ok" or response.startswith("ok "):
                if marlin and capability:
                    return "MARLIN_F401"
                raise FirmwareError("Firmware is not the F401 USB-update build")
            elif response.startswith(("ERR", "Error", "E3AUX1")):
                raise FirmwareError(f"Unexpected firmware identity: {response}")
        raise FirmwareError("No confirmed F401 updater or application identity")

    def hold_updater(self) -> None:
        if self.identity() == "MARLIN_F401":
            # Explicit maintenance request, accepted only with idle mechanics,
            # both fan commands OFF and all heater targets OFF. No motion.
            self.send("M997")
            try:
                self.expect("E3USB:1 ENTERING_UPDATER")
            except ResponseTimeout as exc:
                # Older STM32 Marlin can jump before buffered TX drains,
                # leaving truncated or garbled bytes. Those bytes grant no
                # erase authority: HOLD and a fresh exact updater identity
                # below remain mandatory. Never resend M997. Preserve an
                # explicit refusal, including one missing its final newline.
                partial = exc.partial.strip().lower()
                if not partial or partial.startswith((b"error", b"err", b"alarm", b"!!")) or (
                    partial.startswith(b"e3usb:") and
                    not b"e3usb:1 entering_updater".startswith(partial)
                ):
                    raise
                self.mark("entry acknowledgement incomplete; checking updater before any erase")
            time.sleep(0.5)
            self.port.reset_input_buffer()
        self.send("HOLD")
        response = self.receive()
        if response == "E3AUX1 UPDATER 0.3.0 BOARD=0401C013":
            response = self.receive()
        if response != "OK HOLD":
            raise FirmwareError(f"Updater did not hold: {response!r}")
        if self.identity() != "UPDATER":
            raise FirmwareError("Expected F401 updater before erase")


def upload(link: Link, image: bytes) -> None:
    payload = validate_image(image)
    link.hold_updater()
    link.send(f"BEGIN {BOARD:08X} {len(payload):08X} {crc32(payload):08X}")
    # One 128 KiB application-sector erase; stop-and-wait.
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
    options = {"exclusive": True} if sys.platform.startswith("linux") else {}
    if sys.platform.startswith("linux"):
        import subprocess
        try:
            result = subprocess.run(
                ["systemctl", "show", "e3-hardware-node.service", "--property=ActiveState", "--value"],
                text=True, capture_output=True, timeout=5, check=False,
            )
        except subprocess.SubprocessError as exc:
            raise FirmwareError("Service inactive state could not be confirmed; no port was opened") from exc
        if result.returncode or result.stdout.strip() not in {"inactive", "failed"}:
            raise FirmwareError("Stop e3-hardware-node.service before USB maintenance; no port was opened")
    port = serial.Serial(port=None, baudrate=115200, timeout=0.1, write_timeout=3, **options)
    try:
        port.dtr = False
        port.rts = False
        port.port = name
        port.open()
        # Preserve startup text for Link.settle; no reset pulse is requested.
    except BaseException:
        port.close()
        raise
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
        command.add_argument("--port", required=True, help="Explicit Pi-local maintenance serial port (E3 service stopped)")
        command.add_argument("--hardware-enabled", action="store_true")
        if name in ("upload", "interrupt-upload"):
            command.add_argument("image", type=Path)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    link = None
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
            link = Link(port, progress=lambda text: print(text, flush=True))
            link.settle()
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
        stage = f" during {link.stage}" if link is not None else ""
        print(f"Stopped:{stage}: {exc}" if stage else f"Stopped: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
