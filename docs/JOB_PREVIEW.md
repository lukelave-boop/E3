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

The cyan head marker at the end of the timeline is the final point in that
generated stream. Automatic hardware preflight and successful-powered-job
Home/park/motor-release actions belong to `MachineService`, occur outside the
stream, and are not drawn. The machine job remains running until configured
completion actions finish; the Laser panel reports the drain, home, park, and
motor-release phases after the previewed stream reaches 100%.

## Controls and readouts

- Drag the time slider or use **Start** and **Play/Pause** to inspect order.
- Use Space to play/pause, Home/End to jump to the timeline boundaries, and
  Left/Right to step by one second.
- Playback speeds range from 0.1× to 40× and affect only the animation.
- The current-move readout shows rapid/feed state, laser-off or explicit planned
  percentage and `S` value, feed rate, X/Y destination, layer, and pass.
- Toggle rapid travel, controller-power shading, display inversion, and the
  legend without changing the job. Power shading retains distinct `S` values
  within one operation instead of assigning one shade to the complete layer.
- Pan, zoom, fit the bed, and save the current view as a PNG.
- Statistics separate cut and travel distance/time and report total estimated
  time and maximum planned power. With nearest-path planning they also report
  rapid-travel distance saved relative to source order.

The Job inspector deliberately keeps prepared and executing state separate.
After generation it reports the maximum power present in the prepared job, for
example `20.0% / S200`. Controller polling reports execution progress on its own
line and cannot replace that prepared-job value.

Generation and exact-preview preparation do not monopolize the Qt event loop.
An owned worker clones the framework-independent project model before planning;
authoring controls are temporarily held while that worker reads the live model,
but software STOP remains available. The worker then generates and indexes the
immutable `JobPlan`. Separate worker and renderer request owners accept a result
only while its token, source document, and revision remain current. Raw G-code,
the workspace overlay, and the dedicated Preview painter paths are populated in
bounded GUI-thread slices; no Qt object is created or mutated by a worker.

The Laser inspector shows preparation progress and blocks overlapping Generate,
Frame, Preview, Run, and export commands until all exact views are complete.
Closing an unfinished Preview, software STOP, project replacement, or a project
revision invalidates the whole unfinished result. Application close requests
cancellation and keeps task ownership until every worker has returned before
stopping the runtime. Late success and failure callbacks cannot clear a newer
job's busy state or install their result. Generation remains in memory and does
not create a G-code artifact automatically; only the explicit **Export G-code**
command writes a file.

## Exactness and invalidation

The immutable `JobPlan` records every supported G0/G1 move in stream order. Its
durations use the final feed values plus configurable acceleration and
per-command latency estimates. Firmware buffering and other machine-specific
delays can still differ. Preview display options do not mutate the G-code. Large
backward timeline jumps rebuild painter paths in bounded event-loop slices, so
scrubbing does not monopolize the GUI. Any project revision invalidates the
generated program and closes its Preview, requiring generation again before
preview, export, or run.

The existing guarded run path remains authoritative. Preview cannot connect,
home, enable motion, arm the laser, or submit commands, and it does not relax
bounds checking, the conservative command allowlist, rapid-with-laser rejection,
or stop/disarm behavior. It is not a safety-rated beam-location guarantee; dry
frame every real job and keep the operator present.

Preview includes an operation table with independent display visibility,
cut/time/power statistics, and a generated-layer legend. Generation can retain
source ordering or apply nearest-path ordering; the exact selected planner is
recorded in the program and Preview heading. Above 512 vector paths, nearest
ordering falls back to recorded source order so quadratic planning cannot stall
the desktop.

The canvas and a scrollable review inspector share a resizable split view so
the graphical path keeps a useful working area on compact screens and with
larger text. Long current-move descriptions are elided in the timeline row;
hovering the readout shows the complete layer, pass, power, feed, and position.

Line, closed-vector fill, binary vector raster, and ordered-dither grayscale
images use the same exact-program Preview. Image rows are sampled at the
configured exact physical line pitch and absolute machine-coordinate scan angle,
with bounded area prefiltering before arbitrary affine sampling, then remain
serpentine. Complementary high-frequency source phases and equivalent source
resolutions therefore produce the same physical dither plan. Source
top/mirror/rotation orientation matches the
canvas. Raster lead-in, white gaps, and lead-out are shown as laser-off
engraving-feed moves and are bounds checked. Image-only and mixed image/vector
projects contribute their transformed bounds to dry framing. PNG/JPEG/BMP
metadata and conservative decoded bytes are bounded before decode; TIFF is
rejected consistently. The workspace decodes the exact bounded byte payload
used to compute its SHA-256 identity, and divides its preview-memory budget
across the current project's unique raster sources to avoid decode-cache churn.
Retained previews are reduced when more unique sources enter the project and
are reloaded from their exact payload when a larger per-source budget becomes
available, so removing sources restores inspection quality without rereading
assets on unrelated edits. Recovery is cancellable and processes one unique
source per event-loop turn so a multi-image quality increase does not block
ordinary interaction.
Generated project jobs retain those identities so a changed or moved source can
invalidate prepared authority before Preview, export, or execution. A same-path
source change is also compared with the SHA displayed on the canvas before a
generated job can be installed. The canvas refreshes to the new exact payload,
that first result is rejected, and the operator must Generate and review again;
unseen raster content cannot become runnable merely because its path stayed the
same. **Prepare Start Here** can replace the prepared job at a reviewed move
boundary; it records the configured controller photography pose, inserts a
fresh absolute-mm, laser-off positioning prologue, and opens another Preview.
The replacement Preview therefore includes the physical laser-spot approach
from Home/park to the selected boundary, including the configured spot offset.
It never starts the machine, and it requires the normal confirmation, homing,
bounds, preflight, and arming path afterward.
