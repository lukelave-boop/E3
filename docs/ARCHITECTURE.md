# Architecture

The repository has two user interfaces over a shared camera, calibration,
geometry, vision, G-code, and machine core.

## Runtime map

```text
Browser entry point                     Desktop entry point
laser_aligner.__main__                  laser_aligner.desktop.main
          |                                       |
          v                                       v
      CoreRuntime                             CoreRuntime
          |                                       |
          v                                       v
      AppContext                              AppContext
          |                                       |
          v                                       v
  AppHTTPServer + web UI                  DesktopController
          |                                       |
          v                                       v
 single-SVG placement                 E3MainWindow + WorkspaceView
          |                                       |
          v                                       v
  gcode.generator                    ProjectDocument + CommandStack
                                                  |
                                                  v
                                         project.toolpath
          |                                       |
          +-------------------+-------------------+
                              v
                         AppContext machine
                              |
                +-------------+-------------+
                v                           v
       local MachineService          RemoteMachineService
       safety / execution            Windows preflight/upload/
                |                    monitor/STOP facade
                v                           |
       local POSIX serial                    v  E3MACHINE/2
                                     PiJobStore + PiJobService
                                                |
                                                v
                                     one Pi-local MachineService
                                                |
                                                v
                                         local POSIX serial
```

Both entry points use `CoreRuntime` as the UI-neutral lifecycle and active
saved-machine authority around `AppContext`. The browser passes the runtime's
context to `AppHTTPServer`; the desktop performs blocking camera/controller work
through `DesktopController` worker tasks. Neither entry point substitutes raw
configuration machine values for an existing active saved-machine snapshot.

For a network-attached machine, `AppContext` selects `RemoteMachineService` when
the serial endpoint uses `e3bridge://`. Windows owns exact program preparation,
local preflight, bounded upload, START authorization, monitoring, and explicit
STOP. The authenticated `E3MACHINE/2` node persists and independently validates
the immutable program, then its one Pi-local `MachineService` owns controller
connection, Home/park, arming, command/ACK streaming, completion, and failure
cleanup. A successful START response is issued only after durable
`ownership_accepted` state exists; monitoring-client loss afterward does not
stop or steal the serial session. The old `E3BRIDGE/1` raw serial bridge remains
a separately importable legacy component, but the combined node does not host it
and new clients never fall back to it. Direct local serial continues to use the
original local `MachineService` path.

A configured `e3camera://` device independently selects `RemoteCameraService`;
the Pi retains sole ownership of the real `CameraService`, `VideoCapture`, and
V4L2 controls. Camera-client loss has no machine-execution meaning. See
[NETWORK_MACHINE.md](NETWORK_MACHINE.md).

## Module ownership

| Module | Responsibility |
|---|---|
| `config.py` | JSON defaults, merging, validation, and resolved paths |
| `storage.py` | Atomic JSON persistence used by calibration |
| `camera/` | OpenCV/V4L2 and remote camera capture plus camera controls |
| `calibration/` | Lens model, checkerboard solving, bed homography, rectification, targets, bounded fine registration, holdout accuracy scoring, and Qt-free read-only coordinate auditing |
| `vision/` | Workpiece, fiducial, crosshair-grid, camera-object detection, and camera-photo normalization |
| `vision/camera_raster_normalization.py` | Qt-free, authority-free low-frequency illumination modeling and symmetric dark/light raster adaptation for non-grid Camera Trace |
| `vision/camera_trace_eligibility.py` | Qt-free hard physical Trace ROI and trusted empty-bed comparison that produces immutable material eligibility without creating foreground geometry |
| `geometry/` | SVG parsing, curve flattening, transforms, and physical units |
| `gcode/` | Legacy single-SVG generation and G-code parsing/preview utilities |
| `project/` | Desktop project schema, undoable object/shape commands, save/recovery, alignment, and multi-layer toolpaths |
| `project/path_geometry.py` | Qt-free canonical line/cubic path values, schema adapters, exact affine operations and bounds, deterministic flattening, and bounded complexity |
| `project/raster_vectorize.py` | Qt-free exact-payload raster masking, bounded hierarchy-aware contour fitting, native line/cubic persistence, and preview/topology flattening |
| `planning/` | Qt-free stage identities, coordinate-domain contracts, artifact provenance, and shared planner payload models |
| `templates/` | Shared semantic shape geometry, versioned multi-shape grid authoring, atomic library storage, project normalization, and rigid instantiation |
| `materials/` | SQLite material-recipe library, scoped compatibility, and legacy database migration |
| `project/power_correction.py` | Qt-free bounded power mapping, corner analysis, and sparse vector/raster correction profiles |
| `machine/` | Local `MachineService`; authenticated Pi job protocol, durable store, Pi runner/server, and Windows remote facade; neutral transports; immutable controller dialects; and the versioned saved-machine/profile registry |
| `server.py` + `web/` | Local HTTP API and browser UI |
| `core/` | Shared runtime lifecycle for non-HTTP consumers |
| `desktop/` | PySide6 window, workspace, panels, tasks, and presentation logic |

Qt and HTTP types must not leak into the project, calibration, geometry,
vision, G-code, or machine models.

## Camera and calibration flow

```text
CameraService or RemoteCameraService
  -> sequence-numbered raw OpenCV BGR frame
  -> immediate snapshot for live preview, five-frame sharpest interactive still,
     or 45-frame parked-bed FrameBurst for calibration analysis
     (control reapply/readback, settle, unique-frame discard, fresh samples;
      the configured deadline covers that complete acquisition sequence)
  -> cached composed raw-camera-to-bed map when a lens model exists
     (inverse bed homography/residual mesh coordinates distorted back into the
     raw camera domain)
  -> one cv2.remap() into the top-down bed image
     (BedMapper.rectify() remains the no-lens path)
  -> top-down image at configured pixels/mm
  -> workspace background and optional vision detectors
```

The background reader is the sole owner of `VideoCapture.read()`. Camera start,
restart, V4L2 control changes, and precision bursts use
one bounded exclusive-operation contract. Shutdown invalidates the current
camera generation and releases the backend before joining its reader, so a
burst or control request cannot publish state after teardown. Preview snapshots
remain available during a burst: they retain the published immutable frame
reference under the state lock and copy its pixels after releasing the lock.
Sharpness and downstream vision analysis likewise run after camera ownership is
released. This keeps large frame copies and CPU work from delaying new source
frames while preventing two precision workflows from consuming the same capture
sequence. `CameraStatus.fps` measures completed usable-frame publication after
validation and any direct-MJPEG decode; `negotiated_fps` remains the separate
backend-reported mode and is not proof of sustained delivery. Burst diagnostics
include observed and negotiated FPS plus skipped sequence counts.

`RemoteCameraService` adapts one retired status-wire field from legacy physical
Pi nodes without changing the current model. It first copies the returned status
mapping, removes `synthetic` only when its value is the exact boolean `False`,
then constructs `CameraStatus` from every remaining field unchanged. Any other
`synthetic` value is invalid. The adapter neither mutates the received mapping
nor reintroduces a simulated camera path.

On Linux V4L2 MJPG sources with persistent device paths, startup first attempts
a small native V4L2 MMAP backend. It negotiates the configured profile and
publishes each bounded SOI/EOI-framed source JPEG together with one decoded BGR
image under the same sequence, generation, and timestamp. The remote monitor
may forward the compressed representation at native size; snapshots and
precision capture continue to consume the decoded representation. Unsupported
devices or initialization failures close the native descriptor and mappings
before ordinary decoded OpenCV capture is opened, preserving one camera owner.
Raw-monitor metadata carries that Pi-side publication rate as Capture FPS. The
desktop timestamps each complete socket packet before JPEG decode, calculates
Network FPS from every worker-thread receipt, and calculates Display FPS only
from frames Qt presents after latest-frame replacement.

Parked hardware workflows request an unscored raw burst while the temporary
stepper hold is active. Home / park and any other ordinary machine RPC complete
before that hold is acquired. The hold ends immediately after the last frame
has been copied; lens correction and clarity scoring then populate the same
`FrameBurst` outside that scope. The short parked Trace workflow uses the same
two-phase capture/selection path. Each burst retains its camera-session
generation, so a stop or reopen during deferred processing rejects the stale
result. Callers never choose a representative frame from an unscored burst.

The desktop camera panel and workspace share one presentation constant for the
initial corrected-overlay opacity. It is 70%, remains operator-adjustable from
0–100%, and does not alter captured pixels or any vision analysis input.

The corrected workspace overlay offers nominal 0.5, 1, 2, 4, 5, 10, and 15 fps
cadences and defaults to 2 fps. The nearest integer timer period for 15 fps is
67 ms. `DesktopController` permits that interval but retains one corrected-frame
worker at a time: periodic ticks that arrive during an in-flight correction are
dropped, while any number of explicit refresh requests coalesce into one
boolean pending replacement. Correction or network throughput therefore limits
the displayed rate without creating an accumulating work queue. This path is
separate from both raw Live Monitor rate selection and configured camera capture
FPS.

## Coordinate domains

The desktop has three deliberately separate coordinate domains:

- machine/controller millimetres and its independently configured output
  authority;
- calibrated camera coverage in machine millimetres;
- honeycomb-local design millimetres, with ruler zero at `(0, 0)` and an
  orthonormal rigid pose derived from the detected square.

Schema-2 and schema-3 projects persist their coordinate-space kind. Legacy
schema-1 files migrate as machine-coordinate projects. A honeycomb-local project stores only
local geometry; the movable support pose remains calibration state. Camera
rectification maps each local output pixel through the rigid support pose and
the complete bed/lens mapping. Project generation performs the inverse boundary
operation: it plans vectors and raster rows locally, validates them against the
support, rigidly places them in machine coordinates, applies laser-spot
correction, and validates both desired-beam and controller paths against the
selected execution authority. Ordinary jobs use the guarded machine rectangle;
an execution-bound honeycomb job may use the separately configured fixed convex
polygon. Generated G-code remains absolute machine millimetres.

Fine registration, dense fit/validation/confirmation, and accuracy validation
detect every expected cross in every burst frame and screen each center with
median/MAD rejection. The default `stable_clarity_consensus` strategy ranks
frames that remain inliers for every mark by clarity and takes each mark's
median across the best 15 stable frames from the 45-frame burst. Configurable
comparison strategies retain the robust median across all surviving samples
and the single sharpest common-inlier frame. Results are rejected when too few
common samples survive or temporal jitter exceeds the configured limit. Their
persisted reports include frame sequences, sharpness, consensus or selected
frame indices, camera control status, inlier/outlier counts, and jitter. Trace,
template matching, workspace captures, and calibration stills use the sharpest
frame from the same stable capture path; continuous UI preview and streaming
remain single-frame operations.

Interactive Trace, template matching, color sampling, workspace refresh, and
ordinary stable stills use a short five-frame burst with a 0.1-second settle,
two discarded fresh frames, and a two-second deadline. They select only the
sharpest image and do not run calibration mark statistics. The 45-frame burst
is reserved for parked-bed fine registration, dense fit/validation/
confirmation, and accuracy validation.

`LensCalibrator` stores captured checkerboard images, a detection cache, and the
solved camera model. Cold status reads image headers but not pixel bodies. Owned
index and solve operations instead load size-capped immutable encoded payloads,
derive content identity and decoded pixels from the same bytes, and recheck the
selected evidence signature before committing; forced re-indexing rebuilds the
advisory cache after external replacement. `BedMapper` stores image/machine point
pairs plus forward and inverse homographies. A dedicated base-map session can
generate a keyed
5×5 job without a prior homography. Two larger interior crosses orient the
regular grid under all rotations/reflections; incomplete, unkeyed, ambiguous,
zero-power, or stale sessions are rejected. The candidate fit is analyzed without
mutating active state, then reviewed points and the fresh homography are
installed transactionally with rollback on persistence failure. A fresh base
map intentionally clears corrections belonging to its predecessor and records
the keyed generated-coordinate labels as normal on both axes. A reviewed
fine-registration result may compose one
persisted, resettable translation onto that homography. Its separate full-map
path fits raw image coordinates to commanded mark coordinates with RANSAC,
checks inlier count, bed coverage, residual, orientation, scale, and modeled
whole-bed displacement, then retains the previous solved map as a rollback
snapshot. A new full solve clears both refinements. The Qt-independent
registration model owns those gates; Qt only presents review and confirmation.

The same Qt-independent model defines five accuracy-validation holdouts and
fixed pass/fail limits. `AppContext` binds every fine-registration and
validation session to the exact active homography and residual-mesh revision,
persists the generated job/capture/report, and rejects legacy, stale, or
zero-power-only sessions. Live capture checks that identity before Home / park and
camera acquisition. The desktop sends both calibration and validation jobs
through the ordinary guarded preview/run pipeline; validation has no write path
to `BedMapper`.

The native `MachineSetupDialog` owns the primary camera, lens, bed-mapping,
fine-registration, and alignment-check workflow. It calls `AppContext`,
`LensCalibrator`, `BedMapper`, and the portable registration model without
introducing Qt into those modules. The browser retains the legacy coarse
calibration surface; fine registration is desktop-only. Only one UI process
should own a physical camera at a time.

## Vision flows

- Rectangular workpiece detection runs on the active rectified image. It reports
  machine-coordinate placement hints for machine projects and honeycomb-local
  placement hints when a current support frame drives the desktop canvas.
- ArUco, keyed unseeded cross-grid, and rough-map-seeded crosshair detection
  support bed mapping.
- Every ordinary non-grid Camera Trace request keeps the full corrected raster
  and established pixel-to-physical transform, but builds an immutable
  eligibility mask. In machine coordinates the hard ROI is the existing guarded
  output polygon or guarded rectangle. In honeycomb-local coordinates it is the
  intersection of the recorded support rectangle and that same guarded output
  geometry transformed into the existing local frame. This restricts vision
  evidence only; it does not enlarge support, planning, G-code, or laser
  authority. Pixels outside the hard ROI cannot enter color analysis,
  normalization statistics, Otsu, components, scoring, or fitting.
- The accepted empty-honeycomb reference is supplied only after its schema,
  encoded-image SHA-256, bed-mapping digest, support-frame digest, coordinate
  frame, rectification, and final dimensions validate. Correlated locally
  detrended luminance, normalized patch error, and compatible local texture on a
  bounded model produce structural evidence but cannot classify bed alone.
  Strong reference-like seeds drive a bounded robust Lab luminance-affine plus
  chroma-offset planar lighting model. Compensated point and patch appearance
  must also match. The retained 3 mm-radius closing starts only from strong
  combined seeds at model resolution and can add only appearance-consistent,
  structurally supported bridge pixels. Changed or uncertain pixels remain
  material-eligible. A changed sheet is therefore eligibility, not foreground.
  Manual Contrast and explicit Color may fall back to hard-ROI-only eligibility
  when no current reference exists; honeycomb-local Auto fails closed instead of
  claiming a reference-aware result. A supplied mismatched reference is rejected.
- Non-grid Camera Trace **By contrast** fills ineligible pixels only in the
  temporary low-frequency background model, derives scale from eligible
  material, forces excluded response white, then uses the complete source-neutral
  imported-raster vectorization pipeline. Auto Otsu or the operator's manual
  threshold applies to that normalized raster, not absolute camera brightness.
  Each root `RETR_TREE` contour plus all descendants is one indivisible temporary
  review candidate; unrelated failing roots do not discard verified peers.
- Non-grid Camera Trace **Auto detect** is orchestration, not a fourth detector.
  One corrected capture and one eligibility result become one immutable camera-
  normalization result. Auto
  estimates the background once and derives both the dark and light
  `PixelVectorizationSource` values from it, then evaluates ordinary shared-
  raster Otsu for each polarity. It also runs a production Color attempt only
  when eligibility-scoped HSV/Lab evidence identifies a coherent hue that is
  neither negligible nor material-background- or boundary-dominated. A Color
  mask above 35% of eligible material or 25% of its boundary is rejected, and
  Color must beat a credible raster result by eight points. Every successful attempt
  returns authoritative native candidates through its ordinary production path,
  and deterministic quality scoring chooses one prepared result for review. A
  score below 70 is not credible, so Auto may return no interpretation rather
  than choosing the least-bad strategy.
- The Trace panel treats those non-grid Auto choices as strategy-owned: hue,
  sample, threshold, and polarity controls are inactive, output is native
  lines/Béziers, and border offset is zero. Explicit Color owns hue/sample;
  explicit non-grid Contrast owns manual threshold/polarity. Grid Auto preserves
  the specialized output and normalization controls.
- Camera Trace **By contrast** with grid enabled retains the specialized
  global/illumination-corrected/adaptive and signed-local multi-mask detector. It
  ranks repeated-grid hypotheses by coherent filled-region support, can classify
  and normalize cells, and can infer missing cells. Auto with grid enabled uses
  this same repeated-object/lattice architecture rather than literal raster
  components; **By color** remains the explicit operator override.
- Live desktop trace capture establishes the photography pose rather than
  trusting prior machine state: Home / park completes first, temporary hold
  encloses only the stable camera frame set, and rectification and vision
  analysis run after the controller's original idle behavior has been restored.
- Rounded-rectangle output fits center, dimensions, rotation, and radius and
  emits an analytic rounded vector. Simplified and exact modes preserve
  pixel-derived contours; simplification is a bounded polygon reduction, not a
  curve-fitting operation.
- Trace review uses one temporary selectable-candidate scene layer. Candidate
  clicks, Ctrl-clicks, and rubber bands update the same detection-ID set as the
  inspector checkboxes; project objects are non-selectable and non-movable until
  review ends. The temporary layer never mutates `ProjectDocument` and has no
  planning, G-code, or execution consumer.
- For a current non-grid request, the Trace **Camera display** selector can show
  the immutable corrected **Camera** frame, exact production **Exposed bed**
  mask, exact material **Eligible** mask, exact polarity-specific **Normalized**
  threshold input (or grayscale context for Color), or the exact 4× production
  contour **Mask** used by `RETR_TREE`.
  The workspace validates 4× dimensions from the already-rounded source raster,
  not by re-rounding the physical area at the higher display density, so a
  fractional final pixel strip cannot reject the exact mask. Raster mask
  preparation publishes those pixels before
  contour extraction and native fitting; request identity and calibration
  signatures reject late or stale previews. These arrays are diagnostic-only
  and cannot create or authorize geometry.
- Corrected camera pixels have the rectifier's explicit constant pixels/mm even
  though the upstream raw-camera homography has a spatially varying Jacobian.
  Camera normalization uses that physical pitch to choose its low-pass scale;
  it never changes image dimensions or the pitch presented to the vectorizer.
  Non-grid contrast uses the raster vectorizer's pixel-center mapping and exact
  requested fit-tolerance semantics before one final local-to-machine affine.
  Auto's raster attempts use that same path. Auto Color, explicit By color, and
  grid paths convert detector contours to physical machine or honeycomb
  millimetres before classification or fitting.
- Identical-grid normalization derives its shared orientation from populated
  row-center baselines and refits the lattice in that orientation. Grid pose
  snapping is independent of dimension normalization: direct cells may retain
  observed centers and rotations while sharing dimensions and radius, whereas
  inferred cells always require a lattice pose.
- Grid detections retain row/column identity and sort in stable row-major order.
  Proposed cells crossing the work area remain visible but are marked outside
  and unselected by default; occupancy and emitted detections therefore cannot
  silently disagree. Desktop Trace capture and color picking use the project's
  coordinate domain: machine projects retain the configured-area behavior,
  while honeycomb-local projects use the current support frame and review the
  mapped machine-output polygon independently.
- The workspace previews the proposed vector that object creation will consume.
  The exact analyzed frame is delivered with the result and remains frozen
  during review. Corrected pixels use the rectifier's exact pixels/mm scale
  instead of deriving a slightly different scale from rounded raster dimensions.
  until the review is cleared or committed.
- The role-labeled overlay key is a viewport control rather than scene geometry.
  It defaults to the upper-left, remains fixed through canvas scrolling,
  workpiece movement, zooming, and refits, and moves only when dragged directly.
- Inferred trace cells are deliberately not selected by default.
- Template alignment compares unordered detections in the active project
  coordinate domain with
  normalized template features, ranks rigid rotation/translation candidates,
  and reports coverage, residual, ambiguity, and scale diagnostics.
- Scale diagnostics are warnings only. The matcher never scales cut geometry.

### Camera-photo raster-normalization contract

The earlier camera/raster convergence treated a rectified photograph as if it
were already artwork. A single full-frame brightness threshold could therefore
promote a broad sheet shadow, vignetting, or a dark machine edge even when the
local sheet was visibly blank. The non-grid raster route now converges one stage
later:

```text
corrected uint8 BGR photograph at a constant pixels/mm
  -> camera-specific low-frequency background normalization
  -> polarity-specific uint8 grayscale raster
  -> PixelVectorizationSource
  -> unchanged shared threshold, cleanup, 4× hierarchy, and native fitter
```

For an image of `W x H` pixels at `p` pixels/mm, the background-model scale is
`s = min(1, 1/p, 512/max(W,H))`. The model dimensions are
`max(1, round(Ws)) x max(1, round(Hs))`, so the expensive low-pass model is at
most approximately 1 pixel/mm and 512 pixels on its longest axis. Grayscale is
converted deliberately to `float32` and reduced with `INTER_AREA`.

One deterministic flat-field guard prevents that finite physical envelope from
turning a large, already-clean solid into a ring. It is deliberately narrower
than general segmentation. The eight most populated four-level-wide bins of the
full-resolution uint8 histogram must cover at least 99.5% of the frame. In the bounded model, a
2 mm outer band has robust median `b`; at least 99.5% of that band must lie within
three grayscale levels of `b`, and that same band around `b` must cover at least
50% of the full model so a machine-colored border cannot impersonate the sheet.
Finally, for `d = abs(I_model - b)`, let
`intermediate` count `3 < d < 32` and `far` count `d >= 32`. The guard requires
`far / (far + intermediate) >= 0.80`, with the ratio defined as one when both
counts are zero. This rejects gradual low-frequency variation even when it has
been quantized into a small palette. When all three tests pass, `B` is the
constant `float32` border level `b`; an interior clean solid can then be larger
than the local envelope without being absorbed. This branch remains one
polarity-neutral background estimate and does not inspect or repair geometry.
The bounded gates accept low-noise flat fields but deliberately fall back for
ordinary photographic entropy or gradual tone ramps. Image-only processing
cannot distinguish every deliberately posterized shadow from flat artwork; a
frame that does not meet every gate uses the rank envelope rather than guessing.

All other frames use the physical rank-envelope model. At each model-axis
pitch, a nominal 35 mm physical diameter becomes the next odd elliptical-kernel
extent, bounded to 35 model pixels. Let `O` and `C` be `BORDER_REFLECT_101`
grayscale opening and closing by that kernel. Their symmetric midpoint is
`E = (O + C) / 2`. The model background is a Gaussian smoothing of `E` at 4 mm
sigma on each physical axis, also bounded to 4 model pixels, and is returned to
full resolution with `INTER_LINEAR`. At the normal 4 pixels/mm corrected pitch,
a 760 x 480 frame therefore uses a 190 x 120, 1-pixel/mm model, a 35 x 35
centered ellipse, and a 4-pixel (4 mm) finishing sigma.

Let `I` be the full-resolution `float32` grayscale image and `B` the upsampled
background. The signed residual is `S = I - B`; uint8 subtraction is never used.
With a three-level sensor-noise floor, the shared magnitude is
`M = max(abs(S) - 3, 0)`. Its nearest-rank 99.5th percentile `q` defines the one
shared response scale `R = clamp(q, 32, 64)`, preventing a few extremes from
setting gain while also bounding noise amplification. The two responses are
`D = max(-S - 3, 0)` and `L = max(S - 3, 0)`. The normalizer exposes each as an
artwork-style raster
`A(X) = uint8(rint(255R / (R + X)))`: blank and opposite-polarity pixels are
white, `X = R` maps to 128, and stronger selected responses approach black
monotonically without the old clipped-black endpoint. Thus the normalized
manual threshold 128 has the concrete meaning "at least the robust response
scale," and level differences supplied by photographic or antialiased input
remain available to 4× reconstruction. A truly two-tone source necessarily
remains two-tone under any pointwise transfer; the unchanged vectorizer still
owns its interpolation and topology behavior. The light adapter inverts `A(L)`
and uses the shared vectorizer's established `invert=True` contract, so its
threshold and polarity behavior remains algebraically identical to ordinary
light artwork.

Automatic raster thresholding retains OpenCV Otsu but accounts for its choice of
the lowest member of an equally optimal plateau. Only when the chosen threshold
has fewer than two levels of low-class interpolation headroom, and unused
grayscale levels exist before the nearest high-class value, does it advance by
at most two levels within that empty gap. Normal polarity measures headroom from
the selected foreground minimum; inverted polarity measures it from the low
background maximum. Source-resolution foreground classification is therefore
identical; ordinary multi-level Otsu, manual thresholds, polarity semantics, and
bicubic 4× reconstruction are unchanged. This bounded plateau nudge prevents a
two-tone endpoint from turning bicubic rounding into false pinholes or islands.

Opening and closing operate only on the bounded temporary background model;
neither is applied to the normalized raster or the production mask. The 35 mm
nominal diameter is larger than the expected 3–10 mm stencil strokes and the
dense 21.5 mm label thickness, while the flat-field guard removes a finite-size
cutoff for qualifying clean interiors. A dark feature is suppressed by the
closing envelope, a light feature by the opening envelope, and their midpoint
retains a symmetric signed response for either polarity; the 4 mm final blur
makes the estimate smooth while following broad illumination. The adapter itself still performs
no threshold, output-mask closing, output-mask opening, gap repair, stroke
growth, hole filling, classification, or geometry inference. Deterministic
clean-raster coverage verifies exact Otsu geometry for qualifying clean inputs,
including dark and light 40 x 40 mm interiors.

`CameraRasterNormalizationResult` owns defensive, immutable-byte-backed,
C-contiguous read-only arrays for corrected BGR (`uint8`), grayscale (`uint8`),
background and signed residual (`float32`), and both normalized rasters
(`uint8`). Its frozen diagnostics record the versioned content key,
physical/image/model dimensions, effective model pitch, envelope diameter and
odd kernel dimensions, smoothing sigma, noise floor, percentile, observed
robust level, reciprocal scale, selected background-model kind, and flat-field
palette/border/separation coverage. It is temporary analysis data with no project,
planning, G-code, or laser authority and never fabricates
`RasterAssetIdentity`.

Opt-in non-persistent timing keeps capture and rectification at the desktop
boundary, records `grayscale_preparation`, `background_estimation`,
`normalization`, and `camera_normalization_total` in the camera adapter, and
retains the shared vectorizer's mask, component-cleanup, 4× preparation,
contour-extraction, native-fitting, and validation stages. Auto records each
raster attempt separately plus one normalization key and a background-estimate
count of one. Timing is diagnostic only; it neither chooses an algorithm nor
relaxes any validator.

This boundary applies only to non-grid Contrast and Auto's dark/light raster
attempts. Explicit **By color**, Auto's conditional Color strategy, and all
**Use grid** production masks retain their chromatic or specialized repeated-
object implementations. Deterministic synthetic gradient/shadow, polarity,
gap/hole, clean-raster parity, immutability, and timing coverage exists; the
physical Coleman stencil scene has not yet been rerun and remains pending.

All vision accuracy depends on current lens calibration, bed mapping, camera
pose, material height, focus, lighting, and resolution.

## Authoring and toolpath flows

### Browser pipeline

```text
SVG text
  -> geometry.svg.parse_svg()
  -> DesignPlacement
  -> placed polylines
  -> work-area validation
  -> gcode.generator.generate_vector_gcode()
```

This is a single-design workflow. Placement state lives in browser memory and
generated files are written under the configured application data directory.

### Desktop pipeline

```text
native shapes / imported SVG / traced outlines / native cubic paths
  -> SceneObject instances
  -> ProjectDocument operation layers
  -> undoable CommandStack changes
  -> project.job_preflight.build_job_preflight_report()
  -> immutable structured findings (blockers stop before exact generation)
  -> project.toolpath.generate_project_gcode()
  -> finalized multi-layer vector G-code + controller-ignored E3 metadata
  -> gcode.job_plan.build_job_plan()
  -> immutable JobPlan used by the dedicated desktop Preview
  -> window-modal exact review and distinct START JOB signal
  -> unchanged guarded main-window run path
  -> the same finalized G-code text is submitted to MachineService
```

#### Desktop presentation topology (`v7`)

`E3MainWindow` keeps `WorkspaceFrame` as the central widget and uses one
full-height right `InspectorTabs` dock for Cuts/Layers, Camera, Objects, Shape,
Templates, Trace, Machine, and Material Recipes. Machine and material pages
reuse their existing panels; moving them does not move state or authority out
of `AppContext`, `DesktopController`, or `MachineService`. The former lower
raw-G-code and Laser/job docks do not exist. The optional Console remains a
separate bottom dock that is hidden by default, so it reserves no normal
workspace height.

`JobProgressWidget` projects the existing preparation and controller-job status
into the main status bar. It keeps prepared maximum power distinct from live
execution percentage and finishing phases. Templates and Trace each emit into
the same main-window Generate `QAction` used by the Job toolbar; they do not own
another planning path. Finalized G-code remains in the prepared job for exact
Preview, explicit export, and guarded submission, but it is not mirrored into a
persistent main-window text pane.

The status bar measures its live text at layout time. It always reserves room
for a compact preparation/execution label, then admits runtime, zoom, and
editing/cursor/selection readouts only when they fit. Hidden runtime detail is
retained on the status-bar tooltip; complete preparation, prepared-power, and
controller summaries remain on the progress tooltip. `QStatusBar.messageChanged`
drives the same layout policy for temporary notices: progress retains its
readable minimum, editing details are suppressed, runtime and zoom remain only
when the notice also fits, and the normal responsive widgets return when the
notice clears.

`RuntimeSafetyStrip` owns only the primary presentation and request signals for
Connect/Reconnect, Disconnect, the deliberately disabled Pause, and software
STOP. `DesktopController` receives those requests and retains the existing
`MachineService` boundary. STOP stays enabled during ordinary background work.
On compact windows the non-hideable strip moves to its own toolbar row, and when
its allocated width falls below 1100 logical pixels it reflows into a status row
above a control row. This reflow changes no connection, motion, arming,
authorization, STOP/`M5`, or execution rule.

The dedicated window-modal exact Preview remains the only visible **START JOB**
gate. Its signal dismisses Preview and enters the same guarded main-window run
path described below. Layout `v7` may reuse compatible older geometry and tab
choices, but it does not restore opaque `v6` dock state that describes the
removed bottom row.

`ObjectPanel` treats each row's color control as a request for its assigned
operation-layer ID. `LayerPanel` remains the single color-dialog owner, and its
`layerEdited` signal continues through `E3MainWindow._layer_edited()` and
`UpdateLayerCommand`. The normal history refresh therefore updates every object
on the shared layer, the workspace, Cuts/Layers controls, and the palette while
retaining prepared-job invalidation and undo/redo behavior.

The structured job-preflight boundary is Qt-neutral and advisory.
`PreflightSeverity` classifies findings as info, warning, or blocker;
`PreflightFinding` carries a stable dotted code, title, message, optional detail,
and immutable structured context; `JobPreflightReport` carries the ordered
findings, severity counts, and derived `ready` / `has_blockers` state. The
builder receives only a detached project snapshot plus a detached
`JobPreflightContext`. It does not construct `SceneObject` values, flatten
geometry, decode pixels, generate G-code, call `MachineService`, or communicate
with a controller.

Preflight projects existing preparation rules into one reviewable report. It
checks project/machine work-area agreement; machine-coordinate versus
honeycomb-local frame, support signature, guarded polygon, and calibration-
profile identity; the read-only bed-calibration validity and honeycomb-support
CURRENT state required for local output; layer/object/output eligibility and
operation-setting validity; configured machine work/travel feed ceilings; and
bounded raster headers and aggregate encoded/decoded/row/sample/command budgets.
Stale bed-calibration or support readiness blocks honeycomb-local generation.
Only provably exact local bounds for unrounded rectangles and valid two-point
lines are eligible for structured bounds blockers. Rounded rectangles, ellipses,
images, paths, and other complex geometry bounds remain explicitly deferred
with vector flattening, fill and raster construction, laser-spot correction,
raster payload identity/decode, motion placement, final stream limits, and
G-code validation. Stable finding codes are the programmatic contract; display
text can improve without changing those identifiers. The existing strict
`generate_project_gcode()` planner may therefore still reject a report that has
no blockers, and its result is the only geometry/program source for exact
Preview. `MachineService` preflight and the guarded start path remain the only
execution authority.

Desktop Generate extends the existing asynchronous owner chain rather than
adding a synchronous UI pass:

```text
GUI captures detached configuration/coordinate facts
  -> owned worker clones the current ProjectDocument
  -> worker builds JobPreflightReport
  -> blockers: release owner and show non-modal structured report
  -> ready/warnings: same request and cancellation token enter exact planning
  -> bounded GUI rendering opens exact Preview with the report embedded
```

Project revision, STOP, replacement, application close, feed-ceiling changes,
and coordinate/calibration/support authority changes invalidate the detached
request under the same token and stale-result checks used by exact generation.
No worker creates or mutates Qt objects. Blocking review is modeless so it does
not hold preparation ownership or hide software STOP; the exact generated-job
Preview remains the window-modal execution review gate.

The staged-planning contract is:

```text
SceneRevision
  -> NormalizedGeometryArtifact
  -> OperationArtifact
  -> PlacedGeometryArtifact
  -> ControllerGeometryArtifact
  -> EncodedProgramArtifact
  -> immutable JobPlan
```

Every artifact declares its coordinate domain, stage version, bounds, warnings,
statistics, and provenance. The first increment moved the existing layer/raster
working records into `laser_aligner/planning/model.py`. LINE-layer object
geometry now crosses `NormalizedGeometryArtifact`, `OperationArtifact`,
`PlacedGeometryArtifact`, and `ControllerGeometryArtifact`, and the exact
finalized program text crosses `EncodedProgramArtifact` before the immutable
`JobPlan` is built. Placement and laser-spot correction still use the existing
`_place_paths()` and `_controller_paths()` implementations unchanged. The
encoded artifact does not rewrite G-code; it records program statistics, the
current encoder provenance, the staged LINE controller-artifact identities, and
the count of non-LINE layers that still use the legacy internal path.
`project.toolpath.generate_project_gcode()` remains the compatibility entry
point and orchestrator. `SceneRevision.source_digest` is a canonical SHA-256
fingerprint of persisted planning source content; project ID, timestamps, and
the monotonic revision counter remain separate identity and bookkeeping fields.
The normalized-geometry stage is version 2. Native path anchors and controls are
first transformed into physical project millimetres and then flattened exactly
once with the deterministic native-path algorithm at
`NATIVE_PATH_FLATTEN_TOLERANCE_MM = 0.025`. The tolerance and algorithm version
participate in the normalized dependency identity, while
`polyline_sequence_digest()` remains at the downstream flattened boundary.
LINE geometry artifacts also carry a deterministic `dependency_digest`
separate from their run-oriented `artifact_id`. The normalized digest covers the
ordered source geometry consumed by that layer, the operation digest adds layer
settings, the placed digest covers effective geometry plus the exact coordinate
frame, and the controller digest covers placed geometry plus laser spot offset.
A revision-only change can therefore produce a new artifact ID while retaining
the same dependency digest. The first reuse boundary is now opt-in normalized
LINE geometry through a caller-owned `PlanningCache`. The cache is bounded,
memory-only, keyed by the normalized dependency digest, and stores copied
geometry payloads rather than artifact metadata. `E3MainWindow` owns one
session-long cache and passes that exact object into each background project
planning snapshot, so normal generate/edit/regenerate cycles can reuse unchanged
normalized geometry. The cache is lock-protected because planning executes off
the GUI thread. Placement is the second reuse boundary: identical effective LINE
geometry plus the same coordinate frame reuses the copied machine-beam paths,
while the normal placed-path safety validation still runs afterward on every
planning request. Controller geometry is the third reuse boundary: identical
placed geometry plus the same laser spot offset reuses copied controller-space
paths, while controller-path bounds or guarded-polygon validation still runs
afterward on every request. Speed or power changes therefore keep normalized,
placed, and controller geometry reusable; geometry, coordinate-frame, or spot-
offset changes invalidate only the applicable dependency keys. Every hit still
constructs fresh artifact metadata for the current project revision. Cached
paths are copied on store and retrieval so downstream path mutation cannot
contaminate later runs. There is no module-global cache, disk persistence,
operation reuse, raster/fill cache, or encoded-program cache yet.
`EncodedProgramArtifact` is intentionally not assigned a cache dependency
because the final stream still contains volatile generation-time text and
whole-job dependencies.

Cache performance is measured explicitly rather than inferred from hit counts.
`scripts/benchmark_planning_cache.py` compares uncached generation, cold-cache
generation, warm identical regeneration, speed-only and power-only edits,
spot-offset invalidation, and one-layer geometry invalidation. It reports local
median/min/max wall time plus normalized/placed/controller hit-miss deltas and
verifies the expected dependency pattern without enforcing a timing threshold in
CI. Performance claims should be recorded from an actual benchmark run rather
than from unit-test duration.

`parse_svg()` retains source user-space polylines and records the exact mapping
from that coordinate system to physical millimetres. Absolute root dimensions
use CSS physical-unit conversions (`96 px = 1 in`); `viewBox` and
`preserveAspectRatio` establish the viewport transform, while a viewBox-only
document treats its user units as CSS pixels. The desktop applies this mapping
before its SVG-Y to machine-Y inversion, then centers the resulting physical
bounds at the requested project placement. Group transforms therefore affect
shape geometry before physical sizing without changing the placement center.

Desktop SVG import is fail-closed. Selector-based CSS, clip paths, masks,
markers, dashed strokes, and geometry-changing CSS are rejected by the parser.
Any remaining lossy warning, such as ignored text or embedded raster content,
also stops desktop import before `SceneObject` creation. Operators must convert
that content to explicit vector paths in the source editor. Its bounded scan
uses the same capped parser to produce detached `SvgGeometry` facts without
constructing a `SceneObject`; the strict loader reparses the approved bytes and
remains authoritative for geometry.

SVG, raster-image, LightBurn, and foreign-G-code desktop imports share the
pre-import review boundary:

```text
selected foreign file
  -> bounded format-specific scan_*_file()
  -> immutable ImportScanManifest + exact source-byte SHA-256
  -> bounded window-modal ImportReviewDialog and explicit non-blocked approval
  -> exact-byte verification + existing authoritative strict parse/probe
  -> native E3 conversion and existing undoable commit path
```

The review presents every manifest category, including discovered layers or
reconstructed operations and explicit errors/unsupported features. Its Qt
projection preserves manifest order while showing at most 200 layer/operation
rows and 200 entries per repeated text section, with an exact omitted count;
the immutable manifest remains complete. A blocked manifest cannot be accepted.
Cancel occurs before the selection tool, project document, command stack,
active layer, or selection is changed. The manifest is advisory rather than a
geometry source: the existing strict parser/probe paths remain authoritative
and can still reject details not fully established by the scan. They verify the
bytes they consume have the reviewed SHA-256, so changed content cannot be
imported under stale approval. SVG retains its one-object undo command;
LightBurn and G-code retain one atomic import transaction; raster retains its
existing object command and, when needed, preceding raster-layer command.

#### Imported raster vectorization

Raster vectorization is a second, explicit authoring step and does not replace
the raster importer. The Objects panel exposes **Trace image to vectors…** only
for exactly one selected `ObjectKind.IMAGE`. The workflow is identity-bound from
the pixels already approved and displayed through project mutation:

```text
selected IMAGE + workspace payload identity
  -> read_raster_asset_payload(expected_source_sha256=displayed SHA-256)
  -> window-modal RasterVectorizationDialog
  -> coalesced quick worker: decoded original + mask + preview-only raw outline
  -> coalesced exact worker: native line/cubic fit + authoritative validation
  -> replace quick overlay with the verified native-path flattening
  -> strict exact-source SHA-256 recheck after approval
  -> one FunctionalCommand for layer + PATH + source replace/visibility choice
```

`PixelVectorizationSource` is the immutable decoded-pixel contract beneath asset
provenance. It owns RGBA, grayscale, white-composited grayscale, alpha, an
optional source-neutral eligibility mask, and a content-derived pixel key. With
eligibility, excluded pixels do not enter Otsu and can never become foreground;
the nearest-neighbor 4× eligibility gate is applied again after component-hole
reconstruction, so interpolation cannot resurrect an excluded pixel.
`RasterVectorizationSource` wraps those same pixels
with a legitimate `RasterAssetIdentity`; only that wrapper performs exact
encoded-byte, path, format, size, and SHA-256 verification. Camera Trace's
normalized dark/light rasters enter through the source-neutral contract; the
corrected photograph and its background model remain in the separate temporary
camera-normalization result, and neither layer synthesizes an asset identity.
`PixelVectorizationResult` likewise owns shared mask, contour, native path,
hierarchy, error, and preview data, while `RasterVectorizationResult` adds
imported-asset provenance and preserves existing project metadata.

`RasterVectorizationOptions` is a frozen validated value. Automatic detection
uses Otsu thresholding over the white-composited image and applies alpha as an
independent mask gate; manual mode uses the selected 0–255 threshold; alpha mode
is available only when decoded opacity contains spatially useful variation.
When eligibility is absent—as it is for ordinary imported rasters—the histogram,
source key, mask, hierarchy, and geometry retain their prior behavior exactly.
For camera Contrast, that image is the selected normalized response, so Otsu or
the manual value measures local contrast rather than absolute exposure.
Inversion changes foreground polarity, and
the connected-component cleanup interprets minimum feature area in square
millimetres from the selected image's displayed size, removing both small
foreground islands and enclosed background pinholes while retaining larger
holes. Preview requests use a
160 ms debounce by default. Quick and exact work each retain at most one running
request plus the latest pending options, so slider input cannot build an
unbounded task queue. A newer request immediately makes the prior result
ineligible for creation; stale quick or exact completion cannot replace it. The
quick stage decodes the source once, builds the production 4× mask and raw
contour tree, and derives a bounded display-only approximation. The exact stage
reuses that immutable preparation for identical settings, but ignores the quick
geometry and performs the complete native fit and validation itself. The overlay
is a cached Qt vector path drawn over the exact source image. Its
magenta, cyan, yellow, white, or black preset and 0–100% opacity are display-only
state: changing them repaints locally without changing options, metadata,
geometry, project layers, or output authority. The dialog and portable
vectorizer do not call `DesktopController`, `MachineService`,
camera, planning, G-code, or execution paths.

`geometry.foreground` owns source-neutral binary connected-component cleanup,
bounded `RETR_TREE` extraction, deterministic outer-tree decomposition, and
even-odd tree rendering. It also owns conservative post-extraction pruning of
non-geometric contour nodes. Bicubic 4× grayscale reconstruction can overshoot
near a retained edge inside the one-source-pixel component halo and create an
isolated threshold pixel; OpenCV then reports a one- or two-point, zero-area
contour even though base-resolution component cleanup already ran. Pruning
removes only contours with fewer than three distinct trace points or zero
trace-pixel polygon area. It retains every positive-area contour, rebuilds the
complete `next`/`previous`/`first_child`/`parent` array in original sibling order,
and rejects a complete root when a degenerate ancestor has a non-degenerate
descendant instead of changing even-odd depth by reparenting. Quick Preview and
exact vectorization consume the same repaired tree, so imported images, manual
camera Contrast, and Auto raster attempts receive identical behavior and
diagnostics. The imported dialog and camera Trace UI remain separate consumers
of the same portable result.

The one authoritative fitter remains in `project.raster_vectorize`:
hard-corner and rotation-independent straight-run classification, persistent
line decisions, constrained cubics, bounded Newton reparameterization/centering,
continuous error proof, frame/extrema checks, self/adjacent-arc validation,
compound clearance, and outer/hole hierarchy are identical for imported and
camera pixels because both now enter the complete pixel pipeline.
`project.native_contour_fit` remains the source-neutral adapter for specialized
Color and grid detector masks that already exist as ordered physical contour
trees; Auto's conditional Color attempt uses that adapter, while Auto's raster
attempts use two `PixelVectorizationSource` values derived from one immutable
camera-normalization result. The adapter does not reconstruct non-grid raster
geometry.

Auto strategy failure and candidate failure are separate. A failed attempt is a
bounded diagnostic and does not stop later strategies. Raster Auto asks the
shared vectorizer for root-isolated results; Color Auto isolates fitting at the
same root-tree boundary. In both cases one root plus every hole and island
descendant is indivisible. A rejected compound tree is never split into fake
objects. The raster forest first runs each ordinary global validator. Only a
non-complexity global failure starts per-root diagnosis; complete surviving
trees are rebased and the unchanged global validator runs again. A cross-root
failure for which every tree passes alone and every complexity-limit failure
remain fatal to the strategy. Valid independent roots may therefore remain
without weakening the frame/extrema, continuous fitting-error,
self/adjacent-arc, compound-clearance, even-odd, or rasterized-hierarchy
authority.

Completed strategies are scored without rewarding raw candidate count. For
normalized terms `V` (valid-root ratio), `F` (useful foreground occupancy), `B`
(non-foreground border quality), `A` (useful retained physical area), `S`
(non-microscopic-root fraction), and `W` (in-frame fraction), the score is
`40V + 20F + 15B + 10A + 10S + 5W - P`. `P` is 35 when foreground and border
occupancy both reach 75%; a result at or above 95% foreground with at least 75%
border foreground is rejected outright. More exactly, with foreground fraction
`f`, minimum feature area `m`, and frame area `r`, the useful floor is
`u = min(0.08, max(0.002, 0.25m/r))`. `F` is `f/u` below `u`, `1` from `u`
through `0.60`, `(0.95-f)/0.35` between `0.60` and `0.95`, and `0` afterward.
`B = 1 - border_fraction`; `A = min(1, valid_area / max(4m, 0.01r))`;
`S = 1 - roots_below_4m / valid_roots`; and
`W = roots_inside_frame / valid_roots`. `V` is the valid-root count divided by
valid plus unusable roots. Any score below 70 is rejected. Exact-score ties have
stable priority: dark raster,
light raster, then Color. The selected result records compact attempt status,
reason, threshold/polarity or hue, occupancy, root counts, invalid counts, score,
and score terms; the operator sees only the selected-strategy summary.

`RasterVectorizationTiming` is opt-in development/test instrumentation and is
never attached to project data. It accumulates inclusive elapsed time and call
counts for image decode/preparation, threshold, mask generation, contour extraction,
corner classification, source-edge refinement, cubic fitting, Newton
reparameterization, continuous fit validation, adjacent merging, authoritative
topology, preview flattening, and rasterized hierarchy validation. Timing does
not select algorithms or relax a budget. The five-million-step
continuous-validation limit remains authoritative;
profiling the Coleman stencil showed that proof was inexpensive compared with
Newton refinement, so it was not weakened or bypassed.

Contour extraction interpolates the immutable pixel source to a 4× internal mask,
uses `RETR_TREE` hierarchy, and maps contour samples into physical coordinates
before fitting. The extracted contour and hierarchy remain the topology
authority. For an independent contour, the exact stage estimates a local normal
over 1.25 source pixels and samples the original composited grayscale and alpha
fields at 0.125-pixel intervals over ±1.25 source pixels. The foreground margin
uses the same manual/Otsu/alpha threshold and inversion semantics as mask
generation. A sample moves only when it has one strong foreground-to-background
crossing, sufficient endpoint margin, contrast and slope, bounded reverse
variation, and a displacement no larger than 0.6 source pixel. Sampling is
chunked to 8,192 contour points. Flat, noisy, multiple-crossing, out-of-frame,
and otherwise unsupported profiles stay at their threshold position. Contours
that participate in nesting are not refined. For imported assets those pixels
come from the verified bounded payload; for non-grid Camera Trace they are the
exact normalized-polarity pixels already used for thresholding, not a second
sample of the raw photograph. Threshold, mask, hierarchy, source-edge evidence,
and native fitting are otherwise the same computation.

Hard corners and their adjacent support samples, along with every persistent or
hard-anchor-promoted straight run, remain fixed. Corner and straight-run
classification for fitting is still performed against the original threshold
contour, so source localization cannot erase a corner or bend a known line. The
source-edge maximum displacement is included in the conservative deviation
envelope. Existing native frame, continuous-error, topology, clearance, and 4×
raster-hierarchy validation remains authoritative. The user-facing 0.10 mm
tolerance and fixed 0.08 mm internal fit budget are unchanged; there is no local
per-span tolerance rule.

Each closed contour is rotated to a coordinate-canonical start
before anchor selection, so OpenCV's arbitrary cyclic start index cannot change
the fit. Corner classification compares turns and straight-arm support across
multiple physical arc-length scales; isolated raster steps are not hard anchors,
while persistent stencil corners retain their exact samples. Before smoothing or
anchor selection, every contour also classifies generic persistent straight runs,
even when hard corners already exist. The evidence combines physical fit
tolerance, source-pixel pitch, oversampled contour pitch, full-run chord residual,
and directional change, so it is rotation-independent and does not mistake a
short pixel plateau on a curve for a source line. Nearby raster-step fragments
merge only when the complete combined run passes the same evidence. Run
boundaries become seam-independent anchors and are protected from smoothing.
Smooth anchors and recursive non-corner splits share tangent directions across
joins. A classified span becomes a native line only after the ordinary
continuous fit validation passes again; absent that positive classification, a
shallow arc remains cubic even if its endpoint chord alone falls within the fit
tolerance. Other spans use bounded cubic Béziers. Cubic candidates use a
positive, tangent-constrained handle solve followed by bounded Newton
reparameterization.
Before accepting a material cubic at its chord-length point correspondence, the
fitter also measures arc-length-weighted RMS error, signed normal bias, and the
fraction of error lying on one side of the curve. A maximum-error-compliant but
materially biased distribution is sent through up to three existing bounded
Newton reparameterizations. This centering gate does not lower the requested
tolerance, add raster stair-step anchors, or replace the continuous proof.
Every accepted line or cubic has a conservative continuous error proof: each
target edge and the corresponding restricted Bézier interval form a difference
cubic whose control hull bounds all between-sample deviation. A candidate with
a hidden lobe, a current-main exact cubic self-topology ambiguity, or excessive
error is split instead. Adjacent like-kind pieces may merge only after a fresh
solve, the same continuous proof, current frame constraints, and the current
adjacent-native-arc topology check all pass. Those fitted segments are stored as
the authoritative native subpath. Separate adaptive flattening supplies only
overlay, topology, diagnostics, and complexity estimates. Source-supported
straight runs therefore remain native lines while curved regions, rounded
corners, and adjoining transitions remain cubic segments. The
result reports raw contour points, fitted segments, preview-flattened points,
validated maximum/mean/RMS fit error, detected hard corners, recursive splits,
verified merges, longest smooth-span size, and maximum estimated deviation.
That deviation includes accepted source-edge displacement, optional smoothing,
fitting error, and preview flattening; it is not a
physical-accuracy certification of the source image. Highly pixel-constrained
glyphs remain an accepted first-release quality limitation because
narrow counters and curved shoulders can still vary with raster phase and
threshold; those cases require a cleaner or higher-resolution source.

Digital boundary transitions are counted at source resolution before the 4×
workspace and again before full-point contour extraction, so a single connected
maze or jagged photographic mask cannot defer the raw-point rejection until
after an oversized contour allocation. Corner non-maximum suppression uses a
bounded circular exclusion window rather than pairwise corner comparisons.
After fitting, exact native cubic extrema are checked against the image-local
frame; an excursion is rejected rather than hidden by clipping only the preview.
Preview points are regenerated from that authoritative native path in physical
millimetres. Duplicate/zero-area contours are rejected. Bounded exact checks
reject cubic self-intersections and ambiguous adjacent native arcs; adaptive
flattening then requires more than the combined curve-error envelopes between
non-adjacent and inter-contour boundaries while verifying the extracted ancestry
in float64. E3 finally rasterizes the transient flattening at the capped 4×
resolution and compares the complete parent/child hierarchy signature with the
extracted tree.

The option, contour, and result records are frozen validated values. Result
validation checks immutable preview arrays, source identity, normalized contour
coordinates, hierarchy/count consistency, and the reported maximum deviation.

The created object is one schema-3 `NativePathGeometry` with `path_version: 1`,
explicit `fill_rule: "evenodd"`, and one closed native subpath for each retained
contour. Each subpath stores only line and cubic segments. It is normalized to
the image-local frame and the source image `Transform` is copied, preserving
displayed width/height, center, rotation, and horizontal/vertical mirrors.
Parent/depth/hole provenance is retained in metadata. Outer contours and holes
remain separate closed subpaths in one PATH; downstream containment planning is
winding-independent and schedules nested contours deepest-first. Preview and
topology polylines are ephemeral analysis data and are never persisted beside
the native path.

The portable vectorizer rejects work above these production limits and returns
guidance to increase minimum feature size or native fitting tolerance, adjust
threshold, or use cleaner artwork:

- 67,108,864 pixels in the 4× internal contour workspace;
- 4,096 retained connected foreground components;
- 8,192 extracted contours;
- 1,000,000 total extracted raw contour points before simplification;
- 100,000 fitted line/cubic segments;
- 5,000,000 bounded continuous fit-validation steps; and
- 250,000 preview/topology flattened points.

The native path model independently caps one object at 8,192 subpaths and
100,000 segments, a project at 250,000 native segments, one flattening result
at 250,000 points, recursion at 18 subdivisions, JSON nesting at eight levels,
and coordinate magnitude at 1,000,000. Limit failures reject deterministically
and recommend simplifying the source artwork.

Replace removes the IMAGE and adds the PATH in the same undo command. Keep adds
the PATH after the IMAGE so it is visually above the unchanged source, and can
hide the source in that command. The new PATH is selected and its layer becomes
active. The active layer is reused only when it is visible `LayerMode.LINE`, 0% power, and
output-disabled; otherwise the transaction creates a visible
`<image name> trace` layer with exactly those non-output settings. This is an
ordinary editable Line layer whose swatch uses the existing layer color picker;
preview-overlay color remains independent. Vectorization
does not generate or authorize a program, enable output, connect to hardware,
Home, move, arm, or start a job. Subsequent output still enters ordinary
Generate, exact Preview, preflight, and guarded execution.

`JobPlan` models the final stream rather than a second approximation of the
project geometry. It records motion order, elapsed time, feed, controller power,
layer/pass/source context, and the physical laser-spot coordinates recovered
from the generated offset comment. Preview-only choices such as travel
visibility, playback speed, color inversion, and power shading never mutate the
program. Project revision changes invalidate both the program and its Preview.
Start Here replacement programs carry the configured photography pose in job
metadata, so their rebuilt plan includes the actual laser-off approach from the
controller's Home/park position to the reviewed move boundary.

The Preview is a window-modal UI gate, not a machine-authority layer. Its
**START JOB** signal closes the dialog synchronously so STOP is available, then
calls the existing guarded main-window run path. Main-window controls can reopen
Preview but cannot submit a job directly. Start Here remains a non-executing
replacement operation and the replacement enters the same exact-preview gate.

The desktop supports line, fill, and raster operation layers. Fill and binary
vector raster convert closed silhouettes into angled scanlines. Imported images
are alpha-composited onto white, sampled on an exact physical-pitch lattice at
the operation's angle in the active project frame, area-prefiltered when
that lattice minifies the source, and converted with deterministic 8x8 ordered
grayscale dithering. The source top edge follows the
same positive-project-Y, mirror, and rotation transform shown by the canvas.
One shared PNG/JPEG/BMP contract bounds encoded bytes, dimensions, bit depth,
channels, and conservative decoded bytes before decode; TIFF is rejected. The
pre-import scan uses that encoded-payload/metadata contract without decoding
pixels or constructing a project object, then reports pixel and display-sizing
facts through the shared manifest. `read_raster_asset_payload` returns metadata,
SHA-256 identity, and the exact bounded stable encoded bytes from one read, and
can require the reviewed digest before desktop import proceeds. Qt workspace
pixels and toolpath identity therefore cannot come from different file
versions. Workspace items retain that
displayed identity across unrelated project refreshes and share their decoded
memory budget across current project sources. Project jobs carry exact raster
identities for later authority checks and must match the canvas identities before
desktop acceptance.
Aggregate row, sample, vector-edge, span, and stream-command work is also capped
before or during planning. Missing,
undecodable, empty, over-resolution, or over-budget assets are rejected. Raster
rows retain serpentine source order instead of entering the nearest-path
planner. Each row traverses its complete image or silhouette span; lead-in,
white gaps, and lead-out remain laser-off at the engraving feed. Both desired
motion and spot-corrected controller motion are bounds checked. Image bounds
also participate in zero-power framing, including rotation and mixed vector projects.
Vector nearest-path planning falls back to recorded source order above 512
paths rather than entering an unbounded quadratic search. Closed vector paths
are grouped by geometric containment independently of winding. Participating
contours run deepest-first and complete all layer passes per contour before a
parent begins; unrelated paths retain pass-major source/nearest scheduling.
Text-to-path, selectable dither algorithms, and calibrated grayscale power
curves remain unsupported and must never be silently dropped.

Native cubic authorization is checked before output in every relevant
coordinate domain. Rectangular authorities use exact cubic derivative extrema.
The arbitrary guarded convex polygon uses recursive de Casteljau subdivision
and the Bézier convex-hull property, with the flattening error envelope included
in the proof. Local project geometry, honeycomb-placed beam geometry, and
spot-corrected controller geometry are checked independently; the ordinary
flattened-path, final G-code, and `MachineService` checks still run and cannot
be bypassed by planning-cache reuse.

For a selected rectangle, the Transform inspector edits width, height, and the
absolute corner radius. `UpdateObjectShapeCommand` validates and applies the
transform and geometry together, so a resize that constrains the radius remains
one undoable document revision. The radius cannot exceed half the smaller
dimension.

Reusable label-sheet geometry follows a separate portable flow:

```text
RectangleGridSpec                         visible output SceneObjects
  | validate <= 500 cells                    | clone project geometry
  | derive pitch and centered cells          | normalize combined bounds
  | store editable authoring metadata        |
  +----------------------+-------------------+
                         v
             normalized cut objects and features
                         |
                         v
             versioned .e3template library item
                         |
                         v
             resilient catalog and file diagnostics
                         |
                         v
       manual selection or ranking on one frozen frame
                         |
                         v
            reviewed rigid center and rotation
                         |
                         v
       one AddObjectsCommand into the active project layer
```

Templates preserve cut geometry and relative spacing but do not preserve a
project's operation-layer settings. The target project's active layer owns the
created objects and its speed, power, and pass settings. The optional
`marker_id` field is reserved metadata; no marker detector consumes it yet.
Independent closed outer contours inside one imported SVG path become separate
matching features; contained hole contours do not. The UI excludes malformed
or duplicate-ID library entries without hiding unrelated valid templates.

`RectangleGridSpec` is Qt-independent. It stores edge gaps as the canonical
spacing and derives center pitch and footprint. The desktop designer may accept
either edge gap or center pitch, but converts to one unambiguous portable recipe.
Grid authoring metadata is optional: templates built from a project retain
arbitrary cut geometry and are not inferred or relabeled as editable grids.
Editing a grid uses exact-ID atomic replacement so a renamed template cannot
leave a second file with the same persistent ID.

Template matching features contain center, dimensions, and orientation, not
rounded-corner radius. Templates that differ only in radius cannot be separated
by geometry ranking and require explicit user selection and overlay review.
See [CUT_TEMPLATES.md](CUT_TEMPLATES.md) for the format and verification
boundary.

Material recipes remain a separate authoring input over the existing
`MaterialPreset` / `MaterialDatabase` implementation:

```text
SQLite MaterialPreset
  -> exact stable machine/tool-head profile compatibility
  -> explicit compatible recipe selection
  -> one UpdateLayerCommand
  -> ordinary portable OperationLayer values
  -> structured preflight and exact planning inspect those values normally
```

Legacy and unscoped custom rows are universal. Scoped rows use exact stable
motion-platform and tool-head profile IDs; no local machine UUID, fuzzy match,
power conversion, or speed scaling participates. Exact machine/tool matches are
shown first, followed by tool-only and universal recipes. Incompatible rows can
remain visible for editing but cannot be applied. Applying a recipe updates the
complete controlled operation settings and an optional recommended color while
preserving the layer ID, authoring name, visibility, priority, and current
`output_enabled` value. It creates no geometry and carries no recipe ID into the
project schema. Hand-authored layers remain fully supported, and
`JobPreflightReport`, the exact planner, `MachineService`, and the guarded start
path continue to inspect only the resulting concrete layer/program state.

The 13 operator-supplied E3 10 W new-project operations and their matching
machine/tool-scoped built-in recipes project from one curated value source.
New-project layers retain their historical field values and output state;
recipe application deliberately does not copy that default output bit. SQLite
schema migration retains legacy row IDs and values, assigns deterministic
defaults only to newly introduced fields, treats old rows as universal, and
uses insert-only default seeding so newer user edits are not overwritten.

Both pipelines generate conservative G-code that is revalidated by
`MachineService` before execution.

## Coordinate conventions

- Machine X increases to the right.
- Machine Y increases toward the top/back in the rectified workspace.
- Browser and image pixels increase downward, so UI/image Y conversion is
  inverted.
- SVG coordinates normally increase downward. Import/placement flips SVG Y so
  artwork appears visually upright in the active project coordinate domain.
- Positive project rotation is counter-clockwise in the active project frame.
- Machine-project coordinates describe the desired physical laser-spot
  location directly. Honeycomb-local project, camera, and template coordinates
  first describe that location in the rigid support frame. Generation places
  local geometry in machine coordinates before applying
  `laser.spot_offset_*_mm`, which is the physical spot relative to the commanded
  controller reference. Local geometry, placed spot geometry, and shifted
  controller motion are checked in their respective boundaries. G-code
  comments carry the offset so Preview can recover the physical spot path.
- Corrected-image coordinates address pixel centers: OpenCV pixel `(i, j)` maps
  directly through the bed transform. The desktop offsets the Qt pixmap by
  `(-0.5, -0.5)` local pixels so its displayed centers, vector overlays, color
  picking, and vision output share that convention.
- Template feature coordinates are local to the center of the combined cut
  bounds. Placement rotates those local coordinates and then translates them
  into the active project domain; it never applies scale.
- CSS display rotation uses the opposite sign because browser Y is inverted.

The project's work area and coordinate-space kind are stored in `.e3laser`.
Machine projects retain the historical requirement that their area match the
configured machine area. Honeycomb-local projects instead require X0..width,
Y0..height to match the current rigid support frame. Their placed beam and
spot-corrected controller paths are checked against the separately configured
fixed convex polygon when that polygon is explicitly carried by the
support-bound preflight; without it, the guarded machine rectangle remains the
execution authority.

## Machine transport and controller dialect boundaries

The machine core separates communication mechanics, controller semantics, and
execution authority:

| Boundary | Responsibility | Explicitly not responsible for |
|---|---|---|
| `MachineTransport` | Open/close, raw/line writes, line reads, and drain mechanics | Controller command meaning, identity decisions, safety gates, retries, or execution authorization |
| immutable `ControllerDialect` | Pure GRBL or Marlin identity, response classification, command-policy, and parsing semantics | Opening or writing transports, locking, mutable service state, authorization, or starting work |
| local `MachineService` | Safety and authorization gates, connection/probe timing, transport ownership, serialized command/ACK exchange, job orchestration, STOP/cancellation, and cleanup | Persisting machine/profile data or delegating authority to a transport or dialect |
| Windows `RemoteMachineService` | Exact local preflight, upload/START client, acceptance recovery, cached monitoring, reconnect identity, and priority STOP RPC | Owning Pi serial, streaming an accepted program, or falling back to raw bridge execution |
| Pi `PiJobStore` / `PiJobService` | Atomic verified program/state persistence, repeat local preflight, one local `MachineService`, durable ownership, progress/result retention | Trusting client paths/metadata or resuming interrupted execution |

`create_machine_transport(backend, port, baudrate)` is the single explicit,
construction-only factory for direct and explicitly legacy raw serial carriers.
`AppContext` recognizes `e3bridge://` first and selects the high-level remote
facade, so Windows can use the Pi job service while local POSIX serial remains
unavailable there with the same clear failure. POSIX and legacy network transport
implementations remain lazy imports, and the former `machine.serial_backend`
exports remain compatibility entry points. The current combined node does not
construct `NetworkSerialTransport` or host the legacy raw server.

The frozen controller-dialect registry contains only the already supported
GRBL and Marlin policies. With direct-local `machine.protocol = auto`, `MachineService`
retains the same deterministic sequence: configured startup delay and drain,
GRBL startup-banner recognition, then `$I` with a 1.0-second response window,
then `M115` with a 1.5-second response window, using the existing accepted
identity markers and fail-closed result. The dialect values describe those
semantics; `MachineService` decides when each probe may be written and owns the
exchange. No additional probe or controller command was introduced.
Remote profiles reject `auto` before controller/network operations and require
the same explicit GRBL or Marlin policy on both hosts.

A saved machine profile supplies reusable physical motion-platform defaults,
including backend, protocol, connection, envelope, homing, and feed settings. A
tool-head profile supplies laser/tool defaults such as power mode/range, feeds,
spot offset, and guarded-boundary settings. The saved machine instance retains a
complete validated snapshot of both. These are configuration and compatibility
identities only: neither profile grants motion, arming, output, or execution
authority.

### Saved-machine and authoring-default lifecycle

The profile and runtime identities have deliberately different lifetimes:

| Term | Meaning |
|---|---|
| `MachineProfile` | Reusable motion-platform/controller starting values |
| `ToolHeadProfile` | Reusable laser/tool starting values |
| `MachineInstance` | Operator-owned, validated concrete saved snapshot |
| running machine | Immutable instance resolved once into the current `CoreRuntime` |
| next-launch machine | Registry persistence choice; it cannot hot-swap current settings or authority |
| curated material recipe | Authoring suggestion scoped by stable profile IDs; never execution authority |

Machine Manager creates a fresh instance by deep-copying the selected machine
and tool defaults. Later profile selection changes identity only unless the
operator explicitly loads that profile's defaults into the editable form and
saves. Creating, editing, duplicating, or choosing the next-launch instance
does not call `MachineService` and cannot connect, Home, jog, arm, move, emit, or
execute. Instances created from profiles, and duplicates created through
Machine Manager, start without camera, calibration, support, or coordinate
evidence from another machine; profile-created instances are also
motion-disabled. Machine Setup may
explicitly persist the active optical/calibration profile IDs onto the running
saved instance for a future launch, but the frozen current runtime identity is
not rewritten and existing calibration/support validity gates still apply.

First-run uses the same built-in physical profiles and schema-1 `MachineRegistry`.
It stores a safe-off real profile snapshot and existing bridge/camera endpoints
without contacting either one.
This adds no controller compatibility beyond the existing GRBL and Marlin
dialects.

New-project authoring defaults are resolved separately from coordinates. A
Qt-neutral resolver reads only source-curated operation records and selects one
highest compatible tier: exact machine+tool, tool-only, universal, or a neutral
fallback. It never reads user SQLite recipes and never mixes tiers. The exact
Ender-3 S1 Pro / generic 10 W match preserves the historical 13 layers. An
unmatched running identity receives one 0%-power, output-disabled Line layer
whose positive speed is capped by the running maximum work feed. The actual
running work area and existing bound honeycomb-support logic continue to choose
the project bounds and coordinate space; a next-launch selection is irrelevant
until a new process resolves it.

This boundary refactor has automated verification only. Existing historical
physical evidence remains historical; the refactored paths have not been
re-verified against physical GRBL or Marlin hardware. No new machine or
controller support is claimed.

## Machine safety boundary

`MachineService` is the only normal path to the controller. It:

- requires process hardware authority before serial access; every normal product
  launcher grants that authority, while the internal guard remains available to
  reject unauthorized callers and tests;
- blocks motion until configuration allows it;
- blocks serial motion and arming until homing/parking establishes the current
  connection's absolute coordinate reference;
- limits diagnostics to read-only queries and `M5`;
- requires temporary arming for positive-power jobs;
- restricts jobs to a conservative absolute-millimetre G0/G1/M3/M4/M5 subset;
- validates every destination against the guarded machine rectangle, or the
  exact configured convex polygon carried by a support-bound preflight;
- exposes incremental desktop jogging only as absolute, feed-controlled `G1`
  laser-off moves from a Home / park-established position; jogs deliberately
  bypass project/work-area
  geometry for physical limit measurement and invalidate their tracked position
  after STOP, disconnect, jobs, or motor release;
- validates offset-corrected controller destinations as well as uncorrected
  physical spot geometry;
- prevents rapid travel while laser state is active;
- serializes ordinary write/ack ownership and complete Home / park or scoped
  camera-hold sequences so concurrent desktop workers cannot consume each
  other's controller replies;
- permits the Coordinate Audit GRBL `?` sampler only under ordinary command
  ownership and rejects it before transmission while a streamed job is running;
- revokes authorization on stop or disarm;
- attempts `M5` on stop, disarm, disconnect, job failure, and scoped motor-
  release cleanup, even when mutable configuration or controller state is
  already untrusted.

The common guarded job-start seam performs `M5 → home → park → idle wait → arm
→ run`. Direct serial executes that seam in the desktop process. Remote serial
first uploads and commits the exact program; the Pi repeats current local
preflight and executes the seam in its local `MachineService`. The durable
`starting` state is not an ownership transfer. The later atomic
`ownership_accepted = true` / `start_accepted_at` record makes the job Pi-owned;
the subsequent START response only reports that durable fact. After START is
sent, a lost or failed response is ownership-uncertain, so Windows queries the
same UUID and never retries START blindly.

After successful powered streaming, the job remains active through
`M5 → planner-complete barrier → home → G21/G90 → park → motion-complete
barrier → motor release`. Stream acknowledgements use a cancellation-aware
completion timeout because GRBL can delay `ok` while its planner drains; the
short interactive-command timeout is not evidence that a queued job failed.
For Pi-owned execution this sequence is local and survives monitoring-client or
Windows-network loss. Zero-power jobs and stop, local failure, emergency, or
controller-disconnect paths do not request this completion motion. A Pi process
restart marks persisted `starting`, `running`, or `stopping` state interrupted
and never resumes it. Controller reset, reconnect, emergency stop, motor release,
or job failure invalidates the session reference. Connection status remains non-ready throughout
protocol detection and GRBL startup cleanup. GRBL startup cleanup ordinarily
requires an acknowledged `M5`; only the exact consumed alarm-lock rejection
`error:9`, with mandatory Home / park configured, permits `$X` followed by a
second required `M5`. Connect performs no homing or motion and leaves coordinate
state untrusted. Every other rejection or ambiguous exchange fails the
connection. Emergency stop intentionally bypasses ordinary operation
serialization.

This is an accidental-command boundary, not functional safety.

## Persistence map

| Data | Current location |
|---|---|
| Main configuration | `config/default.json` plus ignored `config/local.json` |
| Calibration JSON/images | configured `app.data_dir` |
| Captures, logs, generated G-code | configured `app.data_dir` |
| Pi-owned uploaded jobs/results | Pi-local configured `app.data_dir/pi_machine_jobs` (bounded metadata and G-code retention) |
| Desktop projects | user-selected `.e3laser` paths |
| Project backups | adjacent `.e3laser.bak` files |
| Autosaves | OS-native per-user data root under `backups/` by default |
| Material recipes | OS-native per-user data root as `materials.sqlite` by default |
| Cutting templates | configured application data directory under `templates/` |
| Window geometry, dock topology, and active desktop tab | Qt `QSettings`; current topology is `v7`, opaque `v6` dock state is ignored, and compatible geometry/tab fallbacks migrate independently |

Autosaves, packaged-config fallback data, and material recipes share the
`storage.default_user_data_dir()` platform abstraction. When the native root
differs from the pre-portability `~/.local/share/e3-positioning-system` root,
autosave recovery files and the material database are copied forward once
without deleting or overwriting the legacy source. Migration failure falls back
to that source so existing operator data does not disappear.

## Platform boundary

The portable core and authenticated `E3MACHINE/2` `e3bridge://` client run on
Windows and Linux. Direct local serial and camera hardware remain Linux-only:

- `machine.transport` exposes the neutral byte/line protocol.
- `machine.transport_factory` still constructs direct/legacy serial-family
  transports, while `AppContext` routes `e3bridge://` to the high-level remote
  facade before any local POSIX platform gate.
- `machine.serial_backend` retains its compatibility exports and imports the
  POSIX implementation only when local serial hardware is selected.
- Unsupported systems report no local serial ports and reject local serial
  selection clearly; an authenticated Pi job endpoint remains supported on
  Windows.
- Camera enumeration and controls use `/dev/video*`, V4L2, and `v4l2-ctl`.
- Linux shell launch/install assets support the direct local-hardware path.
  Windows packaging and update assets support the normal hardware-capable
  desktop and authenticated remote-Pi clients; they do not add direct local
  POSIX serial or V4L2 support. The installed frozen E3-to-visible-Inno handoff
  remains package-unverified.
- Fast Development CI runs Windows Python 3.12 Ruff, desktop dependency/
  bytecode validation, and the complete desktop-enabled suite with four bounded
  workers for `fix/**`, `feature/**`, `agent/**`, `cleanup/**`, and
  `architecture/**` pushes. Compatibility CI runs serial pytest on Windows
  Python 3.10 without desktop extras and Windows Python 3.12 with desktop extras,
  plus repository Ruff, for `main` pushes, pull requests targeting `main`, and
  manual dispatch. Linux/Pi components receive focused verification when
  changed; there is no standing Ubuntu compatibility matrix.

Platform implementations must remain lazy so unavailable hardware backends do
not prevent portable libraries from importing or create fake availability. See
`CURRENT_STATE.md` for the verification record and recommended next sequence.
