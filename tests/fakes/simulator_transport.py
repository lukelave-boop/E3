from __future__ import annotations

import queue

from .simulator_controller import SimulatedController


class SimulatedTransport:
    """In-process transport mechanics backed by a simulated controller peer."""

    test_only_allow_legacy_input_synchronization = True

    def __init__(self, controller: SimulatedController | None = None):
        self.is_open = False
        self._queue: queue.Queue[str] = queue.Queue()
        self._controller = (
            controller if controller is not None else SimulatedController()
        )

    @property
    def x(self) -> float:
        return self._controller.x

    @x.setter
    def x(self, value: float) -> None:
        self._controller.x = value

    @property
    def y(self) -> float:
        return self._controller.y

    @y.setter
    def y(self, value: float) -> None:
        self._controller.y = value

    @property
    def absolute(self) -> bool:
        return self._controller.absolute

    @absolute.setter
    def absolute(self, value: bool) -> None:
        self._controller.absolute = value

    @property
    def laser_on(self) -> bool:
        return self._controller.laser_on

    @laser_on.setter
    def laser_on(self, value: bool) -> None:
        self._controller.laser_on = value

    @property
    def power(self) -> float:
        return self._controller.power

    @power.setter
    def power(self, value: float) -> None:
        self._controller.power = value

    @property
    def step_idle_delay_ms(self) -> int:
        return self._controller.step_idle_delay_ms

    @step_idle_delay_ms.setter
    def step_idle_delay_ms(self, value: int) -> None:
        self._controller.step_idle_delay_ms = value

    def _publish(self, lines: tuple[str, ...]) -> None:
        for line in lines:
            self._queue.put(line)

    def open(self) -> None:
        self.is_open = True
        self._publish(self._controller.startup_lines())

    def close(self) -> None:
        self.is_open = False

    def write_raw(self, data: bytes) -> None:
        self._publish(self._controller.receive_raw(data))

    def write_line(self, line: str) -> None:
        self._publish(self._controller.receive_line(line))

    def read_line(self, timeout: float = 1.0) -> str | None:
        try:
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def drain(self) -> list[str]:
        output: list[str] = []
        while True:
            try:
                output.append(self._queue.get_nowait())
            except queue.Empty:
                return output
