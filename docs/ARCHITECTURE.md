# Architecture

## Data flow

```text
C920/V4L2
   │
   ▼
CameraService ──► LensModel.undistort()
   │
   ▼
BedMapper.rectify() ──► top-down workspace JPEG
   │
   ├──► workpiece contour detector
   │
Browser UI ◄────────── local HTTP server
   │
   ├── SVG text + placement
   ▼
SVG parser ──► placed polylines ──► work-area validation ──► G-code
                                                        │
                                                        ▼
                                            MachineService safety gates
                                                        │
                                          simulator or POSIX serial controller
```

## Why a dependency-free HTTP layer

The application uses Python's threaded standard-library HTTP server and plain browser JavaScript. This keeps installation simple on an old Linux computer and avoids requiring Node.js or a large web framework. OpenCV and NumPy remain the primary external runtime dependencies.

## Coordinate conventions

- Machine X increases to the right.
- Machine Y increases toward the top/back in the rectified workspace.
- Browser pixels increase downward, so UI Y conversion is inverted.
- SVG coordinates also normally increase downward. The placement engine flips SVG Y into machine coordinates so the design appears visually upright on the top-down photograph.
- Positive design rotation is counter-clockwise in machine coordinates. CSS display rotation uses the opposite sign because browser Y is inverted.

## Calibration files

`data/lens_calibration.json` stores camera intrinsics and distortion. `data/bed_points.json` stores point pairs. `data/bed_calibration.json` stores image-to-machine and inverse homographies plus error data. `data/bed_reference.jpg` stores the fixed-pose image used for point clicking.

Atomic JSON writes reduce corruption from interrupted saves.

## Machine safety boundary

`MachineService` is the only normal path to the controller. It blocks serial access unless the process is started with `--hardware`, blocks motion until allowed in configuration, limits the diagnostic console to read-only queries and `M5`, requires temporary arming before a powered generated job starts, restricts streamed jobs to a conservative absolute-millimetre G0/G1/M3/M4/M5 subset, checks every XY destination against the configured work area, prevents rapid travel while laser state is active, revokes authorization on stop/disarm, and attempts `M5` on stop/disconnect.

This is an accidental-command boundary, not functional safety.
