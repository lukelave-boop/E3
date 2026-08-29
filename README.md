# Laser Camera Aligner / E3 Positioning System

A self-hosted camera-alignment and vector-placement application for a
laser-converted 3D printer. It includes a native PySide6 project and machine
setup workspace plus a legacy dependency-light browser workflow. The initial hardware target is
an **Ender-3 S1 Pro**, a **Creality 10 W blue-diode laser module**, and a
stationary overhead **Logitech C920**.

> **Project status: early alpha.** The portable geometry, calibration, project,
> tracing, and G-code layers are under active development. Windows supports the
> authenticated Raspberry Pi controller/camera bridge; direct local serial and
> Linux-specific camera controls remain Linux-only. E3 has no simulated hardware
> runtime: unavailable hardware remains unavailable. Powered alignment experiments are recorded in
> `CURRENT_STATE.md`, but the controller/profile and final physical accuracy
> are not yet verified configurations.

> The network-hardware candidate also allows a Windows desktop to keep the guarded
> `MachineService` locally while using an authenticated Raspberry Pi hardware node
> for the controller and camera. Direct USB hardware remains Linux-only. The
> Windows-to-Pi path is software-tested but is not physically verified; see
> [docs/NETWORK_MACHINE.md](docs/NETWORK_MACHINE.md).

> The optional S1 Pro Z / CR Touch reference path uses a second authenticated
> high-level Pi service while retaining the laser controller as the only real
> X/Y authority. Its software ceiling and cold-start confirmation do not replace
> a physical Z-max switch. See
> [docs/S1_PRO_Z_HOMING.md](docs/S1_PRO_Z_HOMING.md).

Read [CURRENT_STATE.md](CURRENT_STATE.md) for the active branch and verification
boundary. [PROJECT_STATUS.md](PROJECT_STATUS.md) is the dated 2026-08-06 Windows
portability snapshot retained as historical evidence. Read [SAFETY.md](SAFETY.md)
before connecting hardware.

For permanent-camera calibration, follow the single current-version
[Permanent Camera Setup Runbook](laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md).
It gives the exact five-tab button sequence, completion gates, and calibration-job
handoff rules. The longer calibration documents are technical references, not
alternative operator sequences.

## What is implemented

Shared core and browser workflow:

- Browser-based local calibration and placement interface with no paid cloud service
- Logitech C920 capture through Linux V4L2/OpenCV
- Manual focus, exposure, white-balance, and gain locking when exposed by the camera driver
- Precision analysis capture that settles, discards buffered frames, measures a
  configurable fresh-frame burst, rejects temporal outliers, and reports mark
  jitter plus camera-control readback
- Multi-image checkerboard lens calibration
- Multi-point image-pixel to machine-XY homography with RANSAC and residual error reporting
- Perspective-corrected, top-down bed photograph
- Traditional computer-vision detection of a rectangular workpiece
- SVG loading and display with drag, size, and rotation controls
- In-house SVG vector parser for paths, lines, polylines, polygons, rectangles, circles, ellipses, groups, transforms, curves, and arcs
- Path flattening, nearest-path ordering, design bounds checks, zero-power framing, and vector G-code generation
- Linux serial communication without a mandatory pyserial dependency
- GRBL/Marlin identification probes and a read-only diagnostic command console (`M5` is the only actuator command accepted there)
- Temporary laser arming, automatic disarming, and software stop
- Unit tests and GitHub Actions configuration

Native desktop workflow:

- Pi-owned S1 Pro Z/CR Touch status, self-test, fixed-edge or material-surface
  reference homing, unknown-position confirmation, and a dynamic Z ceiling;
  autofocus remains disabled until its optical offset is physically calibrated
- Dimensional inputs accept either metric or imperial suffixes (`25.4 mm` or
  `1 in`; likewise `mm²`/`in²` and `mm/min`/`in/min`) while project geometry,
  calibration, safety bounds, and generated G-code remain in millimetres
- LightBurn-inspired compact icon chrome with a bright drafting bed, persistent
  runtime/safety status, always-present selection properties, one full-height
  right sidebar for Cuts, Camera, Objects, Shape, Templates, Trace, Machine, and
  Material Recipes, and a fixed 30-color operation palette. Objects rows expose
  their shared operation color as a clickable swatch that uses the normal
  undoable Cuts/Layers color path. The former lower Laser and raw-G-code docks
  are removed so the workspace extends downward
- Consolidated primary runtime controls keep Connect/Disconnect and the
  intentionally disabled Pause beside the always-available software STOP. The
  strip moves to its own toolbar row and wraps status above controls at compact
  widths, while global preparation/execution progress remains at the bottom
- The shared Generate action remains available from the Job toolbar and appears
  directly below Create cuts in Templates and Create objects in Trace
- PySide6 machine-coordinate workspace with camera overlay, pan, zoom, grid,
  rulers, snapping, toolpath preview, a 70%-opacity camera default, selectable
  corrected-overlay rates from 0.5 through 15 fps with a 2 fps default, and a
  draggable viewport-fixed overlay key. These are requested maximum cadences;
  slow correction or network delivery drops timer ticks instead of queuing
  overlapping frame work
- Multi-object `.e3laser` projects with operation layers, undo/redo, grouping,
  alignment, distribution, ordering, autosave, backup, and recovery
- Schema-3 native PATH/POLYGON geometry with versioned line and cubic Bézier
  segments, multiple open or closed subpaths, and explicit even-odd or nonzero
  fill rules. Curves remain native through project editing, transforms,
  duplication, grouping, save/reopen, autosave, recovery, and workspace
  rendering; planning flattens them deterministically in physical millimetres
  at one controlled boundary. Saving as schema 3 is forward-incompatible with
  older E3 builds that understand only schema 2; those builds reject the newer
  file instead of silently discarding native geometry
- Rectangle, rounded rectangle, ellipse, line, imported SVG/LightBurn paths,
  imported 2-D laser G-code (`.gc`, `.gcode`, `.nc`, `.tap`) reconstructed into
  output-disabled speed/power layers, vector outline text, and automatically
  bridged stencil-safe text objects, with
  numeric width/height and corner-radius editing for rectangles
- Shared pre-import review for SVG, raster images, LightBurn, and foreign G-code
  manifests, reached through one **File > Import** submenu. Blocked scans
  disable Import, valid or warning-only scans require explicit approval, and
  Cancel leaves project, history, selection, active layer, and authoring state
  intact. The review caps each rendered list at 200 with exact omitted counts,
  and its SHA-256 approval is rejected if source bytes change before strict
  import
- A dedicated **Trace image to vectors…** action for exactly one selected raster
  image. Its modal review first shows the decoded original, foreground mask, and
  a bounded **Quick preview** contour overlay, then replaces that display with
  the exact **Verified** native fit in a separate worker. Quick geometry is
  display-only; **Create vectors** remains disabled until the verified fit and
  all authoritative validation finish. High-contrast color presets and
  preview-only opacity repaint locally; the workflow supports
  automatic, manual, or usable-alpha detection; and creates one native
  multi-contour line/cubic PATH while preserving the image frame, position,
  rotation, and mirrors. Closed-contour fitting uses full-cycle seam
  canonicalization, physically persistent corner evidence, scale-aware straight-
  run classification on both cornered and smooth contours, shared tangents at
  non-corner joins, constrained cubic handles, Newton reparameterization, and
  conservative continuous fit validation. Material straight source runs persist
  as native lines after revalidation, while shallow arcs and rounded transitions
  remain cubic, so raster phase does not invent angular shoulders or hide
  between-sample lobes.
  Compatible adjacent spans merge only after the same fit and native-topology
  checks pass. Replace, Keep, optional source hiding, safe-layer creation, and
  vector creation are one undoable operation
- Persistent rectangle drawing directly on the bed with a live active-layer
  preview, snapping, immediate selection, and undo/redo-backed commits
- Direct single-object corner resizing and rotation on the canvas, including
  fixed-size handles, Shift-to-snap rotation, and undo/redo-backed commits
- Camera-traced, locked Stock boundaries plus a contextual layout toolbar for
  horizontal/vertical centering, rotation parallel to meaningful stock edges,
  and fit-to-stock scaling with preset or custom uncut margins
- Per-layer line, fill, vector-raster, and grayscale-image speed, power, pass
  count, ordering, exact scan interval/absolute machine angle, laser-off raster
  overscan, estimates, and image-aware zero-power framing
- Material-specific, acceleration-aware Vector and Raster Power Correction
  layered over GRBL `M4`, with bounded corner/image-edge ramps and exact Preview
  diagnostics
- Mandatory window-modal exact-job Preview with a distinct **START JOB** gate,
  time scrubber, animated playback up to 40×, cut/travel visibility, power
  shading, live move coordinates, timing and distance statistics, and PNG export
- Machine-aware SQLite material recipes with strict stable-profile
  compatibility, complete one-step operation-layer application, and preserved
  custom CRUD; recipes remain authoring aids and never become execution
  authority
- Camera focus/sharpness controls
- Authenticated raw Live Monitor for Pi-hosted cameras, preferring exact source
  JPEG passthrough from the single-owner native Linux V4L2 backend and visibly
  falling back to 1280×720/10 fps transcoded JPEG when native capture is
  unavailable, with distinct Capture, Network, Display, and source Age diagnostics
- Native Machine Setup for camera controls and preview, checkerboard lens
  calibration, a fresh keyed 5×5 base-map job with automatic orientation and
  transactional installation, manual/CSV fallback mapping, eight-point fine registration,
  reviewed translation/full-bed refinement with rollback, bounded 5×5 local
  correction, independent 4×4 holdout validation, and guided five-point
  holdout accuracy validation. Registration and validation can use precision
  multi-frame capture and can be repeated without another home cycle to
  separate camera variation from homing variation
- Profile-driven first-run and Machine Manager for multiple saved GRBL and
  Marlin machine/tool snapshots. Edits select the next launch without
  hot-swapping the immutable running identity or contacting hardware; new
  projects resolve curated operation defaults from that running identity and
  fall back to a visible 0%-power, output-disabled layer when unmatched
- A sixth read-only Coordinate Audit tab with running-machine/calibration
  binding checks, immutable capture-time GRBL pose evidence, machine/support
  overlays, and clicked-point tracing across camera, beam, honeycomb, and
  carriage coordinate frames. Only its explicit capture action commands
  hardware; refresh, report copy, and point inspection are observational. Bed
  Mapping displays the same saved-machine physical span read-only and blocks
  support detection until that dimension is configured in Machine Manager
- Versioned `.e3template` cutting-template library with manual selection,
  geometry-based automatic matching, role-labeled camera/cut overlays, rigid
  alignment review, and one-step undo when aligned cut objects are created
- Dedicated regular-grid template designer with a live preview, editable cut
  size/radius, rows, columns, edge-gap or center-pitch spacing, and direct
  template-library or project-object creation
- Guarded controller connection, camera-pose parking, diagnostics, job run, and
  software stop; successful powered jobs drain queued motion, Home/park, and
  only then release the motors, with visible completion phases and failure alert
- Validated G-code export; no operator capability requires the browser UI

Follow the [Permanent Camera Setup Runbook](laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md)
for the native calibration workflow. See [docs/MACHINE_SETUP.md](docs/MACHINE_SETUP.md)
for technical detail and browser parity, and [docs/JOB_PREVIEW.md](docs/JOB_PREVIEW.md)
for the generated-job review workflow.
See [G-code project import](docs/GCODE_IMPORT.md) for the supported foreign
program subset, layer reconstruction, and review boundary.
See [Power Correction](docs/POWER_CORRECTION.md) for its mapping, motion model,
overscan interaction, limitations, and tuning guidance.

To convert imported single-color artwork, select exactly one image in the
**Objects** panel and choose **Trace image to vectors…**. Adjust the physical
minimum-feature area, smoothing, and **Native fitting tolerance** while
reviewing the original, mask, and overlaid contours. **Quick preview** appears
without waiting for cubic fitting and topology checks; **Refining verified
vectors…** remains responsive, and only **Verified** enables **Create vectors**.
Changing settings invalidates both stages and coalesces work to the newest
values. Choose a magenta, cyan,
yellow, white, or black comparison overlay and adjust its opacity without
rerunning or changing the trace, then choose whether to replace or retain the
source. Keeping the source leaves it in place beneath the new, selected vector
and preserves the hide-source choice. Preserving all contours keeps counters and
closed child subpaths in the same native PATH. The fitted lines and cubic
Béziers are persisted directly; the review reports validated maximum/RMS fit
error plus hard-corner, recursive-split, and verified-merge counts. Material
curved spans also use RMS and signed-normal distribution evidence to avoid a
one-sided fit that is numerically bounded but visibly off-center.
Before exact fitting, eligible non-nested curve samples use the original
grayscale/alpha raster to recover a bounded subpixel threshold crossing along
their local contour normal. Ambiguous profiles are left unchanged, and detected
hard corners plus persistent straight runs remain locked to the extracted
contour. The 0.10 mm displayed tolerance and existing 0.08 mm internal fit
budget remain fixed; this source-edge step does not introduce a local span
tolerance or give Quick Preview geometry authoring authority.
Preview/topology samples are not a second authoritative geometry copy. When
planning is requested, E3 applies the complete
object transform and then adaptively flattens the native curve at the fixed
0.025 mm planning tolerance before the existing G0/G1 pipeline. A trace uses an
existing layer
only when it is a visible Line layer already at 0% power with output disabled;
otherwise E3 creates `<image name> trace` with those safe settings and selects
both that layer and the vector. Its ordinary layer swatch uses the same color
picker as other Line layers. This is an offline authoring action: it does not
generate G-code, enable output, connect, move, Home, arm, or start a job.

The desktop also contains an automated and behaviorally tested
object-tracing workflow for converting detected camera outlines into editable
project objects or one construction-only Stock boundary. Rounded-label output
previews the same fitted vector that will be created. A Stock boundary persists
in the project and drives center, edge-rotation, and fit-to-stock commands while
remaining excluded from all laser output and framing. Repeated rounded-label
grids can share one fitted cell geometry and
lattice so damaged observations and inferred gaps produce corresponding
identical row/column objects, while pixel contours remain available for
irregular objects. **Cutout / silhouette** instead freezes one corrected frame
and lets the operator click inside only the physical objects, holes, or stencils
they intend to trace. Disconnected high-contrast lettering is ignored unless it
is clicked. A blue raw contour appears first; a worker then applies the shared
physical contour-to-native-path fitter, preserves holes and islands with
even-odd topology, and replaces it with verified native lines/cubics before
Create is enabled. Grid filters, normalization, and missing-cell inference
remain unchanged and are disabled only while this seeded mode is active. The
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

## Deliberately disabled by default

The packaged default is a setup template, not a runnable machine. Its controller
port is the explicit `SELECT_CONTROLLER_PORT` placeholder; the former implicit
`/dev/ttyUSB0` default is not treated as a configured machine. First-run must
save a real machine before `CoreRuntime` is created. In every configured profile:

- Every normal browser and desktop launch is hardware-capable, but startup does
  not automatically connect to the configured controller.
- Motion remains blocked until `machine.allow_motion` is explicitly changed.
- Positive laser commands receive a one-use, time-limited authorization for the
  exact prepared program when Preview's **START JOB** enters the guarded run path.
- A zero-power program contains no `M3`/`M4` laser-enable command; positive
  output is never used merely to outline a job.
- Laser-head mounting offsets default to zero. Real-hardware profiles can set
  `laser.spot_offset_x_mm` and `laser.spot_offset_y_mm`; generated jobs show the
  applied values and validate both desired spot and shifted controller bounds.
- The shipped server configuration binds to `127.0.0.1`. An explicitly
  configured IPv4 wildcard bind still rejects non-loopback clients and requires
  the guarded Host, Origin, JSON, and per-process token checks.

These are software guardrails, not safety-rated controls.

## Fastest way to try it on Linux

On Linux Mint, Ubuntu, or Debian:

```bash
unzip laser-camera-aligner-0.2.0.dev0.zip
cd laser-camera-aligner
./install.sh
./run.sh
```

The browser opens to `http://127.0.0.1:8080`. A configured but unreachable
controller or camera is shown as offline; E3 never substitutes fake hardware.

For the native desktop after the base installation:

```bash
./install-desktop.sh
./run-desktop.sh
```

The desktop includes the complete native Machine Setup workflow. The browser
uses the same calibration files and remains available as a legacy alternative.

## Platform and CI status

The browser and native desktop applications run on Windows as normal
hardware-capable processes. Windows supports configured `e3bridge://` and
`e3camera://` endpoints; direct local POSIX serial and V4L2 discovery/control
remain Linux-specific and are imported lazily. Missing real hardware remains
offline rather than being replaced with simulation or synthetic camera state.
Autosaves and material recipes use a writable OS-native per-user data root.

Windows packaging and automatic-update assets are implemented and
automated-test covered. The installed frozen PyInstaller E3 to visible Inno
Setup handoff still requires package-level verification in a disposable
interactive Windows environment.

GitHub Actions has two validation tiers. Pushes to `fix/**`, `feature/**`,
`agent/**`, `cleanup/**`, and `architecture/**` run Fast Development CI on
Windows Python 3.12: repository Ruff, desktop dependency and bytecode
validation, and the complete desktop-enabled pytest suite with four bounded
xdist workers. Compatibility CI runs for pushes to `main`, pull requests
targeting `main`, and manual dispatch. It runs serial core/non-desktop tests on
Windows Python 3.10, serial desktop-enabled tests on Windows Python 3.12, and
repository Ruff as a separate Windows Python 3.12 job. Linux/Pi components
retain focused verification when changed; there is no standing Ubuntu
compatibility matrix. Each major CI phase records its duration in the job
summary.

From an existing desktop-enabled virtual environment, launch the native UI with
a completed real-machine configuration:

```powershell
.\.venv\Scripts\python.exe -m laser_aligner.desktop.main `
  --config path\to\configured-machine.json
```

Launch the browser with:

```powershell
.\.venv\Scripts\python.exe -m laser_aligner `
  --config path\to\configured-machine.json
```

The browser and desktop both resolve the active saved machine through
`CoreRuntime`; raw JSON machine/laser values cannot override an existing
registry selection. Legacy simulator or unconfigured-placeholder state fails
closed with real-machine setup instructions. Desktop legacy recovery runs before
credentials or runtime services and requires an explicit physical-machine
selection; cancel performs no migration and exits. A normal packaged legacy
fallback is repaired into upgrade-preserved user state, while an explicit
`--config` is repaired in place. Normal startup is
hardware-capable, while the existing motion, arming, coordinate, bounds, and
exact-program gates remain authoritative.

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
4. Keep serial motion disabled during camera and calibration work.
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

Only after the port, protocol, coordinate system, reachable laser work area,
power scale, and framing behavior are verified should `machine.allow_motion` be
changed to `true` and the normal application restarted with:

```bash
./run.sh
```

See [docs/HARDWARE_BRINGUP.md](docs/HARDWARE_BRINGUP.md).

## Repository layout

```text
laser_aligner/
  camera/        local C920/V4L2 and remote camera capture
  calibration/   lens model, bed mapping, printable target generation
  core/          UI-neutral runtime lifecycle
  desktop/       native PySide6 project workspace
  geometry/      SVG parsing, curves, transforms, units
  gcode/         placement, validation, generation, preview parsing
  machine/       POSIX/network serial and controller safety service
  z_axis/        Pi-owned S1 Pro Z/CR Touch protocol and homing state machine
  materials/     SQLite material-recipe library and compatibility model
  project/       project model, history, persistence, alignment, toolpaths
  templates/     reusable templates, grid authoring, library, and rigid placement
  vision/        workpiece, fiducial, object tracing, and template alignment
  web/           dependency-free browser interface
config/          setup template and hardware examples
docs/            setup, calibration, safety, and architecture notes
targets/         exact-size SVG calibration targets
tools/           camera/controller probes and release tooling
tests/           unit and integration tests
```

## Development commands

Linux:

```bash
# Run all tests for broad changes or merge/release preparation
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

- Direct local serial/camera hardware remains Linux-only; Windows uses the
  authenticated Raspberry Pi controller/camera bridge.
- Line vectors, generated vector text, closed-vector fills, binary vector
  rasters, and imported grayscale images with deterministic ordered dithering
  are supported. Existing generated text cannot yet be reopened for editable
  regeneration; selectable dither algorithms and calibrated grayscale power
  curves are not implemented.
- Raster vectorization is intentionally a single-foreground tracer for logos,
  line art, silhouettes, stencils, and similar artwork, not a full-color or
  multi-layer tracer. Eligible independent curve samples are localized against
  the original intensity/alpha transition before fitting; unsupported,
  ambiguous, nested, hard-corner, and classified-straight samples retain their
  threshold-contour positions. Source resolution, antialiasing, threshold, and
  smoothing therefore still limit real accuracy. Projects retain fitted native line and
  cubic segments; bounded flattened points are transient preview, topology, and
  planning artifacts rather than editable node data. Persistent straight-source
  evidence is scale-aware and rotation-independent; short flat raster plateaus
  on curved regions are not sufficient to force a line. Exact fitted extrema must
  remain inside the reviewed source frame, and accepted compound contours must
  retain provable separation through their flattening-error envelopes. Highly
  pixel-constrained `A` and `S` glyphs can still vary at narrow counters and
  curved shoulders; using a cleaner or higher-resolution source for those cases
  is an accepted first-release quality limitation.
- SVG text and embedded images are not converted. Native desktop import stops
  before creating an object when either is present; convert them to paths in
  the design program.
- CSS stylesheets, clipping paths, masks, markers, dashed strokes, and
  geometry-changing CSS are not supported and are explicitly rejected rather
  than imported with a mismatched cut path.
- The camera mapping assumes the material top surface is on the calibration plane. Height/parallax compensation is planned but not yet implemented.
- Cutting-template identification and alignment have automated coverage but
  have not been verified with real corrected label-sheet images. Automated
  tests cannot validate lens/bed calibration, parallax, camera pose, material height,
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
