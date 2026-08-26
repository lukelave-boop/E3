# Native machine setup

> **Operator directions:** Follow the packaged
> [Permanent Camera Setup Runbook](../laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md).
> It is the canonical current-version five-step calibration sequence. Machine
> Setup also includes a sixth read-only Coordinate Audit tab after those steps.
> This document explains implementation details and optional diagnostics; it is
> not a competing step order.

Open **Tools > Machine Setup…** in the desktop application. The Camera panel's
**Calibrate lens…** and **Bed alignment…** buttons open the same window at the
relevant step. The dialog uses the shared `AppContext`; it does not start a web
server or create a second camera owner.

## Saved machines and the running process

**Machine Manager** uses the existing saved-machine registry. A machine profile
is reusable motion-platform/controller starting data; a tool-head profile is
reusable laser/tool starting data; and a saved machine instance is the
operator's concrete validated copy. **Add** creates from the selected profiles.
Editing an existing instance does not silently reload profile defaults, and the
separate profile-default buttons only change the form after confirmation.

The dialog distinguishes **Running now** from **Use on next launch**. The running
machine was resolved once when `CoreRuntime` was created. Selecting or editing a
next-launch machine cannot change the current controller, Material Recipe
compatibility, workspace bounds, preflight, calibration state, or execution.
Restart E3 to run another saved instance.

Connection fields are conditional: endpoint and baud apply to the serial/
`e3bridge://` backend, while GRBL step-idle applies only to GRBL or an automatic
selection that may resolve to GRBL. The current built-ins are Generic GRBL,
Generic Marlin, Ender-3 S1 Pro, and Custom Machine, with Generic 10 W Diode and
Custom Laser Head tool profiles. This UI
does not claim compatibility with an untested printer or controller.

New profile-created machines start with motion permission off, default and frame
power at zero, low-power framing off, and no inherited camera, calibration, or
honeycomb binding. Machine Setup shows the running profile identity, active
optical profile, and any saved binding. If the running saved machine is unbound
or mismatched, **Bind active profile for a later launch** explicitly records the
current camera/calibration profile IDs on that saved instance. It performs no
hardware action, does not change the immutable current runtime, and does not
assert that calibration evidence is valid. Restart and satisfy the ordinary
bed/support validity checks before honeycomb-local coordinate authority can be
used.

The first-run wizard requires one of these physical profiles. It stores the
bridge/camera endpoints and a safe-off saved
snapshot; its optional network test is reachability-only. First-run never
connects, Homes, jogs, arms, moves, emits, or physically verifies the machine.

New projects use curated operations compatible with the **running** profile
identity. The current Ender-3 S1 Pro / generic 10 W match retains its exact 13
historical operations. Other current combinations receive one clearly named,
0%-power, output-disabled Line operation capped by the running work-feed limit.
Apply a compatible Material Recipe or configure that layer before deliberately
enabling output. User-created SQLite recipes are never selected automatically.

Machine-specific physical dimensions are edited separately in **Machine
Manager**. Under **Work area and motion**, **Physical honeycomb ruler span** is
blank until explicitly measured and configured for that saved machine. Changing
or applying a generic motion-platform profile does not infer a span, including
for the Ender-3 S1 Pro profile, and applying profile defaults preserves an
existing measured value. The operator can still clear the field explicitly.
Bed Mapping displays this value read-only as **Configured physical ruler span**
and supplies it unchanged to both automatic four-edge detection and the
three-hint fallback. If it is unset, both detection paths remain blocked and
direct the operator back to Machine Manager; Machine Setup never invents or
edits a span. The value is also consumed by the read-only Coordinate Audit.
Machine-scoped fixture-reach evidence remains the foundation for a later reach
editor; that editor is not part of the current interface.
Fixture-reach evidence remains diagnostic only and does not change GRBL
settings, work areas, guarded polygons, G-code, arming, laser power, or motion.

Home / park, stable and precision captures, checkerboard capture and solve, and
camera diagnostics run as one owned background operation at a time. The footer
shows indeterminate progress while the remaining setup controls and Close action
are held. **STOP / LASER OFF** remains available in the dialog and requests the
same non-waiting software stop as the main desktop control. Close the dialog only
after the operation reports that machine/camera cleanup has finished. Software
STOP can interrupt motion; it does not forcibly terminate camera or OpenCV work,
and it is not a substitute for the physical emergency stop. Each submitted
worker is bound to the machine's request-time STOP generation, so Home / park
that was queued before STOP cannot begin afterward.

All six setup pages scroll inside the center of the dialog when their controls
need more room. The safety warning, machine connection and STOP controls, tabs,
operation status, and Close action therefore remain visible at compact window
sizes.

Starting a new Home / park, lens solve, registration capture, or validation
capture immediately clears its prior review and disables every related Apply
action. A failure or software stop leaves that prior result invalid rather than
restoring it. A result that arrives after STOP is discarded.

## Machine configuration and controller boundary

The saved machine instance contains a complete validated snapshot of its
machine and laser settings. Its machine profile describes reusable physical
motion-platform defaults, including the controller backend/protocol, connection,
work envelope, homing behavior, and feed ceilings. Its separate tool-head
profile describes laser/tool defaults, including power mode/range, feeds, spot
offsets, and guarded-boundary settings. Applying or selecting either profile
does not connect, home, move, arm, or enable output; the running settings and
`MachineService` safety gates remain authoritative.

At connection time, one construction-only factory maps the saved `backend`,
`port`, and `baudrate` to local POSIX serial or
authenticated `e3bridge://` network transport. Transport objects carry only raw
bytes and lines; they do not interpret commands or grant controller access. The
bridge URI is recognized before the local-platform check, so Windows can use an
authenticated bridge while direct local serial remains unavailable there.
Platform transports are imported lazily, preserving portable imports on Windows.

GRBL and Marlin behavior is described by immutable, UI-neutral dialect policy,
but `MachineService` still owns connection timing, command writes, response
ownership, retries, safety checks, and cleanup. Exact `grbl` or `marlin` settings
select only their existing semantics. `auto` remains deterministic and sends no
new probes: after the configured startup delay and drain it recognizes the GRBL
banner, otherwise tries `$I` for 1.0 seconds and then `M115` for 1.5 seconds,
using the same accepted identity markers and failing closed if neither matches.

This architecture change adds no controller or machine compatibility. It has
automated verification only; physical GRBL and Marlin behavior through the
refactored boundaries remains to be re-verified before it can be recorded as
physical evidence.

## 1. Camera

- `camera.view_rotation_degrees` rotates every raw-camera image shown in native
  Machine Setup clockwise by 0, 90, 180, or 270 degrees. Clicks are transformed
  back into the original sensor coordinates before calibration, ruler fitting,
  or manual point entry. This is a presentation setting: it does not rotate
  captured files, change lens/bed calibration coordinates, swap controller
  axes, or alter motion and output bounds. Choose the quarter-turn that makes
  machine X increase to screen-right and machine Y increase toward screen-top.
- Inspect a raw preview and save a corrected still.
- Apply every camera control from the active configuration. Unsupported or
  rejected V4L2 controls are reported rather than silently treated as applied;
  supported controls are read back so a driver mismatch is visible.
- Review the active precision profile. Analysis captures settle, discard stale
  buffered frames, and collect unique fresh frames; live preview remains
  immediate.
- The status line reports both observed FPS and the mode negotiated with the
  camera driver. A negotiated rate below the configured request remains visible
  even when live delivery is steady.
- Precision capture, camera-control changes, and device restart have one
  exclusive camera owner. A competing action fails with a bounded busy message
  instead of sharing frames or changing controls mid-burst. Closing the runtime
  cancels that owner and clears the cached frame. The profile timeout covers
  control reapplication, settling, discards, and sample acquisition after the
  action gains ownership.

If another application has exclusive camera access, the desktop presents one
acknowledgeable **Camera unavailable** message. Repeated live-refresh failures
remain in the status display and do not open more dialogs or steal focus.
Automatic refresh continues quietly so recovery can be detected. After closing
the other application, use **Refresh camera** for an explicit retry. If the
camera is disconnected, frame-less, or reporting a read error, that action
releases the failed capture and reopens the configured device before requesting
a new frame. A successful frame clears the fault latch and reports recovery.

A stale, legacy, or otherwise untrusted bed map is reported separately as
**Bed mapping required**. This state does not claim that the camera is
unavailable. The corrected-overlay refresh is blocked before camera work is
queued, and repeated timer refreshes remain silent. While the camera is offline,
the desktop reports the mapping requirement in its status bar without opening a
modal recovery prompt. When the camera is online, the dialog opens Machine Setup
at Lens when no accepted lens model exists or at Bed mapping when the lens model
is already accepted. The fresh keyed base-map workflow remains automatic; it
does not require coordinate entry. A corrected-view processing failure while the
camera remains healthy is likewise reported as an overlay error, not as a camera
ownership failure. A visible overlay is cleared as soon as its mapping or focus
provenance changes. In-flight frames are bound to the exact lens and bed models
that produced them, so a late result from the prior calibration cannot be
displayed.

Mount the camera rigidly, independently of the moving bed and gantry. Changing
the mount invalidates alignment evidence. Resolution and locked manual focus
select separate persistent calibration profiles after restart. A newly selected
profile must complete the workflow once; returning to a previously calibrated
profile restores its own lens, bed, registration, validation, and ruler data.
The profile does not make a moved camera valid merely because its focus value is
unchanged.

## 2. Lens

Print `targets/checkerboard_9x6_20mm.svg` at 100% scale and verify its square
size. Capture at least the configured minimum number of usable views with the
complete flat board at the image center, corners, and edges and at several
modest tilts. The Lens tab reports whether the live camera is ready for
calibration. On hardware, the configured resolution and critical focus,
exposure, and white-balance controls must be current and verified before
capture or solve is enabled.

Captured images are grouped by exact resolution. The current camera group is
identified explicitly and is the only group used by **Solve current-resolution
calibration**; stored images from another resolution are never silently mixed
into that solve. The evidence table reports filename, resolution, checkerboard
detection, sharpness, board coverage and image region, plus exposure/contrast
measurements. **Delete selected capture** and **Clear all captures** are
confirmation-gated and retain the solved model. New captures are lossless PNG;
legacy JPEG captures remain readable.

Opening Machine Setup catalogs legacy image dimensions from bounded headers and
then indexes pending evidence on a background worker. The worker reads each
selected file into one size-capped immutable encoded payload and derives its
digest, dimensions, decoded pixels, and preview measurements from those exact
bytes. Preview detection and sharpness/coverage use an image no larger than
`640 x 360`, and the selected set has a bounded aggregate encoded-byte budget,
so a cold catalog cannot run full-size checkerboard detection on the Qt thread.
The progress row names the current capture and keeps the other Setup tabs
usable; capture, deletion, clearing, solve, and Close are held while the evidence
set is owned. An interrupted or externally changed set is rejected rather than
being silently marked current.

The preview index records a content digest and strictly validates every cached
field before presenting it. Old stat-only indexes and malformed advisory rows
remain read-only during status refresh and appear as pending. Background index
and full solve both decode the same immutable bytes they digest, then compare a
fresh final evidence signature before committing. A byte replacement with the
same file size and modification time is still rejected. Mixed-case PNG/JPEG
file extensions are recognized, with PNG selected deterministically when the
same stem also has a legacy JPEG. **Re-index all captures** is enabled whenever
captures exist and rebuilds every advisory row, including a ready-looking row
whose bytes were replaced externally.

Each preview sharpness cell includes its measurement dimensions. New captures
are written as atomic lossless PNG staging files and measured from those exact
persisted bytes through the same bounded decoder, resize, and quality pipeline
as background-indexed evidence. Comparisons remain meaningful only for captures
with the same displayed dimensions and scene.

The bounded result is only table feedback. Once indexing finishes and the
current-resolution capture count reaches the configured minimum, **Solve**
decodes those original files and detects their corners again at full resolution.
The final PASS, WARNING, or REJECT gate therefore does not inherit a bounded
preview false negative. Review its reasons, RMS and mean reprojection errors,
coverage and tilt-diversity metrics, and the worst per-view RMS/P95/maximum
errors. A low aggregate error is not sufficient: center-only or nearly
identical flat poses are rejected because they cannot constrain a trustworthy
lens model.

Clearing the solved model retains captured checkerboard images. It does not
delete the bed-map file, but the provenance check immediately marks that map
**STALE**. Replacing the lens model does the same. Fine registration, accuracy
validation, and other bed-dependent actions remain disabled until the bed map
is redone against the current lens model.

## 3. Bed mapping

After a camera move, use **Fresh automatic base mapping (keyed 5 x 5)**. It
does not use the old camera map and does not require manual point entry:

The powered base-map pattern is the sole support-containment bootstrap
exception in Machine Setup: its image-to-machine map must exist before the
honeycomb corners can be expressed in machine coordinates. The job remains
bounded by the configured machine area. The restrained sacrificial sheet must
cover the exact reviewed target pattern; the honeycomb outline does not
authorize this step.

1. Rigidly restrain a clean sacrificial sheet that covers every displayed grid
   coordinate.
2. Enter a previously established visible-marking power,
   and choose **Prepare powered base-map job**. Review the exact Preview and use
   its **START JOB** control to submit it through the normal guarded job path.
3. On successful completion, Setup reopens automatically and starts
   **Home / park, capture and detect base grid**. The machine holds through
   Home / park and the precision burst, then restores normal idle behavior and
   releases the motors before image analysis. Use the button manually only to
   retry.
4. Inspect the numbered overlay. Apply only when every circle is centered on
   its mark and the reported 25-point fit passes.
5. After applying the map, choose **Home / park, capture ruler overlay**. The
   raw parked-camera view shows a 10 mm machine-coordinate grid with larger
   coordinate labels every 40 mm, the configured camera/work boundary in
   orange, and the guarded laser-output area
   after boundary margin and any configured laser-spot offset in green. Compare
   both axes to rigid honeycomb rulers.
   This is a diagnostic for camera-to-machine origin, scale, and crop errors;
   it never changes configuration automatically and does not prove laser reach
   or collision clearance. If it exposes a discrepancy, stop and correct the
   camera/machine calibration evidence before proceeding. Do not resize the
   machine-output envelope merely to match the movable honeycomb.
6. Confirm **Configured physical ruler span** displays the measured span
   configured for this running saved machine, then choose **Detect honeycomb
   automatically**. If it displays **Not configured**, use Machine Manager to
   configure the measured physical span for the saved machine before returning
   to this workflow; do not guess it in Machine Setup. Vision segments the
   dominant rectangular honeycomb and independently fits all four
   cutting-surface edges.
   The active bed map maps those four measured intersections into machine
   coordinates and preserves their semantic order as origin, +X, opposite, and
   +Y. **Configured physical ruler span** defines the nominal honeycomb-local
   width and height; it does not replace the four measured corners or fabricate
   observed edge lengths. Printed tick recognition is not required.
   Review and accept the detected outline. Acceptance stores a schema-2 support
   plus the exact reviewed teaching image, its four image corners, and digests
   binding the image, support frame, and complete bed map. Only this accepted
   automatic four-edge result is execution-verifiable. At Start, image
   registration uses only fresh, spatially distributed features inside the
   accepted cutting surface and projects the four taught corners as the pose
   measurement. Missing, stale, insufficiently covered, ambiguous,
   displaced, scaled, or non-square evidence fails before arming.
7. **Fallback: detect with 3 hints** is a display/diagnostic last resort. Its
   X-ruler, approximate shared-zero, and Y-ruler clicks only select search
   corridors; they are not measured corners. It can save a legacy visual
   reference, but it lacks four-corner evidence and cannot authorize a
   honeycomb-local job or any powered post-map Machine Setup job. Run automatic
   detection successfully before powered work.

The execution-verifiable result defines the movable honeycomb's rigid local job
frame from X0/Y0 to the configured physical span on each axis. The live camera,
grid, Trace, and project geometry share that frame. Green maps the separately
configured machine-coordinate output authority into it; its local coordinates
depend on the accepted support pose and configured span. Automatic detection
never moves or expands that authority. Features outside green remain red,
unchecked, and blocked. Ordinary machine-coordinate jobs retain their guarded
rectangle. Preflight, arming, and execution use the same selected authority.
The laser-burned keyed map remains the camera-to-machine calibration. Use
**Clear visual reference** or re-detect automatically after the honeycomb moves.

The generated pattern contains 23 regular crosses plus a larger and a medium
interior cross. Those two keys let the detector resolve rotation and reflection
without assuming that camera-right is machine X-positive. Detection is bound to
the exact powered-session targets, work area, boundary margin, and laser-spot
offset. Zero-power-only, stale, incomplete, unkeyed, ambiguous, non-unique, or altered
sessions are rejected. Application requires all 25 RANSAC inliers, no more than
`0.50 mm` RMS fit error, and no more than `0.80 mm` error at any point.

The reviewed installation replaces points and the homography as one
transaction. A failed write restores the prior files and in-memory map; a
successful fresh solve clears obsolete fine translation, full-map refinement,
and dense residual correction. The two key sizes bind the marks to the exact
generated controller-coordinate labels, so a fresh automatic map records normal
X/Y label orientation. A separate laser-off direction and bounds check remains
required before normal production, but it does not interrupt Step 3 to Step 4.

Manual and CSV-assisted entry remain behind **Show manual / CSV fallback**.
Keep the laser incapable of emission, put the machine at its repeatable
photography pose, capture a fixed bed image, then click each mark center and
enter its exact machine X/Y. Four point pairs are the minimum; nine or more
well-distributed points are preferred.

A coordinate CSV may use `x_mm`/`y_mm`, `machine_x`/`machine_y`, or `x`/`y`
headers, with an optional `fiducial`, `index`, `id`, or `label` column. It loads
the target coordinates in sequence; the operator still clicks each observed
pixel. **Detect grid using current rough map** remains available only when a
trustworthy rough mapping can predict the search regions; it is not the fresh
camera-remount workflow.

Solve and review RMS error, maximum error, and inlier count. A planar mapping
is valid only at the calibrated material height and repeatable bed pose.

A symmetric cross grid can produce a low residual even when its machine X or Y
labels are reversed. With the laser incapable of emission, verify that positive
controller motion goes toward the positive workspace direction shown by the
camera. If an existing map is proven mirrored, change **X mapping** or
**Y mapping** from NORMAL/OFF to REVERSED/ON and review the re-solved transform.
It must not be used to compensate for a loose workpiece, changed camera mount,
or non-repeatable bed pose.

The native controls now show explicit **NORMAL (OFF)** or **REVERSED (ON)**
states. These states are stored with the bed calibration and restored whenever
Machine Setup or the application reopens. Legacy or manually created maps
without this metadata show an inferred state and a separate confirmation button. Confirming
that display records the state without changing points; changing either toggle
mirrors the saved labels and re-solves the map. Perform the laser-off direction
check before confirming or changing either state. A stale bed map still reports
its saved orientation accurately, while keeping the orientation controls disabled
until the map is valid again.

Machine Setup also remembers its window geometry, selected tab, cross sizes,
and marking speeds in the active application data directory.
Verified marking power deliberately returns to `0%` on every open so a previous
powered calibration value is never silently carried into another session.

On a bed-slinger, rigidly restrain the calibration surface or workpiece to the
moving bed. Motion of the surface relative to the bed invalidates both the
mapping and any alignment comparison made from its camera image.

## 4. Fine registration

Fine registration verifies the solved bed map using eight fresh crosses placed
between the common 5×5 grid locations. Use a clean sacrificial surface at the
calibrated material height and rigidly restrain it to the moving bed.

Powered preparation requires the current accepted automatic four-corner
honeycomb reference. The targets are laid out inside that cutting surface and
the configured machine area. Generation checks every powered segment against
the support polygon and the complete program against machine bounds. The
prepared session binds its exact G-code, support corners, and complete bed-map
identity. Start repeats those checks, performs one laser-off Home, and begins
the validated program without another camera capture or photography-position
park. This remains a software guardrail, not a
safety-rated containment system.

Each newly prepared session records the exact active homography and
residual-mesh revision. If either changes before capture, or an older session
lacks that identity, capture is rejected before Home / park and a new mark job
must be prepared and run.

1. Set the cross size and marking speed, then enter a visible-marking power that
   has already been verified
   for the material, and choose **Prepare powered mark job**. Review the exact
   Preview and use **START JOB**; the one-use authorization is internal.
2. On successful completion, Setup reopens automatically and starts
   **Home / park, precision capture**. Use the button manually only to retry.
3. Review every commanded coordinate, observed coordinate, and X/Y residual.

The result also reports captured/discarded frames, rejected temporal outliers,
and worst per-mark jitter. Without moving anything, choose **Recapture without
homing** to test camera/detection repeatability independently of another home
cycle. The button stays disabled until this Machine Setup session completes a
park or home-first precision capture. Stable no-home results combined with
changing home-first results point toward homing or camera-pose repeatability;
changing no-home results point toward the camera, lighting, vibration, or mark
detection.

The **Use** checkbox permits at most two clearly obstructed, damaged, or
incorrectly detected crosses to be excluded. A detection beyond the bounded
search/residual limits is suggested as excluded automatically and remains
visible in the table for review. Unchecking ordinary points merely to obtain a
preferred answer is not a valid calibration. At least six of the eight marks
must remain.

The application classifies a consistent residual as one global translation and
offers **Apply reviewed translation** only when the centered scatter is small,
every mark is within the detection limit, and the cumulative correction is no
more than 5 mm. A changing residual is reported as position-dependent and
cannot be applied as a translation.

The tab also reports an independent **Full-bed fit**. **Apply reviewed full-bed
map** is enabled only with at least seven RANSAC inliers, broad bed coverage,
sub-millimetre fit residuals, bounded full-bed movement, and no orientation flip
or excessive local scale. The full map is confirmation-gated and never inferred
from only six accepted marks. **Reset full-bed refinement** restores the exact
solved map retained before application. Reset an applied fine translation before
applying or resetting a full-bed refinement.

Resetting an applied fine translation retains and immediately re-reviews the
current eight captured marks. When that capture passes the full-bed gates,
**Apply reviewed full-bed map** enables without burning or capturing the marks
again.

If neither correction passes, check bed/workpiece restraint, camera rigidity,
surface height, and redo the full bed mapping. **Reset fine translation** removes
only the bounded translation. Solving or reversing the base bed map clears all
fine registration.

The fine translation belongs to camera-to-machine registration. It is not a
laser-head mounting offset and does not modify `laser.spot_offset_x_mm` or
`laser.spot_offset_y_mm`.

Applying or resetting a translation/full-bed refinement changes the complete
bed-map identity and clears the support reference. Capture a new ruler overlay,
run automatic four-edge detection, and accept the new teaching image before
preparing another powered post-map calibration job.
The prior accepted image may guide pixel registration during this re-teach even
though its old map binding is no longer executable. Automatic detection must
still refit all four current edges and pass the new map's size and closure gates.

### Dense local correction

With an explicit honeycomb-output polygon, the powered 5×5 fit uses five
machine-axis nodes across a 180 × 180 mm center span so the residual mesh samples
near the complete cutting-surface boundary. The 5 mm crosses must fit entirely
inside both the accepted support and that fixed output polygon. The prepared
session binds the exact polygon through generation, Preview, and Start.

If the remaining error changes by bed position after the homography and fine
translation are stable, use **Dense local correction (5 × 5)**. Review the
exact powered 25-cross Preview, then run it on a clean, restrained sacrificial
surface at calibration height. The reviewed mesh is a bounded
nonlinear residual layer over the existing map and can be reset independently.

Powered dense preparation requires the current accepted automatic four-corner
support. Its regular Cartesian grid remains machine-axis aligned inside a
shrunken rectangle whose complete powered crosses fit the support and machine
area. The session is bound and reverified at Start in the same way as fine
registration.

The mesh is applied consistently to camera-to-machine conversion,
machine-to-camera placement, and image rectification. It rejects missing or
low-confidence marks, corrections over 3 mm, and abrupt local distortion.
After applying it, use the 4 × 4 mesh check on **Accuracy validation**. Its 16
interstitial marks were not used for fitting; passing requires at most 0.30 mm
RMS and 0.60 mm maximum error. Applying or resetting the mesh clears the support
because it changes the bed-map identity; automatically detect and accept the
support again before preparing the powered 4×4 check.

If that first independent check has a coherent bounded residual, **Apply
reviewed validation refinement** becomes available. This applies one guarded
update to the existing mesh; it cannot be repeated against the same mesh. Use a
new sheet, the clean reverse side, or another clean restrained surface before
running **Prepare powered shifted confirmation**. The shifted 16 positions
are different from both the original 25 fit marks and the 16 refinement marks.
Applying the reviewed validation refinement also clears the support; detect and
accept it again before preparing the shifted powered job. Both 4×4 jobs require
support-contained powered segments and the exact support/map Start check. Only
that fresh confirmation result is the final accuracy measurement.

A failed fit is still a review result. The captured image and all 25 measured
positions remain visible. One occluded or clearly unreliable grid detection can
be excluded automatically and shown in amber as **INFERRED**; its mesh node is
estimated from the other 24 cells. Two unreliable cells, excessive fitted
movement, or excessive local distortion keep application disabled. Every
inferred fit still requires the independent 4×4 holdout validation.

## 5. Accuracy validation

This is the independent holdout check for a translation or full-bed refinement.
It uses five locations that are not among the eight fine-registration marks and
does not fit or change calibration.

Powered validation requires the current accepted automatic four-corner support.
Its powered segments fit both that polygon and the configured machine area. The
session is bound to the exact active homography, residual-mesh revision, support,
and G-code; **START JOB** rejects a changed binding before its single laser-off Home and
arming sequence.

1. Put a clean sacrificial surface at the calibrated height and rigidly restrain
   it to the moving bed.
2. Enter a previously verified visible-marking power, choose
   **Prepare powered validation job**, review the exact Preview, and use its
   **START JOB** control; the one-use authorization is internal.
3. On successful completion, Setup reopens automatically and starts
   **Home / park, precision capture**. Use the button manually only to retry.

Use **Recapture without homing** under the same restrictions when comparing
capture repeatability with homing repeatability.

All five crosses are required. The program reports each commanded and observed
coordinate, X/Y error, total error, RMS error, maximum error, and mean bias. A
**PASS** requires RMS error no greater than `0.5 mm`, maximum error no greater
than `1.0 mm`, and confident detection of every mark. Low-confidence, incomplete,
zero-power-only, or stale-map sessions are rejected rather than scored. Validation
never applies a correction; a failure means the camera map is not physically
verified.

The tab also retains rectangular-workpiece and ArUco-fiducial detection as
secondary camera diagnostics. Those detectors are not accuracy proof.

## 6. Coordinate audit

The final tab is a read-only evidence view. **Refresh audit**, **Copy report**,
and corrected-image point inspection read current in-memory state and never
connect to or command the controller. Only **Home / park and capture audit
view** invokes hardware; it reuses the existing laser-off Home / park,
photography-position, precision-capture, and motor-release path.

The audit identifies the running saved machine and its machine/tool-head
profiles, compares its expected calibration binding with the actually active
calibration profile, and reports controller protocol, Home/reference state,
GRBL workspace/G92 state, work rectangle, photography position, guarded beam
and carriage authority, boundary margin, laser spot offset, camera/lens/bed-map
state, and the accepted honeycomb support. The physical support span comes only
from `machine.honeycomb_span_mm`; an unset value is explicit and blocks READY.
No 190/191 mm value is inferred.

During an audit capture, diagnostic GRBL position samples use only the realtime
`?` byte through the existing `MachineService` transport, including
`e3bridge://`. The immutable evidence records MPos, WPos, WCO, workspace and G92
state, commanded-versus-reported error, before/after stability, capture timing,
and bed-map identity. Normal cleanup still releases the motors and clears
current coordinate trust, so the panel distinguishes **TRUSTED AT CAPTURE**
from **CURRENTLY TRUSTED**.

The overlay shows the configured machine boundary, guarded output authority,
current accepted support, and positive machine/support axes. Clicking it traces
display pixel to corrected source pixel, desired beam/machine coordinate,
honeycomb-local coordinate when valid, and spot-corrected carriage coordinate.
Containment results are informational and never expand motion or laser-output
authority.

## Browser parity

The desktop is the primary operator UI. It now exposes the browser's camera
controls, still capture, lens calibration, manual/CSV/5×5
bed mapping, workpiece detection, fiducial detection, SVG import/placement,
G-code generation/export, controller connection, diagnostics,
camera-pose parking, guarded execution, software stop, fine registration, and
independent holdout accuracy validation. The browser remains a legacy
single-SVG alternative, not a required setup surface.

During controller initialization the desktop displays **Connecting** and keeps
Connect, Disconnect, and Home / park unavailable until the settings check and
motor-release cleanup finish. Software Stop remains available. Do not interpret
an open serial port as a ready controller before this state completes.

For serial-hardware jobs, Preview's **START JOB** enters a guarded run path that
performs `M5`, Home, camera-pose parking, and an idle wait before arming and
streaming the job. A failed preflight blocks the run. This removes the need to
press **Home / park** manually before every job; it does not replace the
operator's laser-off origin/direction checks.

After a successful powered job, `machine.home_and_release_after_powered_job`
keeps the job in its running state while the controller acknowledges `M5`,
drains all accepted toolpath motion, homes, returns to the configured camera
pose, waits for the park move to finish, and releases the motors. It does not
send fan or coolant commands. The default is enabled. The Laser panel labels
the drain, home, park, and release phases; a completion-command failure also
raises a one-time desktop error. The engraving may already be complete, so
inspect the controller log and machine state before retrying. Zero-power jobs, stopped
or failed jobs, emergency actions, and disconnects skip the additional homing
and parking motion.

GRBL continuous hold (`$1=255`) is used only around a camera capture. Normal
cleanup restores the value that preceded the capture. If an interrupted run
leaves `255` persisted across a controller restart, the next serial connection
restores `machine.grbl_step_idle_delay_ms`, which defaults to 250 ms. This
recovery then explicitly releases the motors. Connection requests motor release
even when `$1` was already finite, because driver state at process startup is
not trusted. This recovery changes no fan setting and sends no laser-enable
command.
