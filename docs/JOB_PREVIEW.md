# Generated-job Preview

For native project generation, the desktop first builds a structured,
Qt-neutral job-preflight report from a detached project snapshot. A blocking
report stops before exact toolpath generation and opens a reusable non-modal
findings window; there is no generated program to preview. A ready or warning-
only report continues to the authoritative planner and is shown inside the
resulting Preview. Warnings are visible but do not replace or block exact
planning.

The native desktop opens the dedicated graphical Preview after successful
**Generate** and after calibration-job preparation. This window-modal review is
the mandatory final gate before execution and answers two separate questions:

1. What exact motion and controller power did generation produce?
2. In what order and at approximately what time will each move occur?

Preview parses the finalized G-code text that is exported or passed to
`MachineService`; it does not regenerate geometry independently. Generated
controller-ignored comments retain layer, pass, and source-object context.
Configured laser-spot offsets are converted back to physical spot coordinates,
so the graphical path remains registered to the camera workspace while the
raw program retains controller coordinates. For a honeycomb-local project, the
machine-coordinate plan is transformed through the prepared rigid honeycomb
frame for display on the local X0..width, Y0..height canvas. This display
transform does not replace or reindex the immutable plan.

The cyan head marker at the end of the timeline is the final point in that
generated stream. Automatic hardware preflight and successful-powered-job
Home/park/motor-release actions belong to `MachineService`, occur outside the
stream, and are not drawn. The machine job remains running until configured
completion actions finish; the Laser panel reports the drain, home, park, and
motor-release phases after the previewed stream reaches 100%.

## Structured preflight report

Each finding has an info, warning, or blocker severity; a stable dotted code;
a title and message; and optional detail and structured context. The reusable
Qt view shows deterministic severity totals and a bounded projection of the
ordered report. The complete immutable report remains available to the desktop.

Preflight summarizes existing preparation constraints: project/machine work-
area agreement, coordinate-space and honeycomb execution binding, calibration-
profile identity, read-only bed-calibration validity and honeycomb-support
CURRENT state for local output, eligible output, operation settings, machine
work/travel feed ceilings, and bounded raster metadata/resource budgets. Stale
bed-calibration or support readiness is a honeycomb-local blocker. Only provably
exact local bounds for unrounded rectangles and valid two-point lines can become
structured bounds blockers. Rounded rectangles, ellipses, images, paths, and
other complex geometry remain intentionally deferred. Preflight does not
flatten those vectors, decode raster pixels, apply final placement or spot
correction, generate commands, or run machine preflight. A ready report is
therefore not a promise that exact generation will succeed.
`generate_project_gcode()` remains authoritative for geometry and the final
program, and `MachineService` remains authoritative at execution time.

Blocked findings are presented only after the active preparation owner has been
released, and the modeless window cannot continue to Preview. Warning-only and
ready reports stay attached to the same generation request and appear in the
exact Preview sidebar. Neither presentation can connect, home, enable motion,
arm the laser, submit G-code, or grant execution authority.

## Controls and readouts

- Drag the time slider or use timeline **⏮ Start** and **Play/Pause** to inspect
  order. These controls animate only and never execute hardware.
- Use Space to play/pause, Home/End to jump to the timeline boundaries, and
  Left/Right to step by one second.
- Playback speeds range from 0.1× to 40× and affect only the animation.
- The current-move readout shows rapid/feed state, laser-off or explicit planned
  percentage and `S` value, speed in mm/s and as a percentage of the configured
  work or travel limit, destination, layer, and pass. Honeycomb-local jobs show
  both the honeycomb X/Y destination and the underlying machine X/Y destination;
  machine-coordinate jobs show machine X/Y.
- Toggle rapid travel, controller-power shading, display inversion, and the
  legend without changing the job. Power shading retains distinct `S` values
  within one operation instead of assigning one shade to the complete layer.
- Pan, zoom, fit the bed, and save the current view as a PNG.
- Use the distinct bottom-right **START JOB** only after reviewing this exact
  program. It dismisses Preview before the guarded run attempt so software STOP
  is accessible.
- Statistics separate cut and travel distance/time and report total estimated
  time and maximum planned power. With nearest-path planning they also report
  rapid-travel distance saved relative to source order.

The Job inspector deliberately keeps prepared and executing state separate.
After generation it reports the maximum power present in the prepared job, for
example `20.0% / S200`. Controller polling reports execution progress on its own
line and cannot replace that prepared-job value.

Generation and exact-preview preparation do not monopolize the Qt event loop.
An owned worker clones the framework-independent project model, builds the
structured preflight report, and only for a non-blocked report enters exact
planning. Authoring controls are temporarily held while that worker reads the
live model, but software STOP remains available. The worker then generates and
indexes the immutable `JobPlan`. Preflight and planning share the request's
cancellation token and authority snapshot. Separate worker and renderer request
owners accept a result only while its token, source document, revision,
coordinate/support/calibration binding, feed ceilings, and runtime identity
remain current. Raw G-code, the workspace overlay, and the dedicated Preview
painter paths are populated in bounded GUI-thread slices; no Qt object is
created or mutated by a worker.

The Laser inspector shows preparation progress and blocks overlapping Generate,
Frame, Preview, Start, and export commands until all exact views are complete.
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
preview, export, or run. A honeycomb-local prepared job also binds the exact
support pose, camera-to-machine map, and configured output polygon. Preview
construction, reopening, export, Start Here, and Start reject the job if any of
those bindings changed.

The existing guarded `run_current_job()` path remains authoritative. Preview's
**START JOB** signal dismisses the modal dialog and delegates synchronously to
that path; Preview itself cannot connect, home, enable motion, arm the laser, or
submit commands. The handoff does not relax revision and bound-source checks,
bounds checking, the conservative command allowlist, rapid-with-laser rejection,
or stop/disarm behavior. The main Job panel and menu action only reopen Preview,
so they cannot bypass review. Preview is not a safety-rated beam-location
guarantee; zero-power frame every real job and keep the operator present.

**Prepare Start Here…** never runs hardware. It asks for confirmation owned by
the modal Preview, replaces the prepared program, and requires the replacement
to finish and be reviewed in its own exact Preview before **START JOB** is
available.

Preview includes an operation table with independent display visibility and
generated speed shown in mm/s plus percentage of the configured work-feed
limit, Vector/Raster Power Correction settings, and cut/time/power statistics
alongside a generated-layer legend. Inline corrected `S` values are parsed from
the exact program, so maximum power and power shading include correction. The
raw generated G-code retains the controller-required `F` words. Generation can retain
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
projects contribute their transformed bounds to zero-power framing. PNG/JPEG/BMP
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
It preserves the original prepared coordinate-space and fixed-polygon binding
and retains the reviewed source move index even when its canvas is displayed in
honeycomb-local coordinates. It never starts the machine, and it requires the
normal confirmation, homing, bounds, preflight, and arming path afterward.
