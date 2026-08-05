import os
import pty
import select

from laser_aligner.machine.serial_posix import PosixSerial


def read_master(fd: int, timeout: float = 1.0) -> bytes:
    readable, _, _ = select.select([fd], [], [], timeout)
    assert readable, "serial test timed out waiting for bytes"
    return os.read(fd, 4096)


def test_posix_serial_round_trip_over_pseudoterminal() -> None:
    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)
    serial = PosixSerial(slave_path, 115200)
    try:
        serial.open()
        serial.write_line("M115")
        assert read_master(master_fd) == b"M115\n"

        os.write(master_fd, b"FIRMWARE_NAME:TEST\r\nok\r\n")
        assert serial.read_line(timeout=1.0) == "FIRMWARE_NAME:TEST"
        assert serial.read_line(timeout=1.0) == "ok"

        serial.write_raw(b"?")
        assert read_master(master_fd) == b"?"
    finally:
        serial.close()
        os.close(master_fd)
        os.close(slave_fd)

import threading
import time

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.machine.service import MachineService


def test_machine_service_over_pseudoterminal() -> None:
    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)
    os.set_blocking(master_fd, False)
    stop = threading.Event()
    commands: list[str] = []

    def controller() -> None:
        buffer = bytearray()
        while not stop.is_set():
            readable, _, _ = select.select([master_fd], [], [], 0.05)
            if not readable:
                continue
            try:
                chunk = os.read(master_fd, 4096)
            except BlockingIOError:
                continue
            for byte in chunk:
                if byte == ord("?"):
                    os.write(master_fd, b"<Idle|MPos:100.000,100.000,0.000|FS:0,0>\r\n")
                elif byte in (10, 13):
                    if not buffer:
                        continue
                    line = buffer.decode("ascii", errors="replace")
                    buffer.clear()
                    commands.append(line)
                    if line == "$I":
                        os.write(master_fd, b"[VER:1.1h.test:PTY]\r\nok\r\n")
                    else:
                        os.write(master_fd, b"ok\r\n")
                else:
                    buffer.append(byte)

    thread = threading.Thread(target=controller, daemon=True)
    thread.start()
    machine = MachineService(
        MachineSettings(
            backend="serial",
            protocol="grbl",
            port=slave_path,
            allow_motion=True,
            home_before_photo=False,
            photo_x=100,
            photo_y=100,
            read_timeout=1.0,
        ),
        LaserSettings(arm_timeout_seconds=10),
        hardware_enabled=True,
    )
    try:
        machine.connect()
        parked = machine.prepare_photo_position()
        assert parked["position"]["x"] == 100
        machine.arm(machine.ARM_PHRASE)
        machine.start_job("G21\nG90\nM5\nG0X10Y10F1000\nM4S5\nG1X20Y20F500\nM5\n", "pty.gcode")
        deadline = time.monotonic() + 3
        while machine.status()["job"]["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert machine.status()["job"]["error"] is None
        assert "G0 X100.000 Y100.000 F3000.000" in commands
        assert "M4S5" in commands
    finally:
        machine.disconnect()
        stop.set()
        thread.join(timeout=1)
        os.close(master_fd)
        os.close(slave_fd)
