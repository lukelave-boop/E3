# Architecture

The repository has two user interfaces over a shared camera, calibration,
geometry, vision, G-code, and machine core.

## Runtime map

```text
Browser entry point                     Desktop entry point
laser_aligner.__main__                  laser_aligner.desktop.main
          |                                       |
          v                                       v
      AppContext <--------------------------- CoreRuntime
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
                         MachineService
                              |
                   +----------+----------+
                   v                     v
          SimulatedTransport       platform serial transport
```

The browser uses `AppContext` directly. The desktop uses `CoreRuntime` as a
UI-neutral lifecycle wrapper around the same context and performs blocking
camera/controller work through `DesktopController` worker tasks.

For a network-attached machine, `MachineService` remains on the desktop and
selects `NetworkSerialTransport` when `machine.port` uses `e3bridge://`. The Pi
node terminates that authenticated connection and opens the local POSIX serial
device. A configured `e3camera://` device selects `RemoteCameraService`; the Pi
retains sole ownership of the real `CameraService`, `VideoCapture`, and V4L2
controls. Neither network service bypasses the desktop's project or machine
safety policy. See [NETWORK_MACHINE.md](NETWORK_MACHINE.md).

## Module ownership

| Module | Responsibility |
|---|---|
| `config.py` | JSON defaults, merging, validation, and resolved paths |
| `storage.py` | Atomic JSON persistence used by calibration |
| `camera/` | OpenCV/V4L2 capture, camera controls, synthetic scenes, and portable corrected-test-image validation |
| `calibration/` | Lens model, checkerboard solving, bed homography, rectification, targets, bounded fine registration, and holdout accuracy scoring |
| `vision/` | Workpiece, fiducial, crosshair-grid, and camera-object detection |
| `geometry/` | SVG parsing, curve flattening, transforms, and physical units |
| `gcode/` | Legacy single-SVG generation and G-code parsing/preview utilities |
| `project/` | Desktop project schema, undoable object/shape commands, save/recovery, alignment, and multi-layer toolpaths |
| `templates/` | Shared semantic shape geometry, versioned multi-shape grid authoring, atomic library storage, project normalization, rigid instantiation, and deterministic test-frame generation |
| `materials/` | SQLite material-preset library |
| `project/power_correction.py` | Qt-free bounded power mapping, corner analysis, and sparse vector/raster correction profiles |
| `machine/` | Safety policy, simulator, protocol probing, and serial transports |
| `server.py` + `web/` | Local HTTP API and browser UI |
| `core/` | Shared runtime lifecycle for non-HTTP consumers |
| `desktop/` | PySide6 window, workspace, panels, tasks, and presentation logic |

Qt and HTTP types must not leak into the project, calibration, geometry,
vision, G-code, or machine models.

## Camera and calibration flow

```text
CameraService or SyntheticCameraService
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
restart, V4L2 control changes, synthetic-scene changes, and precision bursts use
one bounded exclusive-operation contract. Shutdown invalidates the current
camera generation and releases the backend before joining its reader, so a
burst or control request cannot publish state after teardown. Preview snapshots
remain available during a burst: they retain the published immutable frame
reference under the state lock and copy its pixels after releasing the lock.
Sharpness and downstream vision analysis likewise run after camera ownership is
released. This keeps large frame copies and CPU work from delaying new source
frames while preventing two precision workflows from consuming the same capture
sequence. Burst diagnostics include negotiated and observed FPS plus skipped
sequence counts; negotiated FPS is evidence reported by the backend, not proof
of sustained delivery.

Parked hardware workflows request an unscored raw burst while the temporary
stepper hold is active. The hold ends immediately after the last frame has been
copied; lens correction and clarity scoring then populate the same `FrameBurst`
outside that scope. The short parked Trace workflow uses the same two-phase
capture/selection path. Each burst retains its camera-session generation, so a
stop or reopen during deferred processing rejects the stale result. Callers
never choose a representative frame from an unscored burst.

The desktop camera panel and workspace share one presentation constant for the
initial corrected-overlay opacity. It is 70%, remains operator-adjustable from
0–100%, and does not alter captured pixels or any vision analysis input.

## Coordinate domains

The desktop has three deliberately separate coordinate domains:

- machine/controller millimetres and its independently configured output
  authority;
- calibrated camera coverage in machine millimetres;
- honeycomb-local design millimetres, with ruler zero at `(0, 0)` and an
  orthonormal rigid pose derived from the detected square.

Schema-2 projects persist their coordinate-space kind. Legacy schema-1 files
migrate as machine-coordinate projects. A honeycomb-local project stores only
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

In safe simulation only, `AppContext` can replace the final corrected frame
with a thread-safe, memory-only override. The source is either a validated
top-down full-bed PNG/JPEG or a deterministic frame generated from template
features at a known pose. `rectified_frame()` then exposes that same frozen
frame to the workspace, object tracer, and template matcher. Source-generation
tokens reject stale live-camera results, and clearing the override restores the
synthetic camera. Template and trace review holds compose independently: either
one prevents a live refresh from replacing the exact frame under review. Trace
request tokens also reject late detection callbacks after a new request, clear,
source change, or shutdown. The override is never written to the capture cache
or project file and is unavailable when hardware access or a non-simulator
machine backend is enabled.

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
Simulation startup creates a synthetic perspective scene and a known mapping
automatically.

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
- The object-tracing pipeline evaluates color, global/illumination-corrected/
  adaptive filled contrast, and signed local contrast hypotheses. It ranks a
  repeated-grid hypothesis by coherent filled-region support so narrow gaps or
  highlights cannot win merely by producing more clean contours, optionally
  infers missing cells, then records both the observed raster contour and the
  proposed vector geometry in the rectified image's active coordinate domain.
- Live desktop trace capture establishes the photography pose rather than
  trusting prior machine state: temporary hold encloses Home / park and the
  stable camera frame set, while rectification and vision analysis run only
  after the controller's original idle behavior has been restored. Frozen
  simulator frames bypass machine operations.
- Rounded-rectangle output fits center, dimensions, rotation, and radius and
  emits an analytic rounded vector. Simplified and exact modes preserve
  pixel-derived contours; simplification is a bounded polygon reduction, not a
  curve-fitting operation.
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
native shapes / imported SVG / traced outlines
  -> SceneObject instances
  -> ProjectDocument operation layers
  -> undoable CommandStack changes
  -> project.toolpath.generate_project_gcode()
  -> finalized multi-layer vector G-code + controller-ignored E3 metadata
  -> gcode.job_plan.build_job_plan()
  -> immutable JobPlan used by the dedicated desktop Preview
  -> the same finalized G-code text is exported or submitted to MachineService
```

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
that content to explicit vector paths in the source editor.

`JobPlan` models the final stream rather than a second approximation of the
project geometry. It records motion order, elapsed time, feed, controller power,
layer/pass/source context, and the physical laser-spot coordinates recovered
from the generated offset comment. Preview-only choices such as travel
visibility, playback speed, color inversion, and power shading never mutate the
program. Project revision changes invalidate both the program and its Preview.
Start Here replacement programs carry the configured photography pose in job
metadata, so their rebuilt plan includes the actual laser-off approach from the
controller's Home/park position to the reviewed move boundary.

The desktop supports line, fill, and raster operation layers. Fill and binary
vector raster convert closed silhouettes into angled scanlines. Imported images
are alpha-composited onto white, sampled on an exact physical-pitch lattice at
the operation's angle in the active project frame, area-prefiltered when
that lattice minifies the source, and converted with deterministic 8x8 ordered
grayscale dithering. The source top edge follows the
same positive-project-Y, mirror, and rotation transform shown by the canvas.
One shared PNG/JPEG/BMP contract bounds encoded bytes, dimensions, bit depth,
channels, and conservative decoded bytes before decode; TIFF is rejected.
`read_raster_asset_payload` returns metadata, SHA-256 identity, and the exact
bounded stable encoded bytes from one read, so Qt workspace pixels and toolpath
identity cannot come from different file versions. Workspace items retain that
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

## Machine safety boundary

`MachineService` is the only normal path to the controller. It:

- blocks serial access unless the process is hardware-enabled;
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
- revokes authorization on stop or disarm;
- attempts `M5` on stop, disarm, disconnect, job failure, and scoped motor-
  release cleanup, even when mutable configuration or controller state is
  already untrusted.

The desktop job-start path performs `M5 → home → park → idle wait → arm → run`.
After successful powered streaming, the job remains active through
`M5 → planner-complete barrier → home → G21/G90 → park → motion-complete
barrier → motor release`. Stream acknowledgements use a cancellation-aware
completion timeout because GRBL can delay `ok` while its planner drains; the
short interactive-command timeout is not evidence that a queued job failed.
Zero-power jobs and stop, failure, emergency, or disconnect paths do not request this
completion motion. Controller reset, reconnect, emergency stop, motor release,
or job failure invalidates the session reference. Simulation does not require a
hardware homing preflight. Connection status remains non-ready throughout
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
| Desktop projects | user-selected `.e3laser` paths |
| Project backups | adjacent `.e3laser.bak` files |
| Autosaves | OS-native per-user data root under `backups/` by default |
| Material presets | OS-native per-user data root as `materials.sqlite` by default |
| Cutting templates | configured application data directory under `templates/` |
| Active alignment test image | memory only; never persisted automatically |
| Window geometry, dock topology, and active desktop tabs | Qt `QSettings`; dock topology is versioned independently from compatible geometry/tab fallbacks |

Autosaves, packaged-config fallback data, and material presets share the
`storage.default_user_data_dir()` platform abstraction. When the native root
differs from the pre-portability `~/.local/share/e3-positioning-system` root,
autosave recovery files and the material database are copied forward once
without deleting or overwriting the legacy source. Migration failure falls back
to that source so existing operator data does not disappear.

## Platform boundary

The portable core and simulator run on Windows and Linux, while real hardware
is currently Linux-only:

- `machine.serial_backend` exposes the transport protocol and imports the POSIX
  implementation only when real serial hardware is selected.
- Unsupported systems report no serial ports and reject real serial selection
  with a clear simulator-only message.
- Camera enumeration and controls use `/dev/video*`, V4L2, and `v4l2-ctl`.
- Launch/install scripts and desktop integration are Linux shell assets.
- CI currently runs Ubuntu only.

Platform implementations must remain lazy so unavailable hardware backends do
not prevent the simulator or portable libraries from importing. See
`CURRENT_STATE.md` for the verification record and recommended repair order.
