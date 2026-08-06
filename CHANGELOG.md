# Changelog

## Unreleased — `desktop-v1`

### Committed desktop foundation

- Added a native PySide6 workspace alongside the browser application.
- Added multi-object, multi-layer `.e3laser` projects with undo/redo, grouping,
  alignment, distribution, z-ordering, atomic saves, backups, and autosaves.
- Added SQLite material presets and multi-layer vector toolpath generation.
- Added corrected-camera overlay, toolpath preview, camera focus controls, and
  guarded machine panels.
- Kept jogging and pause/resume visibly disabled pending dedicated core APIs and
  physical controller verification.

### Desktop control surface

- Reorganized the native window around a LightBurn-inspired information
  hierarchy while retaining E3 terminology, behavior, and safety boundaries.
- Replaced the large text-button shell with original icon-only command bars, a
  compact drawing rail, a bright gridded drafting bed, thin rulers, and hidden
  canvas scroll bars while retaining direct middle-button/Space panning.
- Added a non-hideable runtime strip showing simulation/hardware authority,
  controller connection, motion permission, and a persistent software-stop
  action with the physical emergency-stop disclaimer. It shares the command row
  on wide screens and moves to a guaranteed-visible row on narrow screens.
- Added a selection-aware numeric property bar for position, size, rotation,
  percentage scaling, aspect locking, millimetre/inch display, mirroring, and
  rectangle corner radius.
- Added direct single-object corner resize and rotation handles with live
  preview, anchored resizing, Shift rotation snapping, stale-model protection,
  and atomic undo/redo commits.
- Replaced fixed center insertion for rectangles with a persistent canvas draw
  tool: press-drag-release shows a live active-operation-colored outline,
  creates the exact snapped dimensions as one undoable command, selects the new
  object, and remains active for consecutive rectangles.
- Made Select and Rectangle exclusive visible tool modes. Select or a canvas
  right-click exits rectangle drawing; middle-button and Space-drag panning
  retain priority, and Escape remains the software-stop shortcut.
- Reworked Operations/Layers into an orderable five-column operation summary
  with inline Output/Show controls, color editing, and explicit unsupported
  fill/raster toolpath status.
- Added a fixed 30-color numbered operation palette; existing colors assign
  selected objects and unused swatches create a matching operation. Added split
  design and laser inspector stacks, a dedicated Window menu, resettable v5
  layout, and workspace-first defaults with Console and G-code Preview on demand.
- Fixed the native view paint path so the light bed and adaptive grid remain
  visible without a camera frame; the corrected image now sits subtly over the
  drafting surface at 18% default opacity.

### Camera object tracing

- Added multi-object camera tracing with color/contrast modes, regular-grid
  inference, reviewed selection, border offsets, and vector-object creation.
- Added one-step undo for a set of traced objects.
- Added synthetic object-tracing tests and an offline trace inspection tool.
- Made fitted rounded-rectangle results preview the same clean proposed vector
  that will be created instead of the simplified camera-pixel contour.
- Renamed the contour control to **Simplify tolerance**, limited it to
  **Simplified contours**, and exposed fitted dimensions and corner radius in
  the results table.
- Preserved exact preview placement when irregular contours become project
  paths by centering them from their actual bounds.
- Froze the analyzed camera frame throughout trace review and rejected stale
  asynchronous results after a new request, clear, source change, or shutdown.
- Registered Qt camera pixels to the OpenCV/BedMapper pixel-center convention,
  removing the half-pixel overlay shift that looked outside on top/left edges
  and inside on bottom/right edges at high zoom.
- Corrected the rounded-fit raster extent from a pixel-center span to a pixel
  count, removing a one-pixel radius underestimate on ideal rounded masks.
- Added a dynamic on-canvas overlay key with explicit color roles and distinct
  line styles: selected Trace results are solid green, aligned template cuts
  are solid cyan, and camera-detected label edges are dashed amber.

### Cutting templates

- Added versioned `.e3template` files and an atomic reusable template library.
- Added a dedicated rounded-rectangle grid designer with live preview,
  rows/columns, cut width/height/radius, edge-gap or center-pitch entry, and
  footprint/pitch/count feedback.
- Limited generated grids to 500 objects, rejected footprints outside the
  project work area, and marked grids with fewer than three cuts as manual-only
  for alignment.
- Applied a `1e-6` mm numerical tolerance consistently at work-area and
  G-code bounds checks so exact-fit layouts survive floating-point noise while
  meaningful overflow remains blocked.
- Persisted versioned grid-authoring metadata so saved grids can be edited while
  retaining their template identity; arbitrary project-authored templates are
  not guessed to be parameter grids.
- Added direct grid creation in the active project layer as one undoable batch.
- Added numeric rectangle corner-radius editing alongside width and height in
  the Transform inspector, applied as one validated undoable shape change.
- Added resilient library scans: malformed entries are reported without hiding
  valid templates, and duplicate persistent IDs are excluded safely.
- Added project-to-template normalization for visible cut objects and rigid
  translation/rotation instantiation with new object IDs.
- Added per-outer-contour matching features for compound imported SVG paths
  while excluding contained holes.
- Added manual template selection, geometry-based candidate ranking with
  synthetic test coverage, ambiguity and scale-mismatch warnings, and reviewed
  overlay adjustment.
- Added one-step batch undo when aligned template objects are created on the
  active project layer.
- Reused one frozen corrected frame across candidate settings, rejected weak or
  unresolved ambiguous matches, and canceled results invalidated by focus or
  library changes.
- Added a safe-simulation alignment source that can load a corrected full-bed
  PNG/JPEG or deterministically generate a selected template at known X/Y and
  rotation with optional noise and missing labels. The frozen frame feeds the
  normal tracer and matcher, remains memory-only, and can be cleared to restore
  the synthetic camera.
- Added source-generation guards, a persistent workspace warning, and camera
  control gating so stale or misleading live-camera state cannot replace a
  frozen test image during review.
- Made maximum-size synthetic grids render through per-label pixel regions
  instead of repeated full-bed buffers.
- Made alignment review reuse the smooth fitted camera boundary shown by Trace,
  and draw its amber dashes over the cyan cut line so both remain identifiable
  when the fit is exact.
- Replaced the generated test image's chromatic antialiased silhouette with an
  exact discrete rounded mask. This removes the one-pixel false size expansion
  while preserving antialiasing for printed detail inside each label.
- Invalidated generated G-code and toolpath previews after project revisions so
  aligned geometry cannot be followed by execution of a stale job.
- Reserved `marker_id` as schema metadata; marker-based identification is not
  implemented.
- Documented that automatic matching does not compare corner radius, so
  otherwise identical templates that differ only by radius require manual
  selection.
- Added focused synthetic and offscreen behavioral tests for schema round trips,
  library behavior, rigid placement, alignment/ranking, review controls,
  object creation/undo, and stale-state rejection. Real-camera and physical
  alignment remain unverified.

### Windows simulation

- Made POSIX serial transport selection lazy so simulator imports remain
  portable.
- Enabled safe browser and native desktop simulator startup on Windows.
- Made POSIX pseudoterminal tests skip explicitly on unsupported platforms.
- Fixed Windows text clipping in the grid designer's primary action and made
  its form, preview, status card, and footer fit compact logical screens.
- Made the Templates, Camera, Trace, and Transform inspectors tolerate narrow
  docks and larger text without hiding controls; concise responsive labels keep
  their full meaning in tooltips.
- Made an explicit choice of the already-selected template activate placement,
  so a one-template library can always open its workspace preview.
- Kept manual template placement visible until the operator explicitly starts
  **Align selected template** or automatic camera identification.
- Added direct manipulation of the transient cyan template preview: dragging a
  cut moves the complete layout and dragging its round handle rotates the
  layout about the reviewed center, with numeric placement controls kept in
  sync and camera detections left fixed for comparison.

### Documentation

- Added repository-wide development instructions and a current-state snapshot.
- Reconciled architecture, platform support, test evidence, and roadmap status.
- Documented the cutting-template workflow, rigid-only placement contract, and
  real-camera verification boundary.

Unreleased items must not be treated as part of version 0.1.0 until they are
committed, verified, and included in a release.

## 0.1.0 — 2026-08-04

- Initial GitHub-ready source tree
- Simulation-first browser application
- C920/OpenCV camera capture and V4L2 controls
- Lens and bed calibration workflows
- SVG parser, placement engine, vector G-code generation, and dry framing
- POSIX serial controller abstraction with GRBL/Marlin probing
- Fixed photography-position homing/parking workflow with controller-idle wait
- Conservative streamed G-code allowlist, compact-word parsing, coordinate checks, and rapid-with-laser rejection
- Dry framing with no `M3`/`M4` command
- Software safety gates, 20 automated tests, end-to-end simulated API validation, documentation, and Linux installation scripts
