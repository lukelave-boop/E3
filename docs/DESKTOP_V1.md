# E3 native desktop v1 foundation

The `desktop-v1` foundation adds a native PySide6 workspace without removing the
existing browser application. Both interfaces use the same calibrated camera,
geometry, G-code, safety and controller services.

> This is a historical milestone record, not the current roadmap or platform
> boundary. The desktop foundation later gained the tracing workflow and the
> generic machine/import/planning architecture recorded in
> [../CURRENT_STATE.md](../CURRENT_STATE.md); current future work is in
> [../ROADMAP.md](../ROADMAP.md).

## Implemented in this milestone

### Native workspace

- Native `QMainWindow` application shell with persistent dock and window layout
- Build-first native window title showing the application name, release version,
  short source revision, current project, and unsaved-change marker
- A full-height right-side inspector for Cuts/Layers, Camera, Objects, Shape
  Properties, Templates, Trace, Machine, and Material Recipes. The obsolete
  lower raw-G-code and Laser/job docks are removed so the workspace uses the
  freed height; preparation and execution progress remain globally visible at
  the bottom
- Physical coordinate-aware workspace with adaptive grid, rulers, origin, pan,
  zoom and selectable snap spacing. Machine projects use machine coordinates;
  current honeycomb-local projects use the detected support's rigid X0/Y0 frame.
- Corrected camera image behind the workspace with adjustable opacity and
  nominal 0.5, 1, 2, 4, 5, 10, and 15 fps refresh choices; 2 fps is the
  current default
- Selection, drag movement, direct corner resize and rotation handles, numeric
  size/position/rotation, mirror, duplicate, delete, group/ungroup, alignment,
  distribution and z-order controls
- Persistent press-drag-release rectangle drawing plus basic rounded rectangle,
  ellipse and line creation, and vector text creation in ordinary outline or
  automatically bridged stencil-safe mode
- Physical-size SVG import for absolute `mm`, `cm`, `in`, and `px` root
  dimensions plus viewBox-only files (`96 CSS px = 1 in`), including transformed
  groups and `preserveAspectRatio` mapping. Imports remain centered at the
  requested project placement. CSS stylesheets, clipping, masks, and any lossy
  parser warning stop the import before a project object is created. A bounded
  manifest scan and explicit review precede the authoritative strict parse.
- Native `.lbrn2` and legacy `.lbrn` vector-project import for rectangles,
  ellipses, transformed groups, line/Bezier paths, shared path data, and text
  with vector backup geometry. Referenced LightBurn layer modes and usable
  speed/power/pass/raster settings become ordinary E3 layers, always with
  output disabled until reviewed. Unsupported content stops rather than being
  silently discarded. See [LIGHTBURN_IMPORT.md](LIGHTBURN_IMPORT.md).
- Shared window-modal pre-import review for SVG, raster images, LightBurn, and
  bounded foreign G-code. It presents the scan manifest's source,
  layer/operation, coordinate,
  warning, approximation, unsupported-feature, and error facts before strict
  import. Blockers disable **Import**; warning-only and valid manifests require
  explicit approval; Cancel leaves project and authoring state unchanged. Each
  rendered layer/text list is capped at 200 with exact omitted counts, while the
  strict loader verifies the reviewed raw-source SHA-256 before parsing.
- Mandatory window-modal exact-job Preview distinguishing rapid, powered and
  unpowered moves, with a distinct **START JOB** gate, time scrubbing, animated
  playback, move coordinates, power, timing/distance statistics, display
  controls, and PNG export

### Current control-surface layout

The desktop shell now follows a LightBurn-inspired information hierarchy
without copying LightBurn branding or changing the E3 machine-control model:

- Boolean options use one consistent compact switch: gray with the control on
  the left for off, and green with the control on the right for on. Disabled
  switches remain visibly unavailable rather than appearing active.

1. Traditional File, Edit, Tools, Arrange, Laser Tools, Window, and Help menus
   retain every existing command. File groups SVG, G-code, LightBurn-project,
   and raster-image actions under one **Import** submenu; the child labels omit
   the repeated word "Import" while retaining the existing shortcuts and
   implementations.
2. Original icon-only File/Edit/Arrange/Job controls share one compact command
   row above the workspace.
3. A non-hideable runtime strip always reports hardware authority, controller
   connection, and motion permission. Connect/Reconnect, Disconnect, the
   intentionally disabled Pause, and software STOP share its primary-control
   region. STOP remains enabled during ordinary background work. The strip
   stays inline when space permits, moves to its own toolbar row on narrower
   windows, and wraps status above controls when allocated less than 1100
   logical pixels so STOP cannot disappear into toolbar overflow.
4. An always-present two-row property bar exposes X, Y, width, height,
   percentage scaling, aspect lock, rotation, millimetre/inch display, and
   explicit `mm` or `in` entry in dimensional fields,
   mirroring, and contextual rectangle radius. Controls disable when the
   selection cannot be edited instead of making the toolbar collapse.
5. A contextual **Stock layout** toolbar appears when a non-stock object is
   selected and a traced Stock boundary exists. Its immediate controls center
   horizontally or vertically, snap rotation to a meaningful stock edge, and
   fit the selection inside the stock with a retained margin; a compact dropdown
   holds the expanded command list.
6. A compact left rail holds selection, creation, trace/template, fit, zoom,
   and snap tools.
7. The central workspace uses the active project's physical coordinate space,
   a near-white adaptive grid, thin light rulers, no permanent scroll-bar
   chrome, auto-fit until the user zooms or pans, and a 70% corrected-camera
   overlay by default. A dynamic
   key identifies transient Trace, camera-detection, template-cut, and toolpath
   lines by both role and line style. The key stays in the upper-left viewport
   corner during canvas interaction unless the operator drags the key itself.
8. Cuts/Layers, Camera, Objects, Shape Properties, Templates, Trace, Machine,
   and Material Recipes share one full-height tabbed inspector on the right.
9. Templates places Generate immediately below Create cuts; Trace places the
   same shared action immediately below its dynamic Create objects control.
   The Job toolbar retains its Generate and Preview actions and shortcuts.
10. A fixed 30-color bottom palette assigns selected objects to existing
    operations; clicking an unused color creates a matching operation. The
    status bar keeps preparation/execution progress globally visible alongside
    direct-edit and live workspace feedback. At compact widths it reserves a
    readable progress label first, then shows runtime, zoom, and editing details
    only while each lower-priority readout fits without compression. Temporary
    status messages receive their own measured space, suppress lower-priority
    readouts as needed, and restore them when the message clears.

There is no persistent main-window raw-G-code pane or lower Laser/job panel.
The optional Console remains available from the Window menu and hidden by
default. Generating or framing a job still opens the dedicated graphical
Preview. **Window > Reset workspace layout** restores this maintained
arrangement. Window geometry, dock state, and the active inspector tab use
versioned Qt settings keys for layout `v7`. Compatible older geometry and tab
choices migrate, while opaque `v6` dock topology is deliberately ignored so it
cannot restore the removed bottom row.

The default unified-sidebar layout is offscreen-tested at `1080x780` and
`900x680` logical pixels with 13 pt application text. The requested window size
is retained, the full-height canvas and visible right sidebar remain disjoint,
global progress stays inside the bottom status bar, and every inspector fits
its horizontal viewport; compact panels use vertical scrolling where needed.
Operation speed, power, pass, interval, angle, and overscan editors update the
project on edit completion rather than creating an undo entry and rebuilding
the inspector for every typed digit.

Trace can now create a locked, teal dashed **Stock boundary (layout only)**
instead of cut geometry. The construction outline survives project save/load
but is excluded from line, fill, raster, generated-job, and zero-power framing
entry points. Selecting ordinary artwork reveals the contextual Stock layout
toolbar for horizontal/vertical centering, rigid rotation parallel to a nearest
or named outward-facing edge, and conservative fit-to-stock scaling with preset
or custom uncut margins. The original irregular polygon controls containment;
its simplified edges are used only for rotation choices.

The Text action now opens a vector-text dialog rather than creating a display-
only TEXT object. **Outline cut** emits ordinary font contours. **Stencil-safe
cut** detects enclosed counters and opens scaled bridges through the glyph so
centers such as O, A, R, B, and 8 remain connected to the parent sheet. Source
text, font, mode, size, bridge width, and bridge count remain in object metadata;
existing vector text is not yet reopened for editable regeneration.

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
Each Objects row shows the same operation color as a 24 px button directly
beside the layer name. That button opens the shared operation-color chooser;
the resulting layer edit is undoable and refreshes every object assigned to the
layer without creating a separate per-object display color.
New projects start with the E3 10 W material profiles in palette slots 00–12:
paper, plywood, MDF, opaque black acrylic, leather, and cardboard cuts followed
by the corresponding raster profiles supplied by the operator. These are
editable starting points, not physically verified material guarantees; saved
projects retain their own layers unchanged.
Fill and raster layers generate scanline toolpaths. Their quick editor exposes
line interval and scan angle; raster additionally exposes laser-off overscan.
Imported PNG, JPEG, and BMP images are alpha-composited onto white,
area-prefiltered when the physical raster pitch minifies the source, and use
deterministic 8x8 ordered dithering at the operation's maximum power. TIFF is
rejected consistently because its Qt decode plugin is not portable.

Exactly one selected image also exposes **Trace image to vectors…** in the
Objects panel. The window-modal review compares the bounded, SHA-verified source,
foreground mask, and vector overlay. A display-only **Quick preview** appears
before expensive fitting and remains visible while **Refining verified
vectors…** runs in a separate worker. It is seamlessly replaced by the exact
**Verified** native result; **Create vectors** is disabled until that exact
result exists. Settings changes coalesce independently across the two bounded
stages, and stale results cannot overwrite newer options. Magenta, cyan, yellow,
white, and black presets plus 0–100% opacity are preview-only and do not rerun
fitting or change output authority. The workflow creates one ordinary compound native PATH
while either replacing or retaining the image. Contours are fitted at 4×
internal resolution with physical cleanup/smoothing/error controls, full-cycle
seam canonicalization, persistent physical corner classification, generic
straight-run classification across both cornered and smooth contours, and shared
tangents only at non-corner joins. Material, rotation-independent straight
source runs persist as lines after continuous revalidation; short curve
plateaus, rounded transitions, and shallow arcs remain cubic. Curved spans use
constrained cubic handle solving, Newton reparameterization, conservative
continuous error bounds, and verified adjacent merging. The displayed fitting
tolerance is a ceiling: each anchor span may automatically tighten it from a
rotation-independent arc/chord scale, stopping at the source-pixel/4×-workspace
resolution floor and keeping the selected budget stable through recursive
splits. Hidden between-sample
lobes and current native-arc topology
ambiguities are rejected. The dialog reports validated maximum/RMS fit error,
hard corners, recursive splits, and verified merges. Nested holes remain
separate closed child subpaths; preview/topology samples are not stored as a
second geometry copy. The copied image transform preserves its full displayed
frame, rotation, and mirrors. A retained image stays beneath the vector, and the
new vector and its ordinary editable Line layer are selected. The entire
layer/object/source choice is one undoable command, and any automatically created
layer is visible Line mode at 0% power with output disabled. This offline
authoring action does not call camera, planning, G-code, controller, or hardware
paths.

### Feature-preservation map

| Existing capability | Current surface | Preservation boundary |
|---|---|---|
| Project file, import, undo/redo, and object commands | Menus, global toolbars, left rail, and context bar | Existing `ProjectDocument` commands and history stack remain authoritative |
| Machine authority, arming, execution, and stop | Persistent primary runtime strip, Machine inspector, and global bottom progress | `DesktopController` and `MachineService` gates are unchanged; the strip and progress widget are presentation only |
| Camera overlay, focus, and corrected test sources | Camera inspector and workspace | Existing camera/controller lifecycle is unchanged |
| Object tracing and stock layout | Trace tool, inspector, contextual Stock layout toolbar, and workspace | Existing frozen-frame review remains authoritative; Stock boundaries are role-tagged construction objects that persist but are filtered from every output path |
| Cutting templates and alignment | Templates tool, inspector, and grid designer | Existing rigid placement, review, and one-command apply path is unchanged |
| Operation layers and materials | Operations/Layers table, bottom palette, and Material Recipes inspector | Existing layer schema, ordering, machine-aware recipes, and explicit unsupported-mode checks are retained; recipes add no execution authority |
| Toolpath generation, framing, preview, and run | Job toolbar, shared Generate controls in Templates/Trace, global bottom progress, dedicated graphical Preview, and explicit G-code export | Preview parses the exact finalized stream; existing revision invalidation, validation, arming, and execution gates are unchanged |
| Browser calibration and placement application | Native Machine Setup plus richer desktop project workflow | Browser remains an optional legacy single-SVG surface; no operator capability requires it |

### Project and operations model

- Framework-independent undo/redo command stack
- Undoable object, transform, layer, grouping, alignment, stock-relative
  layout, and ordering edits
- Transparent `.e3laser` JSON project format
- Atomic project saves, `.bak` files, periodic autosaves and recovery prompts
- Colored operation layers with line/fill/raster mode, speed, power, passes,
  visibility, output state and priority
- Machine-aware SQLite material-recipe library stored outside the source tree;
  legacy/custom rows remain universal, scoped rows use exact stable machine and
  tool-head profile IDs, and incompatible rows cannot be applied
- Per-operation and material-recipe Vector/Raster Power Correction controls;
  zero retains ordinary GRBL `M4`, while nonzero settings add bounded localized
  commanded-power bias near modeled motion changes
- Compatible material recipes apply complete authoring settings to the active
  operation layer as one undoable action without scaling or enabling output;
  hand-edited layer values remain equally valid inputs to preflight and planning

### Toolpaths and machine controls

- Multi-layer vector G-code generation using the existing safety/bounds core
- Honeycomb-local vector, fill, raster, frame, and exact-preview placement
  through the current rigid support frame; emitted G-code remains absolute
  machine millimetres
- Per-layer speed, power and pass count
- Nearest-path travel ordering and time/distance estimates, with a recorded
  source-order fallback above 512 vector paths
- Zero-power framing generation
- Immutable preview plans parsed from the exact finalized G-code stream, with
  layer/pass/source metadata stored only in controller-ignored comments
- Prepared-job status reports maximum planned power independently from live
  controller execution progress
- Closed-vector fill, binary vector raster, and area-prefiltered ordered-dither
  grayscale raster output with bounds-checked laser-off overscan
- Legacy display-only TEXT objects, open fill geometry, missing/changed raster
  assets,
  unsupported raster metadata, and empty dither results are rejected explicitly
  rather than silently omitted
- Zero-effective-power vector, fill, and raster layers never emit `M3` or `M4`;
  their exact motion is counted as travel rather than cut distance
- Existing camera and controller status in native dock panels
- A separate raw Live Monitor window for authenticated `e3camera://` sources,
  with Start/Stop, 5/10/15 fps selection, distinct Pi Capture / socket Network /
  Qt Display FPS and source Age diagnostics, resolution, connection state,
  direct-MJPEG/transcoded source diagnostics, and latest-frame display
  independent of calibration and controller state
- Guarded controller connect, diagnostics, software stop and job run; hardware
  Start automatically performs one laser-off Home before arming and execution, while a
  successful powered job drains queued motion, homes, and parks again before
  motor release and finish. Drain/home/park/release phases remain visibly
  running, and an asynchronous completion failure raises a one-time error
- Powered output receives a one-use authorization for the exact prepared
  program when Preview's **START JOB** delegates to the guarded run path; the
  desktop does not show a confirmation or typed-phrase dialog

After software STOP or another uncertain established-session failure, the
primary runtime strip changes its normal Connect control to an explicit
**Reconnect** action. It performs one operator-requested disconnect/connect
sequence and then leaves the machine HOME REQUIRED. It does not automatically
reconnect, Home, move, resume, or arm. Desktop modal message boxes use a shared
queued first-show polish and repaint so Linux compositors receive complete
dark-theme contents on the initial exposure without blocking the GUI event loop.

Native Machine Setup exposes raw camera preview and controls,
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
toolpath, zero-power framing, or Start Here planning against the clone or
immutable plan.
Distinct worker/render owners plus source-document and revision checks reject
late success and failure results without mutating newer preparation. Workspace
paths, dedicated Preview paths, and backward timeline rebuilds are constructed
in short GUI-thread slices with visible global progress; Qt objects never cross
into workers. Conflicting job actions remain unavailable until every required
exact view finishes, while software STOP stays live. Preview is window-modal,
and the main-window Preview action can only reopen it rather than execute
directly. **START JOB** dismisses Preview before synchronously entering the
existing guarded run path. Closing an unfinished Preview fails closed, and
application close retains workers through completion before runtime teardown.
Generated programs remain in memory until the explicit export action writes
one; there is no persistent raw-G-code pane.

Start Here planning snapshots the configured controller photography pose and
records it in the replacement program. Its exact Preview includes the
laser-off approach from that Home/park pose to the reviewed boundary, with the
configured physical laser-spot offset applied, before normal execution
preflight and arming. Preparing Start Here never executes; its replacement must
complete and pass through another exact Preview.

## Installation

From the repository root:

```bash
./install.sh
./install-desktop.sh
```

The desktop installer adds PySide6 to the existing virtual environment and
creates one **E3 Positioning System** application entry. The normal desktop is
hardware-capable, but startup itself does not open the controller. Rerun the
installer after a source upgrade so it removes the obsolete application entry
previously labeled **E3 Positioning System (Safe)**; that old label no longer
describes a supported launch mode.

Direct launch commands:

```bash
./run-desktop.sh
```

## Project format

`.e3laser` files are readable JSON. The current schema stores:

- physical work area;
- an explicit machine or honeycomb-local coordinate-space kind;
- operation layers;
- vector objects and their z-order;
- object transforms, visibility, lock and group membership;
- role metadata for construction-only Stock boundaries and source metadata for
  generated vector text;
- embedded original SVG text when available;
- project metadata and timestamps.

Schema 3 stores PATH and POLYGON geometry as one versioned native representation
containing only line and cubic segments, one or more open or closed subpaths,
and an explicit even-odd or nonzero fill rule. Legacy schema-1 and schema-2
`geometry.polylines` migrate in memory to native line-only subpaths. Opening an
old project does not rewrite it; a later explicit save writes canonical schema
3 while preserving the existing atomic-save and backup behavior. Schema-1 files
still migrate explicitly as machine-coordinate projects and are never silently
reinterpreted as honeycomb-local. The format deliberately does not depend on
LightBurn project files. Schema 3 is forward-incompatible with older E3 builds
that support only schema 2: those builds reject the newer file rather than
silently discarding native path fields.

Canonical schema-3 PATH/POLYGON `geometry` is:

```json
{
  "path_version": 1,
  "fill_rule": "evenodd",
  "subpaths": [
    {
      "start": [-0.5, 0.0],
      "closed": true,
      "segments": [
        {"type": "line", "to": [0.0, -0.5]},
        {
          "type": "cubic",
          "control_1": [0.2, -0.5],
          "control_2": [0.5, -0.2],
          "to": [0.5, 0.0]
        }
      ]
    }
  ]
}
```

Coordinates are finite JSON numbers in normalized object-local space. A
current object never stores `polylines` beside this representation.

The workspace builds `QPainterPath` line and cubic elements directly, so zooming
does not reveal a stored point staircase. Whole-object move, resize, rotation,
mirrors, duplicate, group/ungroup, save/reopen, recovery, undo, and redo retain
the native controls. Node/handle editing is not part of this increment. Exact
planning applies the complete object transform before deterministic adaptive
flattening at 0.025 mm and then uses the existing guarded G0/G1 pipeline; it
does not send controller spline or arc commands.

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

- serial access still requires process hardware authority, which the one normal
  desktop launcher explicitly grants;
- motion must still be enabled in the local configuration;
- powered G-code still requires exact-program, one-use authorization, created
  internally when Preview's **START JOB** submits it through the guarded path;
- machine-coordinate programs remain validated against the configured guarded
  machine rectangle. Honeycomb-local geometry is first validated against the
  complete support, then its placed beam and spot-corrected controller paths are
  validated against the exact fixed configured polygon bound to that prepared
  job;
- the desktop passes that stored polygon to `MachineService` unchanged, and a
  configuration change invalidates the immutable preflight before arming or
  Start;
- every generated vector path is bracketed by `M5`;
- rapid travel occurs only while the laser is off;
- the software stop is not a replacement for a physical emergency stop.

The Machine panel provides Home / park and separately tested laser-off
incremental jogging; connection controls and software STOP remain in the
primary runtime strip. Home / park must first establish the current XY position;
every jog press is converted to an absolute move, begins with `M5`, uses an
explicit bounded travel feed on `G1` motion, and intentionally does not apply
the configured work-area rectangle so the operator can measure the actual
travel limits. STOP, disconnect, jobs, and motor release invalidate the jog
position. Pause remains visibly adjacent to STOP but disabled until the Falcon
controller's realtime hold/resume behavior has been physically verified.

## Validation performed for this milestone

- Core project, history, save/recovery, material and toolpath tests
- Desktop source parsing without requiring PySide6 in headless CI
- Stock-boundary persistence/output exclusion, stock-relative center/rotation/
  fit behavior, and vector-text source wiring tests
- Python bytecode compilation
- Shell-script syntax checks
- Editable package installation without desktop dependencies
- Friendly startup failure when PySide6 has not yet been installed

At this milestone, the Qt windows still needed exercise on the Linux Mint
workstation because PySide6 and a graphical display were not present in the
build environment.

During the later simulator era, the complete desktop application started, ran
its event loop, and shut down under Qt's offscreen backend on Windows with
injected test doubles and a simulated controller. That remains historical smoke
evidence rather than an interactive GUI test or current product capability.
Direct local POSIX serial and V4L2 remain Linux-specific; the authenticated
Windows-to-Pi hardware path is described in
[NETWORK_MACHINE.md](NETWORK_MACHINE.md).

## Historical follow-on list

This list records what was next at the time of the desktop-v1 foundation.
Several items—including Windows CI, guarded jog, packaging/update work, and
parts of the authoring backlog—have since been implemented. Use the current
[roadmap](../ROADMAP.md) rather than this list for planning.

1. Windows launch scripts and CI. OS-native user-data paths and legacy-data
   migration are implemented.
2. Broader behavioral Qt tests for the remaining project-editing and object-
   tracing workflows.
3. Multi-selection transform boxes, proportional canvas resizing, node
   editing, and smart snap guides. Single-object resize/rotation and transient
   cutting-template drag/rotation are already implemented.
4. Guarded jog API and tested controller-specific realtime pause/resume.
5. DXF import and editable regeneration of existing vector text.
6. Managed or embedded portable raster assets, selectable dithers, and
   calibrated grayscale power curves.
7. Job history and calibration profiles by material height.
8. Stable release packaging, update checks and rollback.
