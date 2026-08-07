# Laser Camera Aligner / E3 Positioning System

A self-hosted camera-alignment and vector-placement application for a
laser-converted 3D printer. It includes a native PySide6 project and machine
setup workspace plus a legacy dependency-light browser workflow. The initial hardware target is
an **Ender-3 S1 Pro**, a **Creality 10 W blue-diode laser module**, and a
stationary overhead **Logitech C920**.

> **Project status: early alpha.** The portable geometry, calibration, project,
> tracing, and G-code layers are under active development. Linux is the only
> current hardware platform. Windows supports safe browser and desktop
> simulation for UI development, but not serial hardware or Linux-specific
> camera controls. Powered alignment experiments are recorded in
> `CURRENT_STATE.md`, but the controller/profile and final physical accuracy
> are not yet verified configurations.

Read [CURRENT_STATE.md](CURRENT_STATE.md) for the branch snapshot and
[PROJECT_STATUS.md](PROJECT_STATUS.md) for the verification boundary. Read
[SAFETY.md](SAFETY.md) before connecting hardware.

## What is implemented

Shared core and browser workflow:

- Browser-based local calibration and placement interface with no paid cloud service
- Logitech C920 capture through Linux V4L2/OpenCV
- Manual focus, exposure, white-balance, and gain locking when exposed by the camera driver
- Precision analysis capture that settles, discards buffered frames, measures a
  configurable fresh-frame burst, rejects temporal outliers, and reports mark
  jitter plus camera-control readback
- Synthetic camera and GRBL-like controller for development without hardware
- Multi-image checkerboard lens calibration
- Multi-point image-pixel to machine-XY homography with RANSAC and residual error reporting
- Perspective-corrected, top-down bed photograph
- Traditional computer-vision detection of a rectangular workpiece
- SVG loading and display with drag, size, and rotation controls
- In-house SVG vector parser for paths, lines, polylines, polygons, rectangles, circles, ellipses, groups, transforms, curves, and arcs
- Path flattening, nearest-path ordering, design bounds checks, dry framing, and vector G-code generation
- Linux serial communication without a mandatory pyserial dependency
- GRBL/Marlin identification probes and a read-only diagnostic command console (`M5` is the only actuator command accepted there)
- Temporary laser arming, automatic disarming, software stop, and simulation-first defaults
- Unit tests and GitHub Actions configuration

Native desktop workflow:

- LightBurn-inspired compact icon chrome with a bright drafting bed, persistent
  runtime/safety status, always-present selection properties, split design and
  laser inspector stacks, and a fixed 30-color operation palette
- PySide6 machine-coordinate workspace with camera overlay, pan, zoom, grid,
  rulers, snapping, and toolpath preview
- Multi-object `.e3laser` projects with operation layers, undo/redo, grouping,
  alignment, distribution, ordering, autosave, backup, and recovery
- Rectangle, rounded rectangle, ellipse, line, text, and imported SVG-path
  objects, with numeric width/height and corner-radius editing for rectangles
- Persistent rectangle drawing directly on the bed with a live active-layer
  preview, snapping, immediate selection, and undo/redo-backed commits
- Direct single-object corner resizing and rotation on the canvas, including
  fixed-size handles, Shift-to-snap rotation, and undo/redo-backed commits
- Per-layer line, fill, and binary-raster speed, power, pass count, ordering,
  scan interval/angle, laser-off raster overscan, estimates, and dry frames
- Dedicated exact-job Preview with a time scrubber, animated playback up to
  40×, cut/travel visibility, power shading, live move coordinates, timing and
  distance statistics, and PNG export
- SQLite material presets and camera focus/sharpness controls
- Native Machine Setup for camera controls and preview, checkerboard lens
  calibration, manual/CSV/automatic bed mapping, eight-point fine registration,
  reviewed translation/full-bed refinement with rollback, bounded 5×5 local
  correction, independent 4×4 holdout validation, and guided five-point
  holdout accuracy validation. Registration and validation can use precision
  multi-frame capture and can be repeated without another home cycle to
  separate camera variation from homing variation
- Versioned `.e3template` cutting-template library with manual selection,
  geometry-based automatic matching, role-labeled camera/cut overlays, rigid
  alignment review, and one-step undo when aligned cut objects are created
- Dedicated regular-grid template designer with a live preview, editable cut
  size/radius, rows, columns, edge-gap or center-pitch spacing, and direct
  template-library or project-object creation
- Simulation-only frozen alignment images loaded from PNG/JPEG or generated
  deterministically from a selected template at a known pose
- Guarded controller connection, camera-pose parking, diagnostics, job run, and
  software stop
- Validated G-code export; no operator capability requires the browser UI

See [docs/MACHINE_SETUP.md](docs/MACHINE_SETUP.md) for the native calibration
workflow and browser-parity map, and [docs/JOB_PREVIEW.md](docs/JOB_PREVIEW.md)
for the generated-job review workflow.

The desktop branch also contains a synthetically and behaviorally tested
object-tracing workflow for converting detected camera outlines into editable
project objects. Rounded-label output previews the same fitted vector that will
be created. Repeated rounded-label grids can share one fitted cell geometry and
lattice so damaged observations and inferred gaps produce corresponding
identical row/column objects, while pixel contours remain available for
irregular objects. The
real-camera GUI flow is not yet verified. See
[docs/OBJECT_TRACE.md](docs/OBJECT_TRACE.md).

Reusable label-sheet cutting templates can be created from visible project
objects, selected manually or ranked against detected sheet geometry, and
placed by reviewed translation and rotation. Drag the cyan preview directly in
the workspace to move the whole cut layout, or drag its round handle to rotate
it; the canvas and numeric placement fields remain synchronized. Scale is never applied
automatically. Marker IDs are reserved in the file format but marker detection
is not implemented. Automatic review uses one frozen corrected-camera frame,
rejects weak or ambiguous fits, and always leaves final adjustment and approval
to the operator. See
[docs/CUT_TEMPLATES.md](docs/CUT_TEMPLATES.md).

For a regular label sheet, open **Create > Design grid cutting template…**.
Enter the rectangle width, height, corner radius, row/column count, and spacing
as either the clear edge gap or the center-to-center pitch. The designer shows
the resulting footprint and supports at most 500 cuts. A saved grid keeps its
editable parameters; custom templates can still be created with **From current
project…**. Grids with fewer than three cuts remain available for manual
placement but cannot pass automatic matching. Automatic matching does not
compare corner radius, so templates that differ only by radius must be selected
manually.

In safe simulation, the Templates panel can exercise the same detection and
alignment path without a physical camera. Choose **Load test image…** for an
already corrected, top-down image of the complete work area, or select a
template and choose **Generate from selected template…**. Generated images
provide known center X/Y and rotation controls plus deterministic noise and a
missing-label count. The chosen image remains frozen while **Auto identify and
align** or **Align selected template** runs. Choose **Return to synthetic
camera** to clear the override and resume the normal simulated camera feed.
Loaded-image dimensions must describe the configured full bed at one uniform
pixel scale; images are resized to the configured corrected-frame resolution
but are not lens-corrected, perspective-rectified, cropped, or calibrated by
the loader. See
[docs/CUT_TEMPLATES.md](docs/CUT_TEMPLATES.md#testing-alignment-without-hardware).

## Deliberately disabled by default

The shipped configuration uses a synthetic camera and controller. In a real-hardware profile:

- Serial access requires the `--hardware` command-line flag.
- Motion remains blocked until `machine.allow_motion` is explicitly changed.
- Positive laser commands require temporary arming with the phrase displayed in the UI.
- Low-power laser framing is disabled, and the default dry-frame file contains no `M3`/`M4` laser-enable command at all.
- Laser-head mounting offsets default to zero. Real-hardware profiles can set
  `laser.spot_offset_x_mm` and `laser.spot_offset_y_mm`; generated jobs show the
  applied values and validate both desired spot and shifted controller bounds.
- The server binds only to `127.0.0.1`.

These are software guardrails, not safety-rated controls.

## Fastest way to try it on Linux

On Linux Mint, Ubuntu, or Debian:

```bash
unzip laser-camera-aligner-0.1.0.zip
cd laser-camera-aligner
./install.sh
./run.sh
```

The browser opens to `http://127.0.0.1:8080`. Simulation mode starts with an automatically mapped perspective bed, a test workpiece, and a simulated controller.

Load `sample_data/sample_design.svg`, drag it over the simulated workpiece, generate G-code, type the temporary arming phrase, and run it against the simulator.

For the native desktop after the base installation:

```bash
./install-desktop.sh
./run-desktop.sh
```

The desktop includes the complete native Machine Setup workflow. The browser
uses the same calibration files and remains available as a legacy alternative.

## Windows development status

The browser and native desktop applications run on Windows with the synthetic
camera and simulated controller. POSIX serial code is imported only if the
serial backend is selected; Windows serial hardware, V4L2 camera controls,
install/launch scripts, OS-native user-data paths, and CI are not implemented.

From an existing desktop-enabled virtual environment, launch the native UI in
safe simulation mode:

```powershell
.\.venv\Scripts\python.exe -m laser_aligner.desktop.main `
  --safe `
  --config config\default.json
```

Launch the browser simulator with:

```powershell
.\.venv\Scripts\python.exe -m laser_aligner `
  --config config\default.json
```

These commands do not enable real serial hardware.

## Moving to the real C920

1. Copy the hardware example:

   ```bash
   cp config/ender3_s1_pro.hardware.example.json config/local.json
   ```

2. Find the stable C920 device path:

   ```bash
   ls -l /dev/v4l/by-id/
   .venv/bin/python tools/camera_probe.py
   ```

3. Replace the camera device placeholder in `config/local.json`.
4. Keep the controller backend set to `simulator` during camera and calibration work, or leave serial motion disabled.
5. Start with:

   ```bash
   ./run.sh
   ```

See [docs/CAMERA_SETUP.md](docs/CAMERA_SETUP.md) and [docs/CALIBRATION.md](docs/CALIBRATION.md).

## Controller bring-up

Do not guess whether the Creality conversion is speaking GRBL or Marlin. With laser power physically disconnected if practical, run:

```bash
.venv/bin/python tools/controller_probe.py --port /dev/serial/by-id/YOUR_CONTROLLER
```

The probe only sends identity/configuration requests (`$I`, `$$`, and `M115`); it does not send motion or laser-enable commands. Record the output in a GitHub issue or commit it to a local bring-up note with secrets removed.

Only after the port, protocol, coordinate system, reachable laser work area, power scale, and framing behavior are verified should `machine.allow_motion` be changed to `true` and the application started with:

```bash
./run-hardware.sh
```

See [docs/HARDWARE_BRINGUP.md](docs/HARDWARE_BRINGUP.md).

## Repository layout

```text
laser_aligner/
  camera/        C920/V4L2 and synthetic capture
  calibration/   lens model, bed mapping, printable target generation
  core/          UI-neutral runtime lifecycle
  desktop/       native PySide6 project workspace
  geometry/      SVG parsing, curves, transforms, units
  gcode/         placement, validation, generation, preview parsing
  machine/       POSIX serial, simulator, controller safety service
  materials/     SQLite material-preset library
  project/       project model, history, persistence, alignment, toolpaths
  templates/     reusable templates, grid authoring, library, and rigid placement
  vision/        workpiece, fiducial, object tracing, and template alignment
  web/           dependency-free browser interface
config/          simulation and hardware examples
docs/            setup, calibration, safety, and architecture notes
targets/         exact-size SVG calibration targets
tools/           camera/controller probes and release tooling
tests/           unit and integration tests
```

## Development commands

Linux:

```bash
# Run all tests
.venv/bin/python -m pytest

# Generate calibration targets
.venv/bin/python -m laser_aligner --config config/local.json --generate-targets

# Run with verbose logs
./run.sh --verbose

# Build a clean source ZIP
.venv/bin/python tools/make_release.py
```

Windows PowerShell:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
```

POSIX pseudoterminal tests skip explicitly on Windows. Contributors should read
[AGENTS.md](AGENTS.md) before making changes.

## GitHub setup

```bash
./bootstrap-git.sh
git remote add origin git@github.com:YOUR_USERNAME/YOUR_REPOSITORY.git
git push -u origin main
```

Keep `config/local.json`, captures, calibration photographs, logs, and generated G-code out of Git by default. Calibration JSON files also live under `data/`; back them up separately after the physical rig is finalized.

## Current limitations

- Linux is the only current hardware platform. Windows is limited to the
  synthetic camera and simulated controller.
- Line vectors, closed-vector fills, binary vector rasters, and imported
  threshold raster images are supported. Text-to-path and grayscale/dithered
  raster power modulation are not implemented.
- SVG text and embedded images are ignored. Convert text to paths in the design program.
- CSS stylesheets, clipping paths, masks, and every edge case of the full SVG specification are not supported.
- The camera mapping assumes the material top surface is on the calibration plane. Height/parallax compensation is planned but not yet implemented.
- Cutting-template identification and alignment have synthetic coverage but
  have not been verified with real corrected label-sheet images. The desktop's
  loaded/generated test-frame workflow verifies software behavior only; it
  cannot validate lens/bed calibration, parallax, camera pose, material height,
  controller motion, or laser accuracy. Placement is rigid translation/rotation
  only, and `marker_id` is currently metadata rather than an active marker
  detector.
- Automatic template matching cannot distinguish layouts whose matching
  features differ only in rounded-corner radius; use manual selection and
  inspect the overlay for those templates.
- No safety-rated enclosure, interlock, flame detector, or hardware E-stop can be implemented in this software.
- The exact Creality controller protocol and `S` power range for this particular conversion kit remain unverified.
- Software stop is not a substitute for immediately removing power with a hardware emergency stop.

## References used for the initial design

- OpenCV camera calibration documentation: https://docs.opencv.org/master/d4/d94/tutorial_camera_calibration.html
- OpenCV calibration-pattern guidance: https://docs.opencv.org/master/da/d0d/tutorial_camera_calibration_pattern.html
- Logitech C920 technical specifications: https://support.logi.com/hc/en-us/articles/360023307294-C920-Technical-Specifications
- Creality laser engraver safety/manual material: https://wiki.creality.com/en/laser-engraver/cr-laser-falcon/user-manual
- GRBL source and protocol project: https://github.com/grbl/grbl

## Safety

Read [SAFETY.md](SAFETY.md) before connecting the application to real hardware. A 10 W, approximately 455 nm diode laser is capable of permanent eye injury, fire, harmful smoke generation, and damage from reflected beams. Never leave a laser job unattended.
