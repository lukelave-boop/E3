# Changelog

## Unreleased — `desktop-v1`

- Added a reviewed, rollbackable 5×5 local residual correction for
  position-dependent camera-to-laser error. The bounded mesh is used in both
  mapping directions and image rectification, with an independent 16-point
  interstitial validation workflow and 0.30 mm RMS / 0.60 mm maximum targets.
- Dense-map safety rejections now remain on the review screen. The captured
  image and all measurements are shown, and one camera-occluded grid cell may
  be excluded and conservatively inferred from the other 24. It is highlighted
  as **INFERRED** and still requires independent holdout validation; two bad
  cells or a fit outside the movement/smoothness gates cannot be applied.
- Dense 4×4 validation now overlays every numbered detected center, its cyan
  commanded-map position, and the error vector between them. Matching table
  rows use the same pass/warning/failure colors so detections can be audited
  against overlapping calibration marks.
- Added a one-time reviewed validation refinement for a coherent 16-point
  residual, with stale-map, confidence, update-size, total-displacement, and
  local-gradient gates. A separate checker-shifted 16-point confirmation job
  uses fresh positions and is required after refinement; the same validation
  measurements cannot be reused to claim accuracy.

- Added a GRBL coordinate-state preflight. Home/park records the active
  workspace plus its `G54`-`G59` and `G92` offsets through read-only `$G`/`$#`
  queries; absolute-motion jobs are blocked if that state changes before
  streaming, and the reference is exposed in status and controller logs.
- Fixed calibration analysis consuming a cached pre-park camera image. GRBL
  park completion now uses a positive `G4 P0.01` synchronization dwell, and
  registration/validation allow a six-second physical settling interval before
  waiting for three newly captured frames with a separate six-second freshness
  timeout. This also excludes fresh frames captured while the slow bed is still
  completing its queued park move.
- Added a persistent machine connection status and Connect/Disconnect button to
  the Machine Setup window so calibration recovery does not require closing the
  dialog.

- Stopped recurring camera-refresh failures from opening a modal dialog every
  refresh cycle. One camera fault is acknowledged once, subsequent failures are
  suppressed until recovery or an explicit Refresh camera retry, and successful
  recovery is reported without stealing focus.
- Made explicit Refresh camera perform a real device reconnect when the camera
  is offline, has no frames, or reports a read fault. Reopening runs outside the
  GUI thread and a successful reopen immediately refreshes camera status and the
  corrected workspace image.

- Made Machine Setup axis orientation explicit and persistent: X/Y now show
  NORMAL/OFF or REVERSED/ON, legacy maps are visibly inferred rather than
  falsely shown off, and confirmation can record an inferred state without
  changing calibration points. Setup also restores its window, tab, simulation
  scene, cross sizes, and marking speeds while intentionally resetting marking
  power to zero.
- Replaced ambiguous checkbox indicators throughout the desktop with a
  consistent compact gray-OFF/green-ON switch treatment, including the saved
  X/Y mapping-orientation controls.

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

- Fixed Home/park on GRBL-derived controllers that acknowledge `$H` after the
  endstop sequence but do not return the expected realtime `<Idle...>` report.
  The procedure now continues from that acknowledgement, and the subsequent
  park move uses a zero-duration
  `G4 P0` planner barrier with the existing 120-second completion limit.
- Made controller acknowledgement failures identify the exact command, so a
  setup timeout distinguishes `M5`, `$H`, `G21`, `G90`, the park move, and its
  completion barrier.

- Added a dedicated exact-job graphical Preview opened by generation and dry
  framing. It provides time scrubbing, animated playback from 0.1× to 40×,
  cut/travel display, optional controller-power shading and inversion, current
  feed/power/X/Y/layer/pass details, timing and distance statistics, warnings,
  fit/zoom/pan, and PNG export.
- Built Preview from an immutable parse of the exact finalized G-code submitted
  to the existing guarded execution path, including controller-ignored
  layer/pass/source metadata and physical laser-spot offset recovery.
- Separated prepared-job maximum power from live controller execution status so
  idle polling no longer replaces a generated `20% / S200` summary with
  `no active controller job / 0%`.
- Added an original monitor/toolpath glyph for the Preview toolbar action so
  the icon-only Job toolbar no longer falls back to showing the action words.
- Added per-operation Preview visibility, dynamic generated-layer legends, and
  cut/time/maximum-power statistics.
- Added selectable source-order or nearest-path planning and records the exact
  planner in controller-ignored job metadata and the Preview heading.
- Added real bounded closed-vector fill, binary vector raster, and imported
  50%-threshold image raster G-code. Scan interval/angle are editable; raster
  overscan is laser-off, timed, and controller-space bounds checked.
- Added acceleration and command-latency-aware Preview timing configuration.
- Added guarded **Prepare Start Here** at reviewed move boundaries. Replacement
  programs restore absolute millimetres, laser-off positioning, layer/pass
  context and power, then require the unchanged normal execution gates.

- Added a build-first native title-bar identity containing the application name,
  release version, and short source revision before the current project name.
  Source launches fingerprint the installed application files, while packaged
  builds can supply `E3_POSITIONING_SYSTEM_REVISION` explicitly.
- Suppressed Qt/X11's automatic application-name title suffix because the
  build-first caption already contains the product name.
- Added native **Machine Setup** tabs for camera controls, synthetic scenes,
  checkerboard lens calibration, manual/CSV/automatic bed mapping, residual
  review, workpiece detection, and ArUco inspection.
- Routed the Camera inspector's calibration buttons into the native workflow
  and paused its live overlay while setup owns capture actions.
- Added validated G-code export and documented desktop parity with every
  operator capability in the legacy browser workflow.

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
- Made regular-grid tracing fit a dominant repeated-shape family and one shared
  lattice. Rounded output can now repair malformed direct cells, reject
  duplicate/noisy candidates, synthesize gaps with identical geometry, and
  select the reviewed complete grid in one action.
- Added one-step undo for a set of traced objects.
- Added synthetic object-tracing tests and an offline trace inspection tool.
- Made camera color sampling visibly enter a canvas-pick state, report sampling
  failures in the Trace inspector, and carry the sampled BGR color through the
  detector instead of reducing every sample to a saturated hue.
- Added neutral-color segmentation and signed large-scale contrast masks for
  real-image-like textured backgrounds. Filled rounded rectangles now compete
  as silhouettes instead of being represented only by an expanded local edge
  halo.
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

### Laser spot coordinates

- Added zero-default X/Y laser-spot offsets using the convention `spot =
  controller + offset`, with shared desktop/browser generation.
- Validate both the desired physical spot geometry and the offset-corrected
  controller path. Generated programs record both bounds, and the desktop
  preview removes the controller correction so it remains aligned with the
  corrected camera image.
- Rejected and removed the provisional local X −28 mm, Y −8 mm correction
  after a second physical cut moved farther from the target. The laser-burned
  bed map already references the spot; session homing and axis orientation now
  require laser-off verification before another powered test.
- Added per-connection coordinate-reference tracking. Serial motion and arming
  are rejected until homing/parking succeeds; reconnect, reset, emergency stop,
  and job failure invalidate the reference.
- Made desktop hardware Start run `M5`, home, park, and wait idle before arming
  and execution, removing the need for a separate manual Home / park action.
- Increased only Home/park setup-command acknowledgements to at least six
  seconds for slower controllers; normal job streaming retains its configured
  acknowledgement timeout.
- Added explicit X/Y bed-point reversal controls because a symmetric cross grid
  cannot determine controller-axis sign from image geometry alone.
- Added a native eight-point fine-registration workflow. It prepares dry or
  normally guarded powered cross jobs, captures fresh marks at the homed camera
  pose, reports commanded/observed residuals, distinguishes global translation
  from position-dependent error, and refuses unsafe or excessive corrections.
- Persisted an explicit, resettable bed-map translation separately from the
  zero-default laser-head offset. Application is confirmation-gated, limited to
  5 mm cumulative magnitude, and allowed only for a consistent multi-point
  result; a full bed solve clears the fine translation.
- Added reviewed inclusion checkboxes for fine-registration measurements. Up to
  two clearly obstructed or false detections may be excluded while remaining
  visible; at least six marks and every original confidence/scatter gate remain
  mandatory. The future lower target is moved away from the photo-pose head
  obstruction observed in the first physical run.
- Added a separate reviewed full-bed homography refinement for position-dependent
  fine-registration results. It requires seven RANSAC inliers, broad coverage,
  bounded residual/warp/scale checks, explicit confirmation, and preserves the
  prior solved map for reset; it never weakens the translation thresholds.
- Added guided independent accuracy validation with five holdout crosses. Dry
  and powered jobs use the normal guarded pipeline; automatic capture reports
  per-point, RMS, maximum, and mean error against fixed limits, rejects missing,
  low-confidence, dry-only, or stale-map sessions, and cannot change calibration.

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
