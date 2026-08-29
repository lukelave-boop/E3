# Camera object detection and tracing

The desktop **Trace** inspector detects physical objects in the corrected
camera image and converts reviewed outlines into either normal E3 vector objects
or one locked, non-cutting **Stock boundary** used for camera-aligned layout.

> **Verification status:** the detector, proposed-vector preview, frozen-frame
> review lifecycle, object placement, and batch undo behavior pass automated
> and offscreen tests on Windows and Linux. A 2026-08-09 C920 capture exposed a
> false high-confidence trace of the bright seams between dark labels. The exact
> saved frame now replays as 14 observed full label bodies plus the two genuinely
> occluded cells in a 2 x 8 grid, at about 80.54 x 21.52 mm. That repaired result
> has been inspected read-only in the desktop overlay but has not yet been
> physically cut. A later 2026-08-14 C920 recovery frame replays as a direct
> 2 x 7 grid: the accepted empty-honeycomb teaching image leaves its four
> already-open cells unchecked and selects the ten remaining labels. See
> [../CURRENT_STATE.md](../CURRENT_STATE.md). The new non-grid Auto orchestrator,
> hierarchy-safe degenerate-contour pruning, strategy fallback, and independent-
> root isolation have deterministic automated coverage, but the Coleman stencil
> failure frame has not been rerun on the physical camera. Do not treat this
> implementation status as a physical-camera validation.

## Recommended Trace workflow

1. Home and park at the saved camera pose. Keep the sheet or stock flat and use
   even lighting.
2. Open **Trace** in the Inspector.
3. Choose **Purpose**:
   - **Cut geometry** creates ordinary project objects that may be assigned to
     output layers;
   - **Stock boundary (layout only)** creates exactly one locked construction
     outline and never sends that outline to the laser.
4. Choose **Auto detect**, **By color**, or **By contrast**. With **Use grid**
   off, Auto tries authoritative raster Otsu with both dark and light foreground
   and conditionally tries Color when the image has a strong coherent chromatic
   target; the result message says which strategy won. Auto's hue, threshold,
   and polarity controls are inactive because Auto owns those choices. With
   **By contrast** and **Use grid** off, choose automatic Otsu or a manual 0–255
   threshold and whether dark or light pixels are foreground; hue controls are
   inactive because this path is literal raster vectorization. To sample for
   **By color**, press **Pick color**, wait for the button to read **Cancel color
   pick**, then click the center of the object in the corrected camera image.
   The button reads **Sampling…** while the frame is read and the swatch updates
   when sampling succeeds. A failure is shown directly in the Trace inspector.
5. Set the minimum/maximum area and minimum dimensions so dust, sheet edges,
   and unrelated artwork are excluded.
6. For a repeated label sheet, leave **Use grid** and **Make grid cells
   identical** enabled. Enable **Infer gaps** only when you want suggested
   positions for missing or obscured cells. Disable grid inference when tracing
   one parent-stock outline.
7. Choose the vector output described below and set either a uniform **Border
   offset** or the rounded rectangle's individual edge offsets. Non-grid **By
   contrast** and non-grid **Auto detect** use authoritative native results, so
   those routes fix output to **Native lines / Béziers** and border offset to
   `0.00 mm`.
   For specialized detector routes, start a line-following cleanup pass at
   `0.00 mm`: a negative uniform value trims that amount from every edge and
   therefore cannot follow the detected border.
8. Press **Detect objects**.
9. Review the numbered overlay and the **Geometry** column:
   - green: selected proposed vector output;
   - gray: unselected direct detection;
   - yellow or orange dashed: inferred grid position;
   - red dash-dot: an outline that crosses the configured work area.
   When a current automatic honeycomb teaching reference is available, direct
   grid cells that closely match its exposed honeycomb pixels are marked
   **likely cut/open** and left unchecked.
10. Select only outlines that are correct directly on the camera image: click
    one candidate, Ctrl-click to toggle candidates, drag over multiple direct
    candidates, or click empty space to clear. The inspector checkboxes mirror
    the same selection. Inferred cells are intentionally unchecked by default
    and are not silently promoted by a drag. When the fitted grid is correct,
    **Select complete
    C ? R grid** selects direct and inferred cells together for explicit review.
    Red cells are also unchecked by default. Reposition the sheet fully inside
    the work area and detect again before creating or cutting them. A Stock
    boundary requires exactly one selected outline.
11. Leave **Replace earlier Trace objects** checked for the usual
    one-workpiece-at-a-time workflow, then press **Create separate vectors**,
    **Create one combined vector**, or **Create stock boundary**. The combined
    option creates one even-odd compound path and preserves overlaps; it is not
    a geometric union. Cut geometry replaces only earlier Trace cut
    objects. A Stock boundary replaces only the earlier Stock boundary, so it
    cannot delete imported artwork.

Changing a detection or output setting marks the result stale. Run **Detect
objects** again before creating geometry.

### Stock-boundary layout workflow

After a Stock boundary is created, it appears as a locked teal dashed outline
over the corrected camera image. It is persisted in `.e3laser` projects but is
explicitly excluded from vector, fill, raster, generated-job, and zero-power
frame output.

Select imported SVG artwork, vector text, or other unlocked vector objects to
show the contextual **Stock layout** toolbar above the workspace:

- **Center horizontally** and **Center vertically** move the selection relative
  to the nearest Stock boundary.
- **Snap rotation to stock edge** rotates the selection parallel to the nearest
  meaningful straight edge. Its dropdown can target the top, bottom, left, or
  right outward-facing edge instead. Multi-object selections rotate as one
  rigid layout, preserving their spacing and relative angles.
- **Fit to stock** scales and centers the selection to the largest conservative
  bounding footprint that fits inside the traced outline. Its dropdown offers
  0, 2, 3, 5, and 10 mm uncut margins plus a custom value.
- **Align** provides the same commands in a compact dropdown so later stock-
  relative actions can be added without crowding the main toolbar.

The original traced contour remains authoritative for fit checks; edge
simplification is used only to identify useful rotation lines. Always inspect
the exact Preview before running a job. Camera calibration, parallax, material
height, and physical stock movement remain independent sources of placement
error.

For **Cut geometry**, created shapes are ordinary project objects. They can be
assigned to layers, moved, resized, grouped, framed, and included in generated
G-code. **Clear detection preview** removes only the temporary camera overlay;
it never deletes created project objects. Existing drawings, imports, Stock
boundaries, and other non-Trace project objects are never removed by Cut
geometry replacement.

## Classification and vector output choices

Trace records a semantic classification separately from its vector-output
choice. Direct high-confidence silhouettes may be classified as circle,
ellipse, triangle, regular polygon, rounded rectangle, washer, or freeform contour.
Classification is quantitative and ambiguous outlines remain freeform. This
classification can improve template matching; it is not itself a request to
change how the observed pixels are reconstructed.

Washer recognition is deliberately strict. It considers only parent/child
contours preserved by filled-region masks, fits the inner and outer circles
independently, and checks circular residuals, circularity, containment,
diameter ratio, and center offset. A passing annulus becomes one logical Trace
object whose green proposed vector shows both contours. Offset or irregular
nested contours are not forced into washers.

- **Best-fit analytic shapes** uses a recognized circle, ellipse, triangle,
  regular polygon, washer, or rounded rectangle when its quantitative fit
  passes. Rounded rectangles fit center, width, height, rotation, and corner
  radius, then builds a clean analytic rounded-rectangle vector. The green
  preview is this proposed vector, not the jagged camera-pixel boundary. The
  **Geometry** column reports the fitted dimensions and radius. If a detection
  is not sufficiently rectangle-like, this mode falls back to its contour.
- **Native lines / Béziers** is the required output for non-grid Contrast and
  non-grid Auto. Auto's dark/light attempts come directly from the shared raster
  vectorizer; its conditional Color attempt and explicit Color use the same
  authoritative physical fitter through the contour-tree adapter. Grid routes
  retain their specialized output choices. Independent candidates remain
  independent, and each candidate retains its complete outer/hole/island
  hierarchy.
- **Simplified contours** follows the detected pixel boundary and removes
  points within the **Simplify tolerance**. A lower tolerance preserves more
  edge detail. This is polygon simplification; it does not turn an irregular
  edge into a smooth curve.
- **Exact contours** preserves the pixel-derived boundary without applying the
  simplification tolerance. It can contain many points and will naturally look
  stair-stepped when magnified.

For non-grid Contrast, E3 sends the corrected pixels through the imported-raster
pixel pipeline itself: grayscale/Otsu or manual threshold and polarity, physical
component and pinhole cleanup, 4× mask reconstruction, `RETR_TREE`, physical
mapping, source-edge refinement, native fitting, and topology validation. Each
root foreground contour plus every descendant is one candidate. The green
outline is the exact fitted geometry that will be created: real straight runs
remain lines and curved runs use constrained cubic Béziers. No camera-specific
outline finder, second contour extraction, or refit occurs afterward.

Auto's conditional Color, explicit Color, and grid routes fit chosen detector
masks through the physical contour-tree adapter. Analytic circle, ellipse,
rounded-rectangle, and washer evidence keeps its semantic path when analytic
output is chosen on the explicit Color and grid routes.

**Offset mode** defaults to **Uniform**, which applies the existing single
**Border offset** equally on all sides. Positive values expand the output and
negative values trim it. For **Fitted rounded rectangles**, choose **Per edge**
to adjust **Top**, **Right**, **Bottom**, and **Left** independently. These are
the detected object's own rotated edges, not screen directions. Moving one edge
also moves its two adjoining rounded corners while leaving the opposite edge
and the other two corners fixed. For example, a negative Top value with the
other three values at zero trims only the object's top edge and top corners.
Per-edge mode is intentionally unavailable for simplified and exact contours,
whose arbitrary boundaries do not have four analytic sides.

The detector retains its observed contour separately from the proposed vector.
This keeps diagnostics honest while ensuring the preview and created object use
the same geometry. Non-rectangle contours are centered from their actual bounds
when created, so rotated or asymmetric traces do not shift after approval.

## Detection modes

- **Auto detect**, with **Use grid** off, is an orchestrator over production
  tracing tools. It captures and rectifies once, reuses one immutable pixel
  source for shared-raster Otsu dark and Otsu light, and runs Color only when
  weighted HSV/Lab evidence finds a coherent target. The Color gate requires at
  least `0.005 × pixel count` total normalized chroma weight, at least 60% of
  that weight in one ±14-hue window, at least 1.5× separation from the strongest
  hue window more than 28 hue bins away, 0.2–60% resulting foreground, and no
  more than 50% foreground on the frame border. Auto uses hue tolerance 14 and
  minimum saturation 45 for this attempt; disabled manual Color values do not
  steer it.
- **Auto detect**, with **Use grid** on, retains the specialized repeated-object
  detector. Repeated-grid fits are ranked from observed filled-region support,
  not merely the number of clean contours, then proceed through lattice fitting,
  normalization, inference, and damaged/open-cell evidence.
- **By color** uses hue for automatic/manual numeric targets. **Pick color**
  additionally retains the sampled BGR/Lab color, allowing neutral gray and
  low-saturation objects to be separated from a warmer or cooler backing
  surface while tolerating moderate lighting variation.
- **By contrast**, with **Use grid** off, is pixel-literal raster vectorization.
  Automatic mode uses Otsu; manual mode uses the selected 0–255 threshold;
  polarity chooses dark or light foreground. Minimum area controls physical
  raster cleanup, including tiny islands and pinholes. Maximum area and minimum
  dimensions filter complete vector candidates afterward.
- **By contrast**, with **Use grid** on, retains the specialized object/grid
  detector. It evaluates dark and light filled-region hypotheses from global
  Otsu, illumination-corrected, adaptive, signed-local, and closed-outline
  evidence, ranks coherent repeated bodies, classifies cells, normalizes the
  fitted lattice, and can infer gaps.

### Auto scoring and fallback

Auto requires authoritative verified native geometry and never rewards a
strategy simply for returning more candidates. Its deterministic score is
`40V + 20F + 15B + 10A + 10S + 5W - P`, where:

- `V` is the fraction of independent roots that are valid;
- `F` rewards a useful foreground fraction and tapers to zero for a nearly full
  frame;
- `B` rewards a clean, non-foreground image border;
- `A` rewards useful retained physical foreground area;
- `S` penalizes results dominated by roots smaller than four times the selected
  minimum feature area;
- `W` is the fraction of valid candidates fully inside the reviewed frame; and
- `P` is a 35-point penalty when foreground and border occupancy both reach 75%.

A result with at least 95% foreground and at least 75% foreground border is
rejected as a background interpretation. Stable equal-score ties prefer dark
raster, then light raster, then Color. Status reports the selected polarity and
Otsu threshold or the selected hue/tolerance. Internal diagnostics retain each
attempt's success, rejection, or skip reason plus occupancy, root counts, valid
and invalid counts, and score terms; the inspector does not display a debug dump.

One failed strategy does not end Auto. Likewise, one failed independent contour
tree does not have to discard verified peers. Isolation occurs only at a root
tree boundary: the root and every hole or island descendant are fitted and
validated together, then all are accepted or all are omitted. E3 never detaches
a hole or island to evade topology validation. The existing fitting-error,
frame/extrema, self-intersection, adjacent-arc, compound-clearance, even-odd, and
rasterized-hierarchy checks are unchanged. Raster validation begins with the
complete forest and uses per-root checks only to diagnose a failed global pass.
The complete survivor forest is rebased and globally revalidated. If every root
passes alone but the roots fail together, or a complexity limit fails, the
strategy remains rejected. If all meaningful strategies fail, the operator
receives one bounded summary instead of a modal for each attempt.

### Degenerate 4× contours

The reproduced cause of the real-camera `fewer than three distinct points`
failure is not a large stencil-letter contour. Bicubic grayscale reconstruction
at 4× can overshoot near a retained edge inside the one-source-pixel component
halo. A nominal-background sample may cross the threshold and form an isolated
one-pixel fragment whose OpenCV contour has one or two points and zero polygon
area.

The shared pixel pipeline now prunes only contours with fewer than three
distinct trace points or zero trace-pixel polygon area after `RETR_TREE`
extraction. Positive-area features remain eligible. Removing a degenerate leaf
rebuilds all `next`, `previous`, `first_child`, and `parent` links in original
sibling order. If a degenerate contour has a legitimate descendant, its complete
root tree is rejected rather than reparenting the descendant and changing its
even-odd depth. Imported raster Quick Preview, exact imported vectorization,
manual camera Contrast, and Auto raster attempts share this behavior.

## Regular-grid inference

Grid inference is conservative and intended for repeated objects of similar
size. It first chooses the dominant mutually similar shape family, fits regular
row and column spacing, keeps only the best candidate in each cell, and rejects
unrelated or duplicate contours.

For an execution-grade honeycomb reference, Trace also rectifies the accepted
empty cutting-surface photograph into the exact same local frame. A strong
interior Lab-color match provides deterministic evidence that a detected grid
cell is already open. The comparison is used only when the image hash, complete
bed-map digest, and support-frame digest all remain current; otherwise Trace
cannot use the photograph. Its conservative within-sheet texture comparison
remains active in parallel and is the only open-cell signal when trusted
background evidence is unavailable. Both forms of review evidence change
default selection only. They do not expand output authority or authorize a cut.

With **Fitted rounded rectangles** and **Make grid cells identical**, every
accepted direct cell and inferred gap uses one robust shared width, height, and
corner radius. With **Snap cells to fitted grid** enabled, centers and rotations
also use the fitted lattice. With it disabled, direct cells retain their
observed centers and rotations while inferred cells, which have no observed
pose, still use the lattice. The raw direct observation remains in diagnostics
and the Geometry tooltip. If one direct observation is materially narrower or
shorter than the shared repeated-object size, its center is usually biased by
the missing edge. In that case only the affected center axis is repaired from
the fitted lattice; the other observed axis and rotation remain independent.
The Geometry tooltip discloses this repair. Grid objects are numbered and named
in stable row-major order.

Direct cells are also reviewed against their repeated-cell family. A material
rotation, width, height, or repaired-center disagreement is labeled
**damaged?**, remains visible with an amber dash-dot outline, and is left
unchecked. The proposed shared-size trace remains available for explicit
operator approval.

For grids with at least three direct observations, Trace compares texture and
edge density inside a shrunken cell interior. Cells with substantially stronger
exposed-bed texture than the lower-texture family baseline are labeled
**likely cut/open**, drawn cyan dash-dot, and left unchecked. This is
self-calibrated evidence from the current sheet, not a permanently learned
honeycomb model: heavily printed, transparent, reflective, or unusually
textured labels can remain ambiguous and require review.

When snapping is disabled, **Identical-cell anchor** controls how the shared
height is applied to direct observations. **Center** preserves the observed
center. **Detected top edge** preserves each independently detected top edge
and grows the shared height downward; use it when printing or damage extends
from the bottom of otherwise clean labels. This does not force direct labels
onto ideal grid positions.

Missing cells are visual suggestions, not observed edges, and are never
selected automatically. When **Infer gaps** is enabled, a missing cell may
also use conservative local grayscale boundary evidence inside its predicted
grid ROI: the shared grid keeps width, height, and angle fixed, while visible
long side runs can make a small center refinement. Diagnostics distinguish
`evidence_supported` recovery from an unsupported legacy blind gap and report
the predicted/recovered centers, side support, evidence score, and shift.
At least one horizontal and one vertical expected side must be supported, so
internal text or isolated texture cannot claim a physical label boundary.
Inspect the proposed complete lattice before using
**Select complete grid**. Do not use inference as a substitute for a clear
view; move the laser head away whenever practical. Grid normalization is
intentionally unavailable for simplified or exact contours because making
arbitrary pixel contours identical would require choosing a separate canonical
path.

Direct and inferred grid cells are retained in the review even when their
proposed vector crosses the guarded laser-output boundary. For ordinary
machine-coordinate work, that boundary is the configured camera/work rectangle
inset by `laser.boundary_margin_mm` and reduced for the physical spot offset.
For a current honeycomb-bound project, it can instead be an explicit fixed
machine-coordinate polygon. Its honeycomb-local coordinates depend on the
accepted support pose and the physical span configured for the running saved
machine. Camera detection does not move or enlarge it. Red cells report the
exceeded edge and distance and remain
unchecked. If raw observed geometry was inside but shared grid sizing caused
the overrun, the Geometry tooltip says so explicitly.

An observed mask that reaches any corrected-raster edge is separately labeled
**cropped**, drawn red, and left unchecked even when its partial contour is
inside the selected authority. The unseen part of an edge-touching object cannot
be inferred safely from that observation. Reposition the workpiece, or verify
the configured crop and guarded limits with the Step 3 ruler overlay, before
creating output. Never treat physical support on a movable honeycomb as proof
that the laser spot can reach the same coordinate.

When Step 3 has a current automatic four-edge honeycomb reference, Trace draws
its configured-span support outline in magenta and uses its rigid local frame.
The three-click fallback remains diagnostic-only and cannot authorize output.
The magenta support and green output polygon are distinct evidence: detection
never changes the configured polygon. Camera coverage, the selected guarded-
output authority, and crop-edge evidence affect the review gates.

These checks keep the fitted grid and its occupancy internally coherent while
making the required remedy explicit. Trace and color sampling also refuse to
start when the loaded project and configured machine work areas do not match,
because corrected camera pixels would otherwise be displayed in a different
coordinate system from the detector.

## Frozen review behavior

Trace captures one corrected frame for a detection request. Live camera updates
are held while that result is being reviewed, so the displayed pixels remain
the pixels that produced the vectors. Clearing the preview, creating the
objects, changing the camera/calibration evidence, or stopping the controller
invalidates outstanding trace work. Late results from an older request are
ignored.

The displayed candidate items are review-only scene objects. A newer detection
replaces them as one set, and clearing or creating removes them without adding
anything to the project, planning, G-code, or execution paths. Project-object
selection and movement are temporarily disabled during candidate review and
restored when the review ends.

## Accuracy notes

Detection runs on the rectified camera image and outputs machine millimeters.
At the current default of 4 pixels/mm, one image pixel represents 0.25 mm.
Rasterization and thresholding can therefore move a fitted edge, dimension, or
radius by part of a pixel or more even on an ideal generated image. A clean
rounded vector is a geometric fit to those pixels; it is not evidence of
sub-pixel physical accuracy.

The upstream lens/bed transform may be projective and therefore have a
spatially varying raw-camera Jacobian. Trace does not fit in those raw sensor
coordinates. `capture_parked_trace_frame()` rectifies into the configured work
area at one explicit pixels/mm, so the corrected image consumed here has a
constant physical pixel pitch (apart from a possible fractional strip caused by
integer raster dimensions). Non-grid Contrast follows the imported-raster
coordinate contract. The 4× contour sample center is mapped in the image-local
physical frame, with Y inverted once, and the complete native path receives one
final affine into the active machine or honeycomb frame. The user tolerance has
the same meaning as in imported raster vectorization; selecting a value smaller
than one corrected camera pixel does not establish equivalent physical accuracy.
Eligible independent contours may move a non-corner threshold sample toward one
strong source-intensity crossing by at most 0.6 source pixel; nested contours
remain on the threshold. Reported deviation includes accepted edge-centering
shift and continuously validated native fit error. Auto raster strategies use
the shared raster coordinate contract; Auto Color, explicit Color, and grid
paths retain their camera-mask physical adapter and corrected-pixel resolution
floor.

The desktop registers the center of each displayed camera pixel to the same
OpenCV/BedMapper coordinate used by the detector. At very high zoom, a smooth
curve will still cross different portions of the staircase-shaped edge pixels;
that is normal raster sampling, not an alternating machine-coordinate offset.
The rounded fitter likewise distinguishes a raster's pixel-center span from its
pixel count so an ideal integer-pixel radius is not underestimated by one pixel.
The workspace scales corrected pixels by the rectifier's exact pixels/mm value,
including when rounding the raster dimensions leaves a fractional strip at a
non-integral work-area edge.

Real placement accuracy also depends on lens calibration, bed mapping, camera
pose, material height, focus, lighting, and resolution. A trace can be
geometrically neat but globally offset when the mapping is stale. Use a saved
cutting template when the intended cut geometry is already known; use Trace for
geometry that must be recovered from the image, and always review before
generating or running a job.
