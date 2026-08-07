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

The desktop now also owns a native Machine Setup workflow covering configured
camera controls, raw preview, checkerboard lens calibration, manual and
CSV-assisted bed mapping, 5×5 cross-grid detection, residual review, and
workpiece/fiducial checks. It now includes a separate eight-point fine-
registration stage that prepares dry or normally guarded powered cross jobs,
classifies multi-point residuals, and can apply only a reviewed global
camera-map translation within a 5 mm cumulative limit. Validated G-code can be
exported from the desktop. The browser remains available, but no operator
capability requires it.

Machine Setup now stores explicit X/Y mapping-orientation flags with the bed
calibration and presents unambiguous NORMAL/OFF and REVERSED/ON controls. Legacy
maps infer their current effective orientation and remain visibly marked as
unrecorded until the operator confirms them after a laser-off direction check;
that confirmation does not mirror points. Window geometry, selected tab,
simulation scene, cross sizes, and marking speeds persist in the active data
directory. Marking power intentionally resets to zero each time Setup opens.
All desktop checkbox-based boolean options now use the same compact gray-OFF,
green-ON switch presentation; the Machine Setup X/Y controls use those switches
instead of one-shot reversal buttons.

Live camera-refresh errors are now latched before the first modal notification.
The operator acknowledges one Camera unavailable message; timer-driven repeats
remain silent until a frame succeeds or the operator explicitly selects Refresh
camera. Recovery clears the latch and posts a non-modal status notice. This
prevents a camera owned by another application from repeatedly stealing focus.
Explicit Refresh camera now distinguishes a healthy capture from an offline,
frame-less, or faulted one. A failed capture is released and the configured
V4L2 device is reopened asynchronously before the corrected image is retried.

The current Trace repair makes color picking an explicit, visible canvas state,
reports sampling failures in-panel, retains an actual sampled BGR/Lab color for
neutral targets, and evaluates signed large-scale contrast masks so a filled
object can be fitted from its silhouette rather than an expanded local edge
halo. These paths are covered with textured, unevenly lit, neutral rounded-
rectangle fixtures and offscreen picker integration tests. Repeated rounded
objects can now fit a dominant shape family and regular lattice, reject noisy
or duplicate candidates, and normalize every accepted or inferred grid cell to
one shared width, height, corner radius, and rotation. The normalized 2×2 fit
was also checked read-only against the current corrected C920 capture: all four
cells produced identical `81.77 × 20.97 mm` geometry. Created-object placement
from this new normalized result has not yet been physically cut.

Home/park setup and park commands now receive a scoped minimum six-second
acknowledgement window for the slow controller observed in the real workflow.
Homing and park-completion waits remain 120 seconds; ordinary streamed job
lines retain the configured `machine.read_timeout`. The first implementation
still required a GRBL realtime `<Idle...>` report after `$H`; the physical
controller completed its double-touch homing cycle but did not provide the
expected report, first exposing a later six-second setup timeout and then the
120-second idle timeout. Home/park now continues from the `$H` acknowledgement
received after the endstop sequence and sends `G4 P0` after the park move as a
planner-synchronization barrier. This correction is automated-test verified
but still requires a repeated physical Home/park check.

The native window title now begins with the application name, package version,
and a short fingerprint of the installed application files before the project
name. This makes restarted source builds visibly distinguishable; release
packaging can override the fingerprint with
`E3_POSITIONING_SYSTEM_REVISION`. Its application display name is intentionally
empty because Qt/X11 otherwise appends a duplicate product-name suffix to the
complete native caption; the application name itself remains configured.

The desktop now opens a dedicated graphical Preview after project generation,
dry framing, and registration/validation job preparation. An immutable
`JobPlan` is parsed from the exact finalized G-code stream and retains
controller-ignored layer/pass/source context, physical laser-spot coordinates,
per-move timing/feed/power, and cut/travel statistics. The Preview provides
scrubbing, animated playback, display-only travel/power/inversion controls,
live move details, warnings, fit/pan/zoom, PNG export, per-operation visibility
and statistics, keyboard timeline navigation, and a dynamic generated-layer
legend. Generation can use source or nearest-path order, and Preview reports
the latter's rapid-travel savings against source order. Closed-vector fill,
binary vector raster, fixed-threshold raster image import, raster overscan, and
configurable acceleration/command-delay time estimation feed the same exact
program model. A confirmation-gated **Prepare Start Here** action creates a new
bounded absolute-mm program at a reviewed move boundary without starting the
machine. Prepared maximum
power is displayed independently from controller execution progress, fixing
the idle-polling presentation that previously replaced a generated power value
with `no active controller job / 0%`. Project revisions invalidate the plan and
close its Preview. The existing generation, homing, arming, validation, and
streaming path is unchanged.

## Verified in the current Linux checkout

- **385 tests passed** with Qt using the offscreen platform.
- Exact-job Preview verification covers final-stream parsing, immutable move
  context, spot-offset recovery, powered-rapid warnings, time scrubbing,
  keyboard timeline navigation, operation visibility, planner comparison,
  fill/raster generation, threshold image rastering, overscan bounds rejection,
  guarded Start Here rebuilding, PNG rendering, and separation of prepared
  maximum power from controller progress. The complete dialog was also rendered
  and visually inspected offscreen at 1120 × 760. Interactive desktop use and
  real-hardware execution of this Preview revision remain unverified.
- The icon-only Job toolbar now renders Preview as an original monitor/toolpath
  glyph rather than falling back to the action text. The glyph was rendered and
  visually inspected at high resolution in addition to its Qt mapping test.
- Build-identity verification covers content-sensitive source revisions,
  sanitized packaging overrides, and build-first project window titles.
- Full `E3MainWindow` construction under Qt's offscreen platform displayed the
  build-first identity in the native title bar.
- Focused coordinate-reference verification covers rejection of serial motion
  and arming before homing, successful home/park acceptance, emergency-reset
  invalidation, desktop preflight ordering, X/Y mapping reversal, and the
  hardware/simulation status presentation.
- Focused Trace verification covers the complete button-to-canvas picker state,
  sampled neutral-color acceptance, configured maximum-area rejection, and
  contrast recovery of a filled rounded rectangle on a noisy wood-like image.
  It also covers repeated-grid normalization, repair of one malformed direct
  cell, common grid-object creation metadata, and explicit complete-grid
  selection including inferred cells.
- Focused Home/park verification confirms that setup acknowledgements use six
  seconds without changing the normal one-second test streaming timeout.
- Focused laser-offset verification covers zero-default configuration,
  configured-value loading and excessive-value rejection, desktop and browser
  coordinate correction, dry-frame correction, camera-aligned preview, and
  rejection when corrected controller motion would leave the work area.
- Focused Machine Setup tests cover native tab availability, safe runtime
  authority, synthetic preview capture, manual bed-point add/delete, and
  explicit axis-reversal and fine-registration controls. They now also cover
  axis-state persistence across mapper/application reopen, legacy-state
  confirmation without point mutation, prominent reversed-state presentation,
  and persistence of non-power setup preferences.
- Focused desktop-controller tests cover stale camera callbacks, one-notice
  latching across repeated and changing refresh errors, recovery reset, and
  explicit operator retry, including release/reopen before image refresh.
- Focused fine-registration verification covers bounded target placement,
  laser-off and powered G-code sequencing, zero-power rejection, sparse-cross
  detection, translation classification, position-dependent rejection,
  persistent translation application/reset, the 5 mm cumulative limit,
  seven-inlier full-map acceptance, six-inlier and low-confidence rejection,
  and persistent full-map rollback.
- Focused accuracy-validation verification covers distinct holdout placement,
  fixed-limit pass/fail classification, low-confidence rejection, laser-off
  session rejection, powered synthetic capture, persistence, stale-map
  rejection, and native job handoff.
- Files changed for Trace, build identity, coordinate-reference gating, bed
  mapping, and fine registration pass Ruff.
- Repository-wide Ruff currently reports 66 pre-existing findings outside this
  change; the repository as a whole is not lint-clean.
- Fine-registration capture, reviewed exclusion, and a later 8/8-inlier
  full-map application have been interactively exercised against the C920.
  Independent five-point holdout validation then passed physically with
  `0.258 mm` RMS, `0.417 mm` maximum error, and mean X `+0.019`, Y `+0.078 mm`.

## Physical observation requiring confirmation

On 2026-08-06, a real powered rounded-rectangle job exposed a repeatable-looking
tool-reference displacement. The hardware profile selected GRBL over the
configured serial device, but the exact controller model and firmware identity
were not recorded, so this is **not** a physically verified configuration.

- Configuration at the time: work area X/Y `10..210` mm, boundary margin 5 mm,
  zero software spot offset, `M4 S500`, 2000 mm/min, one pass.
- Generated desired/controller bounds (offset was zero):
  X `86.326..164.326`, Y `115.585..135.585` mm; commanded center
  `(125.326, 125.585)` mm.
- The corrected 4 px/mm camera capture at
  `data/captures/workspace.jpg` placed the new cut center at approximately
  `(97.6, 117.3)` mm.
- Observed spot displacement was therefore approximately
  `(-27.7, -8.3)` mm. A provisional X `-28` mm, Y `-8` mm spot correction was
  tested, but a second cut moved still farther from the target. The later job
  commanded its X center about `+27.7` mm while the observed cut moved about
  `-27.7` mm in the corrected image. The ignored local profile has therefore
  been returned to zero spot offset; the provisional values must not be reused.

The bed map was solved from controller-positioned laser-burned crosses, so it
already references the laser spot. The failed correction instead exposes an
unresolved controller/workspace coordinate-reference problem. The operator
confirmed that Home / park completed before the second job, ruling out omitted
manual homing as the cause of this miss. The near-equal, opposite X response
was strong evidence that saved bed-point X labels were mirrored relative to the
controller. At that stage axis reversal was physically unverified and a
laser-off check was required. That check was not performed; the subsequent
powered result nevertheless confirmed the direction diagnosis.

The operator subsequently applied **Reverse X mapping** and performed a powered
10% rounded-rectangle job on 2026-08-06 at 20:10 despite the requested
laser-off check. The generated bounds were X `55..133`, Y `111..131` mm with
zero software spot offset. In the corrected 4 px/mm capture saved at 20:12, the
new burn is nearly coincident with the intended shaded rectangle. Visual
comparison places the remaining displacement at approximately 3 mm toward
negative X and no more than roughly 1 mm in Y, but overlapping old marks, burn
width, and the manually positioned target make that estimate unsuitable as a
calibration value. This physically confirms that X reversal removed the major
error; it does not yet verify final accuracy or justify a new spot offset.

The next hardware action should be a laser-off homed dry frame followed by an
independently measured, sparse fine-registration check rather than another
overlapping full rectangle. Do not encode the estimated residual until
controller identity/firmware, work-coordinate offsets, homing state, workpiece
restraint, and X/Y directions are recorded and the displacement repeats at
multiple bed locations.

The first eight-point fine-registration job was physically marked and captured
on 2026-08-06 at 20:39. Seven detector overlays visually matched their crosses;
point 7 was obstructed by the laser head at the photography pose and produced
an obvious false result of approximately X `-8.46`, Y `-12.18` mm. Excluding
that point leaves a proposed camera-map correction of approximately X `+2.67`,
Y `+2.40` mm, but the remaining scatter is `1.23` mm RMS with a `2.24` mm
maximum. That is position-dependent under the current acceptance thresholds,
so no translation has been applied. The review UI now retains explicit Use
checkboxes, permits at most two reviewed exclusions, and moves the corresponding
future target away from the head/park corner.

The fine-registration review now also computes a separate, confirmation-gated
full-bed homography refinement directly from camera pixels and commanded mark
coordinates. It requires seven geometric inliers, broad coverage, bounded
residual/scale/whole-bed movement, and retains the prior solved map for reset.
The latest saved physical recapture at 20:47 was evaluated without applying it.
Its low-confidence review excluded points 2 and 7; of the remaining six, RANSAC
retained only points 1, 3, 4, 5, and 8 and rejected point 6. Its five-inlier
result is therefore refused. A fresh physical run using the relocated point 7
is required; the new full-map apply and rollback controls are automated-test
verified but not physically verified.

A subsequent physical capture at 21:03 detected all eight relocated marks. It
reported a translation candidate of approximately X `+3.019`, Y `+1.512` mm
with `0.613` mm centered scatter, and a full-map fit with 8/8 inliers, `0.262`
mm in-sample RMS, 53% convex-hull bed coverage, and `4.641` mm maximum modeled
bed correction. The operator applied that reviewed full-bed refinement; the
previous solved map is retained in `bed_calibration.json` for reset. This is a
physical application of the workflow, not yet an independent accuracy
verification.

Machine Setup now includes a separate five-point Accuracy validation workflow.
It prepares dry or normally guarded powered holdout jobs, binds the session to
the active homography, homes/parks for capture, and automatically reports
per-point, RMS, maximum, and mean error. A pass requires all five confident
detections, no more than `0.5 mm` RMS error, and no more than `1.0 mm` maximum
error. Dry-only and stale-map sessions are rejected, and validation has no path
that mutates calibration. On 2026-08-06 at 21:21, an independent powered
holdout capture passed: all five marks were detected, RMS error was `0.258 mm`,
maximum error was `0.417 mm`, and mean error was X `+0.019`, Y `+0.078 mm`.
This verifies the saved camera-to-laser map for that restrained surface,
material height, camera pose, controller connection, and session; it is not a
safety certification or a guarantee after the setup changes.

The local files `label-sheet-test.png`, `trace-preview.png`, and
`trace-result.json` are preserved for the developer who created them. They are
ignored by Git and explicitly excluded from release archives; they are not
test fixtures or product assets.

Use `git status --short --ignored` when those local files need to be audited;
normal `git status` intentionally omits them.

## Product shape

The repository contains:

1. A dependency-light legacy browser application for camera calibration, single-SVG
   placement, G-code generation, and guarded controller execution.
2. A PySide6 desktop application with native machine setup, a native workspace, multi-object
   projects, operation layers, undo/redo, project persistence, materials,
   toolpath preview, and guarded machine controls.
3. Shared camera, calibration, geometry, vision, G-code, and machine services.
4. A native camera-object tracing workflow whose earlier real-camera use exposed
   defects now covered by synthetic/offscreen tests; the newest normalized-grid
   created-object result still awaits a physical cut check.
5. A reusable cutting-template workflow with a versioned library, manual
   selection, geometric candidate ranking, rigid alignment review, and
   undoable project-object creation, plus a dedicated parametric designer for
   regular rounded-rectangle grids and a safe-simulation alignment-image
   workflow.

The desktop is now the primary complete calibration interface. The browser
retains an equivalent single-SVG workflow but is not required for setup.

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
  and scan interval, angle, and raster overscan controls.
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
- Native Machine Setup with camera control application, raw preview, synthetic
  scenes, checkerboard capture/solve, manual and CSV-assisted point entry,
  automatic 5×5 grid detection, bed-map solve/residuals, eight-point fine
  registration, workpiece detection, and fiducial inspection.
- Validated generated-G-code export.
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

- No guarded jog implementation.
- No tested pause/resume behavior.
- No text-to-outline conversion.
- No DXF import. Raster image import currently stores an external absolute
  asset path and uses a fixed binary threshold; embedded portable assets,
  grayscale power modulation, and dithering are not implemented.
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

- GRBL is selected and powered output has been observed, but controller
  identity/firmware and the power scale remain unverified.
- The physical cut response confirmed that the saved X map required reversal,
  but machine limits, final offsets, repeatability, and photo-pose accuracy
  remain unverified.
- C920 controls, real calibration residuals, repeatability, and parallax are
  unverified.
- Powered output has been observed, but placement was displaced and no powered
  behavior is yet verified.

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
