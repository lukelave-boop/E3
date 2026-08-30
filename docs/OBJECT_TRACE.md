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
> [../CURRENT_STATE.md](../CURRENT_STATE.md). The non-grid hard Trace ROI,
> trusted-reference suppression, material eligibility, Auto orchestrator,
> hierarchy-safe pruning, and camera-photo normalization have deterministic
> synthetic coverage. That coverage includes reflective periodic honeycomb,
> 20/50/84% stock coverage, lighting/white-balance-related drift, blur, sensor
> noise, a highlight, machine surround, dark/light polarity, narrow gaps, a
> hole, an underline, variable-tone long glyphs with darker surface marks,
> homogeneous 4× foreground/background classification, warm false-Color and
> real bounded-Color cases, exact 4× display scaling, coordinate equivalence,
> and imported-raster parity. In a 2026-08-30 operator-reported Coleman stencil
> run, manual threshold 128 produced a much cleaner Mask but still failed the
> bounded native-topology proof, manual threshold about 150 produced usable
> geometry, and Auto selected 170 and produced a good trace. Controller,
> firmware, configuration, and measured placement details were not recorded here,
> so this is scene evidence rather than formal physical acceptance. Straighten
> itself has not yet been physically exercised.

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
   off, E3 first restricts all strategies to trusted physical Trace geometry,
   suppresses only confidently matched empty-bed structure, and keeps changed
   or uncertain material eligible. Auto corrects broad lighting variation once,
   then runs bounded automatic threshold selection against both dark- and light-
   feature responses. It conditionally tries Color only for bounded eligible chroma;
   the result message says which credible strategy won. Auto's hue, threshold,
   and polarity controls are inactive because Auto owns those choices. With
   **By contrast** and **Use grid** off, choose bounded Auto or a manual 0–255
   threshold and whether dark or light local contrast is foreground. The
   threshold applies after lighting normalization, not to raw camera brightness;
   hue controls are inactive. To sample for
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
8. Press **Detect objects**. Starting a new request immediately clears the old
   temporary candidates, so a failed request cannot look like the previous
   mode's result. When non-grid mask preparation completes, **Camera display**
   becomes available and defaults to **Mask**. Switch among **Camera**,
   **Eligible**, **Normalized**, and **Mask** to inspect the corrected frame, the
   exact source-resolution hard-ROI/reference eligibility, the threshold input,
   and the immutable exact 4× production contour mask used by `RETR_TREE`. The
   desktop displays the 4× mask at 4× pixels/mm, so every mode covers the same
   physical area without resizing production bytes. A provisional Auto message
   says evaluation is still running; only the selected-strategy callback says
   production/native fitting completed. Changing the selector is display-only.
9. Review the numbered overlay and the **Geometry** column after fitting
   completes:
   - green: selected proposed vector output;
   - gray: unselected direct detection;
   - yellow or orange dashed: inferred grid position;
   - red dash-dot: a specialized grid outline that crosses the configured work
     area. Ordinary non-grid roots clipped by the hard Trace ROI are omitted.
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
11. For a successful non-grid **Cut geometry** result using **Native lines /
    Béziers**, E3 may show a
    conservative **Detected skew** review with an explicit clockwise or
    counterclockwise direction. The estimate uses only the currently selected,
    verified native vectors. Press **Straighten** only if you want the suggested
    correction; it is never automatic. The selected vectors rotate together in
    the overlay while Camera, Exposed bed, Eligible, Normalized, and Mask remain
    unchanged. **Reset** restores the exact fitted vectors.
12. Leave **Replace earlier Trace objects** checked for the usual
    one-workpiece-at-a-time workflow, then press **Create separate vectors**,
    **Create one combined vector**, or **Create stock boundary**. The combined
    option creates one even-odd compound path and preserves overlaps; it is not
    a geometric union. Cut geometry replaces only earlier Trace cut
    objects. A Stock boundary replaces only the earlier Stock boundary, so it
    cannot delete imported artwork.

Changing a detection or output setting marks the result stale. Run **Detect
objects** again before creating geometry.

### Optional Straighten review

Straighten is a downstream geometry review, not another detection mode. It is
available only for selected Cut-geometry candidates from a successful, non-grid,
authoritative native fit. Stock boundaries retain their photographed physical
outline and do not offer Straighten. A failed or partial native fit cannot be
straightened: E3 does not estimate orientation from the Mask, observed contours,
or any partially fitted path. Changing the selection computes a new estimate
from the already fitted native geometry and does not recapture, normalize,
threshold, or refit anything.

The estimator is geometric and uses no OCR, text recognition, or machine
learning. It combines physical-length native line evidence, only those cubic
Béziers that are demonstrably near-linear, anisotropic candidate axes, and—in a
multi-candidate selection—candidate-center alignment. Evidence is reduced
modulo 90 degrees and combined with a bounded robust consensus. Confidence
accounts for the inlier fraction, angular spread, physical support, independent
features, supporting candidates, and evidence-family diversity. Candidate and
family weights and total analysis complexity are capped so many tiny fragments
or curve samples cannot win by vote count alone. Candidate-center alignment can
strengthen a consensus but cannot manufacture independent candidate support. A
reliable candidate-local angle—or a tight conflict-only mode from several strong
candidate axes—that conflicts with another selected group is a conservative veto,
even if one group has more total weight.

Positive internal skew means counterclockwise in the X-right/Y-up project frame;
the correction has the opposite sign. The UI always spells out the direction.
Skew below about 0.4 degrees is treated as already straight. Offers normally
stop at 10 degrees; 10–15 degrees requires exceptional agreement, and larger,
diffuse, conflicting, circular, square-like, or otherwise ambiguous evidence is
suppressed. No offer is safer than a confident-looking guess.

When accepted, all selected candidates receive one rigid rotation around the
combined selected native-geometry bounds center. Candidates do not rotate about
their individual centers, so spacing, baselines, subpaths, holes, islands, fill
rule, line segments, cubic segments, and candidate identities are preserved.
Only the temporary vector overlay changes. **Reset** rebuilds that overlay from
the retained original fitted geometry rather than applying an inverse transform.

Straighten remains temporary review state with no project, planning, G-code,
motion, arming, or laser authority. **Create separate vectors** gives every new
object the mathematically equivalent common group transform; **Create one
combined vector** applies the equivalent transform to the compound object. Both
produce the same physical geometry, participate in the ordinary one-step undo
history, and commit nothing until **Create** is pressed.

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

For non-grid Contrast, E3 first applies the hard physical ROI, suppresses
confidently matched exposed bed, and removes low-frequency photographic
background variation from eligible material. It then sends that normalized raster through the imported-raster pixel
pipeline itself: bounded Auto or manual threshold and polarity, physical component and
pinhole cleanup, 4× mask reconstruction, `RETR_TREE`, physical mapping,
source-edge refinement, native fitting, and topology validation. Each root
foreground contour plus every descendant is one candidate. The green outline is
the exact fitted geometry that will be created: real straight runs remain lines
and curved runs use constrained cubic Béziers. No camera-specific outline
finder, second contour extraction, or refit occurs afterward.

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

### Camera-photo normalization

Rectification makes the camera scale constant, but it does not turn the result
into clean artwork: exposure gradients, shadows, vignetting, glare, and dark
machine areas remain photographic brightness. The old direct grayscale/Otsu
route could therefore classify a shadowed blank sheet as foreground. Non-grid
raster tracing now asks how far each pixel is above or below its slowly varying
local background before any ordinary threshold is chosen.

Before this model runs, ordinary non-grid Trace keeps the full corrected raster
but creates one immutable material-eligibility mask. Machine-coordinate work
uses the existing guarded output polygon or rectangle. Honeycomb-local work
intersects that geometry with the recorded support rectangle in the existing
local frame. The accepted empty-bed reference is used only after its stored
image hash, bed-map digest, support-frame digest, coordinate frame,
rectification, and final dimensions validate. Locally detrended luminance
correlation, normalized patch error, and compatible texture first identify
structural candidates on a model bounded to 800 pixels and 2 pixels/mm. Strong
candidates whose uncompensated Lab luminance/chroma distances are at most 32/24
levels seed a deterministic robust lighting model: current Lab
luminance is fitted from reference luminance with a 0.72–1.28 gain, ±48-level
offset, and ±32-level whole-frame X/Y gradients, while each chroma channel uses
a ±24-level offset and ±16-level gradients. At most 50,000 deterministic seed
samples enter the Tukey-weighted fits, so changed stock or ink outliers cannot
dominate ordinary exposed-bed evidence.

The fitted reference appearance is compared with the current image after a
0.35 mm mild-blur allowance. Point luminance/chroma/combined residuals must be
at most 34/22/38 levels and the existing 1.5 mm patch means must be at most
26/18/30. A stricter 12/8/14 point match may bypass a contaminated patch mean,
preserving real bed pixels immediately beside a changed-material boundary.
Structural evidence alone can never exclude a pixel. The existing 3 mm-radius morphological closing is retained
at bounded model resolution for real honeycomb continuity, but only strong
structure-plus-appearance seeds are closed, and new bridge pixels must retain
appearance consistency and loose structural support. Closing therefore cannot
cross a clearly changed sheet or artwork region, and an isolated false seed does
not expand. Changed or uncertain pixels remain eligible. Thus a sheet becomes
material that may be inspected, not foreground, and its perimeter is not created
by the reference stage. Honeycomb-local Auto requires valid reference
evidence. Manual Contrast and explicit Color may fall back to hard-ROI-only
eligibility when no reference exists; a supplied mismatched reference is an
error.

The background model is tied to corrected physical scale. Ineligible pixels are
inpainted only in its temporary model input; the actual residual is zeroed
outside eligibility and its robust scale is derived only from eligible pixels.
It is downsampled to
at most about 1 pixel/mm and 512 pixels on its longest axis. A deliberately
conservative flat-field guard recognizes a low-noise, near-discrete clean frame:
the eight most populated four-level histogram bins must cover at least 99.5%, a
2 mm outer model band must be at least 99.5% within three levels of its median,
that background band must cover at least half the model, and far-away tones must
outnumber gradual intermediate tones by the bounded 80% separation rule. A
qualifying frame uses that robust border median as one constant background, so
a clean interior solid larger than a local filter remains filled for either
polarity. A dark machine-colored border, gradual quantized shadow, or realistic
noisy gradient fails the guard and cannot select this shortcut.

Other frames use a 35 mm elliptical grayscale kernel to compute opening and
closing envelopes in `float32`; each envelope is smoothed with the same 4 mm
Gaussian and returned to full resolution. At the default 4 pixels/mm, the
rank-envelope diameter would be 140 corrected pixels, but it is only 35 pixels
in the bounded model. That diameter is larger than the expected 3–10 mm stencil
strokes and the reproduced 21.5 mm label thickness. The smoothed closing is the
dark-feature background and the smoothed opening is the light-feature
background. Their midpoint remains available only as compact signed diagnostic
context. Production response amplitude is not measured from that midpoint:
doing so can copy half of a glyph into its own background and let a dark surface
mark cancel otherwise sound neighboring pixels. Frames that do not satisfy
every flat-field condition deliberately fall back here; image-only logic cannot
disambiguate every posterized shadow from flat artwork.

For full-resolution `float32` grayscale `I`, let `C` be the smoothed closing and
`O` the smoothed opening. The one-sided distances are `dD = C - I` and
`dL = I - O`. Each pixel is assigned only to the larger distance (ties remain
blank), retaining the prior exclusive dark/light decision without halving its
contrast. The selected response then subtracts the common three-level noise
floor. One nearest-rank 99.5th-percentile scale over the maximum selected
response is shared by both polarities and clamped to 32–64 grayscale levels, so
one extreme edge or highlight cannot set arbitrary gain and ordinary sensor
noise cannot be blown up without bound. For response `X`, the deliberate uint8 transfer is
`round(255R / (R + X))`: blank/opposite-polarity pixels are white, a response
equal to the robust scale `R` maps to 128, and stronger responses approach black
without a clipped-black endpoint. Manual threshold 128 therefore means at least
the robust response scale. **Trace light pixels** selects the symmetric light
response through the established raster inversion contract; it does not switch
back to raw brightness.

For automatic thresholding, only eligible pixels enter the histogram; excluded
pixels are forced background in every candidate source mask. Candidate generation
is capped at 12 values and is derived from the current normalized raster: the
stabilized Otsu baseline, Triangle, interpolation from Otsu toward the foreground
and background class medians, and thresholds for 1%, 3%, 8%, 16%, and 30%
foreground occupancy. There is no saved physical byte or fixed successful-camera
threshold in this family.

Each candidate is assessed at source resolution before component cleanup, 4×
reconstruction, contour extraction, or native fitting. The cheap score is
`22T + 18C + 14N + 14F + 14B + 12R + 6W - D`: `T` combines coherent-mask IoU
(65%), significant-component agreement (25%), and hole-count agreement (10%) at
nearby thresholds; `C` is coherent foreground; `N` penalizes speck area and count;
`F` rewards reasonable occupancy; `B` rewards a clean eligibility/image border;
`R` rewards retaining coherent stroke area; `W` rewards narrow foreground retained
inside significant components; and `D` penalizes foreground/background dominance.
The nearby radius is image-derived from the 5th–95th percentile span and bounded
to 2–8 byte levels. A credible candidate must retain a significant component,
keep foreground below 90% and border foreground below 80%, and keep at least half
of its foreground in coherent components. A non-Otsu winner must also clear a
two-point baseline departure margin; that margin rises by 1.25 points for each
significant component beyond two new components (capped at eight points) and by
200 times worsened border occupancy (capped at four points). This permits stable
legitimate strokes to move the result while resisting a component or border
explosion. Only the winning byte enters the unchanged production raster path.

OpenCV's lowest equally optimal Otsu plateau member is still advanced by at most
two unused levels when its low class lacks interpolation headroom. Normal polarity
measures that headroom across the selected foreground span; inverted light tracing
measures it above the low background endpoint. The nudge stays inside an empty
histogram gap, so source-pixel classification is unchanged. The eligibility mask
is nearest-neighbor reconstructed to 4× and gates again after component-hole
reconstruction, preventing interpolation from resurrecting machine or bed pixels.
Manual thresholds and polarity semantics remain unchanged. Bicubic reconstruction
continues to place the edge inside a one-source-pixel transition band, but every
cleaned source pixel whose complete 3×3 neighborhood has one classification is
locked to that foreground or background classification at 4×. This range-safe
interior rule prevents cubic ringing from inventing a positive-area pinhole or
island where no source-resolution boundary exists. The Otsu headroom adjustment
remains useful for the transition band and two-tone endpoints.

The frozen normalization value retains immutable-byte-backed corrected BGR,
grayscale, the diagnostic `float32` midpoint background and signed residual,
both uint8 polarity rasters, a versioned content key, physical/model scale,
envelope/kernel size, smoothing sigma, selected model and polarity-response
kind, flat-field bin/border/background/separation coverage, noise-floor,
percentile, reciprocal transfer, and response-scale diagnostics. It is temporary
analysis data only. Opening and closing affect only the temporary background
estimate; they never touch the normalized raster or production mask.
Normalization does not threshold, close output gaps, grow or erode output
strokes, fill holes, repair letters, infer cells, or create project geometry;
all mask, hierarchy, and native geometry decisions remain in the established
raster pipeline.

This adapter is used only by non-grid **By contrast** and Auto's dark/light
raster attempts. **By color** still uses chromatic evidence but is hard-ROI
gated, Auto's conditional Color attempt shares material eligibility, and **Use grid** retains
its specialized repeated-object detector, normalization, classification, and
inference behavior.

## Detection modes

- **Auto detect**, with **Use grid** off, is an orchestrator over production
  tracing tools. It captures and rectifies once, estimates one low-frequency
  material eligibility and background, derives immutable dark and light rasters
  from that same result,
  and runs bounded source-resolution Auto threshold selection on each. It runs
  Color only when weighted HSV/Lab
  evidence finds a coherent target within eligible material. The Color gate requires at
  least `0.005 × pixel count` total normalized chroma weight, at least 60% of
  that weight in one ±14-hue window, at least 1.5× separation from the strongest
  hue window more than 28 hue bins away, 0.2–35% resulting foreground, and no
  more than 25% foreground on the eligibility boundary. A credible Color result
  must exceed the best credible raster score by eight points. Auto uses hue
  tolerance 14 and minimum saturation 45 for this attempt; disabled manual Color
  values do not steer it.
- **Auto detect**, with **Use grid** on, retains the specialized repeated-object
  detector. Repeated-grid fits are ranked from observed filled-region support,
  not merely the number of clean contours, then proceed through lattice fitting,
  normalization, inference, and damaged/open-cell evidence.
- **By color** uses hue for automatic/manual numeric targets. **Pick color**
  additionally retains the sampled BGR/Lab color, allowing neutral gray and
  low-saturation objects to be separated from a warmer or cooler backing
  surface while tolerating moderate lighting variation.
- **By contrast**, with **Use grid** off, is normalized-raster vectorization.
  Automatic mode selects from the bounded, image-derived family on the selected
  local-contrast raster; manual mode applies the selected 0–255 value to that
  same raster. Polarity chooses the
  symmetric dark or light response. Minimum area controls physical raster
  cleanup, including tiny islands and pinholes. Complete independent root trees
  that already violate maximum area or minimum dimensions are rejected before
  expensive native fitting without changing their mask or splitting their
  holes/islands. A later fitting or topology failure omits only that complete
  root and leaves verified peers available for review.
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
rejected as a background interpretation, and every result below the absolute
score floor of 70 is rejected. Hard-ROI-clipped, confidently suppressed,
outside-output, review-filtered, and invalid-native candidates contribute no
positive score. Stable equal-score ties prefer dark
raster, then light raster, then Color. Status reports the selected polarity and
exact Auto threshold or the selected hue/tolerance. Internal diagnostics retain each
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

The read-only **Chosen threshold** row starts at `—`. A successful non-grid
Contrast Auto result or Auto raster winner shows the exact byte used by its
production mask. A new detection replaces it, changing settings makes it stale,
and Clear or any failed detection restores `—` even when diagnostic images are
retained. If non-grid Auto selects Color, the row shows `N/A`. Manual threshold
mode keeps the existing editable 0–255 control and never substitutes the Auto
result into that field.

### Diagnostic preview and timing

The **Camera display** selector is an inspection surface for one frozen request,
not a second image-processing path:

- **Camera** is the corrected production BGR frame.
- **Exposed bed** is the exact source-resolution immutable
  `exposed_bed_mask` whose inverse is combined with the hard ROI. It is not a
  UI reconstruction.
- **Eligible** is the exact source-resolution hard-ROI plus reference-aware
  material mask used by that request.
- **Normalized** is the exact polarity-specific grayscale delivered to the
  shared threshold stage for raster Contrast/Auto. For Color it is grayscale
  context; the chromatic production route remains unchanged.
- **Mask** is the exact 4× contour mask consumed by production `RETR_TREE`,
  including the production threshold, source-resolution component cleanup, and
  4× reconstruction.

The workspace uses each image's actual pixels/mm: the exact 4× Mask therefore
occupies the same machine area as Camera, Exposed bed, Eligible, and Normalized
without a display copy or mutation. Because corrected source dimensions are rounded once,
the desktop validates the Mask as exactly four times that already-rounded source
raster rather than independently rounding the physical area again at 4×. This
preserves a possible fractional final pixel strip and never resizes the production
mask. The application log temporarily records each stored slot's dimensions,
format, byte count, and pixel SHA-256 for physical display verification. The mask
callback runs synchronously in the worker as soon as exact mask
preparation finishes and before contour extraction or native fitting. The
desktop publishes the read-only arrays to the GUI through a queued signal,
checks both request ID and review signature, creates display images only on the
GUI thread, and defaults the selector to **Mask** on the first current preview.
If an unexpected display-size or pixmap conversion failure occurs, the workspace
hides the preceding image instead of leaving Camera pixels under another selected
label.
Auto may publish provisional dark/light masks with explicit evaluation-running
wording while it evaluates them, then
publishes the winning strategy again; the final selector state therefore
matches the selected result. No preview is independently recomputed, and a late
or stale callback cannot replace current pixels or geometry.

Developers can reproduce this boundary without the desktop or native fitting:

```powershell
.\.venv\Scripts\python.exe scripts\diagnose_camera_trace_raster.py data\corrected-frame.png --pixels-per-mm 4.0 --polarity dark --threshold auto
```

The input must already be a local corrected/rectified BGR frame; the script does
not capture or rectify a raw camera image. Unless `--output-dir` is supplied, it
writes `corrected.png`, `background.png`, `normalized.png`, the exact 4×
production `mask.png`, and `diagnostics.json` under the gitignored
`data/trace_diagnostics/` directory. The JSON records both normalized and mask
dimensions, the chosen model kind and scale diagnostics, the actual threshold,
the bounded Auto candidates and scores when applicable, component count, and
normalization/mask timings.

Trace timing is opt-in diagnostic metadata under `diagnostics.timing`; it is not
persisted and does not alter scoring or validation. The request boundary records
`prepare_photo_seconds`, `hold_acquisition_seconds`, `camera_burst_seconds`,
`precision_capture_total_seconds`, `capture_seconds`, `rectification_seconds`,
`capture_rectification_total_seconds`, `detect_objects_seconds`, and
`request_total_seconds`. The camera adapter records
`hard_roi_preparation`, `structural_reference_match`,
`photometric_compensation`, `appearance_veto`, `morphology_closing`,
`reference_comparison`, `material_eligibility`, `trace_eligibility_total`,
`grayscale_preparation`, `background_estimation`,
`normalization`, and
`camera_normalization_total`. Shared raster timing separately records mask
generation/preparation, threshold, component cleanup, 4× preparation, contour extraction,
cheap root-review filtering, native fitting, and authoritative topology and
raster-hierarchy validation. Manual Contrast retains one raster-vectorization
snapshot; Auto retains per-dark/per-light snapshots, the common normalization
snapshot, its normalization key, and `background_estimate_count = 1`.

### Degenerate 4× contours

The reproduced cause of the real-camera `fewer than three distinct points`
failure is not a large stencil-letter contour. Bicubic grayscale reconstruction
at 4× can overshoot near a retained edge inside the one-source-pixel component
halo. A nominal-background sample may cross the threshold and form an isolated
one-pixel fragment whose OpenCV contour has one or two points and zero polygon
area.

A separate reproduced failure occurred away from a real edge: a solid
source-resolution foreground region containing a shallow 2×2 intensity plateau
was entirely selected at threshold 128, but cubic ringing raised reconstructed
samples above 128 and created a positive-area child hole. Degenerate-contour
pruning correctly could not remove that apparent topology. The homogeneous
3×3 classification lock described above now prevents both foreground holes and
background islands in regions where the cleaned source mask has no boundary;
real holes, gaps, cleanup decisions, and transition-band edge localization are
preserved.

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

On hardware, Trace completes Home / park before acquiring the temporary stepper
hold. The hold covers only the short settle/discard/stable raw-frame burst and
is released before sharpness selection, rectification, or detection.

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
strong normalized-source intensity crossing by at most 0.6 source pixel; nested
contours remain on the threshold. The fitter does not return to raw photographic
brightness after the mask is built. Reported deviation includes accepted edge-
centering shift and continuously validated native fit error. Auto raster
strategies use the shared raster coordinate contract; Auto Color, explicit
Color, and grid paths retain their camera-mask physical adapter and corrected-
pixel resolution floor.

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
generating or running a job. The deterministic normalization regressions remain
synthetic. The 2026-08-30 operator-reported Coleman run is useful scene evidence,
but a recorded controller/firmware/configuration run with measured placement,
shadow, glare, focus, material height, and exposure results is still required for
formal physical acceptance. Straighten requires its own physical review.
