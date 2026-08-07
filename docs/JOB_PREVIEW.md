# Generated-job Preview

The native desktop opens a dedicated graphical Preview after **Generate** or
**Dry frame**. It answers two separate questions before execution:

1. What exact motion and controller power did generation produce?
2. In what order and at approximately what time will each move occur?

Preview parses the finalized G-code text that is exported or passed to
`MachineService`; it does not regenerate geometry independently. Generated
controller-ignored comments retain layer, pass, and source-object context.
Configured laser-spot offsets are converted back to physical spot coordinates,
so the graphical path remains registered to the camera workspace while the
raw program retains controller coordinates.

## Controls and readouts

- Drag the time slider or use **Start** and **Play/Pause** to inspect order.
- Use Space to play/pause, Home/End to jump to the timeline boundaries, and
  Left/Right to step by one second.
- Playback speeds range from 0.1× to 40× and affect only the animation.
- The current-move readout shows rapid/feed state, laser-off or explicit planned
  percentage and `S` value, feed rate, X/Y destination, layer, and pass.
- Toggle rapid travel, controller-power shading, display inversion, and the
  legend without changing the job.
- Pan, zoom, fit the bed, and save the current view as a PNG.
- Statistics separate cut and travel distance/time and report total estimated
  time and maximum planned power. With nearest-path planning they also report
  rapid-travel distance saved relative to source order.

The Job inspector deliberately keeps prepared and executing state separate.
After generation it reports the maximum power present in the prepared job, for
example `20.0% / S200`. Controller polling reports execution progress on its own
line and cannot replace that prepared-job value.

## Exactness and invalidation

The immutable `JobPlan` records every supported G0/G1 move in stream order. Its
durations use the final feed values plus configurable acceleration and
per-command latency estimates. Firmware buffering and other machine-specific
delays can still differ. Preview display options do not mutate the G-code. Any project revision
invalidates the generated program and closes its Preview, requiring generation
again before preview, export, or run.

The existing guarded run path remains authoritative. Preview cannot connect,
home, enable motion, arm the laser, or submit commands, and it does not relax
bounds checking, the conservative command allowlist, rapid-with-laser rejection,
or stop/disarm behavior. It is not a safety-rated beam-location guarantee; dry
frame every real job and keep the operator present.

Preview includes an operation table with independent display visibility,
cut/time/power statistics, and a generated-layer legend. Generation can retain
source ordering or apply nearest-path ordering; the exact selected planner is
recorded in the program and Preview heading.

Line, closed-vector fill, binary vector raster, and imported 50%-threshold
raster images use the same exact-program Preview. Raster overscan is shown as
laser-off feed and is bounds checked. **Prepare Start Here** can replace the
prepared job at a reviewed move boundary; it inserts a fresh absolute-mm,
laser-off positioning prologue and opens another Preview. It never starts the
machine, and it requires the normal confirmation, homing, bounds, and arming
path afterward.
