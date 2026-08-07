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
- measure the laser spot relative to the controller's carriage/tool-reference
  position on both axes
- determine the camera photography position

Update `machine.work_area` to the verified laser-spot rectangle, not the advertised print volume.

Record the measured vector under `laser.spot_offset_x_mm` and
`laser.spot_offset_y_mm`. The convention is:

```text
physical laser spot = commanded controller position + configured spot offset
```

For example, a module whose spot is 28 mm left and 8 mm toward negative Y from
the controller reference uses `X = -28`, `Y = -8`. The generator subtracts
this vector from desired bed coordinates. Generated G-code records both the
desired spot bounds and shifted controller bounds; both must pass work-area
validation. Do not estimate these values from bracket drawings or from one
misplaced cut. If the bed mapping was solved from crosses burned by the same
laser under known controller coordinates, that mapping already references the
laser spot and the separate spot offset should normally remain zero.

A symmetric grid can refine scale and perspective but cannot prove which end
is controller X-min/X-max or Y-min/Y-max. Verify axis signs with laser-off
motion. If saved point labels are mirrored, **Machine Setup → Bed mapping** can
reverse X or Y point labels and re-solve; this is not a substitute for the
physical direction check.

## Phase 4: dry-motion software test

Set `machine.allow_motion` to `true` while leaving low-power framing disabled. Start with `./run-hardware.sh`, generate a small rectangular frame well inside the verified limits, and watch a complete dry-motion run. The generated dry-frame program contains no `M3` or `M4` laser-enable command.

Check that every UI direction, camera direction, and physical axis direction
agrees. After configuring a spot offset, verify a dry frame first and then a
minimum-power mark at a measured interior point before any normal job.

The desktop automatically runs `M5`, homes, parks at the configured camera
pose, waits for idle, and only then arms and starts each hardware job. The core
rejects serial motion or arming when that coordinate-reference preflight has
not succeeded in the current connection.

Home/park allows at least six seconds for acknowledgement of each setup and
park command; homing and final motion completion retain their separate
120-second limits. The GRBL `$H` acknowledgement received after its endstop
sequence allows the procedure to continue. After the park move, `G4 P0`
supplies a planner-synchronization
barrier without adding a dwell; this avoids depending on optional or
controller-specific realtime `<Idle...>` status reports. This allowance is
scoped to the coordinate-reference preflight. Normal streamed job lines still
use `machine.read_timeout`, so a slow setup response does not silently weaken
failure detection throughout a job. Timeout messages name the command that
failed.

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
