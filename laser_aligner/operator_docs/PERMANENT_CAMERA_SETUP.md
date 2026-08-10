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
3. Confirm the configured work area and camera pose match the physical rig.
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
7. With the new map active, click **Home / park, capture ruler overlay**. The
   orange outline is the configured camera/work rectangle; the green outline
   is the smaller laser-output rectangle after the configured boundary margin
   and any configured laser-spot offset.
   Compare the 10 mm machine grid (coordinate labels every 40 mm) to both rigid
   honeycomb rulers. If origin, scale, or an edge disagrees, stop: correct the work-area
   configuration, restart, create or load a matching project, and repeat Step
   3. The ruler is a diagnostic reference, not automatic proof of laser reach.
8. To keep a detected outline of the movable honeycomb rulers visible for
    later comparison, set **Detected ruler span** to the printed span (normally
    `190 mm`), then click **Detect ruler reference (3 hints)**. Click roughly
    near the first endpoint, shared corner, and other endpoint. The clicks only
    define search corridors. Vision must independently fit both ruler baselines,
    detect repeated 1 mm tick marks, verify their spacing and perpendicularity,
    and snap the endpoints to detected ticks. Review the detected magenta outline
    and fit report before saving it. If those checks fail, nothing is saved.

    **These three hints do not calibrate the camera or machine and are not used
    as ruler coordinates.** The detected result is an optional visual annotation
    only. It cannot change the laser-burned
    25-point bed map, machine work area, guarded laser limits, Trace selection,
    template matching, generated paths, preflight, arming, or execution. The
    keyed 25-point map remains the sole camera-to-machine calibration. Clear or
    re-record the reference whenever the movable honeycomb shifts.

**Continue directly to Step 4 when:** the reviewed base map has been applied
and the ruler overlay has no unexplained origin, scale, or crop discrepancy.
Recording the optional detected honeycomb annotation is not a completion
gate.
The separate laser-off direction/bounds check is a pre-production hardware
check, not a hidden Step 3-to-Step 4 gate. Do not change **Reverse X mapping** or
**Reverse Y mapping** merely because the camera image is rotated.

If detection fails after a successful burn, do not reburn or move the sheet.
Preserve the sheet and saved capture while diagnosing the detector. A verified
saved capture receipt can be reanalyzed after an application restart.

## 4. Fine Registration

**Goal:** measure and, when justified, correct the remaining camera-to-machine
error at eight positions not used by the base map.

1. Secure a clean sacrificial sheet at the same calibrated surface height.
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

**Continue to Step 5 when:** the eight-point result has been reviewed and any
chosen eligible correction has finished applying.

Dense 5 x 5 local correction is optional troubleshooting for repeatable,
position-dependent residuals. It is not part of the normal five-step path.

## 5. Accuracy Validation

**Goal:** independently test the final map at five positions that were not used
to fit it.

1. Secure a clean sacrificial sheet at the calibrated surface height.
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
2. In the main **Machine** panel, choose a small Jog step (`0.1 mm` or `1 mm`)
   and a conservative speed, then use **X−**, **X+**, **Y−**, and **Y+** to
   confirm direction and measure the physical limits. Each press begins with
   `M5` and uses the trusted Home / park position. Jogging intentionally does
   not apply the configured work-area rectangle, because this is the control
   used to determine whether that rectangle is correct; approach a mechanical
   endpoint with progressively smaller steps.
3. A fresh keyed base map records normal generated-coordinate labels
   automatically. Change a Reverse toggle only when repeated physical evidence
   proves that a legacy or manually labeled map is mirrored.
4. Back up the accepted lens, bed, registration, and validation files.

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
| Trace says a label is outside even though it sits on the honeycomb | Compare the Step 3 ruler overlay. The honeycomb support, camera/work crop, and guarded laser-output rectangle are separate limits. |
| A restart says the exact job did not run | Preserve the sheet and session. A matching verified saved capture receipt may be recovered; never fabricate or edit it. |
| Lens RMS improved but the gate rejects | Add edge, distance, and two-axis pose diversity; do not optimize only the error number. |
| Step 5 fails | Return to Step 4 or Step 3 based on the residual pattern; validation itself never applies a correction. |
