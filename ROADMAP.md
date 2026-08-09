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
  selected only on supported systems.
- Native lens calibration and fresh keyed 5×5 base mapping that does not depend
  on a prior camera homography; real-machine verification remains outstanding.

Current branch feature:

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

- Add Windows CI alongside the existing Linux Python matrix.
- Add PowerShell setup/launch scripts and platform-appropriate CLI messages.
- Separate portable OpenCV capture from Linux V4L2 discovery/control.

Windows hardware control is not required to complete the portable simulator
milestone. If added later, it needs its own serial transport and tests.

## Milestone 3 — native calibration and camera bring-up

- Physically verify the native lens and keyed automatic base-map workflow after
  a camera remount, including dry bounds, keyed detection, and axis direction.
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
- Physically verify guarded jog direction, endpoint behavior, and STOP response
  on the named controller profile.
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
