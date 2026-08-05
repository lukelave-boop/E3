# Ender-3 S1 Pro and Creality laser hardware bring-up

The accessory family can be used with several printer/controller combinations. Do not assume the serial protocol, firmware behavior, power scale, or coordinate limits from a product page.

## Phase 1: identify devices without motion

With the laser output physically disconnected or otherwise made incapable of emission where practical:

```bash
lsusb
ls -l /dev/serial/by-id/
python tools/controller_probe.py --port /dev/serial/by-id/YOUR_CONTROLLER
```

Save the full startup banner and responses to `$I`, `$$`, and `M115`.

## Phase 2: establish protocol and power scale

Determine whether the controller is GRBL-like or Marlin-like and whether the expected laser command is `M3` or `M4`. Determine the configured maximum spindle/laser power value rather than assuming `255`, `1000`, or another scale.

Keep `laser.default_power` and `laser.frame_power` at zero during this phase.

## Phase 3: verify axes with laser disabled

Before enabling `machine.allow_motion`:

- confirm homing behavior
- confirm X and Y positive directions
- confirm machine zero and usable bed coordinates
- confirm the laser module cannot collide with frame components
- determine the actual laser-spot reach, which may differ from nozzle reach
- determine the camera photography position

Update `machine.work_area` to the verified laser-spot rectangle, not the advertised print volume.

## Phase 4: dry-motion software test

Set `machine.allow_motion` to `true` while leaving low-power framing disabled. Start with `./run-hardware.sh`, generate a small rectangular frame well inside the verified limits, and watch a complete dry-motion run. The generated dry-frame program contains no `M3` or `M4` laser-enable command.

Check that every UI direction, camera direction, and physical axis direction agrees.

## Phase 5: minimum controlled optical test

Only inside the completed enclosure and with extraction/interlocks operational, establish the lowest reliable visible marking level on a sacrificial material. Do not begin with the repository's example power number; it is not a material recommendation.

After the scale is known, record it in a versioned machine profile and keep the local profile conservative.

## Information to capture for the repository

- controller startup banner
- protocol and firmware version
- serial baud rate and stable by-id path
- power maximum and laser mode
- homing command and coordinate origin
- verified X/Y laser-spot limits
- photo pose
- measured camera-to-bed height
- laser-head/nozzle offset if relevant
- dry-run and marking-test results
