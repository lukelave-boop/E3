# Current repository state

Snapshot: **2026-08-06**

Branch: **`desktop-v1`**

Baseline before consolidation: **`778532b` — Polish desktop controls and add camera focus workflow**

Consolidated feature commits:

- **`ccac7c2` — Add multi-object camera tracing**
- **`e091e82` — Add batch object insertion command**
- **`c54b143` — Add desktop camera trace workflow**
- **`421091a` — Add offline trace inspection tool**
- **`f42511a` — Exclude local trace artifacts from releases**

Release metadata: **`0.1.0-alpha`**

This document describes the branch, not only the last release. Update it
when verification, platform support, known gaps, or active feature work changes.

## Repository status at this snapshot

The object-tracing implementation was consolidated into focused vision,
project-command, desktop-workflow, offline-tool, and artifact-policy commits.
The documentation was then reconciled with that state.

The subsequent Windows portability update selects POSIX serial lazily. Safe
browser and native desktop simulation now start on Windows without loading
`termios`; serial hardware remains unavailable there.

The current desktop update adds a simulation-only, memory-resident corrected
camera source. An operator can load a full-bed PNG/JPEG or generate a selected
template at a known pose, run the normal trace and alignment pipeline on the
frozen frame, and then restore the synthetic camera. This path is unavailable
when hardware access or a non-simulator machine backend is enabled.

The local files `label-sheet-test.png`, `trace-preview.png`, and
`trace-result.json` are preserved for the developer who created them. They are
ignored by Git and explicitly excluded from release archives; they are not
test fixtures or product assets.

Use `git status --short --ignored` when those local files need to be audited;
normal `git status` intentionally omits them.

## Product shape

The repository contains:

1. A dependency-light browser application for camera calibration, single-SVG
   placement, G-code generation, and guarded controller execution.
2. A PySide6 desktop application with a native workspace, multi-object
   projects, operation layers, undo/redo, project persistence, materials,
   toolpath preview, and guarded machine controls.
3. Shared camera, calibration, geometry, vision, G-code, and machine services.
4. A native camera-object tracing workflow whose algorithm and project-command
   layers are tested, but whose real-camera GUI path is not end-to-end verified.
5. A reusable cutting-template workflow with a versioned library, manual
   selection, geometric candidate ranking, rigid alignment review, and
   undoable project-object creation, plus a dedicated parametric designer for
   regular rounded-rectangle grids and a safe-simulation alignment-image
   workflow.

The browser remains the complete calibration interface. The desktop reads the
same calibration files but does not yet provide native calibration wizards.

## Architecture

Browser path:

```text
laser_aligner.__main__
  -> AppContext
  -> AppHTTPServer
  -> web/index.html + web/app.js
  -> SVG placement
  -> gcode.generator
  -> MachineService
```

Desktop path:

```text
laser_aligner.desktop.main
  -> CoreRuntime
  -> AppContext
  -> DesktopController
  -> E3MainWindow / WorkspaceView / panels
  -> ProjectDocument + CommandStack
  -> project.toolpath
  -> MachineService
```

Shared camera/vision path:

```text
CameraService or SyntheticCameraService
  -> optional LensModel.undistort
  -> BedMapper.rectify
  -> optional memory-only corrected-frame override in safe simulation
  -> workpiece / fiducial / object-trace detection
  -> machine-coordinate geometry
```

Cutting-template path:

```text
rectangle-grid recipe or visible project output objects
  -> normalized cut objects and matching features
  -> versioned .e3template library item
  -> optional deterministic known-pose corrected test frame
  -> manual selection or geometric candidate ranking
  -> reviewed translation + rotation overlay with synchronized canvas controls
  -> one AddObjectsCommand into the active project layer
```

Execution path:

```text
generated G-code
  -> MachineService validation and safety gates
  -> SimulatedTransport or platform serial transport
```

See `docs/ARCHITECTURE.md` for module ownership and persistence boundaries.

## Verified in the current Windows checkout

Audit environment:

- Python 3.14
- PySide6 6.11.1

Results:

- **307 tests passed and 2 POSIX-only tests skipped.**
- The complete suite collected, including app simulation and machine-service
  tests.
- Focused template/test-image runs passed their model, library, renderer,
  matcher, controller, widget, workspace, and desktop-integration checks.
- The browser simulator served a healthy API response and its HTML interface.
- The native desktop started with the synthetic camera and simulated controller
  under both Qt's offscreen backend and a native Windows 1600 x 900 visual
  render, ran its event loop, and shut down cleanly.
- An offscreen `E3MainWindow` smoke test saved and reloaded a template, created
  aligned objects as one history command, and undid the operation.
- A second offscreen `E3MainWindow` smoke test drove the modal grid designer,
  saved and edited a template in place, added four rectangles as one history
  command, and undid the entire grid insertion.
- Layout regression tests cover both Save and Update designer actions, compact
  600 x 430 logical screens, 360 px inspector viewports, and 13 pt text without
  hidden horizontal content.
- Generated corrected frames pass the real color/contrast detector and rigid
  matcher at known poses. The desktop controller path recovers a known pose,
  source switching rejects stale camera results, and the 500-feature renderer
  is structurally verified to use local pixel regions instead of full-bed work
  per feature.
- Trace regressions verify that rounded output previews a clean proposed vector
  matching its fitted width, height, rotation, and radius; the analyzed frame
  stays frozen during review; stale callbacks are rejected; and exact or
  simplified contours retain their previewed world placement when created.
  Corrected-image pixel centers are also registered to their OpenCV/BedMapper
  machine coordinates without a half-pixel overlay shift, and ideal discrete
  rounded masks recover their radius without a center-span off-by-one.
- Transient canvas geometry now has a dynamic key: selected Trace results are
  solid green, aligned template cuts are solid cyan, and fixed camera evidence
  is dashed amber. Alignment review uses the same smooth fitted camera boundary
  as Trace and keeps both lines visible when they overlap.
- The native shell has been visually checked on Windows in safe simulation;
  extended manual interaction and real camera/controller use remain unverified.
- Ruff was not available in the current virtual environment.

The cutting-template coverage includes versioned persistence, resilient
catalog scans, compound imported paths, rigid matching, ambiguity and weak-match
rejection, frozen-frame review, cancellation of stale results, transient
overlays, direct-canvas rigid drag/rotation, object creation/undo, generated-job
revision invalidation, strict full-bed image validation, copy-isolated in-memory
source state, deterministic known-pose rendering, source/timer restoration, and
control/badge state. Rectangle width/height/radius edits, regular-grid
generation, editable authoring metadata, exact-ID replacement, gap/pitch
conversion, live preview, and work-area/object-count rejection remain covered.
Toolpath coverage also verifies that microscopic floating-point noise at an
exact work-area edge is accepted while a real overflow is still rejected.

The two skipped tests require POSIX pseudoterminals and `termios`. The exact
updated branch has not been run as a complete Linux suite during this audit.

## Historically verified on Linux

The 0.1.0 release documentation records:

- package compilation and automated tests;
- synthetic camera and automatic bed-map startup;
- a simulated HTTP workflow;
- guarded machine behavior;
- POSIX pseudoterminal serial framing and streamed jobs.

Those claims apply to the earlier release state. They are not evidence that the
consolidated desktop/object-trace branch passes unchanged on Linux.

## Implemented feature set

### Shared core

- Validated JSON configuration.
- Synthetic and OpenCV camera services.
- Linux V4L2 camera control application.
- Checkerboard lens calibration.
- RANSAC bed homography and perspective rectification.
- Workpiece, ArUco, and crosshair-grid detection.
- SVG shape/path parsing and curve flattening.
- Bounds-checked vector G-code and dry framing.
- Simulator and guarded machine service.

### Browser

- Camera, lens, and bed-calibration pages.
- Automatic and manual bed-point workflows.
- Single-SVG placement, sizing, rotation, and mirroring.
- Workpiece detection.
- G-code generation and download.
- Controller connection, diagnostics, arming, execution, and software stop.

### Desktop

- Native workspace with machine coordinates, grid, rulers, pan, zoom, snap,
  and corrected-camera overlay.
- LightBurn-inspired desktop hierarchy with original compact icons, a bright
  drafting bed, a non-hideable responsive runtime/safety strip, always-present
  numeric properties, split design/laser inspector stacks, and a fixed 30-color
  operation palette.
- Multiple objects and operation layers.
- Rectangle, rounded rectangle, ellipse, line, text, and SVG-path objects.
- Persistent press-drag-release rectangle drawing with a live active-layer
  outline, endpoint snapping, normalized drag direction, exact-size commit,
  immediate selection, and one-step undo/redo.
- Numeric width, height, and corner-radius editing for a selected rectangle,
  applied as one undoable validated shape change.
- Direct single-object corner resize and rotation handles with live preview,
  anchored-corner resizing, 15-degree Shift snapping, and undoable commits.
- Five-column operation summaries for mode, speed/power, output, and
  visibility, with inline toggles, operation-color editing, ordering controls,
  and explicit no-toolpath labeling for fill/raster modes.
- Transform, mirror, duplicate, delete, group, ungroup, align, distribute, and
  z-order commands.
- Undo/redo.
- `.e3laser` save/load, backup, autosave, and recovery.
- SQLite material presets.
- Multi-layer vector toolpaths, dry frames, previews, and estimates.
- Automatic invalidation of generated G-code and toolpath previews after any
  project revision changes.
- Camera focus controls and sharpness measurement.
- Guarded machine connection, park, diagnostics, run, and software stop.
- Simulation-only loading or deterministic generation of frozen corrected
  alignment frames, with camera-control gating and a persistent workspace badge.

### Camera-object tracing

- Automatic color/contrast detection.
- Click-to-sample hue.
- Direct and inferred regular-grid detections.
- Analytic fitted rounded rectangles plus simplified and exact pixel-derived
  contours.
- Separate observed and proposed-vector contours, with the workspace preview
  showing the geometry that object creation will consume.
- Border offsets.
- Review and selective conversion to editable project objects.
- One captured corrected frame held across detection review, with monotonic
  request cancellation and stale-result rejection.
- One-step undo for a created detection set.

The trace algorithms and native review lifecycle pass synthetic and offscreen
behavioral tests. The workflow has not been exercised end to end with the real
camera and calibration.

### Reusable cutting templates

- Versioned `.e3template` JSON with atomic, safe-filename library storage.
- Resilient catalog scans that keep valid unique templates available while
  reporting malformed files and excluding duplicate persistent IDs.
- Creation from visible, output-enabled project objects without mutating the
  source project.
- Dedicated regular-grid designer with a live preview, rows/columns, cut
  width/height/radius, and spacing entered as edge gap or center pitch.
- A 500-object grid limit and project-work-area validation before either saving
  a grid template or adding its editable rectangles to the current project.
- Versioned rectangle-grid authoring metadata, preserving template identity
  across parameter edits and distinguishing editable grids from arbitrary
  project-authored geometry.
- Direct creation of a grid in the active project layer as one undoable batch.
- Template-local normalization around the combined cut bounds.
- Per-outer-contour matching features for compound imported SVG paths, with
  contained holes excluded.
- Manual library selection plus synchronized numeric and direct-canvas
  center/rotation adjustment of the complete transient cut preview.
- A role-labeled overlay key and color-independent solid/dashed styling for
  distinguishing aligned cut geometry from camera-detected feature edges.
- Synthetic geometry-based template ranking, weak-match rejection, and
  template/pose ambiguity warnings.
- One corrected frame shared across all candidate trace settings and frozen
  while an accepted overlay is reviewed.
- Safe-simulation loading of corrected full-bed PNG/JPEG images with a strict
  uniform-scale contract and Unicode-safe paths.
- Deterministic corrected-frame generation from a selected template at known
  X/Y/rotation, with optional noise and missing labels; maximum-size grids use
  per-label rendering regions and exact discrete rounded silhouettes that do
  not introduce a detectable antialias fringe.
- One in-memory test frame shared by the workspace, tracer, and matcher, with
  stale-source rejection and explicit restoration of the synthetic camera.
- Rigid translation/rotation placement with scale differences reported but
  never applied.
- New object identities, active-layer assignment, and one-step batch undo.
- Optional `marker_id` schema metadata reserved for future identification.

Automatic matching requires at least three features; one- and two-cell grids
remain available for manual placement only. Matching compares feature centers,
dimensions, and orientation but not rounded-corner radius, so templates that
differ only in radius require manual selection and overlay review.

The portable model/library, generator, and matcher have focused synthetic tests,
and the native controls, review overlay, test-source lifecycle, application,
undo, stale-result handling, and generated-job invalidation have behavioral
offscreen coverage. The generated frame is intentionally idealized and the
workflow has not been verified with real corrected label-sheet images or
physical placement. No marker detector is implemented. See
[docs/CUT_TEMPLATES.md](docs/CUT_TEMPLATES.md).

## Known gaps

### Cross-platform

- No Windows serial backend, hardware camera discovery/control layer,
  install/launch scripts, or CI job exists.
- Selecting real serial hardware on Windows fails clearly and directs the user
  back to the simulator.
- Camera hardware handling assumes V4L2 and `/dev/video*`.
- Desktop autosave and material paths use Linux-style `~/.local/share` paths on
  every OS.
- CI covers Ubuntu and Python 3.10–3.12 only.

### Desktop and authoring

- No native calibration wizards.
- No guarded jog implementation.
- No tested pause/resume behavior.
- No fill or raster engine.
- No text-to-outline conversion.
- No image or DXF import.
- Ellipse and line creation remain one-shot centered inserts; only rectangles
  currently have the persistent canvas drawing interaction.
- Single visible, unlocked objects have corner resize and rotation handles.
  Shared multi-selection transform boxes, node editing, proportional resize
  gestures, and smart guides are not implemented. The transient
  cutting-template preview retains its separate rigid-body drag and rotation
  controls.
- No full interactive end-to-end GUI automation.
- Cutting-template matching uses provisional software acceptance gates, but has
  no real-camera validation dataset or physically measured accuracy threshold.
- Object tracing has no sub-pixel edge estimator or real-camera accuracy
  dataset. At the default 4 pixels/mm, one corrected-image pixel is 0.25 mm;
  fitted dimensions and radii remain raster- and threshold-dependent.
- Loaded test images must already be corrected full-bed views; the loader does
  not infer bed corners or calibrate an ordinary photograph. Generated images
  reuse ideal template geometry and therefore cannot expose lens, homography,
  parallax, material-height, lighting, or mounting errors.
- Automatic template ranking cannot distinguish otherwise identical layouts
  whose only difference is rounded-corner radius.
- `marker_id` is stored but no QR/ArUco/marker identification path consumes it.
- Template placement intentionally supports translation and rotation only; it
  will not scale geometry to conceal calibration or material-height errors.

### Hardware

- Controller firmware/protocol and power scale are unverified.
- Machine coordinates, axis directions, work limits, offsets, and photo pose
  are unverified.
- C920 controls, real calibration residuals, repeatability, and parallax are
  unverified.
- No powered laser behavior is verified.

## Recommended next sequence

1. Manually exercise the safe native UI on Windows, including loaded and
   generated alignment images, and record usability issues.
2. Add PowerShell setup/launch scripts and OS-native user-data paths.
3. Add Windows CI while retaining Linux Python-version coverage.
4. Separate portable OpenCV capture from Linux V4L2 discovery/control.
5. Run the complete current suite on Linux and record the exact result.
6. Extend behavioral Qt coverage for the remaining project-editing and
   object-tracing workflows.
7. Validate template matching against curated corrected camera images at known
   material heights and define residual/confidence acceptance thresholds.
8. Verify that release archives continue to exclude local camera/trace output.
9. Only then proceed with documented physical camera/controller bring-up.

## Evidence terminology

- **Tested** — covered by a currently passing automated test.
- **Smoke-tested** — imported or constructed, but not exercised end to end.
- **Implemented, unverified** — source exists but lacks current execution
  evidence.
- **Historically verified** — recorded for an earlier commit or release.
- **Physically verified** — exercised on identified real hardware with recorded
  configuration and results.
