# Camera object detection and tracing

The desktop **Trace** inspector detects multiple physical objects in the
corrected camera image and converts reviewed outlines into normal E3 vector
objects.

> **Verification status:** the detector, proposed-vector preview, frozen-frame
> review lifecycle, object placement, and batch undo behavior pass synthetic
> and offscreen tests on Windows and Linux. Earlier Trace revisions were used
> with the real C920 and exposed the color, silhouette, and inconsistent-grid
> defects described in the repository state. The normalized-grid detector has
> been checked read-only against a current C920 corrected capture, but its new
> created-object result has not yet been physically cut. See
> [../CURRENT_STATE.md](../CURRENT_STATE.md).

## Recommended label workflow

1. On the real machine, home and park at the saved camera pose. Keep the sheet
   flat and use even lighting. In safe simulation, load or generate a frozen
   test image instead.
2. Open **Trace** in the Inspector.
3. Choose **Auto detect**, **By color**, or **By contrast**. To sample an
   object, press **Pick color**, wait for the button to read **Cancel color
   pick**, then click the center of the object in the corrected camera image.
   The button reads **Sampling…** while the frame is read and the swatch updates
   when sampling succeeds. A failure is shown directly in the Trace inspector.
4. Set the minimum/maximum area and minimum dimensions so dust, sheet edges,
   and unrelated artwork are excluded.
5. For a repeated label sheet, leave **Use grid** and **Make grid cells
   identical** enabled. Enable **Infer gaps** only when you want suggested
   positions for missing or obscured cells.
6. Choose the vector output described below and set any **Border offset**.
7. Press **Detect objects**.
8. Review the numbered overlay and the **Geometry** column:
   - green: selected proposed vector output;
   - gray: unselected direct detection;
   - yellow or orange dashed: inferred grid position.
9. Check only outlines that are correct. Inferred cells are intentionally
   unchecked by default. When the fitted grid is correct, **Select complete
   C × R grid** selects direct and inferred cells together for explicit review.
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
- **By color** uses hue for automatic/manual numeric targets. **Pick color**
  additionally retains the sampled BGR/Lab color, allowing neutral gray and
  low-saturation objects to be separated from a warmer or cooler backing
  surface while tolerating moderate lighting variation.
- **By contrast** evaluates both local detail and signed large-scale luminance.
  The large-scale candidates preserve a darker or lighter filled silhouette
  instead of fitting the expanded outer edge of a local contrast halo.

## Regular-grid inference

Grid inference is conservative and intended for repeated objects of similar
size. It first chooses the dominant mutually similar shape family, fits regular
row and column spacing, keeps only the best candidate in each cell, and rejects
unrelated or duplicate contours.

With **Fitted rounded rectangles** and **Make grid cells identical**, every
accepted direct cell and inferred gap uses one robust shared width, height,
corner radius, and rotation. Centers are snapped to the fitted lattice. The raw
direct observation remains in diagnostics and the Geometry tooltip, but the
green preview and created project rectangle use the normalized geometry. Grid
objects are named by row and column when created.

Missing cells are visual suggestions, not observed edges, and are never
selected automatically. Inspect the proposed complete lattice before using
**Select complete grid**. Do not use inference as a substitute for a clear
view; move the laser head away whenever practical. Grid normalization is
intentionally unavailable for simplified or exact contours because making
arbitrary pixel contours identical would require choosing a separate canonical
path.

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
