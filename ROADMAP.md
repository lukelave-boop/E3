# Roadmap

This roadmap describes remaining work from the current `desktop-v1` branch. It
is not a hardware-readiness claim. Active verification state belongs in
[CURRENT_STATE.md](CURRENT_STATE.md); [PROJECT_STATUS.md](PROJECT_STATUS.md) is
the retained 2026-08-06 Windows portability snapshot.

## Foundation already present

Original browser/core foundation:

- Simulation camera and controller.
- C920/V4L2 capture and control locking.
- Checkerboard lens calibration.
- Bed homography and rectified view.
- SVG vector parser and guarded G-code generation.
- Browser placement/calibration interface.
- Conservative machine safety service and POSIX transport.

Desktop foundation:

- Native machine-coordinate workspace and camera overlay, including a
  70%-opacity photographic default and a draggable overlay key that remains
  fixed to the viewport during canvas work.
- Multi-object/layer project model and `.e3laser` persistence.
- Undo/redo, grouping, alignment, distribution, and ordering.
- Material presets, multi-layer vector toolpaths, preview, and estimates.
- Guarded machine controls, including planner-drained post-powered-job
  Home/park, bounded laser-off incremental jogging, visible completion phases,
  failure alerts, and motor release; pause/resume remains disabled.
- Safe browser and desktop simulator startup on Windows, with POSIX serial
  selected only on supported systems, plus a Windows/Linux CI matrix.
- Native lens calibration and fresh keyed 5×5 base mapping that does not depend
  on a prior camera homography; the workflow has been exercised on the current
  Linux/C920 rig, while named-controller repeatability remains outstanding.

Current branch feature:

- Shared importer manifests, bounded LightBurn and foreign-G-code scans, and a
  native pre-import review gate. Blocked scans cannot proceed; valid and
  warning-only scans require explicit approval before the existing strict
  importer creates one undoable project transaction.
- Machine-aware read-only Coordinate Audit as Machine Setup tab 6, including
  calibration-binding blockers, machine-specific physical honeycomb span,
  diagnostic GRBL realtime pose evidence through local or `e3bridge://`
  transports, and image-bound clicked-point coordinate/containment inspection.
  Bed Mapping consumes that same saved-machine span read-only; realtime audit
  sampling is excluded while a streamed job owns the controller. Permanent
  fixture reach editing and combined-bounds proposals remain later work.
- Mandatory window-modal exact Preview as the only visible path to
  **START JOB**, shared by project and prepared Machine Setup programs.
- Raw Live Monitor diagnostics distinguish Pi usable-frame Capture FPS,
  desktop socket Network FPS, Qt presentation Display FPS, and source Age while
  retaining bounded latest-frame replacement.
- Multi-object camera tracing with color/contrast modes, grid inference,
  reviewed selection, and conversion to project vectors.
- Versioned label-sheet cutting templates with manual selection, synthetic
  geometric ranking, rigid translation/rotation review, and one-step batch
  insertion into a project.
- Dedicated regular rounded-rectangle grid authoring with live preview,
  width/height/radius controls, rows/columns, edge-gap or center-pitch input,
  editable template metadata, and direct one-step insertion into a project.
- Numeric rectangle width/height/radius editing as one undoable shape change.
- Behavioral offscreen coverage for template library controls, one-frame
  alignment review, stale-result cancellation, object creation, and undo.

These features have different verification levels; see `CURRENT_STATE.md`.

## Milestone 1 — trace/template validation and release hardening

- Add broader behavioral Qt tests for trace review and general project editing.
- Exercise tracing against real corrected images at known material heights.
- Build curated label-sheet captures with measured geometry and establish
  confidence/residual acceptance thresholds for template alignment.
- Validate the tested explicit-choice behavior for ambiguous look-alike
  templates against curated real-camera captures.
- Include pairs that share centers and dimensions but differ in corner radius;
  the current matcher intentionally requires manual selection because radius is
  not part of its observable feature model.
- Keep local label/trace captures ignored and excluded from release packages.
- Keep the complete Linux suite and release-package smoke green as the branch
  evolves.

## Milestone 2 — Windows/Linux development consistency

- Keep the Windows/Linux CI matrix and package-import coverage green.
- Add PowerShell setup/launch scripts and platform-appropriate CLI messages.
- Separate portable OpenCV capture from Linux V4L2 discovery/control.
- Validate the new Windows-to-Pi hardware-node path on the real Pi 3 B+,
  including controller disconnect behavior, camera controls, precision-burst
  repeatability, and fresh calibration before declaring Windows network
  hardware support verified. Direct Windows USB serial remains out of scope.

Windows hardware control is not required to complete the portable simulator
milestone. If added later, it needs its own serial transport and tests.

## Milestone 3 — native calibration and camera bring-up

- Repeat the physically exercised lens and keyed automatic base-map workflow
  after a camera remount, including Preview bounds, keyed detection, and axis
  direction.
- Validate stable C920 selection on the Linux workstation.
- Add camera control presets and calibration-image quality guidance.
- Add coverage visualization and repeatability measurements.
- Exercise object tracing with real corrected images at known material heights.
- Exercise selected-template and automatic-template alignment against real
  corrected sheets without applying scale.
- Add optional marker identification only after choosing and testing a marker
  format; the current `marker_id` field is metadata only.

## Milestone 4 — controller profile

- Capture controller identity and settings.
- Confirm GRBL versus Marlin behavior.
- Confirm homing, coordinate origin, X/Y direction, and laser-head offset.
- Establish verified work-area limits and a safe photo pose.
- Add a versioned machine profile based on measured results.
- Record the controller/firmware identity and repeat the observed jog direction
  and endpoint behavior; physically verify STOP response on that named profile.
- Test controller-specific realtime pause/resume before enabling it.

## Milestone 5 — improved authoring

- Shared multi-selection transform boxes and node editing. Single-object
  on-canvas resize and rotation handles are implemented.
- Smart snap guides.
- DXF import and managed or embedded raster assets.
- Text-to-outline conversion.
- Improved SVG `<use>`, stylesheet, and clipping support.
- Fiducial-based automatic rotation/translation.

Multiple designs, grouping, alignment, layer ordering, and toolpath preview are
already present in the desktop foundation and are no longer roadmap items.
Regular rectangle-grid generation and numeric rectangle corner-radius editing
are also implemented; future authoring work should extend rather than duplicate
those controls.

## Milestone 6 — height compensation

- Material-thickness input connected to calibration profiles.
- Camera-height and optical-center model.
- Parallax correction or multiple calibration planes.
- Optional low-cost distance-sensor integration.

## Milestone 7 — advanced raster and production features

- Hatch/fill optimization beyond the existing closed-vector scanline engine.
- Selectable dither methods and calibrated grayscale power curves beyond the
  existing area-prefiltered ordered-dither raster engine.
- Estimated duration refinement and reproducible job manifests.
- Job history.
- Recovery and controlled pause/resume where supported.
- Stable packaging, update checks, and rollback.

## v1.0 criteria

- Repeatable physical alignment performance documented across the work area.
- Verified controller profile and laser power scale.
- Hardware interlock integration documented and tested.
- Installation and recovery tested on a clean supported Linux system.
- Portable simulator and development suite passing on Windows and Linux.
- No known path-generation or out-of-bounds defects in the supported SVG and
  project subsets.
- Documentation accurately distinguishes automated, interactive, and physical
  verification.
