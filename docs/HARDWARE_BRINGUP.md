# Ender-3 S1 Pro and Creality laser hardware bring-up

This is the hardware-identification and motion-safety reference. For the actual
five-tab calibration sequence, follow the canonical
[Permanent Camera Setup Runbook](../laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md).

The accessory family can be used with several printer/controller combinations. Do not assume the serial protocol, firmware behavior, power scale, or coordinate limits from a product page.

## Phase 1: identify devices without motion

With the laser output physically disconnected or otherwise made incapable of emission where practical:

```bash
lsusb
ls -l /dev/serial/by-id/
python tools/controller_probe.py --port /dev/serial/by-id/YOUR_CONTROLLER
```

Save the full startup banner and responses to `$I`, `$$`, and `M115`.

For GRBL, record `$20`, `$22`, `$23`, `$27`, `$130`, `$131`, and `$132` from
the same dated readback. Controller max-travel settings are configuration
evidence, not proof of laser-spot reach. With emission physically disabled,
home and approach each usable boundary slowly from an interior point; record
the controller position and separately measure the physical laser spot. Set
`machine.work_area` only to the resulting verified rectangle, then redo the
base map, fine registration, and independent accuracy validation.

## Phase 2: establish protocol and power scale

Determine whether the controller is GRBL-like or Marlin-like and whether the expected laser command is `M3` or `M4`. Determine the configured maximum spindle/laser power value rather than assuming `255`, `1000`, or another scale.

Keep `laser.default_power` and `laser.frame_power` at zero during this phase.

### Identified secondary FAN2 controller

The Air Assist fan is not on the primary laser/motion controller. The primary
remains a separate GRBL device, and its exact Pi-local persistent serial path is
still unconfirmed. Never use the secondary path as `machine.port`.

Physical bring-up identified a Creality/Marlin secondary controller at
`/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`, 115200 baud. Sending exactly
`M106 S255` physically started FAN2. The intended OFF command is exactly
`M106 S0`, but its physical result is still pending confirmation. This mapping
uses no `P` parameter and never uses `M107`. Do not generalize this evidence to a
different controller, endpoint, fan output, firmware, or baudrate.

Keep the saved mapping and production output disabled until `M106 S0` is
physically shown to stop FAN2 and the startup-known-OFF, layer transitions,
normal completion, primary-first STOP, secondary failure, and Pi-restart
cleanup paths are recorded. The Pi must be the one persistent owner of this
secondary connection. Windows stores the endpoint only as opaque configuration
and never opens it.

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

After a camera remount, **Fresh automatic base mapping (keyed 5 x 5)** can
prepare a new machine-coordinate pattern without trusting the obsolete camera
map. The two larger interior crosses resolve image rotation/reflection, but the
operator must still inspect the exact Preview bounds, review
all 25 detected centers, and perform the laser-off direction check. With the
current local `10..210 mm` work area and `5 mm` generator boundary margin, the
configured guarded output range is `15..205 mm` before any laser-spot-offset
intersection for ordinary machine-coordinate jobs. The active local hardware
profile separately records an operator-confirmed fixed 210 × 210 mm guarded
polygon for jobs bound to the current honeycomb pose; it maps to local
`X-10..200, Y-10..200` and is not moved by later vision detections. The base-grid
centers are `40, 75, 110, 145, 180 mm` on
each axis. These values are operator-reported/derived and are not physically
verified controller limits until the direction and boundary checks are
recorded.

## Phase 4: direction and coordinate check

Set `machine.allow_motion` to `true` and restart the normal desktop with
`./run-desktop.sh`. Use laser-off Jog to check that every UI direction, camera
direction, and physical axis direction agrees. After configuring a spot offset,
verify it with a minimum-power mark at a measured interior point before normal
production.

The desktop automatically runs `M5`, homes, parks at the configured camera
pose, waits for idle, and only then arms and starts each hardware job. The core
rejects serial motion or arming when that coordinate-reference preflight has
not succeeded in the current connection.

With `machine.home_and_release_after_powered_job` enabled, a successfully
completed powered job also performs `M5`, waits for all accepted toolpath
motion to finish, homes, returns to the configured camera pose, waits for the
park move to finish, restores the configured normal GRBL idle delay if
necessary, and lets standard GRBL step-idle behavior release the motors. A
secondary Air Assist program keeps strict
non-comment `E3AIRASSIST <mapping-sha256> ON|OFF` instructions in its immutable
program bytes. The Pi intercepts them before the primary GRBL stream and
acknowledges primary `M5` before secondary `M106 S0` and completion motion. Keep
the complete homing and parking path clear until the job reports completion.
The desktop reports the finishing phase explicitly and raises an error if a
completion command fails. This post-job motion is not attempted after a stop,
failure, emergency action, disconnect, or zero-power job.

`machine.grbl_step_idle_delay_ms` is the normal non-camera value and defaults
to 250 ms for this profile. The application temporarily uses `$1=255` only
during parked camera capture. Because `$1` persists in controller storage, a
crash can leave that hold active across power cycles; the next serial connection
detects exactly `255` and restores the configured normal value. Standard GRBL
then releases the motors after that finite `$1` delay. `$SLP` is not used as a
motor-release action because it sleeps the controller. A same-primary Air Assist
mapping establishes its trusted OFF command after laser off. Separately, the
Pi's `CrealityControllerOwner` establishes and acknowledges secondary
`M106 S0`; Windows connection or detach causes no fan transition. STOP keeps
primary reset/`M5` authoritative and performs only bounded independent secondary
cleanup. Pi restart marks active work interrupted, never resumes it, and
attempts acknowledged OFF.

For GRBL controllers, this preflight also reads `$G` and `$#` after parking and
records the active `G54`-`G59` workspace, its XYZ offset, and `G92`. Immediately
before an absolute-motion job, the same read-only queries are repeated. A
workspace or offset change blocks the job and invalidates the coordinate
reference. The recorded values are included in machine status and the
controller log for alignment diagnosis. An unchanged value is consistency
evidence for that Home/park cycle; it does not independently prove that an old
bed calibration was made with the same controller state.

Home/park allows at least six seconds for acknowledgement of each setup and
park command; homing and final motion completion retain their separate
120-second limits. The GRBL `$H` acknowledgement received after its endstop
sequence allows the procedure to continue. After the park move, `G4 P0.01`
supplies a short positive planner-synchronization dwell; this avoids depending on optional or
controller-specific realtime `<Idle...>` status reports. This allowance is
scoped to the coordinate-reference preflight. Normal streamed job lines still
use `machine.read_timeout`, so a slow setup response does not silently weaken
failure detection throughout a job. Timeout messages name the command that
failed.

Fine-registration and accuracy-validation capture allow a six-second physical
settling interval after Home/park returns, then wait for three additional new
camera frames with a six-second freshness timeout. The observed slow
GRBL-derived controller can acknowledge its queued park sequence before the bed
has physically stopped; fresh-but-mid-motion and cached pre-motion frames are
both excluded from analysis.

## Phase 5: minimum controlled optical test

Only inside the completed enclosure and with extraction/interlocks operational, establish the lowest reliable visible marking level on a sacrificial material. Do not begin with the repository's example power number; it is not a material recommendation.

After the scale is known, record it in a versioned machine profile and keep the local profile conservative.

## Information to capture for the repository

- primary and secondary controller startup banners
- primary and secondary protocol and firmware versions
- separate serial baud rates and stable by-id paths; never substitute the
  secondary FAN2 identity for the unconfirmed primary GRBL path
- power maximum and laser mode
- homing command and coordinate origin
- verified X/Y laser-spot limits
- photo pose
- measured camera-to-bed height
- laser-head/nozzle offset if relevant
- zero-power motion and marking-test results
- dated secondary FAN2 ON and OFF commands, acknowledgements, and observed
  physical results; ON `M106 S255` is recorded, while OFF `M106 S0` is pending
- startup-known-OFF, layer transition, normal completion, primary-first STOP,
  secondary timeout/failure, and Pi-restart/no-resume cleanup results
- twenty remove/reseat cycles against the physical locating stops, automatically
  re-detecting the honeycomb each time; record four-corner displacement and
  require every corner to remain within the chosen repeatability limit before
  treating the support pose as production-repeatable
