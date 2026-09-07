# FAN1, FAN2, Z probe and Z axis bench firmware

Application **0.2.0 BENCH** runs on the spare board with just 24 V and USB.
The installed **0.1.0 updater** accepts this application through the existing
USB upload procedure. No printer harness swap or new SD installation is needed.

FAN1/FAN2 percentages, probe deployment/contact and Z movement in this build are
**simulated**. There is no command that enables physical fan power, step pulses
or probe-control pulses. The processor reads the real PC14 probe input separately.
Simulation reports cannot establish actual fan, motor or probe behavior.

## Upload and open the console

Use the new package's `host.py`, `bench.py` and `application.e3fw`. Keep the
previous accepted 0.1.0 application file available for restoration. The files
are distinct; do not overwrite the old package.

With the spare powered, SD removed, and other serial clients closed, run each
command separately from the new package folder. Stop on any error:

```powershell
python host.py verify application.e3fw
```

```powershell
python host.py upload application.e3fw --port COM6 --hardware-enabled
```

Require `Application verified and committed. Updater remains active; use boot when ready.`

```powershell
python host.py boot --port COM6 --hardware-enabled
```

```powershell
python host.py inspect --port COM6 --hardware-enabled
```

Expected role: `BENCH`. Its exact identity is:

```text
E3AUX1 APP 0.2.0 BOARD=0401E013 MODE=BENCH OUTPUTS=DISABLED
```

Use `bench.py --status` for the first check, or open the interactive console:

```powershell
python bench.py --port COM6 --hardware-enabled --status
```

```powershell
python bench.py --port COM6 --hardware-enabled
```

Substitute the current spare-board COM port if Windows changes it. Use the
repository's Python installation if `python` is not on PATH. The client needs
only `pyserial==3.5`; ARM tools are needed for building, not operating it.

## Console commands

Commands below are entered at the bench console prompt, not PowerShell.

| Command | Meaning in this build |
| --- | --- |
| `status` | Read virtual fan percentages, probe state, trigger and Z state. |
| `inputs` | Read the real PC14 input as raw 0 or 1. This is not a calibrated probe report. |
| `fan1 50` | Set simulated FAN1 to 50 percent. |
| `fan2 75` | Set simulated FAN2 to 75 percent independently. |
| `deploy` / `stow` | Change the virtual probe state. |
| `trigger on` / `trigger off` | Set the simulated contact signal. |
| `position 10` | Set the virtual Z coordinate to 10 mm while idle. |
| `move -2 1` | Move virtual Z down 2 mm at 1 mm/s, with the virtual probe stowed. |
| `probe 5 1` | Seek down up to 5 mm at 1 mm/s with the virtual probe deployed. |
| `source sim` | Use the internal simulated trigger, the startup default. |
| `source switch` | Use an optional dry switch on the real PC14 input as the simulated contact source. |
| `stop` | Stop virtual movement, set both virtual fans to zero and stow the virtual probe. |
| `reset` | Restore the simulation defaults while idle. |
| `quit` | Attempt STOP and close the serial connection. |

The console accepts a fixed command set. It is not a raw G-code terminal and
does not connect through the production machine application. Errors or loss of
the expected identity/status cause a stop attempt and close; there is no automatic
reconnect or retry. Ctrl+C also attempts STOP before closing.

Virtual movement advances on the board between commands. Use `status` to refresh
its state. There is no background PC heartbeat or production disconnect watchdog
in this simulation build. A disconnected console cannot guarantee delivery of
STOP; physical outputs nevertheless have no enabled path in this application.

## First tests without any added wiring

1. Run `status`. Expect BENCH mode, physical outputs disabled, both fans zero,
   probe stowed and virtual Z at 10 mm after a fresh startup or `reset`.
2. Run `fan1 50`, then `fan2 75`, then `status`. Check that the two virtual
   percentages remain independent. Run `stop` and check that both become zero.
3. Run `move 2 1`. After two seconds, run `status`; expect virtual Z at 12 mm
   and the move finished. A negative distance moves toward zero.
4. With motion idle, run `position 10`, `trigger off`, `deploy`, then `probe 5 1`.
   Before five seconds elapse, run `trigger on`. Run `status`; expect `CONTACT`
   and virtual movement stopped. Stow the virtual probe and clear the trigger.
5. To exercise missing contact, set `position 10`, `trigger off`, `deploy`, then
   `probe 1 1`. After at least one second, run `status`; expect `NO_TRIGGER` at
   the end of the allowed search. The firmware does not continue descending.
6. Run `stop` before exiting. No physical component should be used to judge these
   simulations; the reported results are deliberately virtual.

Do not turn a late trigger into evidence of contact: if the search already ended
with `NO_TRIGGER`, return to idle and start a new explicit test.

## Simulation bounds and rejection behavior

Virtual Z is limited to 0 through 200 mm; this is a **test range**, not a verified
machine dimension. Each move/search is at most 10 mm; speed is 0.1 through 10 mm/s,
with a maximum requested duration of 30 seconds. Zero-distance motion, movement
outside the range, malformed numbers and extra arguments are rejected. Commands
accept exact micrometre precision and never silently round distances.

Ordinary moves require a stowed virtual probe. Probe searches require a deployed
probe and an initially clear contact signal. Position, source, reset and probe
deployment changes require idle motion. Invalid firmware commands stop virtual
operations and return an error. These are simulation guardrails, not verified
physical travel limits or safety-rated controls.

## Optional switch input

`inputs` samples PC14, the probe sensor signal in the published S1 mapping.
The application configures it as an input with a weak pull-up. `source switch`
treats raw LOW as a pressed bench switch, while all actuation remains simulated.
That convention is for a dry switch and does not establish CR Touch polarity,
timing or contact reliability.

The exact accessible connector contact and ground must be identified on this
board before adding a switch. No connector position or test point is assigned
by this guide; do not use the MCU legs for this test. The first tests above use
`source sim` and require no jumper, switch, motor, fan or probe.

## What remains physical work

The operator has verified the original updater's normal USB update and one
incomplete-transfer recovery case on the spare. This 0.2.0 application's GPIO
sampling and simulation behavior need their own bench acceptance. Actual FAN1/
FAN2 output mapping and electrical states, probe actuation/polarity/timing,
motor direction/current/steps, Z mechanics and installed-machine integration
are later stages. A real-output build will be separate from this simulation.
