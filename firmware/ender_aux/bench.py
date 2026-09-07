"""Operator-run console for the output-disabled E3 auxiliary BENCH firmware.

This is a maintenance client for the spare. All fan/probe/Z controls are virtual
firmware state. INPUTS reads the probe pin; no command enables physical outputs.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from decimal import Decimal

if __package__:
    from . import host
else:
    import host

STATUS_RE = re.compile(
    r"BENCH FAN1=(\d+) FAN2=(\d+) PROBE=(STOWED|DEPLOYED) TRIGGER=([01]) "
    r"SOURCE=(SIM|SWITCH) Z_UM=(\d+) TARGET_UM=(\d+) MOTION=(IDLE|MOVE|PROBE) "
    r"RESULT=(NONE|DONE|CONTACT|NO_TRIGGER|STOPPED|ERROR) OUTPUTS=DISABLED"
)
INPUT_RE = re.compile(r"INPUTS PROBE_PC14=([01])")
HELP = """Virtual controls (physical outputs remain disabled):
  status                         Show virtual state
  inputs                         Read the physical probe input
  fan1 0..100 | fan2 0..100        Set virtual fan percentage
  deploy | stow                   Change virtual probe state
  trigger on|off                  Change simulated contact input
  source sim|switch               Select simulated or physical contact input
  position 0..200                 Set virtual Z position in mm
  move -10..10 [speed]            Move virtual Z; speed 0.1..10 mm/s (default 1)
  probe distance [speed]         Probe down >0..10 mm; speed as above
  reset | stop | quit | help
Movement duration is limited to 30 seconds. Use status to observe progress."""


@dataclass(frozen=True)
class Status:
    fan1: int
    fan2: int
    probe: str
    trigger: int
    source: str
    z_um: int
    target_um: int
    motion: str
    result: str

    def describe(self) -> str:
        return (
            f"Outputs DISABLED | Fans {self.fan1}% / {self.fan2}% | Probe {self.probe.lower()} "
            f"| Trigger {self.trigger} ({self.source.lower()})\n"
            f"Virtual Z {self.z_um / 1000:.3f} mm -> {self.target_um / 1000:.3f} mm "
            f"| {self.motion.lower()} | Result {self.result.lower()}"
        )


def parse_status(response: str) -> Status:
    match = STATUS_RE.fullmatch(response)
    if match is None:
        raise host.FirmwareError(f"Invalid BENCH status or outputs-disabled assurance: {response!r}")
    fan1, fan2, probe, trigger, source, z_um, target, motion, result = match.groups()
    values = tuple(int(value) for value in (fan1, fan2, z_um, target))
    if any(value > 100 for value in values[:2]) or any(value > 200000 for value in values[2:]):
        raise host.FirmwareError("BENCH status contains out-of-range virtual state")
    return Status(values[0], values[1], probe, int(trigger), source, values[2], values[3], motion, result)


def micrometres(text: str) -> int:
    if len(text) > 24 or not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", text):
        raise host.FirmwareError("Use a finite decimal number in millimetres")
    value = Decimal(text) * 1000
    if value != value.to_integral_value():
        raise host.FirmwareError("Values must be exact multiples of 0.001 mm; rounding is not allowed")
    return int(value)


def translate(text: str) -> str:
    """Strict, local allowlist; no raw G-code or arbitrary serial passthrough."""
    if len(text) > 160:
        raise host.FirmwareError("Command is too long")
    words = text.strip().lower().split()
    if len(words) == 1:
        mapping = {
            "status": "STATUS", "inputs": "INPUTS", "deploy": "SIM PROBE DEPLOY",
            "stow": "SIM PROBE STOW", "reset": "SIM RESET", "stop": "STOP",
            "quit": "QUIT", "help": "HELP",
        }
        if words[0] in mapping:
            return mapping[words[0]]
    if len(words) == 2:
        name, argument = words
        if name in ("fan1", "fan2") and re.fullmatch(r"[0-9]{1,3}", argument):
            percent = int(argument)
            if percent <= 100:
                return f"SIM FAN {name[-1]} {percent}"
        elif name == "trigger" and argument in ("on", "off"):
            return f"SIM TRIGGER {int(argument == 'on')}"
        elif name == "source" and argument in ("sim", "switch"):
            return f"SIM SOURCE {argument.upper()}"
        elif name == "position":
            position = micrometres(argument)
            if 0 <= position <= 200000:
                return f"SIM Z SET {position}"
    if len(words) in (2, 3) and words[0] in ("move", "probe"):
        distance = micrometres(words[1])
        speed = micrometres(words[2]) if len(words) == 3 else 1000
        if not 100 <= speed <= 10000:
            raise host.FirmwareError("Speed must be between 0.1 and 10 mm/s")
        if not 0 < abs(distance) <= 10000 or (words[0] == "probe" and distance < 0):
            raise host.FirmwareError("Move must be nonzero and at most 10 mm; probe distance must be positive")
        if abs(distance) * 1000 > speed * 30000:
            raise host.FirmwareError("Requested movement exceeds the 30-second duration limit")
        return f"SIM Z {words[0].upper()} {distance} {speed}"
    raise host.FirmwareError("Unsupported command or arguments; use help for the allowed commands")


class Session:
    def __init__(self, port):
        self.link = host.Link(port)
        self.is_bench = False
        self.stop_sent = False
        self.cleanup_started = False

    def connect(self) -> Status:
        if self.link.identity() != "BENCH":
            raise host.FirmwareError("This console requires the exact output-disabled BENCH 0.2.0 firmware")
        self.is_bench = True
        return self.status()

    def status(self) -> Status:
        if not self.is_bench:
            raise host.FirmwareError("BENCH identity has not been established")
        self.link.send("STATUS")
        return parse_status(self.link.receive())

    def inputs(self) -> tuple[int, Status]:
        if not self.is_bench:
            raise host.FirmwareError("BENCH identity has not been established")
        self.link.send("INPUTS")
        response = self.link.receive()
        match = INPUT_RE.fullmatch(response)
        if match is None:
            raise host.FirmwareError(f"Invalid physical-input response: {response!r}")
        return int(match[1]), self.status()

    def command(self, text: str) -> Status:
        if not self.is_bench:
            raise host.FirmwareError("BENCH identity has not been established")
        wire = translate(text)
        if wire in ("STATUS", "INPUTS", "QUIT", "HELP"):
            raise host.FirmwareError("Use the matching console action for status, inputs, quit or help")
        self.stop_sent = wire == "STOP"
        self.link.send(wire)
        self.link.expect("OK " + wire)
        return self.status()

    def cleanup(self) -> None:
        if self.cleanup_started or not self.is_bench or self.stop_sent:
            return
        self.cleanup_started = True
        self.command("stop")


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--port", required=True, help="Explicit spare-board COM port")
    result.add_argument("--hardware-enabled", action="store_true")
    result.add_argument("--status", action="store_true", help="Read identity, virtual status and physical input, then close")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.hardware_enabled:
        print("Stopped: --hardware-enabled is required; no port was opened", file=sys.stderr)
        return 1
    result = 0
    try:
        with host.open_port(args.port) as port:
            session = Session(port)
            try:
                print(session.connect().describe())
                if args.status:
                    physical, state = session.inputs()
                    print(f"Physical probe input PC14: {physical}")
                    print(state.describe())
                else:
                    print(HELP)
                    while True:
                        try:
                            entered = input("bench> ")
                        except EOFError:
                            break
                        wire = translate(entered)
                        if wire == "QUIT":
                            break
                        if wire == "HELP":
                            print(HELP)
                        elif wire == "STATUS":
                            print(session.status().describe())
                        elif wire == "INPUTS":
                            physical, state = session.inputs()
                            print(f"Physical probe input PC14: {physical}")
                            print(state.describe())
                        else:
                            print(session.command(entered).describe())
            except (host.FirmwareError, OSError) as exc:
                print(f"Stopped: {exc}. Session is closing; no automatic retry.", file=sys.stderr)
                result = 1
            except KeyboardInterrupt:
                print("Interrupted; attempting virtual STOP before closing.", file=sys.stderr)
                result = 130
            finally:
                try:
                    session.cleanup()
                except (host.FirmwareError, OSError, KeyboardInterrupt) as exc:
                    print(f"STOP could not be confirmed: {exc}. Port is closing.", file=sys.stderr)
                    result = result or 1
    except (host.FirmwareError, OSError) as exc:
        print(f"Stopped: {exc}. No automatic reopen.", file=sys.stderr)
        return 1
    return result


if __name__ == "__main__":
    raise SystemExit(main())
