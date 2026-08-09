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
- A full-height right-side inspector for Cuts/Layers, Cameras, Objects, Shape
  Properties, Templates, and Trace, plus a compact bottom row with G-code on
  the left and Laser, Machine, and Material Library tabs beside it
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
- Physical-size SVG import for absolute `mm`, `cm`, `in`, and `px` root
  dimensions plus viewBox-only files (`96 CSS px = 1 in`), including transformed
  groups and `preserveAspectRatio` mapping. Imports remain centered at the
  requested project placement. CSS stylesheets, clipping, masks, and any lossy
  parser warning stop the import before a project object is created.
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
   zooms or pans, and a 70% corrected-camera overlay by default. A dynamic
   key identifies transient Trace, camera-detection, template-cut, and toolpath
   lines by both role and line style. The key stays in the upper-left viewport
   corner during canvas interaction unless the operator drags the key itself.
7. Cuts/Layers, Cameras, Objects, Shape Properties, Templates, and Trace share
   one full-height tabbed inspector on the right.
8. A short dock row beneath the canvas keeps raw G-code in a narrow left panel
   and Laser, Machine, and Material Library in a wider tabbed panel beside it.
9. A fixed 30-color bottom palette assigns selected objects to existing
   operations; clicking an unused color creates a matching operation. The
   status bar reports direct-edit affordances and live workspace feedback.

Raw G-code starts visible in its compact panel. Console remains available from
the Window menu and shares that slot when opened. Generating or framing a job
still opens the dedicated graphical Preview. **Window > Reset workspace
layout** restores this maintained arrangement. Window geometry, dock state,
and active inspector tabs use versioned Qt settings keys for layout `v6`.
Compatible older geometry and tab choices migrate, while obsolete `v5` dock
topology is deliberately ignored so it cannot restore the previous cramped
right-side stack.

The default three-region dock layout is offscreen-tested at `1080x780` and
`900x680` logical pixels with 13 pt application text. The requested window size
is retained, the canvas and all visible dock rectangles remain disjoint, and
every design/runtime inspector fits its horizontal viewport; compact panels
use vertical scrolling where needed. Operation speed, power, pass, interval,
angle, and overscan editors now update the project on edit completion rather
than creating an undo entry and rebuilding the inspector for every typed digit.

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
Imported PNG, JPEG, and BMP images are alpha-composited onto white,
area-prefiltered when the physical raster pitch minifies the source, and use
deterministic 8x8 ordered dithering at the operation's maximum power. TIFF is
rejected consistently because its Qt decode plugin is not portable.

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
- Nearest-path travel ordering and time/distance estimates, with a recorded
  source-order fallback above 512 vector paths
- Dry framing generation
- Immutable preview plans parsed from the exact finalized G-code stream, with
  layer/pass/source metadata stored only in controller-ignored comments
- Prepared-job status reports maximum planned power independently from live
  controller execution progress
- Closed-vector fill, binary vector raster, and area-prefiltered ordered-dither
  grayscale raster output with bounds-checked laser-off overscan
- Unsupported text output, open fill geometry, missing/changed raster assets,
  unsupported raster metadata, and empty dither results are rejected explicitly
  rather than silently omitted
- Zero-effective-power vector, fill, and raster layers never emit `M3` or `M4`;
  their exact motion is counted as travel rather than cut distance
- Existing camera and controller status in native dock panels
- Guarded controller connect, diagnostics, software stop and job run; hardware
  Start automatically homes and parks before arming and execution, while a
  successful powered job drains queued motion, homes, and parks again before
  motor release and finish. Drain/home/park/release phases remain visibly
  running, and an asynchronous completion failure raises a one-time error
- Powered output receives a one-use authorization for the exact prepared
  program when **Start** submits it; the desktop does not show a confirmation
  or typed-phrase dialog

Native Machine Setup exposes raw camera preview and controls, synthetic scenes,
checkerboard capture/solve, a no-prior-map keyed 5×5 base job with automatic
rotation/reflection resolution and transactional application, manual and
CSV-assisted fallback points, seeded grid refinement, residual review, eight-point fine registration, workpiece
detection, and fiducial inspection. Fine registration prepares jobs through the
normal preview/run path. It can apply either a bounded consistent camera-map
translation or a separately gated seven-inlier full-bed homography refinement,
with explicit review, confirmation, persistence, and rollback. The desktop
then provides a separate five-point holdout job with automatic fixed-limit
accuracy scoring and no calibration mutation. The desktop camera panel and
setup dialog use the same corrected-camera core.

Machine Setup keeps the Qt event loop responsive during Home / park, stable and
precision camera captures, and lens solving. It owns one background operation,
shows progress, keeps an in-dialog software STOP available, and prevents the
modal dialog from closing until cleanup finishes. Starting or failing a new
analysis clears the previous review and Apply authority; results arriving after
STOP are not presented.

Project generation follows the same responsiveness contract. The desktop task
pool clones the project while authoring is temporarily held, then performs
toolpath, dry-frame, or Start Here planning against the clone or immutable plan.
Distinct worker/render owners plus source-document and revision checks reject
late success and failure results without mutating newer preparation. Large raw
G-code documents, workspace paths, dedicated Preview paths, and backward
timeline rebuilds are constructed in short GUI-thread slices with visible
progress; Qt objects never cross into workers. Conflicting job actions remain
unavailable until every required exact view finishes, while software STOP stays
live. Closing an unfinished Preview fails closed, and application close retains
workers through completion before runtime teardown. Generated programs remain
in memory until the explicit export action writes one.

Start Here planning snapshots the configured controller photography pose and
records it in the replacement program. Its exact Preview includes the
laser-off approach from that Home/park pose to the reviewed boundary, with the
configured physical laser-spot offset applied, before normal execution
preflight and arming.

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

The source remains in the Git repository. Default user data remains outside it
under `storage.default_user_data_dir()`:

```text
Linux:  $XDG_DATA_HOME/e3-positioning-system/
        (or the Python user base under share/e3-positioning-system/)
Windows: %LOCALAPPDATA%\E3 Positioning System\
         (falling back to %APPDATA% or the Python user base)
```

The current milestone uses:

```text
<user-data-root>/backups/
<user-data-root>/materials.sqlite
```

Existing camera and machine calibration data continues to use the configured
`app.data_dir` until the calibration-profile migration is implemented.

## Safety boundary

The desktop shell does not relax the existing machine controls:

- serial access still requires a hardware-enabled process;
- motion must still be enabled in the local configuration;
- powered G-code still requires exact-program, one-use authorization, created
  internally when the desktop **Start** action submits it;
- programs are validated against the project work area;
- every generated vector path is bracketed by `M5`;
- rapid travel occurs only while the laser is off;
- the software stop is not a replacement for a physical emergency stop.

The Machine panel provides separately tested laser-off incremental jogging.
Home / park must first establish the current XY position; every button press is
converted to an absolute move, begins with `M5`, uses an explicit bounded travel
feed, and intentionally does not apply the configured work-area rectangle so
the operator can measure the actual travel limits. STOP, disconnect, jobs, and
motor release invalidate the jog position. Pause/resume remains disabled until
the Falcon controller's realtime behavior has been physically verified.

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

1. Windows launch scripts and CI. OS-native user-data paths and legacy-data
   migration are implemented.
2. Broader behavioral Qt tests for the remaining project-editing and object-
   tracing workflows.
3. Multi-selection transform boxes, proportional canvas resizing, node
   editing, and smart snap guides. Single-object resize/rotation and transient
   cutting-template drag/rotation are already implemented.
4. Guarded jog API and tested controller-specific realtime pause/resume.
5. DXF import and text-to-outline conversion.
6. Managed or embedded portable raster assets, selectable dithers, and
   calibrated grayscale power curves.
7. Job history and calibration profiles by material height.
8. Stable release packaging, update checks and rollback.
