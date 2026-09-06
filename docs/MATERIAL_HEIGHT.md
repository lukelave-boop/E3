# Material height and camera geometry

Status: the two-height **calibration study** and an operator-positioned CR Touch
measurement path are implemented. The latter uses the existing Creality
controller and Marlin commands, through its shared Air Assist connection. Both
require physical acceptance beyond the first successful border reference.
Applying height compensation to production
tracing/jobs is still unfinished.

## Measure with the existing probe

Open **Tools > Machine Setup > 7 · Material height**. The desktop and Pi E3
service must both contain this feature; an older Pi reports that its service
needs updating. This is an E3 application update, not a controller firmware
update. The Pi must already have its secondary Creality connection configured
as `secondary_marlin_fan`; the probe uses that same owner and serial reader.
This first workflow positions XY through existing Home / park and Jog controls.
It does not automatically select a material point from the camera.

1. Keep the homing/parking path clear and use **Home / park**. The probe must be
   above the solid black border. Reference admission checks the current primary
   carriage position against the configured photo position; that numerical
   check cannot identify the physical border.
2. Enter the available absolute **Z clearance** after border homing. The entry
   is constrained to 20–80 mm; this is a software range, not a physically
   verified gantry limit. It must clear the material and fit the actual gantry.
   Enter **-1.5 mm** for this operator-reported honeycomb-to-border offset.
3. Confirm the surface/clearance and that the Creality XY motors are disconnected,
   then choose **Reference border**. E3 keeps the primary laser off, checks the
   secondary identity, deploys the CR Touch and checks `z_min: open`, stows it,
   runs the archived `G28` / `M420 S0` homing sequence and verifies logical Z
   near 5 mm. This position readback is only a homing check, not the measured
   border height. Three subsequent G30 contacts establish the actual border
   measurement. E3 uses G30's own stow/return between readings, checking M114
   before the next contact, then makes one full Z clearance lift after all three.
   It does not add a full clearance lift immediately after border homing.
4. Use the panel's existing laser-off **Jog** path to put the raised probe over
   solid material. Watch the physical probe location. Do not Home again: a new
   primary home invalidates the reference. The first workflow records carriage
   XY, not an inferred physical probe XY based on unverified offsets.
5. Confirm the solid material and clearance, then choose **Measure Z offset**.
   E3 takes three contacts without G28 or coordinate resets, subtracts the mean
   border contact, and reports signed surface height and thickness above the
   honeycomb. Thickness includes any paper and spacers. The probe returns to
   the same verified clearance and stays at that XY point.

Each contact set must agree within 0.10 mm. G30 must report exactly one finite
`Bed X / Y / Z` measurement for the requested virtual centre. An ACK alone,
position readback, error, reset, missing contact, failed retract or inconsistent
readings produces no accepted height. A contact outside -2 mm through
`clearance - 10 mm` is rejected before another host-commanded move. G30 still
uses the controller's own probe travel and trigger rules; these host checks do
not change its internal descent or establish physical clearance.

Between readings, reported logical Z must be at least 1 mm above the reported
contact and no higher than the configured clearance (0.05 mm readback tolerance).
This conservative software check is not a newly validated physical clearance.
Missing or out-of-range readback blocks another contact. The final full lift
still requires M400 completion and M114 readback before the result is accepted.

The operator observed a successful border reference on 2026-09-06 but reported
excessive full-height lifts. The shortened sequence removes those host-added
lifts; it does not change G30's internal fast/slow probing or the initial G28
homing cycle. The published firmware fixes the fast/slow sequence in
`run_z_probe`; G30 has no slow-only parameter. Therefore this is not exactly
one fast touch followed by three slow touches. The shorter sequence still
needs physical testing; three accepted G30 reports are retained.

The explicit `G30 X110 Y110` avoids depending on the Creality board's fictional
current XY. Only the primary controller moves real XY. Creality's published
[G30 implementation](https://github.com/CrealityOfficial/Ender-3S1/blob/s1_pro_plus/Marlin/src/gcode/probe/G30.cpp)
silently returns when its virtual point is unreachable. That is a possible
explanation for the archived ACK-only test, not proof of the cause or a match
to the installed binary. Its published
[probe implementation](https://github.com/CrealityOfficial/Ender-3S1/blob/s1_pro_plus/Marlin/src/module/probe.cpp)
also includes an early-trigger height check. Start physical acceptance with the
border, then a thin known gauge. A full 12 mm measurement range is **not yet
verified**; a failed tall-material contact must not be interpreted as a height.
No firmware image, G38, G92, M851 change or EEPROM write is used.

`M84 S0` temporarily disables Creality's idle motor timeout so the Z reference
can be retained while the operator jogs primary XY. No EEPROM save is sent and
this operation does not subsequently disable Z motors. This motor hold is not a
brake. Reset, disconnect, stop, primary rehome or an owner-generation change
invalidates the reference. Changing clearance or support offset requires a new
border reference. Results are session observations, not persisted motion or
calibration authority. They do not change laser focus or install camera correction.

Pi admission covers the complete probe operation, blocking competing motion and
job START. Each exchange checks the operation deadline, primary session, STOP
epoch and monitoring socket. Detected socket closure cancels probing; a silent
network partition is bounded by the 110-second operation deadline rather than
guaranteed immediate detection. Primary STOP runs first. Independent secondary
M112/close cleanup bypasses the ACK lock and targets the original secondary
generation. Delivery and emergency-parser behavior require physical testing.
No automatic retract or retry follows uncertain motion. Reconnect and reference
again after investigating a failure.

Secondary errors identify the failed command. Timeout details distinguish no
reply from a partial response, and the Pi service journal records the last eight
bounded response lines under `Secondary exchange failed`. The first operator
attempt on 0.7.21 timed out before visible pin/Z movement; its older error did
not identify the command, so the cause remains unconfirmed. The diagnostic
follow-up only requires updating the Pi service; the 0.7.21 desktop displays
the improved error without rebuilding.

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

On 2026-09-06 the operator measured the honeycomb top **1.5 mm below the black
border**, so this rig's reported signed support offset is **-1.5 mm**. Its lower
paper plane is `-1.5 + paper_thickness_mm`; the same paper on a 12 mm spacer is
`10.5 + paper_thickness_mm`. Paper thickness has not been supplied. The method,
uncertainty and probe repeatability have not been recorded. This report is not
a verified automatic measurement and is not a global configuration default.

Record the reference location, measurement method and uncertainty before
automatic measurement is enabled. Check that Home / park places the deployed probe over solid
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
  Measure and retain the border's actual trigger coordinate using the same
  measurement operation as the material. The difference between those two
  contact coordinates establishes top height and cancels a constant probe
  offset. Do not assume the post-home logical Z or stored nozzle offset is the
  contact coordinate. Repeat the border measurement after a reset/rehome.
- The firmware must execute bounded probing, stop at contact locally, fail on
  no contact, and return the measured trigger coordinate without re-zeroing.
  An ACK alone and ordinary M114 position readback are insufficient evidence.
  Desktop polling of M119 while commanding descent is not an acceptable stop
  mechanism, particularly for a probe with a short trigger signal.
- Use the current Creality controller, probe and firmware. The operator-positioned
  implementation above is ready for physical command verification; its explicit
  virtual-centre G30 is not yet physically verified. Do not infer that an ACK-only
  observation requires replacing firmware. A declaration in a UI is not a
  substitute for recorded physical tests.
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
