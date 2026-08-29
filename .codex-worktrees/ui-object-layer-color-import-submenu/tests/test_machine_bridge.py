from __future__ import annotations

import queue

from laser_aligner.machine.bridge import _fail_safe_disconnect


class FakeSerial:
    def __init__(self) -> None:
        self.raw: list[bytes] = []
        self.lines: list[str] = []

    def open(self) -> None:
        pass

    def close(self) -> None:
        pass

    def write_raw(self, data: bytes) -> None:
        self.raw.append(data)

    def write_line(self, line: str) -> None:
        self.lines.append(line)

    def read_line(self, timeout: float = 1.0) -> str | None:
        return None


def test_grbl_client_loss_uses_realtime_stop_then_laser_off() -> None:
    serial = FakeSerial()
    _fail_safe_disconnect(serial, "grbl")
    assert serial.raw == [b"!\x18"]
    assert serial.lines == ["M5"]


def test_marlin_client_loss_uses_emergency_stop_then_laser_off() -> None:
    serial = FakeSerial()
    _fail_safe_disconnect(serial, "marlin")
    assert serial.raw == []
    assert serial.lines == ["M112", "M5"]


def test_authenticated_client_loss_stops_grbl_before_serial_close() -> None:
    import socket
    import threading

    from laser_aligner.machine.bridge import _serve_authenticated_client

    client, server = socket.socketpair()
    serial = FakeSerial()

    def serve() -> None:
        _serve_authenticated_client(
            server,
            serial_path="/dev/test",
            baudrate=115200,
            protocol="grbl",
            serial_factory=lambda _path, _baud: serial,
        )

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        ready = b""
        while not ready.endswith(b"\n"):
            ready += client.recv(1024)
        assert ready == b"E3BRIDGE/1 READY 115200\n"
    finally:
        client.close()
        thread.join(timeout=1)
        server.close()
    assert not thread.is_alive()
    assert serial.lines[0] == "M5"
    assert serial.raw[-1] == b"!\x18"
    assert serial.lines[-1] == "M5"


def test_authenticated_bridge_forwards_controller_ack_without_filtering() -> None:
    import socket
    import threading
    import time

    from laser_aligner.machine.bridge import _serve_authenticated_client

    class ReplyingSerial(FakeSerial):
        def __init__(self) -> None:
            super().__init__()
            self.responses: queue.Queue[str] = queue.Queue()

        def write_raw(self, data: bytes) -> None:
            super().write_raw(data)
            if data == b"$H\n":
                self.responses.put("ok")

        def read_line(self, timeout: float = 1.0) -> str | None:
            try:
                return self.responses.get(timeout=timeout)
            except queue.Empty:
                return None

    client, server = socket.socketpair()
    serial = ReplyingSerial()
    thread = threading.Thread(
        target=_serve_authenticated_client,
        kwargs={
            "conn": server,
            "serial_path": "/dev/test",
            "baudrate": 115200,
            "protocol": "grbl",
            "serial_factory": lambda _path, _baud: serial,
        },
    )
    thread.start()
    try:
        assert client.recv(1024) == b"E3BRIDGE/1 READY 115200\n"
        client.sendall(b"$H\n")
        client.settimeout(1.0)
        assert client.recv(1024) == b"ok\n"
        assert b"$H\n" in serial.raw
    finally:
        client.close()
        thread.join(timeout=1.0)
        server.close()
        time.sleep(0.01)
    assert not thread.is_alive()
