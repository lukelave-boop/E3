# Safety requirements

This repository controls equipment capable of causing permanent eye injury, fire, toxic smoke exposure, and mechanical injury. It is experimental software and is not a safety-rated control system.

## Required physical safeguards

Use a fully enclosed laser area designed for the laser wavelength and power. The enclosure should include a hardware door interlock that removes laser-enable power, a readily accessible hardware emergency stop that removes hazardous energy, appropriate extraction exhausting safely outdoors, a nonflammable spoil surface, and a suitable fire extinguisher within reach.

Do not depend on the webcam, browser, operating system, USB connection, G-code sender, or a software `M5` command for personnel protection. Any of them can freeze, disconnect, reset, or behave unexpectedly.

## Before every real job

- Confirm that the intended material is appropriate for blue-diode laser processing and does not produce prohibited or highly hazardous fumes.
- Confirm extraction flow and the exhaust destination.
- Remove flammable debris from the enclosure.
- Confirm focus, workpiece restraint, cable clearance, and unobstructed axis travel.
- Run the generated dry framing pass first; it contains no `M3` or `M4` laser-enable command.
- Inspect the dedicated generated-job Preview, including maximum planned power,
  powered/travel motion, bounds, and warnings. Preview is a review aid, not a
  safety function and not a substitute for dry framing.
- Verify the generated bounds and the machine coordinate origin.
- Verify that Home / park succeeds after every controller connection or reset.
  The desktop repeats this homing/parking preflight automatically at job start;
  a failed preflight blocks motion and arming.
- On GRBL, Home / park records the active `G54`-`G59` and `G92` offsets. Every
  subsequent absolute-motion job re-reads them and is blocked if they changed
  after the parked camera alignment. This detects coordinate-state drift but
  does not prove that an unchanged offset matches the one used for calibration.
- Verify any configured laser-spot offset and inspect the generated controller
  bounds; a wrong sign moves the beam farther from the intended location.
- For fine registration, run the dry eight-cross path first. Use only a
  previously established visible-marking power on a clean, restrained
  sacrificial surface; inspect every detected point before applying a result.
- For accuracy validation, run the separate dry five-cross holdout path before
  preparing its powered job. Validation reports camera-to-laser error but is not
  a safety test or proof that unattended operation is safe.
- Keep the operator present for the entire job.

## Software guardrails in this repository

The default project profile is simulation-only. Real serial access requires
`--hardware`; real motion requires `machine.allow_motion` and a successfully
homed coordinate reference for the current connection; the desktop homes and
parks before each hardware job, then arms only after that preflight. Positive
laser commands require temporary arming; arming is cleared after every job;
low-power framing is disabled; streamed jobs are restricted to a conservative
G-code subset; generated paths are checked against a configured rectangular
work area; rapid travel is blocked while laser state is active; and `M5` is
placed before travel and at job end.

Fine registration may apply only a reviewed, multi-point global camera-map
translation no larger than 5 mm. Low-confidence, excessive, or
position-dependent results are rejected as translations. A separate reviewed
full-bed homography refinement requires at least seven geometric inliers, broad
bed coverage, bounded residuals, preserved orientation/local scale, and bounded
modeled movement across the bed. It is confirmation-gated and retains the prior
map for rollback. These are alignment guardrails, not safety functions or proof
of beam location.

Independent accuracy validation uses a separate guarded five-cross job. It
requires every holdout detection, rejects dry-only and stale-map sessions, and
reports fixed RMS/maximum acceptance limits without modifying calibration.

These controls reduce accidental commands. They do not meet any functional-safety performance level and do not make an open Class 4 laser safe.

The desktop graphical Preview is constructed from the exact finalized G-code
text and is invalidated when the project changes. Its display and playback
controls cannot edit that text or bypass generation, homing, motion, arming,
bounds, streamed-command, rapid-with-laser, stop, or disconnect checks. A
correct-looking Preview is not proof that the controller, calibration, focus,
workpiece, or physical beam path is correct.

Fill and raster scanlines can create much longer powered jobs than outlines.
Raster overscan motion is emitted only with the laser off and is included in
controller-space bounds validation. Imported images use a binary 50% threshold;
inspect the powered pattern and maximum power in Preview. A Start Here program
intentionally omits earlier motion. It is prepared only at a complete move
boundary, begins with `G21`, `G90`, and `M5`, positions with the laser off, and
does not bypass the ordinary dry-frame, homing, arming, or execution gates.

## Camera sensor protection

The C920 is not a laser power sensor or a protective viewing system. Direct or specular reflected laser energy can damage an image sensor. Position and shield it so the lens cannot receive a direct beam or a strong specular reflection. Do not place an unverified optical filter over the camera and assume it provides personnel protection.

## Emergency behavior

In an actual emergency, use the hardware emergency stop or disconnect power. The red software stop sends controller-specific reset/stop commands and `M5`, but software and serial delivery can fail.
