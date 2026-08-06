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

- **85 platform-neutral tests passed.**
- Project model/history, persistence, materials, SVG/geometry, homography,
  lens-cache behavior, workpiece/fiducial detection, object tracing, and both
  G-code pipelines passed their available tests.
- Selected native panels and the workspace constructed with Qt's offscreen
  backend.
- Ruff was not installed in the current virtual environment and was not run.

The full suite does not collect on Windows because
`laser_aligner.machine.service` imports `serial_posix`, which imports `termios`,
at module import time. This blocks the browser application, desktop application,
machine simulator tests, app simulation test, and POSIX serial tests even when
hardware is disabled.

Current Windows diagnostic command:

```powershell
.\.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider `
  --ignore=tests/test_app_simulation.py `
  --ignore=tests/test_machine.py `
  --ignore=tests/test_serial_posix.py
```

This is a limitation record, not a supported-platform acceptance command.

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
| Browser single-SVG G-code | Tested on Windows at the library level |
| Desktop project model, history, materials, and toolpaths | Tested on Windows |
| Selected Qt panels/workspace | Smoke-tested offscreen on Windows |
| Complete browser simulator | Historically verified on Linux; currently blocked on Windows |
| Machine simulator safety suite | Historically verified on Linux; currently blocked on Windows |
| POSIX serial transport | Historically verified through a Linux pseudoterminal |
| Full interactive desktop workflow | Implemented, not end-to-end verified |
| Real C920 capture/calibration | Implemented for Linux, not physically verified |
| Real controller motion or laser output | Not physically verified |

Source-presence tests for desktop labels, signals, and method names are not
equivalent to behavioral GUI tests.

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
- real dry-motion and low-power framing behavior.

## Hardware bring-up gate

Do not enable `machine.allow_motion` or use `./run-hardware.sh` until the camera
and controller probes have been captured and reviewed. Keep the laser power
lead physically disconnected during early motion/controller identification
where practical. The first physical execution should be a small, centrally
located dry frame generated by the application, followed by repeatability
measurements before any powered laser test.

See [docs/HARDWARE_BRINGUP.md](docs/HARDWARE_BRINGUP.md) and
[SAFETY.md](SAFETY.md).
