"""Opt-in Linux Pi temperature worker. Serial authority stays in MachineService."""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from threading import Event, Thread

from .machine.cpu_cooling import CoolingPolicy

LOGGER = logging.getLogger(__name__)


def enabled_from_environment() -> bool:
    value = os.environ.get("E3_CPU_COOLING", "0")
    if value not in {"0", "1"}:
        raise ValueError("E3_CPU_COOLING must be 0 or 1")
    if value == "1" and not sys.platform.startswith("linux"):
        raise ValueError("Automatic Pi cooling is supported only on Linux")
    return value == "1"


def read_cpu_temperature() -> float:
    if not sys.platform.startswith("linux"):
        raise OSError("Pi CPU temperature is available only on Linux")
    root = Path("/sys/class/thermal")
    for zone in sorted(root.glob("thermal_zone*")):
        if (zone / "type").read_text().strip() in {"cpu-thermal", "bcm2835_thermal"}:
            value = float((zone / "temp").read_text()) / 1000
            CoolingPolicy().demand(value)  # Validate sensor output before use.
            return value
    raise OSError("No Raspberry Pi CPU thermal sensor found")


class CpuCoolingWorker:
    def __init__(self, machine, *, read_temperature=read_cpu_temperature):
        self.machine = machine
        self.read_temperature = read_temperature
        self.policy = CoolingPolicy()
        self.stop_event = Event()
        self.thread = Thread(target=self.run, name="e3-pi-cpu-cooling", daemon=True)
        self.last_status = None

    def tick(self):
        try:
            temperature = self.read_temperature()
            CoolingPolicy().demand(temperature)
        except (OSError, ValueError):
            temperature = None
        pwm = self.policy.demand(temperature)
        result = self.machine.update_cpu_cooling(pwm)
        status = (temperature is None, pwm, result.get("state"), result.get("fan1_pwm"))
        if status != self.last_status:
            LOGGER.info("CPU cooling temperature_c=%s demand_pwm=%s result=%s", temperature, pwm, result)
            self.last_status = status
        return result

    def run(self):
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as exc:
                status = ("error", str(exc))
                if status != self.last_status:
                    LOGGER.error("CPU cooling paused/error: %s", exc)
                    self.last_status = status
            if self.stop_event.wait(5):
                break

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        self.thread.join(timeout=3)
