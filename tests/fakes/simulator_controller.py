from __future__ import annotations

import time

from laser_aligner.gcode.preview import exact_codes, parse_words, strip_comment


class SimulatedController:
    """Stateful in-process controller peer used by the simulator transport."""

    def __init__(self) -> None:
        self.x = 0.0
        self.y = 0.0
        self.absolute = True
        self.laser_on = False
        self.power = 0.0
        self.step_idle_delay_ms = 250

    def startup_lines(self) -> tuple[str, ...]:
        return ("Grbl 1.1h ['$' for help] (simulated)",)

    def receive_raw(self, data: bytes) -> tuple[str, ...]:
        responses: list[str] = []
        if b"?" in data:
            state = "Run" if self.laser_on else "Idle"
            responses.append(
                f"<{state}|MPos:{self.x:.3f},{self.y:.3f},0.000|"
                f"FS:0,{self.power:.0f}>"
            )
        if b"\x18" in data:
            self.laser_on = False
            self.power = 0.0
            responses.append("Grbl 1.1h ['$' for help] (simulated reset)")
        return tuple(responses)

    def receive_line(self, line: str) -> tuple[str, ...]:
        cleaned = strip_comment(line).upper()
        if not cleaned:
            return ("ok",)
        if cleaned == "$I":
            return ("[VER:1.1h.sim:LaserCameraAligner]", "ok")
        if cleaned == "$$":
            return (
                f"$1={self.step_idle_delay_ms}",
                "$30=1000",
                "$32=1",
                "ok",
            )
        if cleaned.startswith("$1="):
            try:
                self.step_idle_delay_ms = int(cleaned.split("=", 1)[1])
            except ValueError:
                return ("error:3",)
            return ("ok",)
        if cleaned == "$G":
            return ("[GC:G0 G54 G17 G21 G90 G94 M5 M9 T0 F0 S0]", "ok")
        if cleaned == "$#":
            return (
                *(f"[G{code}:0.000,0.000,0.000]" for code in range(54, 60)),
                "[G92:0.000,0.000,0.000]",
                "ok",
            )
        if cleaned in {"$H", "G28"}:
            self.x = 0.0
            self.y = 0.0
            self.laser_on = False
            self.power = 0.0
            return ("ok",)
        if cleaned == "M115":
            return ("FIRMWARE_NAME:GRBL-SIMULATOR", "ok")
        if cleaned in {"M105", "M114", "M503"}:
            return ("SIMULATED_RESPONSE", "ok")

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
        return ("ok",)


__all__ = ["SimulatedController"]
