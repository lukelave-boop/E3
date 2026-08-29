# Ender-3 S1 Pro Z / CR Touch reference system

This feature gives E3 a repeatable Z reference using the modified Ender-3 S1
Pro's stock Z drivers, dual Z motors, and CR Touch. It is experimental control
software, not a safety-rated limit system. A physical normally-closed Z-max
switch is still recommended.

## Hardware and ownership

```text
E3 desktop
  |-- e3bridge://... -> Raspberry Pi -> laser controller -> real X/Y + laser
  `-- e3z://...      -> Raspberry Pi -> Creality board  -> Z motors + CR Touch
```

The Raspberry Pi hardware node owns both Pi-local serial connections. The
desktop never opens the Creality device and never creates a competing serial
owner. The Z service keeps one persistent Marlin connection after first use,
serializes complete high-level operations, and permits one authenticated E3 Z
client. A configured `auto` device selects one stable
`/dev/serial/by-id/*1a86_USB_Serial*` entry; ambiguity fails closed. The current
measured device can be configured explicitly as
`/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`. `/dev/ttyUSB0` is not
hard-coded.

The Creality board's X/Y coordinates are fictional because its X/Y motors are
disconnected. E3 never consumes those coordinates. Fixed-edge positioning
reuses the laser controller's existing Home / Capture XY operation. A material
probe point is transformed centrally using:

```text
probe_position = laser_axis_position + probe_offset
laser_axis_position = desired_probe_position - probe_offset
```

The resulting real laser-axis target is checked against the configured work
area and reached through the existing guarded `MachineService` jog and
motion-completion path.

## Verified firmware input

The implementation is based on operator-recorded physical behavior from an
Ender-3 S1 Pro reporting Marlin `2.0.8.26F4` at 115200 baud:

- deploy CR Touch: `M280 P0 S10`;
- stow CR Touch: `M280 P0 S90`;
- deployed, untouched `M119`: `z_min: open`;
- `G28` performs CR Touch Z homing;
- successful `M114`: approximately `Z:5.00` and `Count Z:2000` at
  `M92 Z400.00`;
- `M420 S0` changes the stale printer-mesh-influenced `Z:5.04` back to
  `Z:5.00`.

This Creality firmware does not support `M401` correctly, and `G30` acknowledges
without probe motion. Neither command is used. E3 does not change `M851`, the
stored mesh, or any EEPROM value.

## Reference state and ceilings

`z_known` starts false and is never persisted. E3/Pi restart, a new desktop Z
session, S1 reset, USB/network disconnect, STOP, timeout, malformed response,
failed probe test, or failed/interrupted home invalidates it. Reconnection does
not restore position knowledge.

An idle desktop-client close invalidates Z knowledge but leaves a healthy Pi
serial owner open. A client loss during an active Z operation, software STOP,
or uncertain Z-motion failure attempts Marlin `M112`, closes the serial
transport, and requires a fresh connection and home. That emergency request is
best effort and still requires physical-machine validation; it is not a
safety-rated stop.

The fixed-machine-reference application ceiling is configurable only up to the
measured hard maximum of `80.0 mm`. Ordinary absolute Z moves above the active
ceiling are rejected. A known-Z pre-home lift is absolute and clamped:

```text
target = min(current_z + prehome_lift, effective_safe_z_max)
```

When Z is unknown, the desktop must display **Z Position Unknown** and receive
the explicit **Gantry Is Clear — Continue** response before it sends any Z
work. The temporary cold-start clearance is then:

```gcode
M280 P0 S90
M420 S0
G91
G1 Z10 F480
G90
```

This exceptional relative move cannot be protected by the Z=80 ceiling because
there is not yet an upper limit switch. Canceling the warning performs no Z or
XY operation.

Material-surface homing requires a non-negative surface height `H` above the
fixed reference. Its reported-coordinate ceiling is visible in the UI and is:

```text
effective_safe_z_max = 80.0 - H
```

An input that leaves no verified range at the expected home position is
rejected before either controller moves.

## Homing state machine

One exclusive homing session performs:

1. Validate process hardware authority, `machine.allow_motion`, no laser job,
   no arm grant, reference mode, work-surface height, target transform, and
   effective ceiling.
2. Connect/authenticate the Pi Z service and verify its serial device identifies
   as Marlin on an Ender-3 S1 Pro.
3. If Z is unknown, require the desktop's explicit clearance confirmation.
4. Stow CR Touch, disable the old mesh, and perform either the clamped known-Z
   absolute lift or the confirmed exceptional unknown-Z relative lift.
5. Keep the Pi Z operation locked while E3 homes/parks real X/Y through the
   laser `MachineService`; for work-surface mode, move from that known pose to
   the offset-corrected laser-axis target and wait for motion completion.
6. Deploy CR Touch, query `M119`, require `z_min: open`, and stow it.
7. Send `G28`, accepting repeated `echo:busy: processing` only within the
   configured bounded homing timeout.
8. Immediately send `M420 S0`, query `M114`, and require logical Z to be
   `5.000 ± 0.250 mm` and below the active ceiling.
9. Publish `KNOWN` only after every check succeeds; otherwise publish `FAULT`
   with a specific cause and keep `z_known=false`.

The **Test CR Touch** action performs deploy -> `M119` -> require open -> stow.
It sends no Z move. A test failure invalidates Z knowledge.

## Configuration and remaining validation

Machine Manager exposes the Pi endpoint, serial timing, safe maximum, lift/feed,
home tolerance, CR Touch X/Y offsets, mechanical probe Z geometry, default work
probe point, reference mode, and surface height. The Pi-local hardware config
uses the same values but sets the endpoint to `auto` or the stable by-id device.
Both desktop and Pi configurations must explicitly permit motion.

`mechanical_probe_z_offset_mm = +7.747` is CAD geometry only. The optional
`laser_focus_offset_from_probe_mm` remains unset and autofocus movement remains
disabled until optical focus relative to the CR Touch trigger is physically
calibrated.

Automated tests use fake Marlin serial and fake real-X/Y services and never
move hardware. The integrated desktop -> Pi -> Creality sequence, disconnect
watchdog, emergency interruption, sign of the measured XY offsets, final Z
repeatability, 80 mm physical margin, and material-surface ceiling must still be
validated on the identified machine with the laser disabled and the physical
emergency stop immediately available.
