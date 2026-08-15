# Permanent Camera Setup Runbook

Applies to E3 Positioning System `0.2.0.dev0` and its five numbered
**Machine Setup** tabs.

This is the canonical operator sequence. Follow the tabs from left to right.
Technical background belongs in `docs/MACHINE_SETUP.md`; it must not add an
unlisted calibration step or interrupt a tab transition described here.

## Safety Boundary

- Software calibration and STOP controls are not safety-rated.
- Keep the laser incapable of emission except during a reviewed, guarded
  powered marking job.
- Use only a marking power already tested on the same sacrificial material.
- Rigidly restrain every sheet to the moving bed.
- Never move the camera, change focus or resolution, or move a calibration
  sheet between marking and its capture.
- A prepared calibration job is already generated. After reviewing its Job
  Preview, close the Preview and use **Start**. Do not click the main
  **Generate** button; it generates from the project canvas and replaces the
  prepared calibration job. Preview's
  **Start/Play** controls animate the preview only; they do not run hardware.
- **Start** attempts to connect automatically while the controller is offline.
  A failed attempt leaves the prepared job available; do not click **Generate**.
- After a powered calibration job completes successfully, the application
  automatically reopens its Setup tab, runs the Home / park precision capture,
  and displays the scored result. A stopped or failed job never starts capture.

## Before Step 1

1. Mount the camera permanently and independently of the bed and gantry.
2. Choose and save the locked focus on the main Camera panel, then restart.
   The active calibration profile is keyed by resolution and focus. A new
   profile needs Steps 1-5 once; returning to a previously completed focus and
   restarting restores that focus's full calibration stack.
3. Confirm the configured machine/output envelope and camera pose match dated
   physical verification. Those limits are independent from the permanent
   honeycomb job coordinates. The active E3 profile's printed zero-to-far-ruler
   span is 191 × 191 mm. Do not reposition the integrated honeycomb to satisfy a
   software rectangle.
4. Connect the camera. Connect the controller only when a step explicitly
   requires Home, park, commanded motion, or a guarded marking job.
5. Open **Tools > Machine Setup**.

## 1. Camera

**Goal:** establish one sharp, stable, repeatable camera mode.

1. Click **Refresh raw preview**.
2. Confirm the complete usable bed area is visible and the parked head does not
   hide any required calibration target.
3. Click **Apply all configured controls**.
4. Confirm the configured resolution, focus, exposure, and white-balance
   controls are reported as current and verified.
5. Do not change camera focus, resolution, controls, or mounting after this
   point.

**Continue to Step 2 when:** the preview is stable and calibration readiness is
`READY`.

## 2. Lens

**Goal:** solve camera lens distortion at the current camera resolution.

1. Print `targets/checkerboard_9x6_20mm.svg` at 100 percent and verify its
   square size.
2. Keep the checkerboard itself rigid and flat. Hold the rigid board at modest
   angles to the camera; do not bend it.
3. Capture the complete checkerboard at the center, edges, and corners of the
   image, with tilts in two different directions and at more than one distance.
4. Wait for background indexing to finish. Delete only clearly bad captures.
5. Click **Solve current-resolution calibration**.
6. Review the gate, RMS and mean error, coverage, pose diversity, and worst
   capture diagnostics. `REJECT` does not qualify. `PASS` or an explicitly
   accepted `WARNING` may continue when calibration readiness says `READY`.

**Continue to Step 3 when:** a solved model is active for the live resolution
and calibration readiness is `READY`.

## 3. Bed Mapping

**Goal:** map corrected camera pixels to machine coordinates without trusting
an old camera map.

The powered base-map job is the sole support-containment bootstrap exception:
the image-to-machine map must exist before honeycomb corners can be expressed
in machine coordinates. It remains bounded by the configured machine area. The
restrained sacrificial sheet must cover the exact reviewed 25-cross pattern.

1. Secure a clean sacrificial sheet at the final calibration surface height.
2. Enter a previously tested visible-marking power
   and click **Prepare powered base-map job**.
3. Review the exact 25-cross Preview, close it, then use **Start**. The desktop
   submits the prepared powered job immediately and creates its one-use
   authorization internally. Do not click **Generate**.
4. Wait for every completion phase to finish. After the burn, do not move the
   sheet, restart the app, prepare another job, or click **Generate**. Setup
   reopens automatically, homes/parks, captures, and detects the base grid.
   Use **Home / park, capture and detect base grid** only to retry.
5. Confirm all 25 numbered circles sit on their crosses. The two differently
   sized keyed marks resolve rotation and reflection. Apply only when the dialog
   reports 25/25 detected, 25/25 inliers, RMS no greater than `0.50 mm`, and
   maximum error no greater than `0.80 mm`.
6. Click **Yes** in **Apply fresh base map**.
7. With the new map active, click **Capture view**. The
   orange outline is the configured camera/work rectangle; the green outline
   is the smaller laser-output rectangle after the configured boundary margin
   and any configured laser-spot offset.
   Compare the 10 mm machine grid (coordinate labels every 40 mm) to both rigid
   honeycomb rulers. If origin, scale, or an edge disagrees, stop and correct
   the camera-to-machine calibration evidence before repeating Step 3. Do not
   resize the machine-output envelope merely to make it visually match the
   permanent honeycomb. The ruler overlay is a diagnostic, not automatic proof
   of laser reach.
8. To establish the permanent honeycomb's rigid job frame, confirm **Configured
    physical ruler span** matches the printed zero-to-far-ruler distance. It is
    read-only in Setup and comes from the machine profile; the active E3 value is
    `191.000 mm`. Then click **Detect honeycomb automatically**. Vision segments the
    dominant rectangle, independently fits all four cutting-surface edges, and
    maps their intersections through the active bed map. It preserves their
    order as origin, +X, opposite, and +Y. **Physical ruler span** defines the
    nominal local width and height; it does not replace the four observed
    corners or fabricate measured edge lengths. Printed tick recognition is not
    required. Review and accept the magenta outline and fit report. Acceptance
    stores the exact reviewed teaching image, its four corners, and the digests
    that bind it to the schema-2 support and complete bed map.

    At Start, registration uses fresh, spatially distributed features only from
    inside the accepted cutting surface and projects the four taught corners as
    the pose measurement. Missing, stale, insufficiently covered, ambiguous,
    moved, scaled, or non-square
    evidence blocks arming.

    **Fallback: detect with 3 hints** is display/diagnostic only. Click anywhere
    along the X ruler, near the shared zero, and anywhere along the Y ruler. The
    clicks only identify search corridors; they are not measured points. If the
    fit fails, nothing is saved. A saved three-hint result remains legacy visual
    evidence and cannot authorize honeycomb-local or powered post-map work.

    **These three hints do not calibrate the camera or machine and are not used
    as ruler coordinates.** Only the accepted automatic four-edge result
    establishes the execution-verifiable honeycomb-local job frame: ruler zero
    is `(0,0)`, +X follows the bottom edge, and +Y follows the left edge. New
    projects use the configured physical span; the active E3 profile is
    X0..191, Y0..191. The live camera, grid, Trace, and object
    coordinates share that frame. Green shows the independently verified
    machine-output envelope mapped into honeycomb coordinates; red observations
    outside green remain unchecked and blocked. The support frame cannot expand
    machine authority. The keyed 25-point map remains the sole camera-to-machine
    calibration. The integrated honeycomb is not removed or reseated during
    normal setup. Re-record its support only after service physically disturbs it
    or after a changed bed map invalidates the binding.

9. Open **Coordinate audit**, classify the honeycomb as **Permanent /
   immovable**, and save that classification. Repeat Home/park + audit capture
   without moving the fixture to assess machine/camera repeatability. Do not use
   a movable-support reseating study for this installation. Full-support laser
   reach remains a separate pre-production physical audit.

**Continue directly to Step 4 when:** the reviewed base map has been applied,
the ruler overlay has no unexplained origin, scale, or crop discrepancy, and a
current accepted automatic four-corner honeycomb frame has been recorded.
Legacy schema-1 and three-hint visual references do not qualify for any powered
post-map Machine Setup job, including a machine-coordinate calibration job.
The separate laser-off direction/bounds check is a pre-production hardware
check, not a hidden Step 3-to-Step 4 gate. Do not change **Reverse X mapping** or
**Reverse Y mapping** merely because the camera image is rotated.

If detection fails after a successful burn, do not reburn or move the sheet.
Preserve the sheet and saved capture while diagnosing the detector. A verified
saved capture receipt can be reanalyzed after an application restart.

The unnumbered **Coordinate audit** tab may be used at any point for
configuration and transform diagnostics. It does not add a calibration step.
Refresh, report copy, and point inspection are read-only; its capture button
uses the same laser-off Home/park motion as **Capture view**. A clicked point
shows the corrected camera pixel, machine coordinate, honeycomb-local
coordinate, and spot-corrected carriage coordinate. Existing 190 mm support
files are intentionally marked stale under the 191 mm profile; run fresh
automatic four-edge detection rather than stretching old evidence.

For this machine, save **Permanent / immovable** as the fixture classification.
Registration readiness and full-support reachability are separate. Reach limits
may be entered from a dated laser-off audit or recorded from trusted jog
positions only while the process is started with `--laser-lockout`. The saved
limits do not change GRBL, machine bounds, G-code, arming, laser power, or output
authority. Cyan shows carriage-derived beam reach and blue shows the intersection
of measured reach with the configured work and guarded-output authorities.

## 4. Fine Registration

**Goal:** measure and, when justified, correct the remaining camera-to-machine
error at eight positions not used by the base map.

Every powered segment in this job must fit the accepted automatic four-corner
honeycomb support, and the complete program must fit the configured machine
area. The prepared session binds the exact G-code, support, and bed map. Start
rechecks containment and the immutable support/map binding, performs one
laser-off Home, and starts without another camera capture or camera-position park.

1. Confirm the accepted automatic support is current, then secure a clean
   sacrificial sheet at the same calibrated surface height.
2. Enter a previously tested marking power,
   and click **Prepare powered mark job**.
3. Review, close Preview, and run the guarded powered job. Do not click
   **Generate**.
4. Do not move the marked sheet. Setup reopens automatically, homes/parks,
   captures, and scores the eight marks. Use **Home / park, precision capture**
   only to retry.
5. Review all eight detections and their X/Y residuals. Exclude a point only
   when the physical mark is genuinely obstructed, damaged, or misdetected.
6. Apply **Apply reviewed translation** only when it is enabled and the vectors
   show one consistent offset. Apply **Apply reviewed full-bed map** only when
   its independent fit gates pass and the translation workflow is not being
   stacked with it. If errors are already acceptably small, applying no
   correction is valid; Step 5 provides the independent verdict.
7. Applying or resetting a translation/full-bed refinement changes the bed-map
   identity and clears the support. If you changed the map, return to Step 3's
   ruler-overlay capture and accept a new automatic four-edge support before
   preparing any other powered post-map job.

**Continue to Step 5 when:** the eight-point result has been reviewed and any
chosen eligible correction has finished applying.

Dense 5 x 5 local correction is optional troubleshooting for repeatable,
position-dependent residuals. It is not part of the normal five-step path. Its
powered 5×5 fit, 4×4 validation, and shifted confirmation all require
support-contained targets and the same exact support/map Start check. Applying
or resetting a mesh/refinement clears the support; re-detect it automatically
before the next powered stage.

## 5. Accuracy Validation

**Goal:** independently test the final map at five positions that were not used
to fit it.

The powered five-cross session requires the current accepted automatic
four-corner support. Its powered segments must fit that polygon, its complete
program remains machine-bounded, and Start rechecks the exact support/map
binding before the single laser-off Home and arming sequence.

1. Confirm the accepted automatic support is current, then secure a clean
   sacrificial sheet at the calibrated surface height.
2. Enter a previously tested marking power,
   and click **Prepare powered validation job**.
3. Review, close Preview, and run the guarded powered job. Do not click
   **Generate**.
4. Do not move the sheet. Setup reopens automatically, homes/parks, captures,
   and scores the five marks. Use **Home / park, precision capture** only to
   retry.
5. Review all five holdout measurements.

**Setup is complete when:** Accuracy validation reports `PASS`, all five marks
are confidently detected, RMS error is no greater than `0.50 mm`, and maximum
error is no greater than `1.00 mm`.

If validation fails, do not tune the validation result. Return to Step 4 for a
coherent global residual, or Step 3 when errors vary by position or the base
mapping is suspect.

## Before Normal Production

These checks do not interrupt the five calibration tabs, but they are required
before relying on normal production output:

1. Keep the laser incapable of emission. If either axis was moved by hand,
   reconnect when required and run **Home / park** before commanding motion.
2. Start E3 with `--laser-lockout`. In the main **Machine** panel, choose a
   small Jog step (`0.1 mm` or `1 mm`) and a conservative speed, then use **X−**,
   **X+**, **Y−**, and **Y+** to confirm direction and measure the largest clearly
   safe carriage positions. Each press begins with `M5` and uses the trusted
   Home / park position. Jogging intentionally does not apply the configured
   work-area rectangle because it is the measurement control; never seek a
   crash point. After each endpoint, reopen Coordinate Audit and choose the
   corresponding **Record current** action, or enter the complete dated limits.
   Review the resulting cyan measured reach and blue combined usable fixture
   area. Do not move the permanent honeycomb.
3. A fresh keyed base map records normal generated-coordinate labels
   automatically. Change a Reverse toggle only when repeated physical evidence
   proves that a legacy or manually labeled map is mirrored.
4. Back up the accepted lens, bed, support, fixture-reach, registration, and
   validation files.

## Capture Hold Contract

Base mapping, dense mapping, fine registration, accuracy validation, and parked
trace captures hold the steppers from Home/park through acquisition of the last
raw camera frame. The normal idle setting is restored and motors are released
before lens correction, sharpness scoring, detection, fitting, or rendering.
Ordinary live preview, still images, and handheld lens-checkerboard captures do
not depend on a machine pose and do not request this hold.

## Fast Troubleshooting

| Symptom | Correct action |
| --- | --- |
| Prepared calibration job is visible but **Start** is disabled | Wait for active preparation to finish. **Start** attempts an offline connection automatically; do not click **Generate**. |
| **Generate** says the project has no enabled output paths | Return to the numbered Setup tab and prepare that calibration job again. |
| A powered grid was burned but detection failed | Do not move or reburn the sheet; diagnose or reanalyze the saved capture. |
| Trace says a label is outside even though it sits on the honeycomb | Compare the Step 3 ruler overlay. The visible honeycomb and mapped guarded machine-output polygon are separate limits; visibility does not grant output authority. |
| A restart says the exact job did not run | Preserve the sheet and session. A matching verified saved capture receipt may be recovered; never fabricate or edit it. |
| Lens RMS improved but the gate rejects | Add edge, distance, and two-axis pose diversity; do not optimize only the error number. |
| Step 5 fails | Return to Step 4 or Step 3 based on the residual pattern; validation itself never applies a correction. |
