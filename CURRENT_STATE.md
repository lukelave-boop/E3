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
  -> workpiece / fiducial / object-trace detection
  -> machine-coordinate geometry
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

- **95 tests passed and 2 POSIX-only tests skipped.**
- The complete suite collected, including app simulation and machine-service
  tests.
- The browser simulator served a healthy API response and its HTML interface.
- The native desktop started with the synthetic camera and simulated controller
  under Qt's offscreen backend, ran its event loop, and shut down cleanly.
- The interactive native workflow has not yet been manually exercised on this
  Windows checkout.
- Ruff was not available in the current virtual environment.

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
- Multiple objects and operation layers.
- Rectangle, rounded rectangle, ellipse, line, text, and SVG-path objects.
- Transform, mirror, duplicate, delete, group, ungroup, align, distribute, and
  z-order commands.
- Undo/redo.
- `.e3laser` save/load, backup, autosave, and recovery.
- SQLite material presets.
- Multi-layer vector toolpaths, dry frames, previews, and estimates.
- Camera focus controls and sharpness measurement.
- Guarded machine connection, park, diagnostics, run, and software stop.

### Camera-object tracing

- Automatic color/contrast detection.
- Click-to-sample hue.
- Direct and inferred regular-grid detections.
- Rounded, smoothed, and exact outlines.
- Border offsets.
- Review and selective conversion to editable project objects.
- One-step undo for a created detection set.

The trace algorithms pass synthetic tests. The native trace workflow has not
been exercised end to end with the real camera and calibration.

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
- No on-canvas resize/rotation handles or smart guides.
- No end-to-end GUI automation.

### Hardware

- Controller firmware/protocol and power scale are unverified.
- Machine coordinates, axis directions, work limits, offsets, and photo pose
  are unverified.
- C920 controls, real calibration residuals, repeatability, and parallax are
  unverified.
- No powered laser behavior is verified.

## Recommended next sequence

1. Manually exercise the safe native UI on Windows and record usability issues.
2. Add PowerShell setup/launch scripts and OS-native user-data paths.
3. Add Windows CI while retaining Linux Python-version coverage.
4. Separate portable OpenCV capture from Linux V4L2 discovery/control.
5. Run the complete current suite on Linux and record the exact result.
6. Add behavioral Qt tests for project editing and object tracing.
7. Verify that release archives continue to exclude local camera/trace output.
8. Only then proceed with documented physical camera/controller bring-up.

## Evidence terminology

- **Tested** — covered by a currently passing automated test.
- **Smoke-tested** — imported or constructed, but not exercised end to end.
- **Implemented, unverified** — source exists but lacks current execution
  evidence.
- **Historically verified** — recorded for an earlier commit or release.
- **Physically verified** — exercised on identified real hardware with recorded
  configuration and results.
