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

## Fixed bed pose on a bed-slinger

The Ender moves the workpiece in Y. The photograph is valid only when the bed is at the same repeatable Y pose used during mapping. The camera can remain stationary; once the job starts, the workpiece moves with the bed.

## Height/parallax limitation

A planar homography is exact only for the plane used during calibration. A workpiece top surface above or below that plane appears shifted, especially near image edges. Until height compensation is implemented, perform bed mapping on the actual material top plane or keep material top surfaces at the calibrated height.

## Recalibrate when

- the camera or mount moves
- focus or resolution changes
- the bed reference surface changes height
- the photography pose changes
- alignment residuals or repeat tests worsen
