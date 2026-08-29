# Calibration workflow

Follow the canonical
[Permanent Camera Setup Runbook](../laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md)
for operator actions and tab transitions. This document is the supporting model,
measurement, and diagnostic reference.

There are two core camera models (lens and bed mapping), followed by fine
registration and independent accuracy validation stages.

## A. Lens calibration

Lens calibration estimates the C920 camera matrix and distortion coefficients. Print `targets/checkerboard_9x6_20mm.svg` at exactly 100% scale. Verify several squares with a ruler or caliper before using it.

The pattern contains 9 × 6 **inner corners**, which means 10 × 7 physical squares. Capture at least ten useful views:

- center, all four corners, and all four edges of the image
- several modest tilts in different directions
- the whole board visible and sharp
- the board physically flat

The application reports an explicit solve gate, its warning or rejection
reasons, RMS and mean reprojection error, image/edge coverage, pose diversity,
and the worst per-view reprojection errors. Low error alone is not enough if
every picture covers the same central region or presents nearly the same flat
pose; those degenerate datasets are rejected.

Lens calibration is tied to camera resolution. Machine Setup groups evidence by
exact dimensions and solves only the group matching the live camera. It never
silently combines or chooses among multiple resolution groups. The saved model can
scale intrinsics only to a matching aspect ratio, so keeping the configured
capture resolution fixed remains preferable.

Legacy evidence is cataloged from image headers, then preview-indexed in the
background at no more than `640 x 360`. Each index operation reads a selected
file once into a size-capped immutable encoded payload; its SHA-256 identity,
source dimensions, decoded pixels, checkerboard result, and quality result all
come from those exact bytes. The selected set also has a bounded aggregate byte
budget. This pass supplies responsive checkerboard, coverage, and image-quality
feedback; it is not calibration input. The solve repeats the exact-payload read
and decodes the selected originals at full resolution, then extracts corners
again. Path identity and a final exact-payload signature are checked around
both operations, so changed evidence is rejected without committing a stale
index or model.

Committed preview evidence includes a SHA-256 content identity in addition to
file size and modification time. Replacing bytes while preserving those two
metadata fields is therefore rejected by solve. Ordinary status refreshes do
not hash or decode image bodies: a legacy or malformed advisory record is shown
as pending and is repaired only by the owned background index operation. Use
**Re-index all captures** to rebuild every advisory row after external file
replacement or whenever solve reports an evidence-digest mismatch; the action
remains available whenever at least one capture exists.

The table labels sharpness as a preview measurement and displays the exact
measurement dimensions beside every score. A fresh capture is first written as
a lossless PNG to an atomic hidden staging path, then measured by the identical
exact-payload decode, resize, and quality pipeline used by re-indexing before it
is published. Compare scores only within the same displayed dimensions and
camera scene; the value is not a resolution-independent optical unit.

The Lens evidence table can delete one capture or clear all capture images with
confirmation without discarding the solved model. Clearing or replacing the
model instead marks the dependent bed map stale and blocks registration and
validation until that map is recreated.

## B. Bed mapping

Bed mapping estimates a 3 × 3 homography from the undistorted camera image to machine X/Y coordinates.

For a fresh map after moving the camera, the desktop can generate a dedicated
keyed 5×5 base pattern without using an existing homography. Prepare, review,
and run its guarded powered job on a clean restrained sheet. **Home / park,
capture and detect base grid** takes a
precision parked-bed capture and detects the exact 25 persisted targets.

Two interior crosses have distinct sizes. They uniquely orient the otherwise
symmetric grid across camera rotations and reflections. The detector rejects a
plain symmetric grid, missing or ambiguous keys, duplicate assignments, an
incomplete grid, a zero-power session, and any session whose target geometry or
generation settings no longer match. Review the numbered overlay before
application. All 25 points must be RANSAC inliers with at most `0.50 mm` RMS and
`0.80 mm` maximum fit error. Installation is transactional and clears
corrections tied to the previous base homography.

Manual fallback remains available:

1. Put the printer and bed at a repeatable photography pose. Once motion bring-up is complete, the **Park at camera pose** button sends `M5`, optionally homes, moves to the configured X/Y pose, and waits for the controller to report idle. Before that point, position the machine manually with laser power disabled.
2. Capture the fixed bed image in the Bed Mapping page only after the machine is stationary.
3. Create or expose marks at known machine coordinates.
4. Click the exact image center of each mark and enter its exact machine X/Y.
5. Use at least four points; nine or more spread across the work area are preferred.
6. Solve and inspect RMS, maximum error, and RANSAC inlier count.

The included `targets/bed_points_220x220.svg` is a coordinate reference template. Do not assume the physical page is aligned to machine coordinates unless it is located against measured stops. Laser-marked dots produced at commanded positions are generally a better machine-coordinate reference, but only create them after motion and laser behavior are independently understood.

A symmetric point grid does not establish axis direction: reversed X or Y
labels can still solve with an excellent residual. Verify both directions with
laser-off controller motion. The desktop Bed Mapping page provides explicit,
confirmation-gated X/Y reversal actions for repairing a proven mirrored saved
map.

Axis orientation is persisted with the solved bed calibration and displayed as
explicit NORMAL/OFF or REVERSED/ON controls. Older maps have no recorded flag;
the desktop infers their current orientation from the transform, labels that
state as legacy/inferred, and offers a separate confirmation action that records
it without mirroring points.

## Fixed bed pose on a bed-slinger

The Ender moves the workpiece in Y. The photograph is valid only when the bed is at the same repeatable Y pose used during mapping. The camera can remain stationary; once the job starts, the workpiece moves with the bed. Rigidly restrain the mapping surface and workpiece to the bed; any slip relative to the bed invalidates the camera-to-machine mapping.

## C. Fine registration

Fine registration is a verification layer on top of a solved bed homography.
The desktop generates eight crosses at locations deliberately separated from
the standard 5×5 grid, then measures their centers in a precision capture at
the homed/parked camera pose. Precision capture waits for the configured settle
period, discards buffered frames, and analyzes a burst of genuinely newer
frames. The default parked-bed profile captures 45 unique frames within an
eight-second deadline. Per-mark centers are screened with a median/MAD filter;
isolated frame outliers are rejected and excessive remaining jitter rejects the
measurement. Frames that remain inliers for every mark are ranked for clarity,
and final coordinates are the per-mark median of the best 15 stable frames.
Diagnostics identify the complete consensus subset and its sharpest
representative image. The older `sharpest_inlier_frame` single-frame experiment
and the full-inlier `median` strategy remain available for explicit comparison.
On a serial GRBL machine, the application reads the controller's current `$1`
step-idle delay after Home / park has completed, temporarily selects continuous
motor hold for the complete precision burst, and restores the saved delay
immediately after the last frame. It then explicitly disables the motors;
raw-frame sharpness scoring, lens correction, mark analysis, fitting, and
rendering all happen after release, so CPU work does not extend motor heating.
A process or power failure can interrupt software
restoration; the next serial connection performs a best-effort finite-delay
repair and explicit release, but inspect the controller setting and motor state
after any abnormal exit before continuing.
Configured camera controls are reapplied before the burst and, on V4L2 systems,
read back where supported. The eight-second profile deadline includes that
control transaction, settling, frame discard, and sample acquisition after the
capture gains exclusive camera ownership. The report records negotiated and
observed FPS and any sequence gaps so a driver that accepts the requested mode
but cannot sustain it remains visible. For each mark it records:

- the commanded machine coordinate
- the coordinate predicted by the current camera map
- the X/Y residual and total error
- the accepted sample count, rejected outlier count, and temporal jitter

After **Home / park, precision capture**, use **Recapture without homing** while
the machine and workpiece remain untouched. If no-home repeats are stable but
home-first repeats move, investigate homing and the photography pose. If
no-home repeats also vary, investigate camera rigidity, focus, lighting,
locked controls, vibration, and mark detection. The no-home action is enabled
only after Machine Setup establishes the camera pose in the current dialog.

If the residual vectors agree within the bounded scatter thresholds, the
desktop may apply one explicit translation to the camera map. The cumulative
translation is limited to 5 mm and can be reset. If errors change with bed
location, the translation remains unavailable. The same reviewed capture is
also evaluated as a possible full-bed homography refinement. That separate
action requires at least seven geometric inliers, broad X/Y and convex-hull bed
coverage, sub-millimetre fit residuals, preserved orientation and local scale,
and no more than 8 mm modeled movement anywhere on a 5×5 bed grid. It never
applies automatically and retains the previous solved map for explicit reset.
If those gates fail, correct the full calibration or physical setup.

Up to two visibly obstructed or falsely detected crosses may be excluded during
review; all excluded measurements remain visible and persisted. At least six
marks must remain, and the remaining detections still have to pass confidence,
residual, and scatter limits. Future target layouts keep the lower registration
point away from the configured head/park corner that obstructed the first real
capture.

A zero-power registration path is not accepted as a marked calibration capture. The
powered mark job must be prepared separately and remains subject to ordinary
job confirmation, arming, homing, bounds, stop, and laser sequencing controls.

## D. Independent accuracy validation

After applying a reviewed correction, use the desktop Accuracy validation tab.
It generates five holdout crosses at locations distinct from the eight points
used by fine registration, then captures and scores them without fitting another
map. It uses the same precision burst, temporal outlier rejection, jitter gate,
and optional no-home diagnostic recapture as fine registration. All five
detections are mandatory. A pass requires no more than `0.5 mm`
RMS error and `1.0 mm` maximum error with confident detections. The prepared job
is bound to the active bed-map matrix; changing calibration invalidates the
session and requires a new job. Validation is diagnostic and cannot apply any
correction.

Dense local correction uses three independent persisted sessions: the 5×5 fit,
the 4×4 interstitial validation, and the shifted 4×4 confirmation. Preparing a
later validation no longer replaces the target metadata needed to recapture the
5×5 fit. Use the capture button labeled for the grid that is physically present;
the application rejects mismatched session types instead of silently detecting
the most recently prepared grid. All three dense capture actions use the same
parked-bed precision burst, temporal rejection, and stable clarity-ranked
consensus strategy described above.

## Height/parallax limitation

A planar homography is exact only for the plane used during calibration. A workpiece top surface above or below that plane appears shifted, especially near image edges. Until height compensation is implemented, perform bed mapping on the actual material top plane or keep material top surfaces at the calibrated height.

## Recalibrate when

- the camera or mount moves
- focus or resolution changes
- the bed reference surface changes height
- the photography pose changes
- alignment residuals or repeat tests worsen
