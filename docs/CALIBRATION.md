# Calibration workflow

There are two separate calibrations.

## A. Lens calibration

Lens calibration estimates the C920 camera matrix and distortion coefficients. Print `targets/checkerboard_9x6_20mm.svg` at exactly 100% scale. Verify several squares with a ruler or caliper before using it.

The pattern contains 9 × 6 **inner corners**, which means 10 × 7 physical squares. Capture at least ten useful views:

- center, all four corners, and all four edges of the image
- several modest tilts in different directions
- the whole board visible and sharp
- the board physically flat

The application reports RMS and mean reprojection error. Low error alone is not enough if every picture covers the same central region. Good image coverage is important.

Lens calibration is tied to camera resolution. The code can scale intrinsic values for a different resolution, but keeping capture resolution fixed is preferable.

## B. Bed mapping

Bed mapping estimates a 3 × 3 homography from the undistorted camera image to machine X/Y coordinates.

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
the standard 5×5 grid, then measures their centers in a fresh homed/parked
capture. For each mark it records:

- the commanded machine coordinate
- the coordinate predicted by the current camera map
- the X/Y residual and total error

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

A dry registration path is not accepted as a marked calibration capture. The
powered mark job must be prepared separately and remains subject to ordinary
job confirmation, arming, homing, bounds, stop, and laser sequencing controls.

## D. Independent accuracy validation

After applying a reviewed correction, use the desktop Accuracy validation tab.
It generates five holdout crosses at locations distinct from the eight points
used by fine registration, then captures and scores them without fitting another
map. All five detections are mandatory. A pass requires no more than `0.5 mm`
RMS error and `1.0 mm` maximum error with confident detections. The prepared job
is bound to the active bed-map matrix; changing calibration invalidates the
session and requires a new job. Validation is diagnostic and cannot apply any
correction.

## Height/parallax limitation

A planar homography is exact only for the plane used during calibration. A workpiece top surface above or below that plane appears shifted, especially near image edges. Until height compensation is implemented, perform bed mapping on the actual material top plane or keep material top surfaces at the calibrated height.

## Recalibrate when

- the camera or mount moves
- focus or resolution changes
- the bed reference surface changes height
- the photography pose changes
- alignment residuals or repeat tests worsen
