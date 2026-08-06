# Camera object detection and tracing

The desktop **Trace** inspector detects multiple physical objects in the
corrected camera image and converts reviewed outlines into normal E3 vector
objects.

> **Verification status:** the detector, proposed-vector preview, frozen-frame
> review lifecycle, object placement, and batch undo behavior pass synthetic
> and offscreen tests on Windows. The workflow has not been exercised end to
> end with the real C920, current physical calibration, or target hardware. See
> [../CURRENT_STATE.md](../CURRENT_STATE.md).

## Recommended label workflow

1. On the real machine, home and park at the saved camera pose. Keep the sheet
   flat and use even lighting. In safe simulation, load or generate a frozen
   test image instead.
2. Open **Trace** in the Inspector.
3. Choose **Auto detect**, **By color**, or **By contrast**. For a colored
   label, **Pick color** can sample the center of one object from the image.
4. Set the minimum/maximum area and minimum dimensions so dust, sheet edges,
   and unrelated artwork are excluded.
5. For a repeated label sheet, leave **Use grid** enabled. Enable **Infer gaps**
   only when you want suggested positions for missing or obscured cells.
6. Choose the vector output described below and set any **Border offset**.
7. Press **Detect objects**.
8. Review the numbered overlay and the **Geometry** column:
   - green: selected proposed vector output;
   - gray: unselected direct detection;
   - yellow or orange dashed: inferred grid position.
9. Check only outlines that are correct. Inferred cells are intentionally
   unchecked by default.
10. Press **Create objects**. The selected set is inserted as one undoable
    operation.

Changing a detection or output setting marks the result stale. Run **Detect
objects** again before creating objects.

Created shapes are ordinary project objects. They can be assigned to layers,
moved, resized, grouped, framed, and included in generated G-code.

## Vector output choices

- **Fitted rounded rectangles** fits center, width, height, rotation, and corner
  radius, then builds a clean analytic rounded-rectangle vector. The green
  preview is this proposed vector, not the jagged camera-pixel boundary. The
  **Geometry** column reports the fitted dimensions and radius. If a detection
  is not sufficiently rectangle-like, this mode falls back to its contour.
- **Simplified contours** follows the detected pixel boundary and removes
  points within the **Simplify tolerance**. A lower tolerance preserves more
  edge detail. This is polygon simplification; it does not turn an irregular
  edge into a smooth curve.
- **Exact contours** preserves the pixel-derived boundary without applying the
  simplification tolerance. It can contain many points and will naturally look
  stair-stepped when magnified.

The detector retains its observed contour separately from the proposed vector.
This keeps diagnostics honest while ensuring the preview and created object use
the same geometry. Non-rectangle contours are centered from their actual bounds
when created, so rotated or asymmetric traces do not shift after approval.

## Detection modes

- **Auto detect** evaluates color and contrast results and uses the stronger
  candidate set.
- **By color** segments a hue while tolerating brightness changes. Use **Pick
  color** when automatic hue selection chooses the wrong artwork.
- **By contrast** uses locally normalized luminance and works best when the
  target silhouette contrasts strongly with its backing surface.

## Regular-grid inference

Grid inference is conservative and intended for repeated objects of similar
size. Missing cells are visual suggestions, not observed edges, and are never
selected automatically. Do not use inference as a substitute for a clear view;
move the laser head away whenever practical.

## Frozen review behavior

Trace captures one corrected frame for a detection request. Live camera updates
are held while that result is being reviewed, so the displayed pixels remain
the pixels that produced the vectors. Clearing the preview, creating the
objects, changing the simulation image source, or stopping the controller
invalidates outstanding trace work. Late results from an older request are
ignored.

## Accuracy notes

Detection runs on the rectified camera image and outputs machine millimeters.
At the current default of 4 pixels/mm, one image pixel represents 0.25 mm.
Rasterization and thresholding can therefore move a fitted edge, dimension, or
radius by part of a pixel or more even on an ideal generated image. A clean
rounded vector is a geometric fit to those pixels; it is not evidence of
sub-pixel physical accuracy.

The desktop registers the center of each displayed camera pixel to the same
OpenCV/BedMapper coordinate used by the detector. At very high zoom, a smooth
curve will still cross different portions of the staircase-shaped edge pixels;
that is normal raster sampling, not an alternating machine-coordinate offset.
The rounded fitter likewise distinguishes a raster's pixel-center span from its
pixel count so an ideal integer-pixel radius is not underestimated by one pixel.

Real placement accuracy also depends on lens calibration, bed mapping, camera
pose, material height, focus, lighting, and resolution. A trace can be
geometrically neat but globally offset when the mapping is stale. Use a saved
cutting template when the intended cut geometry is already known; use Trace for
geometry that must be recovered from the image, and always review before
generating or running a job.
