# Camera object detection and tracing

The desktop **Trace** inspector detects multiple physical objects in the corrected
camera image and converts reviewed outlines into normal E3 vector objects.

> **Verification status:** the feature is consolidated on `desktop-v1`. Its
> detection algorithms and batch undo command pass synthetic automated tests.
> The complete native workflow has not been exercised end to end with the real
> C920, current
> physical calibration, or target hardware. See
> [../CURRENT_STATE.md](../CURRENT_STATE.md).

## Recommended label workflow

1. Home and park at the saved camera pose.
2. Keep the sheet flat and use even lighting.
3. Open **Trace** in the Inspector.
4. Leave **Automatic color / contrast** selected, or press **Pick from image**
   and click near the center of one colored label.
5. Enable the regular row/column option for repeated labels.
6. Press **Detect objects**.
7. Review the numbered overlay:
   - green: direct selected detection;
   - gray: direct detection not selected;
   - yellow dashed: inferred missing/obscured grid position.
8. Check only outlines that are correct.
9. Choose a fitted rounded rectangle, smoothed contour, or exact contour.
10. Set any desired border offset and run detection again when settings change.
11. Press **Create vector objects**.

Created paths are ordinary project objects. They can be assigned to layers,
moved, resized, grouped, framed, and included in generated G-code. Creating a
whole detection set is one undoable operation.

Inferred grid positions are visual suggestions. They are not selected by
default and must be reviewed before vector creation.

## Detection modes

- **Automatic color / contrast** selects whichever result has the stronger set
  of candidates.
- **Colored objects** segments a hue while tolerating large brightness changes.
- **High-contrast objects** uses locally normalized luminance and works best
  when silhouettes contrast strongly with the backing surface.

## Regular-grid inference

Grid inference is conservative and intended for repeated labels or parts of a
similar size. Missing cells are shown as inferred candidates and are not
selected automatically. Inference should not be used as a substitute for a
clear camera view; move the laser head away whenever practical.

## Accuracy notes

Detection runs on the rectified camera image and outputs machine millimeters.
Its placement accuracy therefore depends on the current lens and bed
calibration, camera pose, material height, focus, and image resolution. A trace
can be geometrically neat but still be globally offset if the camera mapping is
stale.
