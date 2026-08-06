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
- Dockable Cuts/Layers, Objects, Transform, Camera, Move/Machine, Material
  Library, Job, Console and G-code panels
- Physical machine-coordinate workspace with adaptive grid, rulers, origin,
  pan, zoom and selectable snap spacing
- Corrected camera image behind the workspace with adjustable opacity
- Selection, drag movement, numeric size/position/rotation, mirror, duplicate,
  delete, group/ungroup, alignment, distribution and z-order controls
- Basic rectangle, rounded rectangle, ellipse, line and text creation
- Existing SVG parser connected to the native project document
- Visual toolpath preview distinguishing rapid, powered and unpowered moves

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
- Unsupported fill, raster, text and image output is rejected explicitly rather
  than being silently omitted
- Zero-power vector layers never emit `M3` or `M4`
- Existing camera and controller status in native dock panels
- Guarded controller connect, home/park, diagnostics, software stop and job run
- Powered output still requires the exact temporary arming phrase

The existing browser calibration workflow remains the validated calibration UI
while native lens/bed calibration dialogs are built. The desktop camera panel
already uses the same corrected-camera core.

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

Selected panels and the workspace have since been constructed with Qt's
offscreen backend on Windows, but this remains a smoke test rather than an
interactive GUI test. The complete desktop application currently fails before
startup on Windows because the shared machine service imports the POSIX-only
`termios` transport unconditionally. Linux remains the only application and
hardware platform until the transport boundary is made lazy and portable.

## Next desktop milestones

1. Portable simulator/application startup and CI on Windows and Linux.
2. Native lens and camera-to-machine calibration wizards.
3. Behavioral Qt tests for project editing and object tracing.
4. On-canvas resize and rotation handles plus smart snap guides.
5. Guarded jog API and tested controller-specific realtime pause/resume.
6. DXF/image import and text-to-outline conversion.
7. Fill and raster engines with overscan and scan-direction tests.
8. Job history and calibration profiles by material height.
9. Stable release packaging, update checks and rollback.
