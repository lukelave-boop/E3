import os
import select
import threading
import time

import pytest

from laser_aligner.config import LaserSettings, MachineSettings
from laser_aligner.errors import MachineError, SafetyError
from laser_aligner.machine.service import MachineService

pytestmark = pytest.mark.skipif(
    os.name != "posix",
    reason="POSIX pseudoterminals and termios are unavailable on this platform",
)


def read_master(fd: int, timeout: float = 1.0) -> bytes:
    readable, _, _ = select.select([fd], [], [], timeout)
    assert readable, "serial test timed out waiting for bytes"
    return os.read(fd, 4096)


def test_posix_serial_round_trip_over_pseudoterminal() -> None:
    import pty

    from laser_aligner.machine.serial_posix import PosixSerial

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


def test_posix_serial_reopen_discards_prior_replies_and_partial_bytes() -> None:
    import pty

    from laser_aligner.machine.serial_posix import PosixSerial

    master_fd, slave_fd = pty.openpty()
    slave_path = os.ttyname(slave_fd)
    serial = PosixSerial(slave_path, 115200)
    try:
        serial.open()
        os.write(master_fd, b"stale-ok\r\npartial")
        deadline = time.monotonic() + 1.0
        while (
            serial._queue.qsize() != 1 or bytes(serial._buffer) != b"partial"
        ) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert serial._queue.qsize() == 1
        assert bytes(serial._buffer) == b"partial"

        serial.close()
        serial.open()

        assert serial.read_line(timeout=0.05) is None
        assert bytes(serial._buffer) == b""
        serial.write_line("M5")
        assert read_master(master_fd) == b"M5\n"
    finally:
        serial.close()
        os.close(master_fd)
        os.close(slave_fd)


def test_posix_serial_latches_hangup_without_consuming_and_reopen_clears_it() -> None:
    import pty

    from laser_aligner.machine.serial_posix import PosixSerial

    master_fd, slave_fd = pty.openpty()
    serial = PosixSerial(os.ttyname(slave_fd), 115200)
    second_master_fd: int | None = None
    second_slave_fd: int | None = None
    try:
        serial.open()
        os.close(master_fd)
        master_fd = -1
        deadline = time.monotonic() + 1.0
        while serial.fault is None and time.monotonic() < deadline:
            time.sleep(0.01)

        assert serial.fault is not None
        with pytest.raises(MachineError, match="Serial (?:connection closed|read failed)"):
            serial.raise_if_faulted()
        # The passive health snapshot must not consume the sole reader's error.
        with pytest.raises(MachineError, match="Serial (?:connection closed|read failed)"):
            serial.read_line(timeout=0.1)

        serial.close()
        os.close(slave_fd)
        slave_fd = -1
        second_master_fd, second_slave_fd = pty.openpty()
        serial.path = os.ttyname(second_slave_fd)
        serial.open()

        assert serial.fault is None
        serial.raise_if_faulted()
        serial.write_line("M106 S0")
        assert read_master(second_master_fd) == b"M106 S0\n"
        serial.close()
        assert serial.fault is None
    finally:
        serial.close()
        for fd in (master_fd, slave_fd, second_master_fd, second_slave_fd):
            if fd is not None and fd >= 0:
                os.close(fd)


@pytest.mark.parametrize(
    "startup",
    [
        b"start\nready\n\x13\xfaBAD",
        b"start\rready\r\x13\xfaBAD",
        b"start\nready\n\x13\xfaBAD",
        b"start\r\nready\r\n\x13\xfaBAD",
    ],
    ids=["lf", "cr", "lf-invalid-utf8", "crlf"],
)
def test_synchronize_input_discards_queued_partial_and_kernel_rx_bytes(
    startup: bytes,
) -> None:
    import pty

    from laser_aligner.machine.serial_posix import PosixSerial

    master_fd, slave_fd = pty.openpty()
    serial = PosixSerial(os.ttyname(slave_fd), 115200)
    try:
        serial.open()
        os.write(master_fd, startup)
        deadline = time.monotonic() + 1.0
        while serial._queue.qsize() != 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert serial._queue.qsize() == 2
        assert bytes(serial._buffer) == b"\x13\xfaBAD"
        assert b"\x13\xfaBAD" + b"M106 S0\n" == b"\x13\xfaBADM106 S0\n"

        # Keep the reader outside the kernel queue while adding one final byte.
        # Synchronization must flush that unread byte as well as framed state.
        with serial._receive_lock:
            os.write(master_fd, b"K")
            serial.synchronize_input()

        assert serial.read_line(timeout=0.05) is None
        assert bytes(serial._buffer) == b""
        serial.write_line("M106 S0")
        assert read_master(master_fd) == b"M106 S0\n"
    finally:
        serial.close()
        os.close(master_fd)
        os.close(slave_fd)


def test_posix_serial_rejects_an_unbounded_controller_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pty

    import laser_aligner.machine.serial_posix as serial_module

    monkeypatch.setattr(serial_module, "_MAX_SERIAL_LINE_BYTES", 32)
    master_fd, slave_fd = pty.openpty()
    serial = serial_module.PosixSerial(os.ttyname(slave_fd), 115200)
    try:
        serial.open()
        os.write(master_fd, b"x" * 64)
        assert serial._stop.wait(timeout=1.0)

        with pytest.raises(MachineError, match="exceeded 32 bytes"):
            serial.read_line(timeout=1.0)
        assert bytes(serial._buffer) == b""
    finally:
        serial.close()
        os.close(master_fd)
        os.close(slave_fd)


def test_posix_serial_rejects_an_unbounded_unsolicited_line_queue(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pty

    import laser_aligner.machine.serial_posix as serial_module

    monkeypatch.setattr(serial_module, "_MAX_QUEUED_SERIAL_LINES", 2)
    master_fd, slave_fd = pty.openpty()
    serial = serial_module.PosixSerial(os.ttyname(slave_fd), 115200)
    try:
        serial.open()
        os.write(master_fd, b"one\r\ntwo\r\nthree\r\n")
        assert serial._stop.wait(timeout=1.0)

        with pytest.raises(MachineError, match="exceeded 2 lines"):
            serial.read_line(timeout=1.0)
    finally:
        serial.close()
        os.close(master_fd)
        os.close(slave_fd)


def test_posix_serial_write_backpressure_has_a_bounded_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import laser_aligner.machine.serial_posix as serial_module

    serial = serial_module.PosixSerial("unused", 115200)
    serial._fd = 123
    monkeypatch.setattr(serial_module, "_SERIAL_WRITE_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(serial_module.os, "write", lambda _fd, _data: (_ for _ in ()).throw(BlockingIOError()))
    monkeypatch.setattr(serial_module.select, "select", lambda *_args: ([], [], []))

    started = time.monotonic()
    with pytest.raises(MachineError, match="timed out"):
        serial.write_raw(b"M5\n")
    assert time.monotonic() - started < 0.25
    serial._fd = None


def test_machine_service_over_pseudoterminal() -> None:
    import pty

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
                    elif line == "$$":
                        os.write(master_fd, b"$1=250\r\n$30=1000\r\n$32=1\r\nok\r\n")
                    elif line == "$G":
                        os.write(
                            master_fd,
                            b"[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]\r\nok\r\n",
                        )
                    elif line == "$#":
                        os.write(
                            master_fd,
                            b"[G54:0.000,0.000,0.000]\r\n"
                            b"[G55:0.000,0.000,0.000]\r\n"
                            b"[G56:0.000,0.000,0.000]\r\n"
                            b"[G57:0.000,0.000,0.000]\r\n"
                            b"[G58:0.000,0.000,0.000]\r\n"
                            b"[G59:0.000,0.000,0.000]\r\n"
                            b"[G92:0.000,0.000,0.000]\r\nok\r\n",
                        )
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
            home_before_photo=True,
            photo_x=100,
            photo_y=100,
            read_timeout=1.0,
        ),
        LaserSettings(arm_timeout_seconds=10),
        hardware_enabled=True,
    )
    try:
        machine.connect()
        assert not machine.status()["coordinate_reference_ready"]
        with pytest.raises(SafetyError, match="Home / park"):
            machine.start_job(
                "G21\nG90\nM5\nG0X10Y10F1000\nM5\n",
                "unreferenced.gcode",
            )
        parked = machine.prepare_photo_position()
        assert parked["position"]["x"] == 100
        assert machine.status()["coordinate_reference_ready"]
        program = machine.preflight_program(
            "G21\nG90\nM5\nG0X10Y10F1000\nM4S5\nG1X20Y20F500\nM5\n"
        )
        machine.arm_program(machine.ARM_PHRASE, program)
        machine.start_validated_program(program, "pty.gcode")
        deadline = time.monotonic() + 3
        while machine.status()["job"]["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert machine.status()["job"]["error"] is None
        assert "$H" in commands
        assert "G0 X100.000 Y100.000 F3000.000" in commands
        assert "M4S5" in commands
    finally:
        machine.disconnect()
        stop.set()
        thread.join(timeout=1)
        os.close(master_fd)
        os.close(slave_fd)
