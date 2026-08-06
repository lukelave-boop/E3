# Project status

Version metadata: **0.1.0-alpha**

Working branch at audit: **`desktop-v1`**

Status updated: **2026-08-06**

The project is an early alpha. Simulation and substantial platform-neutral
functionality are implemented, but physical camera/controller behavior and all
powered laser behavior remain unverified. See [CURRENT_STATE.md](CURRENT_STATE.md)
for the exact consolidated branch state and local-artifact policy.

## Verification levels

The repository uses these terms deliberately:

- **Tested** — covered by a currently passing automated test.
- **Smoke-tested** — imported or constructed, but not exercised end to end.
- **Implemented, unverified** — source exists without current execution evidence.
- **Historically verified** — recorded for an earlier commit or release.
- **Physically verified** — exercised on identified hardware with recorded
  configuration and results.

## Current Windows audit

Environment:

- Python 3.14
- PySide6 6.11.1

Results:

- **307 tests passed and 2 POSIX-only tests skipped.**
- Project model/history, persistence, materials, SVG/geometry, homography,
  lens-cache behavior, workpiece/fiducial detection, object tracing, and both
  G-code pipelines passed their available tests.
- App simulation and machine-service tests passed on Windows.
- The browser simulator served its health endpoint and HTML interface.
- The native desktop started, ran, and shut down with Qt's offscreen backend,
  the synthetic camera, and the simulated controller.
- A native Windows 1600 x 900 safe-simulation render verified the compact icon
  chrome, responsive persistent STOP control, fitted light drafting bed,
  corrected-camera blend, split inspector stacks, and fixed color palette.
- Focused cutting-template and test-image runs covered the portable
  model/library, deterministic renderer, real detector/matcher, controller
  acceptance and source-generation behavior, widgets, direct-canvas
  drag/rotation, overlay, apply/undo, and stale generated-job protection.
- An actual offscreen `E3MainWindow` smoke test exercised template library
  refresh, placement, one-command application, and undo.
- A second offscreen `E3MainWindow` smoke test drove the modal designer through
  parametric grid save, exact-ID editing, four-object insertion as one command,
  and one-step undo.
- Focused layout regressions verify unclipped Save/Update actions, a compact
  600 x 430 logical designer, and the Templates, Camera, Trace, and Transform
  inspectors at 360 px with 13 pt text.
- Focused desktop-shell regressions cover selection-aware numeric controls,
  the persistent runtime/safety strip, compact Operations/Layers behavior,
  operation color editing, and direct resize/rotation handles with atomic
  history integration.
- Focused rectangle-drawing regressions cover non-mutating tool activation,
  live preview, four drag directions, endpoint snapping, degenerate no-ops,
  repeated creation, Select/right-click cancellation, Space-pan priority,
  minimum-size float tolerance, direct handle editing while the tool remains
  active, immediate selection, and one-command undo/redo.
- Focused trace regressions verify fitted rounded-vector preview geometry,
  radius reporting, contour placement parity between preview and creation,
  frozen analyzed-frame review, stale asynchronous result rejection, and exact
  pixel-center registration between corrected images and vector overlays.
  Perfect rounded raster masks recover their known integer-pixel radii.
- Focused alignment-review regressions verify the role-labeled overlay key,
  solid cyan aligned cuts, dashed amber camera edges drawn above coincident cut
  lines, and exact 78 x 21 mm / 3 mm-radius generated label geometry.
- Ruff was not installed in the current virtual environment and was not run.

The template suite also verifies that one corrected frame is reused for every
candidate option group, camera delivery remains frozen during review, and stale
camera, match, or G-code results cannot be applied after relevant state changes.
It exercises Unicode-safe corrected-image loading, strict uniform-scale aspect
validation, copy-isolated memory-only source state, generated color/contrast
frames at known poses, controller pose recovery, live-timer restoration, camera
control gating, and the persistent frozen-source badge. The maximum 500-feature
renderer has a structural regression proving that label blending stays within
local pixel regions.

The parametric rectangle-grid builder and designer, exact-ID template
replacement, gap/pitch conversion, 500-object and work-area gates, and atomic
rectangle width/height/radius editing remain covered. Toolpath coverage also
verifies that insignificant floating-point drift at an exact work-area boundary
is accepted while meaningful overflow remains blocked. The complete 307-test
suite passed after the desktop control-surface integration and review fixes.

POSIX serial is selected lazily, so `termios` is not imported by simulator or
application startup on Windows. Selecting the real serial backend on Windows
returns a clear unsupported-platform error. The two pseudoterminal tests skip
because their facilities are POSIX-only.

Current Windows test command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

This verifies safe simulator behavior, not Windows camera or serial hardware.

## Historically verified on Linux

The 0.1.0 release state recorded:

- package compilation and a passing automated suite;
- synthetic camera startup with automatic perspective bed mapping;
- lens-calibration capture/solve, bed homography, rectification, workpiece
  detection, SVG parsing, placement, path ordering, and G-code generation;
- local HTTP health and a complete simulated API workflow;
- POSIX pseudoterminal framing, controller acknowledgements, photography-pose
  parking, arming, and streamed jobs;
- rejection of unsafe modal commands, offsets, unsupported arcs, out-of-bounds
  coordinates, rapid travel with the laser active, and programs lacking a final
  `M5`;
- stop/disarm behavior that revoked authorization and requested `M5`.

The previous “20 passed” number described that earlier release and is no longer
the current test inventory. The exact consolidated desktop/object-trace branch
from 2026-08-06 has not been run as a complete Linux suite during this audit.

## Feature verification boundary

| Area | State |
|---|---|
| Configuration and portable persistence | Tested on Windows |
| SVG parsing and transforms | Tested on Windows with representative SVG cases |
| Lens-cache and bed-homography logic | Tested on Windows with synthetic data |
| Workpiece, crosshair-grid, and object-trace vision | Tested on Windows with synthetic images |
| Native trace preview/review/create lifecycle | Behaviorally tested offscreen on Windows; not real-camera verified |
| Browser single-SVG G-code | Tested on Windows at the library level |
| Desktop project model, history, materials, and toolpaths | Tested on Windows |
| Cutting-template model, library, and rigid instantiation | Tested on Windows |
| Geometric template matching and candidate ranking | Tested with synthetic features on Windows |
| Frozen corrected test-image source and generated known-pose alignment | Behaviorally tested in safe simulation on Windows; not real-camera verified |
| Rectangle-grid builder and editable authoring metadata | Tested with portable model/library checks on Windows |
| Grid designer and ordinary rectangle radius editing | Behaviorally tested offscreen; not interactively verified |
| Native template save/select/review/direct-canvas-adjust/apply workflow | Behaviorally tested offscreen; not real-camera verified |
| Native desktop control surface, operation table, and context controls | Behaviorally tested offscreen on Windows; not yet interactively verified after the redesign |
| Single-object canvas resize/rotation and undo/redo | Behaviorally tested offscreen on Windows |
| Persistent canvas rectangle drawing and undo/redo | Behaviorally tested and visually rendered offscreen on Windows |
| Native desktop simulator startup | Smoke-tested offscreen on Windows |
| Browser simulator startup | Smoke-tested through HTTP on Windows |
| Machine simulator safety suite | Tested on Windows |
| POSIX serial transport | Historically verified through a Linux pseudoterminal |
| Full interactive desktop workflow | Implemented, not end-to-end verified |
| Real C920 capture/calibration | Implemented for Linux, not physically verified |
| Real controller motion or laser output | Not physically verified |

Source-presence checks remain limited evidence; the cutting-template workflow
also has behavioral Qt and actual offscreen-window coverage. It is still not a
substitute for interactive or real-camera testing.

## Not yet physically verified

The following values cannot be responsibly guessed and remain locked or
conservative until measured on the target machine:

- controller protocol and firmware identity;
- serial device path and baud rate;
- controller laser-mode configuration and usable `S` range;
- whether `M3`, `M4`, or another controller-specific mode is appropriate;
- machine origin, axis directions, homing behavior, and reachable laser-head
  area;
- laser-head offset relative to the original nozzle/tool reference;
- repeatable bed photography pose;
- C920 focus/exposure values for the installed lighting and mounting height;
- lens-calibration residuals from real checkerboard photographs;
- bed-mapping residuals and repeatability across cold starts;
- parallax error at different workpiece thicknesses;
- template-match residuals, ambiguity thresholds, and repeatability on real
  corrected label sheets;
- real dry-motion and low-power framing behavior.

Cutting-template placement is deliberately limited to translation and
rotation. The application reports scale mismatch instead of resizing cut
geometry. The `.e3template` `marker_id` field is metadata only; no marker
detector is implemented. Applying a reviewed template creates one undoable
batch of new project objects. The current match gates are conservative,
provisional software checks rather than evidence of physical alignment
accuracy. See
[docs/CUT_TEMPLATES.md](docs/CUT_TEMPLATES.md).

Before application, the transient cyan cut preview can be dragged as one rigid
layout and rotated with its round canvas handle. These gestures update the same
center/rotation values as the numeric controls; they do not resize cuts or move
the frozen amber camera detections.

In safe simulation, the corrected frame can instead come from a validated
full-bed PNG/JPEG or a deterministic template render with known X/Y/rotation,
noise, and missing-label settings. The source is memory-only and unavailable in
a hardware-enabled process. This verifies software flow, not camera correction,
bed calibration, parallax, physical placement, controller motion, or laser
accuracy.

Regular-grid templates support at most 500 rounded-rectangle cuts. Their
authoring recipe stores edge gaps and derives center pitch and footprint; the
desktop can present either spacing form. Grids with fewer than three cells are
manual-placement templates because they cannot meet the automatic feature-count
gate. Corner radius is preserved in cut geometry, but it is not a matching
feature: otherwise identical templates that differ only by radius cannot be
automatically distinguished.

## Hardware bring-up gate

Do not enable `machine.allow_motion` or use `./run-hardware.sh` until the camera
and controller probes have been captured and reviewed. Keep the laser power
lead physically disconnected during early motion/controller identification
where practical. The first physical execution should be a small, centrally
located dry frame generated by the application, followed by repeatability
measurements before any powered laser test.

See [docs/HARDWARE_BRINGUP.md](docs/HARDWARE_BRINGUP.md) and
[SAFETY.md](SAFETY.md).
