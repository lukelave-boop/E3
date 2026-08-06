# Architecture

The repository has two user interfaces over a shared camera, calibration,
geometry, vision, G-code, and machine core.

## Runtime map

```text
Browser entry point                     Desktop entry point
laser_aligner.__main__                  laser_aligner.desktop.main
          |                                       |
          v                                       v
      AppContext <--------------------------- CoreRuntime
          |                                       |
          v                                       v
  AppHTTPServer + web UI                  DesktopController
          |                                       |
          v                                       v
 single-SVG placement                 E3MainWindow + WorkspaceView
          |                                       |
          v                                       v
  gcode.generator                    ProjectDocument + CommandStack
                                                  |
                                                  v
                                         project.toolpath
          |                                       |
          +-------------------+-------------------+
                              v
                         MachineService
                              |
                   +----------+----------+
                   v                     v
          SimulatedTransport       platform serial transport
```

The browser uses `AppContext` directly. The desktop uses `CoreRuntime` as a
UI-neutral lifecycle wrapper around the same context and performs blocking
camera/controller work through `DesktopController` worker tasks.

## Module ownership

| Module | Responsibility |
|---|---|
| `config.py` | JSON defaults, merging, validation, and resolved paths |
| `storage.py` | Atomic JSON persistence used by calibration |
| `camera/` | OpenCV/V4L2 capture, camera controls, and synthetic scenes |
| `calibration/` | Lens model, checkerboard solving, bed homography, rectification, targets |
| `vision/` | Workpiece, fiducial, crosshair-grid, and camera-object detection |
| `geometry/` | SVG parsing, curve flattening, transforms, and physical units |
| `gcode/` | Legacy single-SVG generation and G-code parsing/preview utilities |
| `project/` | Desktop project schema, commands, save/recovery, alignment, and multi-layer toolpaths |
| `templates/` | Versioned cut-template schema, atomic library storage, project normalization, and rigid instantiation |
| `materials/` | SQLite material-preset library |
| `machine/` | Safety policy, simulator, protocol probing, and serial transports |
| `server.py` + `web/` | Local HTTP API and browser UI |
| `core/` | Shared runtime lifecycle for non-HTTP consumers |
| `desktop/` | PySide6 window, workspace, panels, tasks, and presentation logic |

Qt and HTTP types must not leak into the project, calibration, geometry,
vision, G-code, or machine models.

## Camera and calibration flow

```text
CameraService or SyntheticCameraService
  -> raw OpenCV BGR frame
  -> LensModel.undistort() when a lens model exists
  -> BedMapper.rectify()
  -> top-down image at configured pixels/mm
  -> workspace background and optional vision detectors
```

`LensCalibrator` stores captured checkerboard images, a detection cache, and the
solved camera model. `BedMapper` stores image/machine point pairs plus forward
and inverse homographies. Simulation startup creates a synthetic perspective
scene and a known mapping automatically.

The browser owns the complete lens and bed-calibration UI. The native desktop
currently consumes those calibration files and displays the corrected image;
native calibration wizards are not implemented.

## Vision flows

- Rectangular workpiece detection runs on the rectified image and reports
  machine-coordinate placement hints.
- ArUco and crosshair-grid detection support bed mapping.
- The object-tracing pipeline segments color or contrast, optionally fits a
  regular grid and infers missing cells, then emits rounded or contour geometry
  in machine millimetres.
- Inferred trace cells are deliberately not selected by default.
- Template alignment compares unordered machine-coordinate detections with
  normalized template features, ranks rigid rotation/translation candidates,
  and reports coverage, residual, ambiguity, and scale diagnostics.
- Scale diagnostics are warnings only. The matcher never scales cut geometry.

All vision accuracy depends on current lens calibration, bed mapping, camera
pose, material height, focus, lighting, and resolution.

## Authoring and toolpath flows

### Browser pipeline

```text
SVG text
  -> geometry.svg.parse_svg()
  -> DesignPlacement
  -> placed polylines
  -> work-area validation
  -> gcode.generator.generate_vector_gcode()
```

This is a single-design workflow. Placement state lives in browser memory and
generated files are written under the configured application data directory.

### Desktop pipeline

```text
native shapes / imported SVG / traced outlines
  -> SceneObject instances
  -> ProjectDocument operation layers
  -> undoable CommandStack changes
  -> project.toolpath.generate_project_gcode()
  -> multi-layer vector G-code
```

The desktop supports multiple objects and line-operation layers. Fill, raster,
text, and image output are represented in the model but rejected explicitly by
toolpath generation until their engines exist. Unsupported content must never
be silently dropped.

Reusable label-sheet geometry follows a separate portable flow:

```text
visible output SceneObjects
  -> clone and normalize around combined bounds center
  -> versioned .e3template in the template library
  -> resilient catalog of valid unique templates plus file diagnostics
  -> manual selection or geometric ranking against one frozen camera frame
  -> reviewed rigid center/rotation
  -> new SceneObject IDs on the active layer through one AddObjectsCommand
```

Templates preserve cut geometry and relative spacing but do not preserve a
project's operation-layer settings. The target project's active layer owns the
created objects and its speed, power, and pass settings. The optional
`marker_id` field is reserved metadata; no marker detector consumes it yet.
Independent closed outer contours inside one imported SVG path become separate
matching features; contained hole contours do not. The UI excludes malformed
or duplicate-ID library entries without hiding unrelated valid templates.
See [CUT_TEMPLATES.md](CUT_TEMPLATES.md) for the format and verification
boundary.

Both pipelines generate conservative G-code that is revalidated by
`MachineService` before execution.

## Coordinate conventions

- Machine X increases to the right.
- Machine Y increases toward the top/back in the rectified workspace.
- Browser and image pixels increase downward, so UI/image Y conversion is
  inverted.
- SVG coordinates normally increase downward. Import/placement flips SVG Y so
  artwork appears visually upright in machine coordinates.
- Positive project rotation is counter-clockwise in machine coordinates.
- Template feature coordinates are local to the center of the combined cut
  bounds. Placement rotates those local coordinates and then translates them
  into machine coordinates; it never applies scale.
- CSS display rotation uses the opposite sign because browser Y is inverted.

The project's work area is stored in `.e3laser`; the execution boundary also
uses the configured machine work area. These values must not be allowed to
silently diverge when a project is run.

## Machine safety boundary

`MachineService` is the only normal path to the controller. It:

- blocks serial access unless the process is hardware-enabled;
- blocks motion until configuration allows it;
- limits diagnostics to read-only queries and `M5`;
- requires temporary arming for positive-power jobs;
- restricts jobs to a conservative absolute-millimetre G0/G1/M3/M4/M5 subset;
- validates every destination against the configured work area;
- prevents rapid travel while laser state is active;
- revokes authorization on stop or disarm;
- attempts `M5` on stop, disconnect, and failure.

This is an accidental-command boundary, not functional safety.

## Persistence map

| Data | Current location |
|---|---|
| Main configuration | `config/default.json` plus ignored `config/local.json` |
| Calibration JSON/images | configured `app.data_dir` |
| Captures, logs, generated G-code | configured `app.data_dir` |
| Desktop projects | user-selected `.e3laser` paths |
| Project backups | adjacent `.e3laser.bak` files |
| Autosaves | `~/.local/share/e3-positioning-system/backups/` by default |
| Material presets | `~/.local/share/e3-positioning-system/materials.sqlite` by default |
| Cutting templates | configured application data directory under `templates/` |
| Window layout | Qt `QSettings` |

The hard-coded desktop user-data paths are Linux-oriented and should be
replaced with an OS-native location abstraction before Windows packaging.

## Platform boundary

The portable core and simulator run on Windows and Linux, while real hardware
is currently Linux-only:

- `machine.serial_backend` exposes the transport protocol and imports the POSIX
  implementation only when real serial hardware is selected.
- Unsupported systems report no serial ports and reject real serial selection
  with a clear simulator-only message.
- Camera enumeration and controls use `/dev/video*`, V4L2, and `v4l2-ctl`.
- Launch/install scripts and desktop integration are Linux shell assets.
- CI currently runs Ubuntu only.

Platform implementations must remain lazy so unavailable hardware backends do
not prevent the simulator or portable libraries from importing. See
`CURRENT_STATE.md` for the verification record and recommended repair order.
