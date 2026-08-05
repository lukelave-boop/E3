from __future__ import annotations

import queue
import time

from ..gcode.preview import exact_codes, parse_words, strip_comment


class SimulatedTransport:
    def __init__(self):
        self.is_open = False
        self._queue: queue.Queue[str] = queue.Queue()
        self.x = 0.0
        self.y = 0.0
        self.absolute = True
        self.laser_on = False
        self.power = 0.0

    def open(self) -> None:
        self.is_open = True
        self._queue.put("Grbl 1.1h ['$' for help] (simulated)")

    def close(self) -> None:
        self.is_open = False

    def write_raw(self, data: bytes) -> None:
        if b"?" in data:
            state = "Run" if self.laser_on else "Idle"
            self._queue.put(f"<{state}|MPos:{self.x:.3f},{self.y:.3f},0.000|FS:0,{self.power:.0f}>")
        if b"\x18" in data:
            self.laser_on = False
            self.power = 0.0
            self._queue.put("Grbl 1.1h ['$' for help] (simulated reset)")

    def write_line(self, line: str) -> None:
        cleaned = strip_comment(line).upper()
        if not cleaned:
            self._queue.put("ok")
            return
        if cleaned == "$I":
            self._queue.put("[VER:1.1h.sim:LaserCameraAligner]")
            self._queue.put("ok")
            return
        if cleaned == "$$":
            self._queue.put("$30=1000")
            self._queue.put("$32=1")
            self._queue.put("ok")
            return
        if cleaned in {"$G", "$#"}:
            self._queue.put("[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]")
            self._queue.put("ok")
            return
        if cleaned in {"$H", "G28"}:
            self.x = 0.0
            self.y = 0.0
            self.laser_on = False
            self.power = 0.0
            self._queue.put("ok")
            return
        if cleaned == "M115":
            self._queue.put("FIRMWARE_NAME:GRBL-SIMULATOR")
            self._queue.put("ok")
            return
        if cleaned in {"M105", "M114", "M503"}:
            self._queue.put("SIMULATED_RESPONSE")
            self._queue.put("ok")
            return

        words = parse_words(cleaned)
        values: dict[str, float] = {}
        for word in words:
            values[word.letter] = word.value
        g_codes = exact_codes(cleaned, "G")
        m_codes = exact_codes(cleaned, "M")

        if 90 in g_codes:
            self.absolute = True
        if 91 in g_codes:
            self.absolute = False
        if "S" in values:
            self.power = values["S"]
        if m_codes.intersection({3, 4}):
            self.laser_on = self.power > 0
        if 5 in m_codes:
            self.laser_on = False
            self.power = 0.0
        if g_codes.intersection({0, 1}):
            if "X" in values:
                self.x = values["X"] if self.absolute else self.x + values["X"]
            if "Y" in values:
                self.y = values["Y"] if self.absolute else self.y + values["Y"]
            time.sleep(0.001)
        self._queue.put("ok")

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
