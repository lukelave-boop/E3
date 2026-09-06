# Material height and camera geometry

Status: the two-height **calibration study** is implemented. Automatic probing
and applying this model to production tracing/jobs are not implemented. The
study is an evidence-collection step toward those features, not a claim that
height compensation is already active or physically verified.

## Fixed reference

The operator identifies the black border around the honeycomb, directly under
the probe after Home / park, as a reliable reference. This agrees with the
archived fixed-edge positioning design. Retain that datum; do not invent a new
probing location or probe into a honeycomb opening.

Use signed heights, positive upward from the border:

```text
border = 0
material top = honeycomb relative to border + total stack above honeycomb
material thickness = material top - honeycomb height - other spacers
camera-plane change = material top - calibration target top
```

For example only, if the honeycomb is 2 mm below the border and paper is
measured as 0.10 mm, the lower target is -1.90 mm. The same paper on a measured
12.00 mm spacer is +10.10 mm. A 6.00 mm spacer gives a check at +4.10 mm.
These are illustrative numbers, not configuration values or measured rig data.
An equal error in the arbitrary datum cancels from plane differences, but a
wrong spacing between target heights does not.

The operator can measure the border-to-honeycomb difference. Record its sign,
reference location, method, and uncertainty before automatic measurement is
enabled. Check that Home / park actually places the deployed probe over solid
border with the current XY/probe offsets. A support move invalidates that setup.

## Two-height calibration study

Open **Tools > Machine Setup > 3 · Bed mapping > Show manual / CSV fallback >
Material height calibration study**.

1. Fix camera mounting, manual focus, resolution, photography pose, and the
   machine XY reference. Targets must be flat and rigidly restrained. Maintain
   the verified focus/clearance procedure if laser-marking a raised target.
2. At the lower height, create and solve a fresh base map with the ordinary
   existing workflow. Before adding fine registration or a residual mesh, open
   the study, name the fixed reference, enter the actual top height relative to
   it, and choose **Save lower map**. Paper thickness is part of that height.
3. Repeat with a supported, parallel upper target, e.g. paper on a measured
   12 mm spacer. Choose **Save upper map**. The XY coordinates must describe the
   actual target locations; moving the same loose printed sheet upward without
   locating it in XY is not a valid second map.
4. **Fit and check camera model** fits one camera pose to the original measured
   points at both heights. It does not blend rectified images, independently
   normalized homographies, or residual meshes. The fixed lens intrinsics and
   already-undistorted observations define the image domain.
5. Acquire a third, independent base map near the middle of the height range
   and choose **Save independent check**. The check is excluded from fitting.
   It must lie in the middle 60% of the range. Using different XY positions is
   preferable when practical. Replacing either endpoint clears the old check.
6. With a captured Bed Mapping photograph, fit and use **Preview corrected
   photograph** at an assumed height between the two endpoints. This reprojects
   the existing photograph; it does not predict occluded surfaces, synthesize a
   new photograph, fix defocus, or move anything. Missing or failed check results
   remain visible during diagnostic preview.

Each plane requires at least nine unique observations, all original base-map
points accepted, at least 60% X and Y span, and 35% convex-hull coverage of the
configured work area. Endpoint heights must differ by at least 1 mm. All fit
observations must meet 0.50 mm RMS / 0.80 mm maximum error. The separate middle
check uses 0.30 mm RMS / 0.60 mm maximum error. These initial study gates reuse
existing calibration error scales; they are not a new accuracy guarantee.
Extrapolation outside the measured interval is rejected.

Evidence is saved atomically in the current optical profile's
`surface_height_calibration.json` using explicit schema 1. It contains original
point observations, signed heights, the named datum, source-map identities, and
camera/lens/machine/profile/work-area provenance. A future or malformed schema
is rejected, not silently replaced. Fit and check results are recomputed from
evidence when the study is reopened; no saved `valid` boolean grants authority.

The study never replaces `bed_calibration.json`, installs its model into
`BedMapper`, changes support coordinates, edits project objects, or modifies
G-code. The ordinary base-mapping actions used to acquire its inputs still
replace the active base map as before. Keep that distinction in mind before
returning to ordinary work: the last base map is valid at its own measured
height. The study is not yet a selector for the production calibration plane.

## Probe redesign and archived work

Source: tag `archive/s1pro-z-homing-safety-2026-09-05`, revision
`bffecea03a4ea8065bdcf3e3da75668974ed9593`. The archived homing state machine,
reference concepts, offset transform and test cases are useful; its separate
serial owner must not be restored over current `main`.

The archived record identifies Marlin 2.0.8.26F4 at 115200 baud. It records
CR Touch deployment with M280, an open probe indication through M119, and G28
homing followed by a logical Z near 5 mm. G30 acknowledged without moving and
M401 did not work correctly. On 2026-09-06 the operator reported no firmware
change. These are historical observations; they do not verify a new integrated
measurement path. Rehoming on the material and reading the new logical Z loses
the border reference and cannot establish thickness.

The new measurement contract must be conservative:

- Establish Z on the fixed border, once, with current session authority. Keep
  absolute position knowledge in the same reference throughout measurement.
- The firmware must execute bounded probing, stop at contact locally, fail on
  no contact, and return the measured trigger coordinate without re-zeroing.
  An ACK alone and ordinary M114 position readback are insufficient evidence.
  Desktop polling of M119 while commanding descent is not an acceptable stop
  mechanism, particularly for a probe with a short trigger signal.
- Treat the current firmware as unsupported for automatic thickness probing.
  Identify the exact Creality MCU/board, firmware build/configuration, probe
  input behavior and emergency-command support before selecting a firmware
  replacement or board-specific G38/G30 implementation. No generic firmware
  image, configuration, EEPROM write, or motion command is selected by this
  study. A declaration in a UI is not a substitute for recorded physical tests.
- Keep one Pi `CrealityControllerOwner` for both Air Assist and Z, one physical
  reader, typed command clients, and a shared session/fault identity. A reset or
  M112 on that board invalidates both Z knowledge and secondary fan knowledge.
- `MachineService` must own cross-controller admission: an exclusive complete
  measurement blocks job START, arming and competing motion; per-command serial
  locking alone is insufficient. Real XY uses the guarded primary path with
  explicit probe-offset, border footprint, full travel and clearance checks.
- Sequence: disarmed laser-off admission, border reference, verified retract,
  guarded XY positioning, repeated bounded contacts, repeatability check,
  retract/stow, then photography pose. Unknown-Z lift, upper clearance and
  controller stop behavior require physical verification; do not inherit the
  archived 80 mm ceiling or CAD probe offsets as newly verified limits.
- STOP acts on primary output promptly, without waiting behind the secondary
  ACK lock; secondary interruption/cleanup is bounded. Timeout, ambiguous
  response, boot/session change, USB/network interruption during probing, or
  failed contact invalidates the result. Do not retry or retract automatically
  using uncertain coordinates. Reconnection never restores Z knowledge.
- Bind each measurement to controller boot/session/reference epochs, machine
  geometry, support/datum identity, point coordinates, time and repeatability.
  Historical saved values are measurements to review, not live motion authority.

## Remaining production integration

After independent physical height checks, connect the accepted surface model to
an explicit material-plane selection in `AppContext`. Support detection and
support teaching must continue to use the support-height transform; material
images, clicks, overlays and tracing use the top-surface transform. Never
globally swap the active bed homography and accidentally move the honeycomb
frame with it. Keep height-specific fine/residual corrections separate until
their transfer across height is physically validated.

Add height/model/source identities to capture and trace provenance, preview
invalidation, project persistence (explicit migration), and execution preflight.
Recheck identity at START. Changing a plane requires recapture/regeneration,
not silent resizing of already placed geometry. Test both desktop and browser
consumers of shared calibration and preserve fixed machine output bounds.

Physical acceptance should cover independently measured lower, middle and upper
heights, all bed regions, repeated border homing, known gauge thicknesses, no
contact and interruption. Derive allowable probe-height error from the worst
observed XY sensitivity across the work area. One flat plane per capture is the
first target; tilted planes require at least three spaced measurements and
warped material requires more than a single-plane correction. Automatic focus
remains a separate feature with its own optical and mechanical verification.
