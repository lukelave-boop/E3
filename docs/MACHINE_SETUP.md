# Native machine setup

> **Operator directions:** Follow the packaged
> [Permanent Camera Setup Runbook](../laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md).
> It is the canonical current-version five-tab sequence. This document explains
> implementation details and optional diagnostics; it is not a competing step
> order.

Open **Tools > Machine Setup…** in the desktop application. The Camera panel's
**Calibrate lens…** and **Bed alignment…** buttons open the same window at the
relevant step. The dialog uses the shared `AppContext`; it does not start a web
server or create a second camera owner.

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

All five setup pages scroll inside the center of the dialog when their controls
need more room. The safety warning, machine connection and STOP controls, tabs,
operation status, and Close action therefore remain visible at compact window
sizes.

Starting a new Home / park, lens solve, registration capture, or validation
capture immediately clears its prior review and disables every related Apply
action. A failure or software stop leaves that prior result invalid rather than
restoring it. A result that arrives after STOP is discarded.

## 1. Camera

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
- In simulation, select the perspective-bed, checkerboard, or workpiece scene.

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
queued, repeated timer refreshes remain silent, and the dialog opens Machine
Setup at Lens when no accepted lens model exists or at Bed mapping when the lens
model is already accepted. The fresh keyed base-map workflow remains automatic;
it does not require coordinate entry. A corrected-view processing failure while
the camera remains healthy is likewise reported as an overlay error, not as a
camera ownership failure. A visible overlay is cleared as soon as its mapping or
focus provenance changes. In-flight frames are bound to the exact lens and bed
models that produced them, so a late result from the prior calibration cannot be
displayed.

Mount the camera rigidly, independently of the moving bed and gantry. Changing
the mount, resolution, or focus invalidates alignment evidence and requires the
lens and bed calibrations to be checked again.

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

1. Rigidly restrain a clean sacrificial sheet that covers every displayed grid
   coordinate.
2. Enter a previously established visible-marking power,
   and choose **Prepare powered base-map job**. Review it, close Preview, and
   press **Start** to submit it through the normal guarded job path.
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
   This is a diagnostic for origin, scale, and crop errors; it never changes
   configuration automatically and does not prove laser reach or collision
   clearance. If it exposes a discrepancy, correct the configuration, restart,
   use a project with matching bounds, and repeat the fresh base map.
6. Optional: set **Detected ruler span** to the printed span (normally `190 mm`),
   choose **Detect ruler reference (3 hints)**, then click roughly near the first
   endpoint, shared ruler corner, and other endpoint. These are search hints,
   not measured coordinates. The software must detect the physical baselines
   and repeated 1 mm ticks, verify spacing/perpendicularity, and show the fitted
   magenta outline for review. A failed fit saves nothing. The result is a visual
   reference for the movable honeycomb. It is not calibration evidence and
   cannot modify or veto
   the bed map, Trace selection, template matching, generated paths, work area,
   guarded limits, preflight, arming, or execution. The laser-burned keyed map
   remains authoritative. Use **Clear visual reference** or re-detect after the
   honeycomb moves.

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

Machine Setup also remembers its window geometry, selected tab, simulation
scene, cross sizes, and marking speeds in the active application data directory.
Verified marking power deliberately returns to `0%` on every open so a previous
powered calibration value is never silently carried into another session.

On a bed-slinger, rigidly restrain the calibration surface or workpiece to the
moving bed. Motion of the surface relative to the bed invalidates both the
mapping and any alignment comparison made from its camera image.

## 4. Fine registration

Fine registration verifies the solved bed map using eight fresh crosses placed
between the common 5×5 grid locations. Use a clean sacrificial surface at the
calibrated material height and rigidly restrain it to the moving bed.

Each newly prepared session records the exact active homography and
residual-mesh revision. If either changes before capture, or an older session
lacks that identity, capture is rejected before Home / park and a new mark job
must be prepared and run.

1. Set the cross size and marking speed, then enter a visible-marking power that
   has already been verified
   for the material, and choose **Prepare powered mark job**. Review it, close
   Preview, and press **Start**; the one-use authorization is internal.
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

If neither correction passes, check bed/workpiece restraint, camera rigidity,
surface height, and redo the full bed mapping. **Reset fine translation** removes
only the bounded translation. Solving or reversing the base bed map clears all
fine registration.

The fine translation belongs to camera-to-machine registration. It is not a
laser-head mounting offset and does not modify `laser.spot_offset_x_mm` or
`laser.spot_offset_y_mm`.

### Dense local correction

If the remaining error changes by bed position after the homography and fine
translation are stable, use **Dense local correction (5 × 5)**. Review the
exact powered 25-cross Preview, then run it on a clean, restrained sacrificial
surface at calibration height. The reviewed mesh is a bounded
nonlinear residual layer over the existing map and can be reset independently.

The mesh is applied consistently to camera-to-machine conversion,
machine-to-camera placement, and image rectification. It rejects missing or
low-confidence marks, corrections over 3 mm, and abrupt local distortion.
After applying it, use the 4 × 4 mesh check on **Accuracy validation**. Its 16
interstitial marks were not used for fitting; passing requires at most 0.30 mm
RMS and 0.60 mm maximum error.

If that first independent check has a coherent bounded residual, **Apply
reviewed validation refinement** becomes available. This applies one guarded
update to the existing mesh; it cannot be repeated against the same mesh. Use a
new sheet, the clean reverse side, or another clean restrained surface before
running **Prepare powered shifted confirmation**. The shifted 16 positions
are different from both the original 25 fit marks and the 16 refinement marks.
Only that fresh confirmation result is the final accuracy measurement.

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

The validation session is likewise bound to the exact active homography and
residual-mesh revision and fails before motion when that map has changed.

1. Put a clean sacrificial surface at the calibrated height and rigidly restrain
   it to the moving bed.
2. Enter a previously verified visible-marking power, choose
   **Prepare powered validation job**, review it, close Preview, and press
   **Start**; the one-use authorization is internal.
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

## Browser parity

The desktop is the primary operator UI. It now exposes the browser's camera
controls, still capture, synthetic scenes, lens calibration, manual/CSV/5×5
bed mapping, workpiece detection, fiducial detection, SVG import/placement,
G-code generation/export, controller connection, diagnostics,
camera-pose parking, guarded execution, software stop, fine registration, and
independent holdout accuracy validation. The browser remains a legacy
single-SVG alternative, not a required setup surface.

During controller initialization the desktop displays **Connecting** and keeps
Connect, Disconnect, and Home / park unavailable until the settings check and
motor-release cleanup finish. Software Stop remains available. Do not interpret
an open serial port as a ready controller before this state completes.

For serial-hardware jobs, desktop **Start** performs `M5`, Home, camera-pose
parking, and an idle wait before arming and streaming the job. A failed
preflight blocks the run. This removes the need to press **Home / park**
manually before every job; it does not replace the operator's laser-off
origin/direction checks.

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
