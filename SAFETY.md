# Safety requirements

For the current five-tab calibration order, use the canonical
[Permanent Camera Setup Runbook](laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md).
This file defines safety boundaries and does not replace that operator sequence.

This repository controls equipment capable of causing permanent eye injury, fire, toxic smoke exposure, and mechanical injury. It is experimental software and is not a safety-rated control system.

A Raspberry Pi network hardware node does not change that boundary. Wi-Fi, the
Pi, TCP, and the USB links can fail independently. On loss of its authenticated
controller client the node makes a best-effort controller-specific realtime stop
and `M5`, but that software cleanup is not a substitute for a physical emergency
stop, interlock, or removal of hazardous energy. Never expose the bridge directly
to the public Internet, and never rely on reconnection or software status as proof
that motion or laser energy has stopped.

## Required physical safeguards

Use a fully enclosed laser area designed for the laser wavelength and power. The enclosure should include a hardware door interlock that removes laser-enable power, a readily accessible hardware emergency stop that removes hazardous energy, appropriate extraction exhausting safely outdoors, a nonflammable spoil surface, and a suitable fire extinguisher within reach.

Do not depend on the webcam, browser, operating system, USB connection, G-code sender, or a software `M5` command for personnel protection. Any of them can freeze, disconnect, reset, or behave unexpectedly.

The raw Live Monitor is observational only. Its image may be stale, delayed,
cropped, disconnected, or misleading and grants no motion, laser, calibration,
interlock, or safety authority. Never use it as proof that motion or laser
emission has stopped or that the area is safe to enter.

## Before every real job

- Confirm that the intended material is appropriate for blue-diode laser processing and does not produce prohibited or highly hazardous fumes.
- Confirm extraction flow and the exhaust destination.
- Remove flammable debris from the enclosure.
- Confirm focus, workpiece restraint, cable clearance, and unobstructed axis travel.
- Treat **Detect objects** as a motion command on hardware: it automatically
  homes and parks before capturing. Keep the complete travel path clear even
  though tracing never requests positive laser output.
- Trace may display camera pixels beyond the camera-calibration rectangle.
  Only the separate green guarded-output outline indicates software-authorized
  output; red observations outside it remain blocked. Camera visibility is not
  evidence of laser reach. The local hardware profile contains an explicit,
  fixed machine-coordinate polygon for honeycomb-bound jobs. It is not inferred
  or moved by live detection; ordinary jobs retain the guarded machine rectangle.
- A honeycomb-local project displays the complete 190 mm cutting surface. The
  configured green polygon is 210 × 210 mm, extending 10 mm beyond each support
  edge in the accepted pose. Generation rejects powered or laser-off controller
  motion that leaves that polygon; the display never grants reach. Honeycomb-local jobs
  require an accepted automatic four-edge teaching image and bind its four
  measured corners, rigid pose, and complete bed-map digest. A legacy schema-1
  or three-hint visual reference is not execution evidence. **START JOB** rechecks the
  prepared support/map/output-polygon identity but does not capture another
  camera image. Do not move the honeycomb or workpiece after tracing or job
  generation; if either moves, recapture, re-detect, and regenerate. Camera
  alignment is not safety-rated and does not replace physical restraint, end
  stops, or a zero-power frame.
- Inspect the dedicated generated-job Preview, including maximum planned power,
  powered/travel motion, bounds, and warnings. Preview is a review aid, not a
  safety function and not a substitute for reviewing a zero-power frame.
- Verify the generated bounds and the machine coordinate origin.
- Verify that Home / park succeeds after every controller connection or reset.
  The desktop repeats this homing/parking preflight automatically at job start;
  a failed preflight blocks motion and arming.
- Desktop Jog is a laser-off positioning aid, not a safety-rated limit finder.
  It requires a current Home / park position, begins each move with `M5`, and
  intentionally does not enforce the configured work-area rectangle so that
  rectangle can be measured. Start with a small step and conservative feed,
  keep the path clear, and use the physical emergency stop for an actual
  emergency. If an axis was moved by hand, Home / park again before jogging.
- A successful powered serial job automatically issues `M5`, waits behind all
  accepted toolpath motion, homes, parks at the configured camera pose, waits
  for that move to finish, restores the normal GRBL step-idle delay if
  necessary, and releases the motors. It does not change
  fan or coolant state. Keep the complete homing and parking path clear until
  the job reports completion. Stops, job failures, emergency actions,
  disconnects, and zero-power jobs do not initiate this convenience motion.
- On GRBL, Home / park records the active `G54`-`G59` and `G92` offsets. Every
  subsequent absolute-motion job re-reads them and is blocked if they changed
  after the parked camera alignment. This detects coordinate-state drift but
  does not prove that an unchanged offset matches the one used for calibration.
- Verify any configured laser-spot offset and inspect the generated controller
  bounds; a wrong sign moves the beam farther from the intended location.
- For a fresh automatic bed map, inspect the keyed 25-cross Preview and its
  complete bounds. Use only a previously established visible-
  marking power on a clean, restrained sacrificial sheet. Review all numbered
  detections before application and then verify both controller directions with
  laser-off motion. Automatic orientation and a low fit residual do not prove
  the physical origin, usable travel, focus plane, or beam location.
- For fine registration, review the zero-power eight-cross path first. Use only a
  previously established visible-marking power on a clean, restrained
  sacrificial surface; inspect every detected point before applying a result.
- Every powered post-map Machine Setup job—fine registration, dense 5×5 fit,
  five-cross accuracy validation, 4×4 mesh validation, and shifted
  confirmation—requires a current accepted automatic four-corner support. Its
  powered segments are generated inside that support and its complete program
  remains inside the configured machine area. The prepared session binds the
  exact program, support, and bed map; **START JOB** rechecks containment and the
  immutable prepared binding before arming. These are software guardrails,
  not proof of physical containment or a substitute for zero-power review.
- The powered keyed base-map job is the sole setup bootstrap exception because
  that map is required before support corners can be expressed in machine
  coordinates. It remains machine-bounded. Review its exact pattern and use a
  rigidly restrained sacrificial sheet that covers every displayed target.
- Parked-bed precision capture temporarily keeps GRBL motors energized and
  explicitly disables them after its final frame, using FluidNC motor-disable
  or standard GRBL sleep/reset as available, while preserving the prior
  step-idle setting. Releasing motors invalidates trusted position and requires
  another Home / park before subsequent hardware work. Motor
  holding is not a safety brake. After an application crash or power fault,
  verify that the axes and controller idle-delay setting returned to the
  expected state before touching or operating the machine.
- Continuous GRBL hold (`$1=255`) is reserved for the scoped camera window.
  Every serial GRBL connection explicitly releases the motors. If it finds a
  stale `255`, it first restores configured `machine.grbl_step_idle_delay_ms`;
  the default for this profile is 250 ms. A controller that does not report
  `$1` is rejected after a best-effort finite-delay restore and motor release.
- For accuracy validation, inspect the separate five-cross holdout path in
  Preview before running its powered job. It remains subject to the automatic
  four-corner support binding above. Validation reports camera-to-laser error
  but is not a safety test or proof that unattended operation is safe.
- Keep the operator present for the entire job.

## Software guardrails in this repository

The default project profile is simulation-only. Real serial access requires
the exact boolean `--hardware` gate; real motion requires the exact boolean
`machine.allow_motion` gate and a successfully
homed coordinate reference for the current connection; the desktop performs one
laser-off Home before each hardware job, then arms only after that preflight. Positive
laser commands require temporary arming bounded to 1–600 seconds; arming is
cleared after every job;
low-power framing is disabled; streamed jobs are restricted to a conservative
G-code subset; generated paths are checked against either the guarded machine
rectangle or the exact fixed polygon explicitly bound to a honeycomb job;
rapid travel is blocked while laser state is active; and `M5` is
placed before travel and at job end.

MachineService revalidates those gates and numeric ceilings even when settings
are constructed or mutated programmatically. It also re-parses the immutable
program lines and recomputes their digest, motion/power flags, and safety
profile at both Arm and Start. A forged, stale, or altered preflight object is
rejected before controller output, and rejection does not suppress cleanup
`M5`.

Ordinary serial operations own their complete command/ack exchange, and
multi-command Home / park and camera-hold sequences cannot interleave with
another ordinary controller operation. Connect is not reported ready until its
controller cleanup finishes. A GRBL Connect may unlock only an exact consumed
`error:9` rejection of its laser-off command, only when Home / park is mandatory,
and must then receive acknowledgement for a second `M5`. It performs no motion
and does not establish coordinate trust. Software stop remains outside this
serialization so it can still request interruption immediately. These are
protocol-integrity guards, not emergency-stop or functional-safety mechanisms.

Fine registration may apply only a reviewed, multi-point global camera-map
translation no larger than 5 mm. Low-confidence, excessive, or
position-dependent results are rejected as translations. A separate reviewed
full-bed homography refinement requires at least seven geometric inliers, broad
bed coverage, bounded residuals, preserved orientation/local scale, and bounded
modeled movement across the bed. It is confirmation-gated and retains the prior
map for rollback. These are alignment guardrails, not safety functions or proof
of beam location.

Independent accuracy validation uses a separate guarded five-cross job. It
requires every holdout detection, rejects zero-power-only and stale-map sessions, and
reports fixed RMS/maximum acceptance limits without modifying calibration.
Precision capture and **Recapture without homing** are measurement operations:
they do not arm the laser or send motion. The recapture control is available
only after the current Machine Setup session establishes the camera pose. Its
jitter and control-readback diagnostics improve rejection of unreliable
measurements, but are not safety functions or proof of beam location.

These controls reduce accidental commands. They do not meet any functional-safety performance level and do not make an open Class 4 laser safe.

The desktop graphical Preview is constructed from the exact finalized G-code
text and is invalidated when the project changes. Its display and playback
controls cannot edit that text or bypass generation, homing, motion, arming,
bounds, streamed-command, rapid-with-laser, stop, or disconnect checks. A
window-modal Preview is the only normal desktop location for **START JOB**; that
control closes Preview and enters the same guarded run path so software STOP is
available during execution. This workflow is not safety-rated, and a
correct-looking Preview is not proof that the controller, calibration, focus,
workpiece, or physical beam path is correct.

Fill and raster scanlines can create much longer powered jobs than outlines.
Raster overscan motion is emitted only with the laser off and is included in
controller-space bounds validation. Imported grayscale images use deterministic
ordered dithering at the configured exact physical line pitch and scan angle in
the active project frame, with area prefiltering when that pitch minifies the
source. Honeycomb-local rows are rigidly placed in machine coordinates before
the controller checks them. Their lead-in, white gaps, and lead-out are
emitted at engraving feed with the laser off; rows are never passed through
nearest-path reordering. Raster planning accepts only bounded PNG/JPEG/BMP
metadata, file size, and conservative decoded bytes before full decode, then
caps aggregate row, sample, vector-edge, span, and command work. Programs
beyond the 250,000 streamed-command limit are rejected. Inspect the powered
pattern and maximum power in Preview. Nonzero Power Correction deliberately
changes commanded `S` values near modeled corners or insufficiently overscanned
raster edges while retaining GRBL `M4`; positive correction can increase the
commanded value above the layer's base power up to the configured controller
limit. Treat every correction change as a new material/process setting, review
the exact Preview, and validate it only with small supervised sacrificial tests.
A Start Here program
intentionally omits earlier motion. It is prepared only at a complete move
boundary, begins with `G21`, `G90`, and `M5`, positions with the laser off, and
does not bypass the ordinary homing, arming, or execution gates.

## Camera sensor protection

The C920 is not a laser power sensor or a protective viewing system. Direct or specular reflected laser energy can damage an image sensor. Position and shield it so the lens cannot receive a direct beam or a strong specular reflection. Do not place an unverified optical filter over the camera and assume it provides personnel protection.

## Emergency behavior

In an actual emergency, use the hardware emergency stop or disconnect power. The red software stop sends controller-specific reset/stop commands and `M5`, but software and serial delivery can fail.
