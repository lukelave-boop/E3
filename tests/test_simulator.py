from __future__ import annotations

from laser_aligner.machine.simulator import SimulatedTransport
from laser_aligner.machine.simulator_controller import SimulatedController


def test_simulated_controller_exposes_semantics_without_transport_authority() -> None:
    controller = SimulatedController()

    assert controller.startup_lines() == (
        "Grbl 1.1h ['$' for help] (simulated)",
    )
    assert controller.receive_line("$I") == (
        "[VER:1.1h.sim:LaserCameraAligner]",
        "ok",
    )
    assert not hasattr(controller, "open")
    assert not hasattr(controller, "close")
    assert not hasattr(controller, "read_line")
    assert not hasattr(controller, "drain")


def test_simulated_transport_only_delegates_and_queues_peer_responses() -> None:
    calls: list[tuple[str, object]] = []

    class StubController:
        x = 1.0
        y = 2.0
        absolute = True
        laser_on = False
        power = 0.0
        step_idle_delay_ms = 250

        def startup_lines(self) -> tuple[str, ...]:
            calls.append(("startup", None))
            return ("peer-ready",)

        def receive_raw(self, data: bytes) -> tuple[str, ...]:
            calls.append(("raw", data))
            return ("raw-reply",)

        def receive_line(self, line: str) -> tuple[str, ...]:
            calls.append(("line", line))
            return ("line-reply-1", "line-reply-2")

    controller = StubController()
    transport = SimulatedTransport(controller)  # type: ignore[arg-type]

    transport.open()
    transport.write_raw(b"opaque")
    transport.write_line("opaque line")

    assert transport.drain() == [
        "peer-ready",
        "raw-reply",
        "line-reply-1",
        "line-reply-2",
    ]
    assert calls == [
        ("startup", None),
        ("raw", b"opaque"),
        ("line", "opaque line"),
    ]


def test_simulated_transport_preserves_observable_state_and_response_order() -> None:
    transport = SimulatedTransport()
    transport.open()
    assert transport.drain() == ["Grbl 1.1h ['$' for help] (simulated)"]

    transport.write_line("G91")
    transport.write_line("M4 S7")
    transport.write_line("G1 X12.5 Y4")
    assert transport.drain() == ["ok", "ok", "ok"]
    assert transport.absolute is False
    assert transport.x == 12.5
    assert transport.y == 4.0
    assert transport.laser_on is True
    assert transport.power == 7.0

    transport.write_raw(b"?\x18")

    assert transport.drain() == [
        "<Run|MPos:12.500,4.000,0.000|FS:0,7>",
        "Grbl 1.1h ['$' for help] (simulated reset)",
    ]
    assert transport.laser_on is False
    assert transport.power == 0.0


def test_simulated_transport_preserves_subclass_queue_and_state_hooks() -> None:
    class RecordingTransport(SimulatedTransport):
        def __init__(self) -> None:
            super().__init__()
            self.lines: list[str] = []

        def write_line(self, line: str) -> None:
            self.lines.append(line)
            super().write_line(line)

    transport = RecordingTransport()
    transport.x = 23.0
    transport.y = 47.0
    transport._queue.put("injected")
    transport.write_line("M5")

    assert transport.x == 23.0
    assert transport.y == 47.0
    assert transport.lines == ["M5"]
    assert transport.drain() == ["injected", "ok"]
