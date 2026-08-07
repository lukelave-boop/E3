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
| `templates/` | Versioned cut-template schema, rectangle-grid authoring, atomic library storage, project normalization, rigid instantiation, and deterministic test-frame generation |
| `materials/` | SQLite material-preset library |
| `machine/` | Safety policy, simulator, protocol probing, and serial transports |
| `server.py` + `web/` | Local HTTP API and browser UI |
| `core/` | Shared runtime lifecycle for non-HTTP consumers |
| `desktop/` | PySide6 window, workspace, panels, tasks, and presentation logic |

Qt and HTTP types must not leak into the project, calibration, geometry,
vision, G-code, or machine models.

## Camera and calibration flow

```text
CameraService or SyntheticCameraService
  -> raw OpenCV BGR frame
  -> LensModel.undistort() when a lens model exists
  -> BedMapper.rectify()
  -> top-down image at configured pixels/mm
  -> workspace background and optional vision detectors
```

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
solved camera model. `BedMapper` stores image/machine point pairs plus forward
and inverse homographies. A reviewed fine-registration result may compose one
persisted, resettable translation onto that homography. Its separate full-map
path fits raw image coordinates to commanded mark coordinates with RANSAC,
checks inlier count, bed coverage, residual, orientation, scale, and modeled
whole-bed displacement, then retains the previous solved map as a rollback
snapshot. A new full solve clears both refinements. The Qt-independent
registration model owns those gates; Qt only presents review and confirmation.
Simulation startup creates a synthetic perspective scene and a known mapping
automatically.

The same Qt-independent model defines five accuracy-validation holdouts and
fixed pass/fail limits. `AppContext` binds each validation session to the exact
active homography, persists the generated job/capture/report, and rejects stale
or dry-only sessions. The desktop sends both calibration and validation jobs
through the ordinary guarded preview/run pipeline; validation has no write path
to `BedMapper`.

The native `MachineSetupDialog` owns the primary camera, lens, bed-mapping,
fine-registration, and alignment-check workflow. It calls `AppContext`,
`LensCalibrator`, `BedMapper`, and the portable registration model without
introducing Qt into those modules. The browser retains the legacy coarse
calibration surface; fine registration is desktop-only. Only one UI process
should own a physical camera at a time.

## Vision flows

- Rectangular workpiece detection runs on the rectified image and reports
  machine-coordinate placement hints.
- ArUco and crosshair-grid detection support bed mapping.
- The object-tracing pipeline segments color or contrast, optionally fits a
  regular grid and infers missing cells, then records both the observed raster
  contour and the proposed vector geometry in machine millimetres.
- Rounded-rectangle output fits center, dimensions, rotation, and radius and
  emits an analytic rounded vector. Simplified and exact modes preserve
  pixel-derived contours; simplification is a bounded polygon reduction, not a
  curve-fitting operation.
- The workspace previews the proposed vector that object creation will consume.
  The exact analyzed frame is delivered with the result and remains frozen
  until the review is cleared or committed.
- Inferred trace cells are deliberately not selected by default.
- Template alignment compares unordered machine-coordinate detections with
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

`JobPlan` models the final stream rather than a second approximation of the
project geometry. It records motion order, elapsed time, feed, controller power,
layer/pass/source context, and the physical laser-spot coordinates recovered
from the generated offset comment. Preview-only choices such as travel
visibility, playback speed, color inversion, and power shading never mutate the
program. Project revision changes invalidate both the program and its Preview.

The desktop supports line, fill, and raster operation layers. Fill and binary
raster convert closed vector silhouettes into angled scanline segments. Raster
images resolve their explicit asset path and threshold pixels at 50%; missing,
undecodable, or empty assets are rejected. Raster lead-in/out overscan remains
laser-off and both desired scanlines and corrected controller/overscan paths
are bounds checked. Text-to-path and grayscale/dithered power modulation remain
unsupported and must never be silently dropped.

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
  artwork appears visually upright in machine coordinates.
- Positive project rotation is counter-clockwise in machine coordinates.
- Project, camera, and template coordinates describe the desired physical
  laser-spot location. `laser.spot_offset_*_mm` is the physical spot relative
  to the commanded controller reference, so generated motion subtracts that
  vector. The unshifted spot path and shifted controller path are both checked
  against the configured work area. G-code comments carry the offset so the
  desktop preview converts controller motion back to the physical spot path.
- Corrected-image coordinates address pixel centers: OpenCV pixel `(i, j)` maps
  directly through the bed transform. The desktop offsets the Qt pixmap by
  `(-0.5, -0.5)` local pixels so its displayed centers, vector overlays, color
  picking, and vision output share that convention.
- Template feature coordinates are local to the center of the combined cut
  bounds. Placement rotates those local coordinates and then translates them
  into machine coordinates; it never applies scale.
- CSS display rotation uses the opposite sign because browser Y is inverted.

The project's work area is stored in `.e3laser`; the execution boundary also
uses the configured machine work area. These values must not be allowed to
silently diverge when a project is run.

## Machine safety boundary

`MachineService` is the only normal path to the controller. It:

- blocks serial access unless the process is hardware-enabled;
- blocks motion until configuration allows it;
- blocks serial motion and arming until homing/parking establishes the current
  connection's absolute coordinate reference;
- limits diagnostics to read-only queries and `M5`;
- requires temporary arming for positive-power jobs;
- restricts jobs to a conservative absolute-millimetre G0/G1/M3/M4/M5 subset;
- validates every destination against the configured work area;
- validates offset-corrected controller destinations as well as uncorrected
  physical spot geometry;
- prevents rapid travel while laser state is active;
- revokes authorization on stop or disarm;
- attempts `M5` on stop, disconnect, and failure.

The desktop job-start path performs `M5 → home → park → idle wait → arm → run`.
Controller reset, reconnect, emergency stop, or job failure invalidates the
session reference. Simulation does not require a hardware homing preflight.

This is an accidental-command boundary, not functional safety.

## Persistence map

| Data | Current location |
|---|---|
| Main configuration | `config/default.json` plus ignored `config/local.json` |
| Calibration JSON/images | configured `app.data_dir` |
| Captures, logs, generated G-code | configured `app.data_dir` |
| Desktop projects | user-selected `.e3laser` paths |
| Project backups | adjacent `.e3laser.bak` files |
| Autosaves | `~/.local/share/e3-positioning-system/backups/` by default |
| Material presets | `~/.local/share/e3-positioning-system/materials.sqlite` by default |
| Cutting templates | configured application data directory under `templates/` |
| Active alignment test image | memory only; never persisted automatically |
| Window layout | Qt `QSettings` |

The hard-coded desktop user-data paths are Linux-oriented and should be
replaced with an OS-native location abstraction before Windows packaging.

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
