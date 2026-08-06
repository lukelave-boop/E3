# Roadmap

This roadmap describes remaining work from the current `desktop-v1` branch. It
is not a hardware-readiness claim. Verification state belongs in
[CURRENT_STATE.md](CURRENT_STATE.md) and [PROJECT_STATUS.md](PROJECT_STATUS.md).

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

- Native machine-coordinate workspace and camera overlay.
- Multi-object/layer project model and `.e3laser` persistence.
- Undo/redo, grouping, alignment, distribution, and ordering.
- Material presets, multi-layer vector toolpaths, preview, and estimates.
- Guarded machine controls with jogging and pause/resume still disabled.

Current branch feature:

- Multi-object camera tracing with color/contrast modes, grid inference,
  reviewed selection, and conversion to project vectors.

These features have different verification levels; see `CURRENT_STATE.md`.

## Milestone 1 — trace validation and release hardening

- Add behavioral Qt tests for trace review and project-object creation.
- Exercise tracing against real corrected images at known material heights.
- Keep local label/trace captures ignored and excluded from release packages.
- Run the complete consolidated branch suite on Linux.

## Milestone 2 — Windows/Linux development consistency

- Introduce a portable serial transport interface.
- Import POSIX `termios` code only when the POSIX backend is selected.
- Make the browser, desktop, app simulation, and machine simulator run on
  Windows without serial hardware.
- Skip POSIX pseudoterminal tests explicitly on unsupported platforms.
- Add Windows CI alongside the existing Linux Python matrix.
- Introduce OS-native user-data paths for autosaves and material presets.
- Add PowerShell setup/launch guidance and platform-appropriate CLI messages.
- Separate portable OpenCV capture from Linux V4L2 discovery/control.

Windows hardware control is not required to complete the portable simulator
milestone. If added later, it needs its own serial transport and tests.

## Milestone 3 — native calibration and camera bring-up

- Add native lens and bed-calibration wizards.
- Validate stable C920 selection on the Linux workstation.
- Add camera control presets and calibration-image quality guidance.
- Add coverage visualization and repeatability measurements.
- Exercise object tracing with real corrected images at known material heights.

## Milestone 4 — controller profile

- Capture controller identity and settings.
- Confirm GRBL versus Marlin behavior.
- Confirm homing, coordinate origin, X/Y direction, and laser-head offset.
- Establish verified work-area limits and a safe photo pose.
- Add a versioned machine profile based on measured results.
- Add a separately tested guarded jog API.
- Test controller-specific realtime pause/resume before enabling it.

## Milestone 5 — improved authoring

- On-canvas resize and rotation handles.
- Smart snap guides.
- DXF and image import.
- Text-to-outline conversion.
- Improved SVG `<use>`, stylesheet, and clipping support.
- Fiducial-based automatic rotation/translation.

Multiple designs, grouping, alignment, layer ordering, and toolpath preview are
already present in the desktop foundation and are no longer roadmap items.

## Milestone 6 — height compensation

- Material-thickness input connected to calibration profiles.
- Camera-height and optical-center model.
- Parallax correction or multiple calibration planes.
- Optional low-cost distance-sensor integration.

## Milestone 7 — fill, raster, and production features

- Fill engine.
- Raster engraving with overscan and scan-direction tests.
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
