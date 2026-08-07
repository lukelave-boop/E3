# E3 native desktop v1 foundation

The `desktop-v1` foundation adds a native PySide6 workspace without removing the
existing browser application. Both interfaces use the same calibrated camera,
geometry, G-code, safety and controller services.

> This document describes the committed desktop foundation. The branch also
> contains the camera-object tracing workflow described in
> [OBJECT_TRACE.md](OBJECT_TRACE.md) and [../CURRENT_STATE.md](../CURRENT_STATE.md).

## Implemented in this milestone

### Native workspace

- Native `QMainWindow` application shell with persistent dock and window layout
- Build-first native window title showing the application name, release version,
  short source revision, current project, and unsaved-change marker
- Split right-side design tabs for Cuts/Layers, Cameras, Objects, Shape
  Properties, Templates, and Trace, plus Laser, Machine, and Material Library
  execution tabs; Console and G-code remain optional docks
- Physical machine-coordinate workspace with adaptive grid, rulers, origin,
  pan, zoom and selectable snap spacing
- Corrected camera image behind the workspace with adjustable opacity
- Safe-simulation controls to load a corrected full-bed test image or generate
  one from a selected cutting template, with a persistent frozen-source badge,
  then restore the synthetic camera
- Selection, drag movement, direct corner resize and rotation handles, numeric
  size/position/rotation, mirror, duplicate, delete, group/ungroup, alignment,
  distribution and z-order controls
- Persistent press-drag-release rectangle drawing plus basic rounded rectangle,
  ellipse, line and text creation
- Existing SVG parser connected to the native project document
- Dedicated exact-job Preview distinguishing rapid, powered and unpowered
  moves, with time scrubbing, animated playback, move coordinates, power,
  timing/distance statistics, display controls, and PNG export

### Current control-surface layout

The desktop shell now follows a LightBurn-inspired information hierarchy
without copying LightBurn branding or changing the E3 machine-control model:

- Boolean options use one consistent compact switch: gray with the control on
  the left for off, and green with the control on the right for on. Disabled
  switches remain visibly unavailable rather than appearing active.

1. Traditional File, Edit, Tools, Arrange, Laser Tools, Window, and Help menus
   retain every existing command.
2. Original icon-only File/Edit/Arrange/Job controls share one compact command
   row above the workspace.
3. A non-hideable runtime strip always reports simulation or hardware
   authority, controller connection, and motion permission. Its software-stop
   action remains enabled during ordinary background work. It stays inline on
   wide windows and moves to its own row below 1100 logical pixels so STOP can
   never disappear into toolbar overflow.
4. An always-present two-row property bar exposes X, Y, width, height,
   percentage scaling, aspect lock, rotation, millimetre/inch display,
   mirroring, and contextual rectangle radius. Controls disable when the
   selection cannot be edited instead of making the toolbar collapse.
5. A compact left rail holds selection, creation, trace/template, fit, zoom,
   and snap tools.
6. The central machine-coordinate workspace uses a near-white adaptive grid,
   thin light rulers, no permanent scroll-bar chrome, auto-fit until the user
   zooms or pans, and an 18% corrected-camera overlay by default. A dynamic
   key identifies transient Trace, camera-detection, template-cut, and toolpath
   lines by both role and line style.
7. The right side uses two vertically split tab stacks: design and operation
   editing above, laser execution and materials below.
8. A fixed 30-color bottom palette assigns selected objects to existing
   operations; clicking an unused color creates a matching operation. The
   status bar reports direct-edit affordances and live workspace feedback.

Console and raw G-code remain dockable from the Window menu but start hidden so
the workspace has more room. Generating or framing a job opens the dedicated
graphical Preview; raw G-code remains available for diagnostics. **Window >
Reset workspace layout** restores the maintained
default arrangement. Window geometry and dock state use the versioned Qt
settings key `v5`, so stale layouts from the earlier shell do not override the
new default.

Direct resize/rotation handles appear only for one visible, unlocked object on
a visible layer. Resizing keeps the opposite corner fixed; holding Shift while
rotating snaps to 15-degree increments. The document is not mutated during the
live preview. Releasing the handle commits through the existing command stack,
so undo/redo and generated-job invalidation remain consistent. Rectangle
radius clamping is part of the same atomic history command.

Rectangle is an exclusive persistent drawing tool rather than a fixed-size
insert command. Pressing it changes no project state. Dragging on empty bed
space previews a square-cornered rectangle in the active operation color and
reports live width/height; release creates and selects exactly one object through
the command stack. Both endpoints obey the current snap setting, every drag
direction is normalized, and zero-size drags are ignored. The tool remains
active for repeated rectangles. Select or a canvas right-click exits drawing;
middle-button and Space-drag still pan. Escape remains reserved for software
stop / laser off.

The Operations/Layers table summarizes layer color/name, mode, speed/power,
Output, and Show state. Inline toggles, ordering controls, the quick editor,
and operation color selection all update the existing project-layer model.
Fill and raster layers generate scanline toolpaths. Their quick editor exposes
line interval and scan angle; raster additionally exposes laser-off overscan.
Imported raster images currently use a fixed 50% luminance threshold and the
operation's maximum power.

### Feature-preservation map

| Existing capability | Current surface | Preservation boundary |
|---|---|---|
| Project file, import, undo/redo, and object commands | Menus, global toolbars, left rail, and context bar | Existing `ProjectDocument` commands and history stack remain authoritative |
| Machine authority, arming, execution, and stop | Persistent runtime strip plus Machine and Job inspectors | `DesktopController` and `MachineService` gates are unchanged; the strip is status/presentation only |
| Camera overlay, focus, and corrected test sources | Camera inspector and workspace | Existing camera/controller lifecycle is unchanged |
| Object tracing | Trace tool and inspector | Existing frozen-frame review and object-creation path is unchanged |
| Cutting templates and alignment | Templates tool, inspector, and grid designer | Existing rigid placement, review, and one-command apply path is unchanged |
| Operation layers and materials | Operations/Layers table, bottom palette, and Materials inspector | Existing layer schema, ordering, presets, and explicit unsupported-mode checks are retained |
| Toolpath generation, framing, preview, and run | Job toolbar, Job inspector, dedicated graphical Preview, and on-demand raw G-code | Preview parses the exact finalized stream; existing revision invalidation, validation, arming, and execution gates are unchanged |
| Browser calibration and placement application | Native Machine Setup plus richer desktop project workflow | Browser remains an optional legacy single-SVG surface; no operator capability requires it |

### Project and operations model

- Framework-independent undo/redo command stack
- Undoable object, transform, layer, grouping, alignment and ordering edits
- Transparent `.e3laser` JSON project format
- Atomic project saves, `.bak` files, periodic autosaves and recovery prompts
- Colored operation layers with line/fill/raster mode, speed, power, passes,
  visibility, output state and priority
- SQLite material-preset library stored outside the source tree
- Material presets can be applied to the active operation layer

### Toolpaths and machine controls

- Multi-layer vector G-code generation using the existing safety/bounds core
- Per-layer speed, power and pass count
- Nearest-path travel ordering and time/distance estimates
- Dry framing generation
- Immutable preview plans parsed from the exact finalized G-code stream, with
  layer/pass/source metadata stored only in controller-ignored comments
- Prepared-job status reports maximum planned power independently from live
  controller execution progress
- Closed-vector fill, binary vector raster, and imported threshold-image raster
  output with bounds-checked laser-off overscan
- Unsupported text output, open fill geometry, missing raster assets, and empty
  threshold results are rejected explicitly rather than silently omitted
- Zero-power vector layers never emit `M3` or `M4`
- Existing camera and controller status in native dock panels
- Guarded controller connect, diagnostics, software stop and job run; hardware
  Start automatically homes and parks before arming and execution
- Powered output still requires the exact temporary arming phrase

Native Machine Setup exposes raw camera preview and controls, synthetic scenes,
checkerboard capture/solve, manual and CSV-assisted bed points, automatic 5×5
grid detection, residual review, eight-point fine registration, workpiece
detection, and fiducial inspection. Fine registration prepares jobs through the
normal preview/run path. It can apply either a bounded consistent camera-map
translation or a separately gated seven-inlier full-bed homography refinement,
with explicit review, confirmation, persistence, and rollback. The desktop
then provides a separate five-point holdout job with automatic fixed-limit
accuracy scoring and no calibration mutation. The desktop camera panel and
setup dialog use the same corrected-camera core.

## Installation

From the repository root:

```bash
./install.sh
./install-desktop.sh
```

The desktop installer adds PySide6 to the existing virtual environment and
creates two application entries:

- **E3 Positioning System** — serial hardware can be opened after local safety
  checks.
- **E3 Positioning System (Safe)** — serial hardware is locked for design,
  camera and simulation work.

Direct launch commands:

```bash
./run-desktop.sh
./run-desktop-hardware.sh
```

## Project format

`.e3laser` files are readable JSON. Version 1 stores:

- physical work area;
- operation layers;
- vector objects and their z-order;
- object transforms, visibility, lock and group membership;
- embedded original SVG text when available;
- project metadata and timestamps.

The format deliberately does not depend on LightBurn project files.

## Data locations

The source remains in the Git repository. User data remains outside it:

```text
~/.config/e3-positioning-system/
~/.local/share/e3-positioning-system/
```

The current milestone uses:

```text
~/.local/share/e3-positioning-system/backups/
~/.local/share/e3-positioning-system/materials.sqlite
```

Existing camera and machine calibration data continues to use the configured
`app.data_dir` until the calibration-profile migration is implemented.

## Safety boundary

The desktop shell does not relax the existing machine controls:

- serial access still requires a hardware-enabled process;
- motion must still be enabled in the local configuration;
- powered G-code still requires the exact temporary arming phrase;
- programs are validated against the project work area;
- every generated vector path is bracketed by `M5`;
- rapid travel occurs only while the laser is off;
- the software stop is not a replacement for a physical emergency stop.

Jog controls are visible to establish the final interaction design, but remain
disabled in the controller bridge until a separately tested guarded jog API is
added to the core. Pause/resume likewise remains disabled until the Falcon
controller's realtime behavior has been physically verified.

## Validation performed for this milestone

- Core project, history, save/recovery, material and toolpath tests
- Desktop source parsing without requiring PySide6 in headless CI
- Python bytecode compilation
- Shell-script syntax checks
- Editable package installation without desktop dependencies
- Friendly startup failure when PySide6 has not yet been installed

The Qt windows themselves must still be exercised on the Linux Mint workstation
because PySide6 and a graphical display are not present in the build environment.

The complete desktop application has since started, run its event loop, and
shut down under Qt's offscreen backend on Windows with the synthetic camera and
simulated controller. This remains a smoke test rather than an interactive GUI
test. POSIX serial is selected lazily; Linux remains the only hardware platform.

## Next desktop milestones

1. Windows launch scripts, OS-native user-data paths, and CI.
2. Behavioral Qt tests for project editing and object tracing.
4. Multi-selection transform boxes, proportional canvas resizing, node
   editing, and smart snap guides. Single-object resize/rotation and transient
   cutting-template drag/rotation are already implemented.
5. Guarded jog API and tested controller-specific realtime pause/resume.
6. DXF/image import and text-to-outline conversion.
7. Grayscale/dithered image modes and embedded portable project assets.
8. Job history and calibration profiles by material height.
9. Stable release packaging, update checks and rollback.
