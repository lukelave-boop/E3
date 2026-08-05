# Roadmap

## v0.1 — repository foundation

- Simulation camera and controller
- C920/V4L2 capture and control locking
- Checkerboard lens calibration
- Bed homography and rectified view
- SVG vector parser and G-code generation
- Browser placement interface
- Work-area checks and guarded machine service

## v0.2 — physical camera bring-up

- Validate C920 stable by-id selection
- Add camera control editor and saved control presets
- Add calibration-image quality guidance and coverage visualization
- Measure repeatability after cold starts and camera remounting

## v0.3 — Ender/Creality controller profile

- Capture controller identity and settings
- Confirm GRBL versus Marlin behavior
- Confirm homing, coordinate origin, X/Y direction, and laser-head offset
- Establish verified work-area limits and safe photo pose
- Add a versioned machine profile based on measured results

## v0.4 — improved placement

- SVG snapping to detected rectangular workpieces
- Multiple designs and duplication
- Fiducial-based automatic rotation/translation
- Toolpath preview drawn over the bed image
- Better SVG `<use>`, stylesheet, and clipping support

## v0.5 — height compensation

- Material-thickness input
- Camera-height and optical-center model
- Parallax correction or calibration planes
- Optional low-cost distance-sensor integration

## v0.6 — job features

- Raster engraving pipeline
- Material/power/feed presets
- Estimated duration
- Job history and reproducible manifests
- Recovery and controlled pause/resume where supported

## v1.0 criteria

- Repeatable physical alignment performance documented across the work area
- Verified controller profile and laser power scale
- Hardware interlock integration documented and tested
- Installation and recovery tested on a clean Linux Mint system
- No known path-generation or out-of-bounds defects in the supported SVG subset
