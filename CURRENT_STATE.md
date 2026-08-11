# Current repository state

This file records implementation and verification evidence. It is not an
operator procedure. Follow the canonical
[Permanent Camera Setup Runbook](laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md)
for the current five-tab sequence.

Snapshot: **2026-08-11**

The cutting-template designer now authors twelve semantic shapes through one
Qt-independent geometry vocabulary: rectangle, rounded rectangle, circle,
ellipse, capsule, triangle, diamond, regular polygon, star, one-flat circle,
two-flat circle, and washer. New `shape_grid` recipes retain row-major identity
and bounding-box spacing; legacy `rectangle_grid` version-1 templates migrate
as rounded rectangles. Washer OD/ID is stored as one logical compound object,
and containment-aware vector ordering cuts nested closed contours deepest-first
before nearest-travel optimization. Template features carry semantic shape and
optional hole ratio, while legacy/unknown matching retains geometric fallback.
Direct Trace classification now recognizes high-confidence circle, ellipse,
triangle, regular-polygon, and washer silhouettes without forcing weak
candidates. Washer recognition uses parent/child contour hierarchy only from
filled-region masks and requires independent circular residual, circularity,
diameter-ratio, and strict concentricity gates; its proposed vector retains
both contours as one semantic object.
The Trace **Geometry output** selector now names this mode **Best-fit analytic
shapes** rather than the obsolete **Fitted rounded rectangles**, and recognized
washer rows display their fitted outer and inner diameters explicitly.
Repeated-grid review now flags direct cells whose observed rotation/dimensions
or repaired center disagree materially with the shared family as **damaged?**.
It also compares shrunken-interior intensity variation and edge density across
the current grid, marks strong exposed-bed outliers **likely cut/open**, and
leaves both categories unchecked while retaining their proposed traces. This
is a conservative current-sheet texture comparison, not a persistent learned
honeycomb appearance model, and has synthetic/offscreen rather than real-C920
verification.
Automated geometry, migration, UI, matching, Trace, and toolpath tests cover
the implementation. No generated shape or containment ordering has been run on
physical hardware.

Per-operation and material-preset Power Correction is implemented as a bounded,
material-specific commanded-power bias layered over GRBL `M4`. Vector paths use
turn-angle severity and the configured acceleration model to add at most three
collinear ramp blocks on each side of a real junction. Raster correction first
credits laser-off overscan and changes image-area power only when that overscan
is shorter than the modeled braking distance. Zero correction retains the prior
program shape; raw G-code keeps `M4`, laser-off rapid travel, guarded inline `S`
on `G1`, and final `M5`. Projects and migrated material databases default both
new values to zero. Focused model, mapping, geometry, raster, exact-Preview, UI,
material, and guarded-stream tests pass. No corrected powered job has been run
on hardware. The platform-neutral suite passes 1,448 tests with the 103
loopback-server security tests run separately outside the socket-restricted
sandbox; all 1,551 tests pass. Repository-wide Ruff checks also pass.

On 2026-08-11, a read-only `$$` query against the connected controller reported
`$120=500.000` and `$121=500.000` mm/s², matching the configured
`laser.preview_acceleration_mm_s2=500`. It also reported `$110/$111=10000`
mm/min, `$30=1000`, and `$32=1`. Controller/firmware identity and physical
acceleration were not independently measured, so this is stored-setting
readback rather than physical verification.

Exact Job Preview operation rows now translate generated powered-motion feed
into mm/s and percentage of the configured work-feed limit, independently from
the requested percentage/controller `S` power pair. The live move readout uses
the configured travel limit for rapid moves and work limit otherwise. Values
come from the immutable parsed G-code plan; this is a review display change and
does not modify generated or submitted controller output, whose required `F`
words remain intact.

All editable desktop dimensional spin boxes now accept explicit metric or
imperial values while continuing to return canonical millimetres to project,
calibration, bounds, and machine code. This includes lengths, areas, feed
rates, template/grid geometry, Trace settings, material/layer settings,
Machine Setup values, and editable jog distance. The browser's coordinate,
size, calibration-point, feed-rate, and coordinate-CSV inputs provide the same
conversion. Focused unit/parser and affected desktop widget suites pass; no
controller, calibration, or G-code storage schema was changed.

Powered Machine Setup calibration jobs now hand off automatically to their
matching Home / park precision capture and scoring operation after successful
completion. The handoff is bound to the exact prepared filename and is cleared
when a job fails, stops, or is replaced; ordinary project jobs are unchanged.
Desktop **Start** no longer shows a powered-job warning or typed arming phrase;
it creates the exact program's one-use temporary authorization internally and
submits the prepared job immediately.

Trace review uses fixed-screen-size, high-contrast numbered badges so object
IDs remain readable over detailed camera imagery. A tri-state **Select /
deselect all** checkbox reflects none, mixed, and complete selection and can
change the whole detection list in one action. The focused desktop panel and
workspace widget suites pass 45 tests for the current implementation. Loose
identical-cell grids also repair only the affected center axis when a missed
edge makes one observed cell materially narrower or shorter than the repeated
size; unaffected placement and rotation remain independently observed.

Branch: **`desktop-v1`**

The Linux-machine work through **`15c2c7a`** was preserved and pushed before
the precision-camera feature commit **`99450df`** was integrated by cherry-pick.
The current branch also includes the later calibration, trace, persistence,
transport, and release-hardening work described below; use Git history rather
than this status document as the authoritative source revision.

Baseline before consolidation: **`778532b` — Polish desktop controls and add camera focus workflow**

Consolidated feature commits:

- **`ccac7c2` — Add multi-object camera tracing**
- **`e091e82` — Add batch object insertion command**
- **`c54b143` — Add desktop camera trace workflow**
- **`421091a` — Add offline trace inspection tool**
- **`f42511a` — Exclude local trace artifacts from releases**

Release metadata: **`0.2.0.dev0` (alpha-stage development build)**

This document describes the branch, not only the last release. Update it
when verification, platform support, known gaps, or active feature work changes.

## Repository status at this snapshot

The integrated feature work adds precision multi-frame camera capture
for clarity-sensitive analysis. Fine registration and accuracy validation now
wait for settling, discard buffered frames, require unique fresh frames,
aggregate subpixel cross centers with median/MAD rejection, and reject excessive
temporal jitter. Camera controls are reapplied and V4L2 values are read back
where available. Persisted reports expose frame sequences, sharpness, control
status, inlier/outlier counts, and per-mark jitter. Machine Setup offers a
guarded no-home recapture after the camera pose has been established, allowing
camera/detection variation to be distinguished from homing/pose variation.
Trace, matching, workspace capture, and calibration stills use a stable sharp
frame; continuous preview and streaming remain immediate single-frame paths.

Camera acquisition now has an explicit single-owner contract. Precision bursts,
V4L2 control changes, restarts, and synthetic scene changes cannot overlap;
ordinary preview snapshots remain available as copies of immutable published
frames. Shutdown invalidates the active generation, releases a blocked backend
before joining its reader, and prevents late control or capture results from
being published. The precision timeout now covers control reapplication,
settling, discarded frames, and samples after ownership is acquired. Diagnostics
report negotiated/observed FPS and sequence gaps, and manual-focus readback plus
its fresh post-settle scoring frame are one owned operation. Parked precision
and Trace captures now end the temporary motor hold immediately after the final
raw frame; lens correction and clarity scoring run afterward. A local 45-frame
1080p benchmark measured roughly 0.73 seconds for clarity scoring that no longer
extends the hold. Forty-five focused
camera/control/precision/application tests cover concurrent bursts, preview
contention, restart and shutdown cancellation, blocked reads, bounded V4L2
timeouts, frame-copy ownership, acquisition-deadline semantics, deferred
post-hold scoring, and restart rejection during deferred analysis. Real C920
backend release timing, negotiated-mode reporting, sustained delivery, and
control settling remain physically unverified.

The Camera panel also provides a non-destructive **Test focus range…** operation.
It serially applies each manual value, records three fresh post-settle sharpness
scores, ranks their medians, and restores the original focus/autofocus controls
without saving configuration or invalidating calibration. This permits focus
comparison against an unchanged scene and calibration. Applying or saving a new
final focus remains an optical change and still requires lens and bed
recalibration before precision placement. The sweep lifecycle and result UI are
automated-test verified; physical C920 sweep repeatability remains unverified.

Calibration state is stored in optical profiles keyed by configured camera
resolution and locked manual-focus value. Saving a new focus selects that
profile on the next restart; its lens captures/model, bed map, registration and
validation sessions, and honeycomb reference remain separate. Returning to a
previously calibrated focus restores that stack. Existing unprofiled data is
copied once into the profile inferred from recorded bed-map camera provenance,
with the legacy source retained. Profile selection does not detect a camera
remount or work-plane-height change; those still require fresh calibration.

Machine Setup now has a dedicated fresh keyed 5×5 base-bed mapping workflow for
a remounted camera. It generates 23 regular crosses plus two larger interior
orientation keys through the ordinary zero-power Preview and guarded powered-job path;
no old homography or manual image/machine point entry is required. Unseeded blob
and symmetric-grid detection uses the two keys to resolve all eight grid
rotations/reflections, rejects incomplete, unkeyed, ambiguous, duplicate,
zero-power, stale, or altered sessions, and requires all 25 RANSAC inliers within `0.50 mm`
RMS and `0.80 mm` maximum fit error. Candidate review does not mutate the active
map. Accepted points and the homography are persisted transactionally with
rollback, clear corrections tied to the old map, and record the unambiguous
generated controller-coordinate labels as normal on both axes. Synthetic
rotations/reflections, generation safety, session rejection,
hold/Home/park/release ordering, and transaction rollback are automated-test
covered.

On 2026-08-09 the fresh base workflow was physically exercised with the C920 at
`1920 x 1080`, the configured GRBL serial profile, X/Y work bounds `10..210 mm`,
a 5 mm laser boundary margin, centers `40,75,110,145,180 mm`, `S400`/40% marking
power, and 1200 mm/min. The saved capture detected 25/25 keyed marks with 25/25
RANSAC inliers, `0.1136 mm` RMS fit error, and `0.2685 mm` maximum error; the
operator reviewed the numbered overlay and applied that base map. The already
running pre-change process stored null axis-label metadata, but the keyed map
itself is applied and does not need to be repeated. Future fresh keyed installs
record normal label metadata directly. Exact controller/firmware identity was
not recorded, so this is physical workflow evidence rather than verification of
a named hardware/firmware configuration or a safety claim.

Later on 2026-08-09, guarded laser-off jogging was physically exercised after
Home / park. The operator designated controller coordinate **X245 mm** as the
mechanical X maximum after successfully reaching it without contacting the hard
stop; the carriage had already moved beyond the complete 190 mm honeycomb
support. Direction was then clarified against the configured Home / park pose
`X15 Y195`: **X−10/X+230 mm** established the selected X5..245 envelope, and
**Y−190/Y+20 mm** established the selected Y5..215 envelope. Y−200 reached the
negative endstop near controller Y−5, while Y−190 retained the operator's 10 mm
clearance. The earlier Y−90/Y105 report was a transcription error and is
superseded. These are measured mechanical-jog reaches only. They are not the
camera/calibration rectangle or authorization for laser output. The local
machine work area remains the physically calibrated X10..210, Y10..210 rectangle;
with the 5 mm laser boundary margin and zero spot offset, guarded output remains
X15..205, Y15..205. Jogging intentionally permits travel beyond that configured
rectangle so the mechanical envelope can be measured without expanding the
camera crop, base-map target layout, or laser-output bounds.

One packaged, versioned Permanent Camera Setup Guide is now the canonical
operator sequence. It is available from the Machine Setup footer and the main
Help menu, opens modelessly at the current numbered tab, and explicitly covers
the Preview-to-main-Start handoff. It warns that Preview Play only animates and
that main **Generate** replaces a prepared calibration job.
Automated UI/content/package tests keep its five tab headings and exact action
labels synchronized with the application.

Step 3 can also record an optional vision-detected outline of the movable
honeycomb rulers after a ruler-overlay capture. Three rough clicks only seed
search corridors; fitted ruler baselines and verified repeated tick marks
define the saved reference, and an unverified fit saves nothing. This remains
separate from calibration and is shown only as a magenta visual reference.
While those three hints are being placed, the picker shows the clean captured
frame rather than the diagnostic coordinate overlay. Cursor-centered wheel zoom,
middle/right-button panning, and double-click-to-fit improve placement without
changing the source-image coordinates supplied to detection.
After detection, each ruler's bed-map-measured span must agree with the entered
physical span within 2 mm (or 1 percent for larger rulers); a poorer hinted fit
is rejected before it can replace the saved visual reference.
Recording or clearing it does not mutate the laser-burned keyed bed map,
configured work area, guarded laser limits, Trace selection, template evidence,
generated paths, preflight, arming, or execution. Automated tests freeze and
compare the active calibration and output-review state across detection and
recording.

The desktop Machine panel now exposes separately tested incremental XY jogging.
Home / park establishes the only accepted starting pose; each request is
translated to an absolute `G0` bracketed by an initial `M5`, explicit `G21` and
`G90`, a configured travel-feed ceiling, and a planner-completion barrier.
The configured work-area rectangle is intentionally not applied to jogs because
jogging is the operator control used to measure the physical travel envelope.
STOP, disconnect, jobs, controller uncertainty, and motor release invalidate
the tracked jog position, and jogging is unavailable while armed or busy.
Automated tests cover repeated and beyond-configured-area moves, numeric/feed
rejection, UI gating, and a STOP/ACK race. Direction and the selected mechanical
endpoints were exercised on the active GRBL machine; STOP/reconnect behavior
was not physically measured during that session.

The native default workspace now gives Cuts/Layers and its related design tabs
a full-height right column. A short row beneath the canvas places a narrow raw
G-code dock on the left and the wider Laser/Machine/Material Library tabs beside
it. All three regions remain resizable Qt docks. The dock-state contract is
versioned as `v6`: compatible older window geometry and active-tab choices may
migrate, but the obsolete `v5` dock topology is not restored. Raw G-code starts
visible, Console remains optional in the same compact slot, and Reset workspace
layout restores the new arrangement. This topology is covered by an offscreen
production-dock geometry test. Compact `1080x780` and `900x680` layouts with
13 pt text retain their requested size, keep the canvas and all docks disjoint,
and expose every inspector without horizontal clipping. Operation numeric
fields commit once on editing completion instead of rebuilding project panels
for each typed digit. Interactive desktop review remains pending.

The parked-bed default requests 45 unique frames within eight seconds. This
fits the configured 15 fps deadline (and the 10 fps synthetic source) with
scheduling margin while bounding a 1080p raw burst to roughly 267 MiB.
Median/MAD and jitter analysis screens the full burst; frames that remain
inliers for every mark are ranked for clarity, and final coordinates use the
per-mark median of the best 15 stable frames. Diagnostics persist the consensus
indices and sharpest representative frame. The prior full-inlier median and
single-sharpest-frame behaviors remain configurable. The operator reported the
single-frame experiment initially improving average RMS from about `0.20 mm`
to about `0.12 mm`, but three subsequent home-first 4×4 captures varied from
`0.457` through `0.169 mm` RMS. The consensus default is therefore automated-
test verified only and now needs the same physical repeatability comparison.

GRBL parked-bed calibration captures now scope motor holding to the measurement
window. The service reads and saves `$1`, selects `$1=255` before Home / park,
keeps both axes energized through the final precision frame, and restores the
saved idle delay before CPU-side image analysis. Restoration on capture failure,
repair of a stale continuous-hold configuration, refusal to guess a missing
`$1` before capture, and simulator no-op behavior are automated-test covered. The
physical controller was read-only probed with `$1=250`; the temporary override
was observed holding the axes, but restoring `$1=250` followed by a planner
dwell—and a subsequent `$1=0` forced-idle experiment—did not de-energize them.
The revised release path restores `$1`, then uses FluidNC's explicit `$MD`
motor-disable command when supported. A rejected `$MD` falls back to standard
GRBL `$SLP` and the required soft reset. Either path invalidates the coordinate
reference because loose axes can move, so the next hardware operation must
home. This explicit-disable revision is automated-test verified and its
physical X/Y release remains to be checked.
The first physical `$SLP` release succeeded, but the next Detect action exposed
the documented post-reset alarm as `M5 error:9`. The fallback now clears that
expected alarm after reset, and Home / park can also recover from this exact
pre-home error by unlocking, reissuing `M5`, and immediately running mandatory
homing. Unrelated errors are not suppressed. Repeat detection remains to be
physically rechecked with this recovery. Every new serial GRBL connection sends
`M5` and checks `$1`. If `$1=255` persisted, it restores the configured finite
idle delay; if `$1` cannot be read, connection performs that best-effort finite
restore and then fails clearly instead of trusting unknown state. Ordinary
connection no longer sends `$SLP` plus a soft reset merely to release
already-idle motors, avoiding the controller's audible reset announcement.
Explicit release after powered jobs and held captures retains the `$MD` or
`$SLP`/reset fallback. An abnormal process or power loss can still interrupt
restoration and warrants checking `$1`.

Connect initialization, individual command/ack exchanges, complete Home / park
sequences, and scoped camera holds now have exclusive ownership of the serial
reply stream. The desktop reports **Connecting** and keeps Connect, Disconnect,
and Home / park disabled until initialization finishes; software Stop remains
available. This fixes a physically observed race in which Home / park consumed
the `$1` line from Connect's `$$` query, producing a false missing-setting error
followed by a false `G21 error:2`. Annotated, spaced, and integral-decimal `$1`
reports are also accepted. The race is automated-test reproduced and fixed;
the physical Connect-then-Home / park sequence still needs rechecking.

Controller-required actions now attempt connection themselves when the
controller is offline. This covers desktop jobs, Home / park, jogging,
diagnostics, all parked Machine Setup captures, and the equivalent HTTP command,
positioning, arming, and run routes. A prepared job therefore keeps **Start**
available. Connection failure is reported as the operation failure and prevents
the requested action; STOP generation invalidation prevents queued work from
reconnecting and continuing. An untrusted connection after STOP or an uncertain
acknowledgement remains blocked until the explicit reconnect path replaces it.

`tools/live_desktop_driver.py` provides a narrow live Qt diagnostic driver for
named status, Connect, Home / park, and camera-frame operations. It always starts
the real runtime with hardware access enabled and process-wide laser lockout;
there is no option to permit positive laser output or submit arbitrary G-code.
Optional screenshots and JSON output make the exercised live state available
for repeatable diagnosis.

Normalized repeated-object tracing now estimates its shared rounded-rectangle
rotation from populated row-center baselines, then refits regular spacing in
that orientation. This avoids amplifying small `minAreaRect` angle errors from
blurred rounded silhouettes. A separate grid-pose toggle allows direct cells
to retain their observed center and rotation while still sharing canonical
width, height, and corner radius; inferred cells remain snapped to the lattice.
Both modes are automated-test verified and await comparison on the physical
label sheet.

Desktop **Detect objects** no longer assumes a prior Home / park. For a live
machine it starts the temporary stepper hold, homes and parks, selects a fresh
interactive stable frame, restores the original GRBL idle behavior, then
rectifies and analyzes after release. A frozen simulator workspace remains an
immediate copy-only path. The sequencing and simulator bypass are automated-
test verified; after explicit motor release the UI intentionally returns to
**HOME REQUIRED**. The complete physical trace cycle remains to be exercised.

Dense calibration now persists the 5×5 fit, 4×4 interstitial validation, and
shifted confirmation as separate sessions. Each capture action explicitly
selects its matching session, preventing a later 4×4 preparation from making
the 5×5 capture reuse 4×4 targets. This repair is automated-test verified and
was subsequently exercised against the restrained physical marked sheet.

Shifted-confirmation preparation now records mutually exclusive confirmation
metadata. The earlier UI path incorrectly set both validation and confirmation,
causing the matching capture action to reject the job it had just prepared.
Shifted confirmation also uses a narrow 14-pixel seeded search and rejects any
detection shifted more than 10 pixels from its predicted location. The earlier
broad search visibly selected older neighboring grid marks and reported false
errors as large as 16 mm.
Attempting to rescore the original interstitial marks after applying their
refinement now explains that those marks are the refinement's input rather than
an independent check and directs the operator to the shifted-confirmation
capture action by its exact label.

Dense review semantics now distinguish unreliable detections from an accepted
single inferred node. Invalid results expose `rejected_ids`, render those cells
red as REJECTED, and report no inferred IDs; amber INFERRED is used only when
exactly one excluded cell is safely reconstructed and the complete fit passes
all application gates.

The same 2026-08-09 physical setup subsequently completed the current fine and
holdout sequence. The eight-mark fine capture reported an `0.342 mm` translation
scatter and an 8/8-inlier full-bed candidate at `0.136 mm` in-sample RMS. The
independent five-point capture passed at `0.283 mm` RMS, `0.385 mm` maximum,
and mean error X `-0.087`, Y `-0.013 mm`. After the dense results recorded above,
the operator also cut a label perimeter that visually tracked the printed
outline closely. The photograph is useful workflow evidence but was not a
metrology setup, so it does not establish a general physical accuracy limit.

Interactive clarity-sensitive operations are separated from the 45-frame
parked-bed calibration profile. Trace, template matching, color sampling,
workspace capture, and ordinary stable stills now select the sharpest of five
fresh frames after a 0.1-second settle and two-frame discard, with a two-second
deadline. This removes the calibration-burst delay from interactive work while
keeping a small sharpness selection step. It is automated-test verified but its
perceived latency has not yet been timed in the physical UI.

The final 2026-08-09 adversarial software audit hardened the boundaries shared
by those workflows. Machine ownership now serializes reconnect, Home, jog, arm,
and STOP generations; serial reopen discards old acknowledgement state and
bounds receive data; motion-only completion drains the controller planner; and
validated receipt/program integrity is rechecked at the hazardous execution
boundary. Configuration, HTTP requests, project/material/template documents,
SVG, G-code inputs, images, camera frames, calibration evidence, and vision
options now reject duplicate keys, nonstandard constants, coerced types,
non-finite values, malformed containers/topology, and oversized resources before
side effects. Calibration state persists before publication, and generated
G-code/captures use exclusive collision-resistant publication. Release ZIPs use
only tracked regular files, reject every symlink component and checkout escape,
and derive their version from canonical package metadata. Automatic post-job
calibration scoring is bound to the exact start receipt and program digest, so a
stale or merely same-named completed job cannot trigger capture.

The integrated Linux checkout passes all 1476 automated tests. Precision capture
is covered for genuinely fresh unique frames, configurable settling/discard and
burst counts, camera-control readback, sharp-frame selection, temporal
median/MAD rejection, jitter limits, persisted diagnostics, home-first capture,
and guarded no-home recapture. Real C920/V4L2 control readback, capture timing,
vibration settling, jitter thresholds, homing repeatability, and physical
fine-alignment accuracy remain to be verified on the laser machine before these
defaults are treated as proven hardware settings.

Successful powered serial jobs now remain active through cancellation-aware
extended command acknowledgements, a final `M5`, a pre-home planner-completion
barrier, automatic homing, a bounded absolute move to the configured
photography pose, a second motion-completion barrier, normal-idle restoration,
and explicit motor release. They do not change fan/coolant state. Zero-power
jobs and every stop, failure, emergency, and disconnect path skip the additional homing
and parking motion. The Laser panel distinguishes drain, home, park, and release
phases after stream progress reaches 100%; a terminal background-job error now
raises one desktop alert and is also copied into the in-app machine log.
Every GRBL connection explicitly releases the motors; connection and camera
cleanup repair a persisted camera-only `$1=255` to configured
`machine.grbl_step_idle_delay_ms` (250 ms by default), preventing a stale camera
hold from becoming normal power-on behavior. Tests cover delayed final `M5`
acknowledgement, planner-busy homing rejection until the new barrier, successful
parking, home and park failures, failed powered streams, FluidNC `$MD`, and the
`$SLP`/reset fallback without issuing fan/coolant commands. On 2026-08-08 the
operator twice observed the head remain at the last powered point without
homing or parking. Process start time, source timestamps, the loaded profile,
and the generated `M4 S100` job rule out the previously suspected stale process
or disabled completion setting. The prior 3-second job acknowledgement timeout
could expire while a synchronized final `M5` drained motion, and `$H` had no
explicit pre-home planner barrier; test doubles reproduce both paths and their
fixes. The exact physical controller error was not captured, so post-job
homing, parking, and explicit release still require a no-power hardware recheck
before being recorded as physically verified.

The final MachineService boundary now rejects non-exact backend and boolean
hardware/motion gates, non-finite or excessive feeds, persistent normal
`$1=255`, and arm timeouts outside 1–600 seconds even when settings bypass the
configuration loader or are mutated after construction. Both Arm and Start
re-parse the exact immutable program lines and recompute their digest,
motion/power flags, and full safety profile. Forged or altered preflight tokens
are rejected before transport output, while STOP, disarm, disconnect, failure,
and scoped motor-release cleanup retain an independent `M5` path. These
guarantees are adversarially automated-test verified; they are not a safety
rating or physical-controller verification.

The object-tracing implementation was consolidated into focused vision,
project-command, desktop-workflow, offline-tool, and artifact-policy commits.
The documentation was then reconciled with that state.

The subsequent Windows portability update selects POSIX serial lazily. Safe
browser and native desktop simulation now start on Windows without loading
`termios`; serial hardware remains unavailable there.

The current desktop update adds a simulation-only, memory-resident corrected
camera source. An operator can load a full-bed PNG/JPEG or generate a selected
template at a known pose, run the normal trace and alignment pipeline on the
frozen frame, and then restore the synthetic camera. This path is unavailable
when hardware access or a non-simulator machine backend is enabled.

The desktop now also owns a native Machine Setup workflow covering configured
camera controls, raw preview, checkerboard lens calibration, manual and
CSV-assisted bed mapping, 5×5 cross-grid detection, residual review, and
workpiece/fiducial checks. It now includes a separate eight-point fine-
registration stage that prepares zero-power or normally guarded powered cross jobs,
classifies multi-point residuals, and can apply only a reviewed global
camera-map translation within a 5 mm cumulative limit. Validated G-code can be
exported from the desktop. The browser remains available, but no operator
capability requires it.

The Machine Setup Lens tab now works against the live camera's exact resolution
and readiness state. It exposes every capture's detection, sharpness, coverage,
region, and exposure evidence; supports confirmed single/all evidence deletion;
and presents structured solve gates with per-view worst errors. Capture, solve,
and model clearing use the shared `AppContext` readiness/invalidation paths.
Cold legacy catalogs now probe only bounded image headers on the calling thread;
pending checkerboard feedback is indexed on a separate worker with detector
inputs capped at `640 x 360`, exact source dimensions retained, visible progress,
and deterministic evidence-mutation/Close guards. That index is advisory only:
the solve re-decodes and detects the selected originals at full resolution.
Index records now require strict finite/type-consistent fields and a SHA-256
content identity; malformed or legacy stat-only rows remain read-only during
status refresh and appear pending. Index and solve read each selected file into
one capped immutable encoded payload and derive the digest, dimensions, and
decoded pixels from those exact bytes. They check path identity around analysis
and compare a newly read final content signature before commit, including for a
same-size and same-mtime replacement. The Lens tab exposes **Re-index all
captures** whenever evidence exists so a ready-looking digest mismatch can be
recovered without deleting captures. Mixed-case PNG/JPEG extensions are
discovered deterministically with lossless same-stem preference. Fresh captures
are atomically staged as lossless PNG and measured through the identical bounded
index pipeline; the table displays exact measurement dimensions so unlike
scales are not presented as directly comparable.
The Camera summary also exposes observed and negotiated FPS.
Replacing or clearing the lens model visibly marks its dependent bed map stale
and disables registration and validation actions until remapping. Compact
offscreen interaction and stale-provenance transitions are automated-test
covered; hardware control readback and a physical diverse-view lens solve remain
unverified.

Machine Setup now runs Home / park, corrected and precision stills, checkerboard
capture and solve, registration/base/dense/validation captures, and workpiece
camera analysis outside the Qt GUI thread as a single owned operation. Its modal
window provides indeterminate progress and its own always-available software
STOP; competing setup actions and Close remain held until worker cleanup
completes. STOP-tainted results are discarded, and starting or failing a new
capture clears prior review/apply state. Offscreen tests cover event-loop
responsiveness, single-flight submission, GUI-thread presentation, STOP result
suppression, request-generation rejection of Home / park queued before STOP,
deferred close, and failure invalidation. Saved axis orientation is
now reported accurately even when bed provenance is stale, while mutation stays
gated on a valid map. Physical motion/camera verification remains pending.

Machine Setup additionally provides an implemented but not yet physically
verified 5×5 dense local-correction workflow for residual error that varies by
bed position. It preserves the current homography underneath, bounds node
movement and local gradients, maps consistently in both directions, and can be
removed independently. A 4×4 interstitial job validates positions not used for
fitting with 0.30 mm RMS and 0.60 mm maximum software gates.

One coherent failed interstitial result can now produce a reviewed, bounded,
one-time mesh refinement. The refinement is tied to the exact mesh revision and
cannot be applied twice. Final verification uses a separately generated shifted
16-point pattern on fresh material. On 2026-08-09 the physical 4×4 check passed
at `0.273 mm` RMS and `0.475 mm` maximum error, and the shifted confirmation
passed at `0.237 mm` RMS and `0.376 mm` maximum error. Those measurements verify
that session and surface, not general repeatability after a setup change.

Machine Setup now stores explicit X/Y mapping-orientation flags with the bed
calibration and presents unambiguous NORMAL/OFF and REVERSED/ON controls. Fresh
keyed maps record their generated labels as normal automatically. Legacy and
manually labeled maps may remain visibly unrecorded until the operator confirms
them after a laser-off direction check; that confirmation does not mirror
points. The direction/bounds check remains a pre-production hardware check and
does not block the Step 3-to-Step 4 calibration transition. Window geometry, selected tab,
simulation scene, cross sizes, and marking speeds persist in the active data
directory. Marking power intentionally resets to zero each time Setup opens.
All desktop checkbox-based boolean options now use the same compact gray-OFF,
green-ON switch presentation; the Machine Setup X/Y controls use those switches
instead of one-shot reversal buttons.

Machine Setup now also presents the shared controller connection state and a
Connect/Disconnect action above every calibration tab. It uses the existing
guarded `MachineService`; connecting does not bypass hardware authority,
motion, homing, or arming gates.

Fine-registration and accuracy-validation sessions now persist the exact bed
homography and residual-mesh revision active when their mark job is prepared.
Capture and offline analysis reject a legacy unidentified session or any map
change; the live capture path performs that check before entering Home / park or
the temporary motor hold. This prevents an old powered session from being
accepted after a fresh base map, translation, full-map refinement, or mesh
change.

Live camera-refresh errors are now latched before the first modal notification.
The operator acknowledges one Camera unavailable message; timer-driven repeats
remain silent until a frame succeeds or the operator explicitly selects Refresh
camera. Recovery clears the latch and posts a non-modal status notice. This
prevents a camera owned by another application from repeatedly stealing focus.
Explicit Refresh camera now distinguishes a healthy capture from an offline,
frame-less, or faulted one. A failed capture is released and the configured
V4L2 device is reopened asynchronously before the corrected image is retried.
An online camera with a stale, legacy, or otherwise untrusted bed map is now
reported separately as **Bed mapping required** rather than being wrapped in
the exclusive-camera warning. Invalid rectification is rejected before worker
submission, repeats are latched, and the recovery action opens Machine Setup at
Lens or Bed mapping according to the accepted lens-model state. Corrected-view
processing failures from an otherwise healthy camera have their own latched
overlay alert. Visible overlays are invalidated immediately by focus or mapping
changes, and every in-flight result is identity-bound to the exact lens and bed
models used to produce it; late results are discarded and one replacement is
queued after calibration review closes.

Trace color picking is an explicit canvas state and retains the sampled BGR/Lab
color for neutral targets. The 2026-08-09 physical C920 workflow then exposed a
new regression: Auto selected the bright horizontal seams between the two
columns of dark labels, reported them as a high-confidence `2 × 6` grid, and
placed their centers about half a row pitch from the actual objects. Replay of
that exact saved frame now selects filled global/adaptive contrast hypotheses,
returns 14 observed full label bodies plus the two genuinely occluded top-left
cells as a `2 × 8` grid, and fits about `80.54 × 21.52 mm` geometry. The overlay
edge error on that frame is 0.25 mm median and 0.75 mm at the 90th percentile.
Synthetic adversaries cover dark/light polarity, severe gradients, low
contrast, internal opposite-polarity highlight bands, missing cells, and pose
jitter. Grid numbering is stable row-major. Trace now distinguishes the
configured camera/work crop from the smaller guarded laser-output area after
boundary margin and laser-spot-offset intersection. On the exact saved frame,
all raw right-column observations end inside the X210 camera crop at
X209.75..209.99; shared identical-cell sizing alone moves them at most 0.32 mm
past that crop. The configured 5 mm guard is stricter: ten fitted cells cross
the X/Y15..205 output area by up to 5.32 mm right, 5.51 mm top, and 1.76 mm
bottom, while eight observed contours touch the right/top raster edge and may
be cropped. Those cells remain visible in red and unchecked. Template matching
also excludes them; exact-frame replay retains only six safe detections and
rejects the resulting 37.5% feature coverage instead of accepting a false
16/16 alignment. Fitted rounded-rectangle output now retains the existing
uniform border offset as its default and also supports independent offsets for
the rotated object's Top, Right, Bottom, and Left edges. A one-edge adjustment
moves that edge and its adjoining corners without moving the opposite edge;
focused core and offscreen panel regressions cover rotated top-only trimming,
mode applicability, and option validation.
Trace object creation now defaults to replacing objects created by earlier
Trace captures as one undoable document operation. This prevents a completed
physical workpiece from remaining in the next generated project job, while
preserving drawings, imported objects, and all other non-Trace content. The
operator can disable replacement to accumulate batches intentionally, and the
temporary overlay action is explicitly distinguished from project-object
removal.

Machine Setup Step 3 now provides a parked work-area ruler reference. It holds
the steppers through Home / park and the final raw frame, releases them before
scoring and annotation, then overlays a 10 mm machine grid, orange camera/work
boundary, and green margin/spot-offset-aware output boundary. Full-size Qt
swatches and 40 mm coordinate labels remain readable at 900x680 and 1080x780.
The reference never changes configuration automatically and explicitly keeps
movable honeycomb support separate from calibrated camera crop and verified
laser reach. The repaired trace and ruler overlay have been reviewed against
the real frame, but the created-object trace has not yet been physically cut.

Home/park setup and park commands now receive a scoped minimum six-second
acknowledgement window for the slow controller observed in the real workflow.
Homing and park-completion waits remain 120 seconds; ordinary streamed job
lines retain the configured `machine.read_timeout`. The first implementation
still required a GRBL realtime `<Idle...>` report after `$H`; the physical
controller completed its double-touch homing cycle but did not provide the
expected report, first exposing a later six-second setup timeout and then the
120-second idle timeout. Home/park now continues from the `$H` acknowledgement
received after the endstop sequence and sends `G4 P0.01` after the park move as
a short positive planner-synchronization dwell. Fine-registration and
accuracy-validation analysis also allows six seconds for the physical bed to
settle after Home/park returns and then waits for three additional frames, with
a separate six-second freshness timeout. This excludes both cached pre-motion
images and fresh frames captured while the slow bed is still completing its
queued park move. This correction is automated-test verified
but still requires a repeated physical Home/park check.

GRBL Home/park now captures the active workspace and the active `G54`-`G59`
and `G92` offsets using read-only `$G`/`$#` queries. Absolute-motion jobs
re-query that state immediately before streaming and are rejected if it changed
after the camera-position reference was established. The snapshot is exposed
in machine status and logged for diagnosis. Focused simulator-backed serial
tests cover unchanged-state acceptance and changed-offset rejection; real
Falcon responses and a physical rejection test remain unverified.

The native window title now begins with the application name, package version,
and a short fingerprint of the installed application files before the project
name. This makes restarted source builds visibly distinguishable; release
packaging can override the fingerprint with
`E3_POSITIONING_SYSTEM_REVISION`. Its application display name is intentionally
empty because Qt/X11 otherwise appends a duplicate product-name suffix to the
complete native caption; the application name itself remains configured.

The desktop now opens a dedicated graphical Preview after project generation,
zero-power framing, and registration/validation job preparation. An immutable
`JobPlan` is parsed from the exact finalized G-code stream and retains
controller-ignored layer/pass/source context, physical laser-spot coordinates,
per-move timing/feed/power, and cut/travel statistics. The Preview provides
scrubbing, animated playback, display-only travel/power/inversion controls,
live move details, warnings, fit/pan/zoom, PNG export, per-operation visibility
and statistics, keyboard timeline navigation, and a dynamic generated-layer
legend. Generation can use source or nearest-path order, and Preview reports
the latter's rapid-travel savings against source order. Raster rows are kept in
fixed serpentine order so individual dark islands cannot be reordered or given
separate acceleration cycles. Imported images are alpha-composited onto white,
sampled at the configured exact physical pitch and absolute machine-coordinate
scan angle, and converted with deterministic 8x8 grayscale dithering. Source
minification is area-prefiltered before affine sampling so high-frequency detail
does not depend on source pixel phase or resolution. Source top/mirror/rotation
orientation matches the canvas. Full-row lead-in, white
gaps, and lead-out remain laser-off at engraving feed; desired and spot-corrected
motion are bounds checked. A shared PNG/JPEG/BMP contract bounds encoded files,
dimensions, bit depth, channels, and a conservative 16 MiB decoded footprint
before decode; row/sample/vector-edge/span work and 250,000-command jobs are
also bounded. TIFF is intentionally rejected rather than depending on an
optional Qt image plugin. Generated project jobs carry SHA-256 identities for
their external raster sources, with a Qt-free verifier available to block a
changed or moved asset. Image-only and mixed projects now include
transformed image bounds in zero-power framing, and zero-power raster cut distance
matches the exact unpowered plan for raster, fill, and line output. Nearest-path
ordering falls back to recorded source order above 512 vector paths instead
of entering quadratic planning.
Closed-vector fill, binary vector raster, raster overscan, and configurable
acceleration/command-delay time estimation feed the same exact program model.
A confirmation-gated **Prepare Start Here** action creates a new
bounded absolute-mm program at a reviewed move boundary without starting the
machine. The replacement records the configured controller photography pose,
and its exact plan includes the laser-off physical-spot approach to the selected
boundary after Home/park. Prepared maximum
power is displayed independently from controller execution progress, fixing
the idle-polling presentation that previously replaced a generated power value
with `no active controller job / 0%`. Project revisions invalidate the plan and
close its Preview. The existing generation, homing, arming, validation, and
streaming path is unchanged.

Desktop exact-job preparation is now owner-tokened from snapshot through final
view construction. The project clone and all Qt-free planning/indexing run on
owned workers; authoring is held only while the live document is cloned, and
software STOP remains available. Raw G-code, workspace paths, dedicated Preview
paths, and large backward timeline jumps are built in bounded GUI-thread slices.
Closing an unfinished Preview, STOP, new/open, project revision, renderer error,
or application close cancels acceptance and cannot enable Run/export or clear a
newer renderer's busy state. Application shutdown defers runtime teardown until
owned workers return. Generation no longer writes an implicit G-code artifact;
explicit export is the only desktop file-write path. Offscreen tests cover 1k
and 100k early close, stale success/failure registration collisions, all three
renderer failure sites, zero-power framing, Start Here, project replacement/shutdown,
250k snapshot responsiveness, and 250k backward scrubbing.

Raster workspace items retain their exact payload-bound SHA across unrelated
document refreshes, eliminating repeated full-file hashes and decodes during
ordinary edits. Cache resolution is budgeted across all unique current project
sources; retained previews downscale as source pressure grows and reload from
their exact payload in cancellable one-source GUI slices when removals make a
larger resolution budget available.
If a source changes in place, the next generation compares its new job
identity with the displayed identity, refreshes the canvas from one exact stable
payload, rejects that result, and requires another Generate/Preview cycle before
Run or export. Tests cover stable-stat same-path mutation, one-read decode/hash
binding, five-source cache residency, zero-reread unrelated edits with a live Qt
heartbeat, sequential live-item/cache rebudgeting with quality recovery, and
first-generation rejection followed by reviewed acceptance. Start Here tests
cover photo-pose metadata, spot-offset approach parity, asset preservation, and
machine program preflight.

## Verified in the current Linux checkout

- **1476 tests passed in 374.33 seconds** with Qt using the offscreen platform,
  including the HTTP security tests with loopback socket access.
- A clean temporary wheel build produced
  `laser_camera_aligner-0.2.0.dev0-py3-none-any.whl` (440,290 bytes, SHA-256
  `0d54ead9f6269afa5205516e17dd019be39d7bc9d803f533fe4f90201256e3dc`).
  Its installed package was byte-identical to the live package tree, both
  entry-point help commands succeeded, packaged web/setup-guide resources were
  present, and the installed simulator produced a `1920 x 1080` frame.
- The tracked-manifest source ZIP contains 210 entries and passes `unzip -t`.
  It excludes local configuration, captures, calibration state, generated jobs,
  logs, caches, and build artifacts. All package/build/install outputs remained
  under `/tmp`; no repository-local artifact was produced.
- Exact-job Preview verification covers final-stream parsing, immutable move
  context, spot-offset recovery, powered-rapid warnings, time scrubbing,
  keyboard timeline navigation, operation visibility, planner comparison,
  fill/raster generation, area-prefiltered grayscale image dithering, alpha handling, canvas-
  consistent source orientation, absolute scan angles, exact non-integral pitch,
  contiguous serpentine rows, image/mixed framing, zero-power metrics, encoded
  and decoded source limits, content-identity invalidation, phase/resolution
  invariance, aggregate pre-iteration overscan/complexity rejection, and the
  large-vector planner fallback,
  guarded Start Here rebuilding, PNG rendering, and separation of prepared
  maximum power from controller progress. The complete dialog was also rendered
  and visually inspected offscreen at 1120 × 760. Interactive desktop use and
  real-hardware execution of this Preview revision remain unverified.
- The icon-only Job toolbar now renders Preview as an original monitor/toolpath
  glyph rather than falling back to the action text. The glyph was rendered and
  visually inspected at high resolution in addition to its Qt mapping test.
- Build-identity verification covers content-sensitive source revisions,
  sanitized packaging overrides, and build-first project window titles.
- Full `E3MainWindow` construction under Qt's offscreen platform displayed the
  build-first identity in the native title bar.
- Focused coordinate-reference verification covers rejection of serial motion
  and arming before homing, successful home/park acceptance, emergency-reset
  invalidation, desktop preflight ordering, X/Y mapping reversal, and the
  hardware/simulation status presentation.
- Focused Trace verification covers the complete button-to-canvas picker state,
  sampled neutral-color acceptance, configured maximum-area rejection, and
  contrast recovery of filled rounded rectangles on noisy wood-like images.
  It also covers global, corrected, adaptive, and signed contrast arbitration;
  bright inter-object seams and opposite-polarity internal highlights; dark and
  light targets; gradients; low contrast; repeated-grid normalization; missing
  cells; stable row-major numbering; retained work-boundary diagnostics; and
  explicit complete-grid selection including inferred cells.
- Focused Home/park verification confirms that setup acknowledgements use six
  seconds, job acknowledgements tolerate planner backpressure with a
  cancellation-aware extended timeout, queued motion drains before `$H`, and a
  delayed synchronized `M5` does not skip post-job completion. Interactive
  commands retain their configured timeout.
- Focused laser-offset verification covers zero-default configuration,
  configured-value loading and excessive-value rejection, desktop and browser
  coordinate correction, zero-power framing correction, camera-aligned preview, and
  rejection when corrected controller motion would leave the work area.
- Focused Machine Setup tests cover native tab availability, safe runtime
  authority, synthetic preview capture, manual bed-point add/delete, and
  explicit axis-reversal and fine-registration controls. They now also cover
  axis-state persistence across mapper/application reopen, legacy-state
  confirmation without point mutation, prominent reversed-state presentation,
  and persistence of non-power setup preferences.
- Focused lens-index tests cover header-only cold status, bounded detector
  inputs, exact-resolution groups, full-resolution solve re-detection,
  malformed/legacy index quarantine, exact-byte digest/decode identity,
  deterministic decode-time A-to-B-to-A replacement rejection during indexing
  and solving, same-stat replacement recovery through forced re-indexing,
  case-insensitive deterministic lossless discovery, fresh/re-indexed PNG
  metric equality, sharpness-scale provenance, atomic external-mutation rejection,
  deterministic capture/delete/clear/solve conflicts, lock-free detector and
  lossless-encode work, progress polling, Qt responsiveness, and deferred
  dialog close. The calibration/imaging/precision-capture/offscreen-Setup
  integration selection passes 128 tests on Linux. A read-only check of the 27
  local legacy `1920 x 1080` JPEGs against the current digest-index schema took
  `0.007794 s`, reported all 27 legacy entries pending, and left the existing
  index bytes unchanged. Before content-digest identity was added, the same
  isolated cold status benchmark fell from the recorded `25.27 s` / about
  `814 MB` peak to `0.0075 s` / `54,128 KiB`; the separate
  bounded background index took `2.909 s` / `147,436 KiB`, produced 27 ready
  entries with 21 preview detections and no errors, and used `640 x 360` inputs.
  Cold status remains header-only with the digest design; the stronger current
  index pass has not been re-benchmarked on that physical evidence set.
- Focused desktop-controller tests cover stale camera callbacks, one-notice
  latching across repeated and changing refresh errors, recovery reset, and
  explicit operator retry, including release/reopen before image refresh.
- Focused fine-registration verification covers bounded target placement,
  laser-off and powered G-code sequencing, zero-power rejection, sparse-cross
  detection, translation classification, position-dependent rejection,
  persistent translation application/reset, the 5 mm cumulative limit,
  seven-inlier full-map acceptance, six-inlier and low-confidence rejection,
  and persistent full-map rollback.
- Focused accuracy-validation verification covers distinct holdout placement,
  fixed-limit pass/fail classification, low-confidence rejection, laser-off
  session rejection, powered synthetic capture, persistence, stale-map
  rejection, and native job handoff.
- Focused precision-capture verification covers configurable settling,
  genuinely fresh discarded frames, unique multi-frame bursts, camera-control
  reapplication/readback, sharp-frame selection, median/MAD temporal outlier
  rejection, per-mark jitter reporting/rejection, home-first capture, no-home
  recapture, and persisted diagnostics.
- Repository-wide Ruff passes on the current Linux checkout.
- Fine-registration capture, reviewed exclusion, and a later 8/8-inlier
  full-map application have been interactively exercised against the C920.
  Independent five-point holdout validation then passed physically with
  `0.258 mm` RMS, `0.417 mm` maximum error, and mean X `+0.019`, Y `+0.078 mm`.

## Physical observation requiring confirmation

On 2026-08-06, a real powered rounded-rectangle job exposed a repeatable-looking
tool-reference displacement. The hardware profile selected GRBL over the
configured serial device, but the exact controller model and firmware identity
were not recorded, so this is **not** a physically verified configuration.

- Configuration at the time: work area X/Y `10..210` mm, boundary margin 5 mm,
  zero software spot offset, `M4 S500`, 2000 mm/min, one pass.
- Generated desired/controller bounds (offset was zero):
  X `86.326..164.326`, Y `115.585..135.585` mm; commanded center
  `(125.326, 125.585)` mm.
- The corrected 4 px/mm camera capture at
  `data/captures/workspace.jpg` placed the new cut center at approximately
  `(97.6, 117.3)` mm.
- Observed spot displacement was therefore approximately
  `(-27.7, -8.3)` mm. A provisional X `-28` mm, Y `-8` mm spot correction was
  tested, but a second cut moved still farther from the target. The later job
  commanded its X center about `+27.7` mm while the observed cut moved about
  `-27.7` mm in the corrected image. The ignored local profile has therefore
  been returned to zero spot offset; the provisional values must not be reused.

The bed map was solved from controller-positioned laser-burned crosses, so it
already references the laser spot. The failed correction instead exposes an
unresolved controller/workspace coordinate-reference problem. The operator
confirmed that Home / park completed before the second job, ruling out omitted
manual homing as the cause of this miss. The near-equal, opposite X response
was strong evidence that saved bed-point X labels were mirrored relative to the
controller. At that stage axis reversal was physically unverified and a
laser-off check was required. That check was not performed; the subsequent
powered result nevertheless confirmed the direction diagnosis.

The operator subsequently applied **Reverse X mapping** and performed a powered
10% rounded-rectangle job on 2026-08-06 at 20:10 despite the requested
laser-off check. The generated bounds were X `55..133`, Y `111..131` mm with
zero software spot offset. In the corrected 4 px/mm capture saved at 20:12, the
new burn is nearly coincident with the intended shaded rectangle. Visual
comparison places the remaining displacement at approximately 3 mm toward
negative X and no more than roughly 1 mm in Y, but overlapping old marks, burn
width, and the manually positioned target make that estimate unsuitable as a
calibration value. This physically confirms that X reversal removed the major
error; it does not yet verify final accuracy or justify a new spot offset.

The next hardware action at that time was a laser-off homed motion review followed by an
independently measured, sparse fine-registration check rather than another
overlapping full rectangle. Do not encode the estimated residual until
controller identity/firmware, work-coordinate offsets, homing state, workpiece
restraint, and X/Y directions are recorded and the displacement repeats at
multiple bed locations.

The first eight-point fine-registration job was physically marked and captured
on 2026-08-06 at 20:39. Seven detector overlays visually matched their crosses;
point 7 was obstructed by the laser head at the photography pose and produced
an obvious false result of approximately X `-8.46`, Y `-12.18` mm. Excluding
that point leaves a proposed camera-map correction of approximately X `+2.67`,
Y `+2.40` mm, but the remaining scatter is `1.23` mm RMS with a `2.24` mm
maximum. That is position-dependent under the current acceptance thresholds,
so no translation has been applied. The review UI now retains explicit Use
checkboxes, permits at most two reviewed exclusions, and moves the corresponding
future target away from the head/park corner.

The fine-registration review now also computes a separate, confirmation-gated
full-bed homography refinement directly from camera pixels and commanded mark
coordinates. It requires seven geometric inliers, broad coverage, bounded
residual/scale/whole-bed movement, and retains the prior solved map for reset.
The latest saved physical recapture at 20:47 was evaluated without applying it.
Its low-confidence review excluded points 2 and 7; of the remaining six, RANSAC
retained only points 1, 3, 4, 5, and 8 and rejected point 6. Its five-inlier
result is therefore refused. A fresh physical run using the relocated point 7
is required; the new full-map apply and rollback controls are automated-test
verified but not physically verified.

A subsequent physical capture at 21:03 detected all eight relocated marks. It
reported a translation candidate of approximately X `+3.019`, Y `+1.512` mm
with `0.613` mm centered scatter, and a full-map fit with 8/8 inliers, `0.262`
mm in-sample RMS, 53% convex-hull bed coverage, and `4.641` mm maximum modeled
bed correction. The operator applied that reviewed full-bed refinement; the
previous solved map is retained in `bed_calibration.json` for reset. This is a
physical application of the workflow, not yet an independent accuracy
verification.

Machine Setup now includes a separate five-point Accuracy validation workflow.
It prepares zero-power or normally guarded powered holdout jobs, binds the session to
the active homography, homes/parks for capture, and automatically reports
per-point, RMS, maximum, and mean error. A pass requires all five confident
detections, no more than `0.5 mm` RMS error, and no more than `1.0 mm` maximum
error. Zero-power-only and stale-map sessions are rejected, and validation has no path
that mutates calibration. On 2026-08-06 at 21:21, an independent powered
holdout capture passed: all five marks were detected, RMS error was `0.258 mm`,
maximum error was `0.417 mm`, and mean error was X `+0.019`, Y `+0.078 mm`.
This verifies the saved camera-to-laser map for that restrained surface,
material height, camera pose, controller connection, and session; it is not a
safety certification or a guarantee after the setup changes.

The local files `label-sheet-test.png`, `trace-preview.png`, and
`trace-result.json` are preserved for the developer who created them. They are
ignored by Git and explicitly excluded from release archives; they are not
test fixtures or product assets.

Use `git status --short --ignored` when those local files need to be audited;
normal `git status` intentionally omits them.

## Product shape

The repository contains:

1. A dependency-light legacy browser application for camera calibration, single-SVG
   placement, G-code generation, and guarded controller execution.
2. A PySide6 desktop application with native machine setup, a native workspace, multi-object
   projects, operation layers, undo/redo, project persistence, materials,
   toolpath preview, and guarded machine controls.
3. Shared camera, calibration, geometry, vision, G-code, and machine services.
4. A native camera-object tracing workflow whose real-camera seam-selection
   failure is reproduced by the exact saved frame and covered by
   synthetic/offscreen adversarial tests; the repaired normalized-grid
   created-object result still awaits a physical cut check. Loose normalized
   grids can retain either each observed center or each detected top edge
   without forcing direct observations onto an ideal lattice.
5. A reusable cutting-template workflow with a versioned library, manual
   selection, geometric candidate ranking, rigid alignment review, and
   undoable project-object creation, plus a dedicated parametric designer for
   regular rounded-rectangle grids and a safe-simulation alignment-image
   workflow.

The desktop is now the primary complete calibration interface. The browser
retains an equivalent single-SVG workflow but is not required for setup.

## Architecture

Browser path:

```text
laser_aligner.__main__
  -> AppContext
  -> AppHTTPServer
  -> web/index.html + web/app.js
  -> SVG placement
  -> gcode.generator
  -> MachineService
```

Desktop path:

```text
laser_aligner.desktop.main
  -> CoreRuntime
  -> AppContext
  -> DesktopController
  -> E3MainWindow / WorkspaceView / panels
  -> ProjectDocument + CommandStack
  -> project.toolpath
  -> MachineService
```

Shared camera/vision path:

```text
CameraService or SyntheticCameraService
  -> cached composed raw-camera-to-bed rectification map
     (lens distortion + bed homography + optional residual mesh)
  -> one cv2.remap interpolation into the top-down bed image
  -> optional memory-only corrected-frame override in safe simulation
  -> workpiece / fiducial / object-trace detection
  -> machine-coordinate geometry
```

Cutting-template path:

```text
rectangle-grid recipe or visible project output objects
  -> normalized cut objects and matching features
  -> versioned .e3template library item
  -> optional deterministic known-pose corrected test frame
  -> manual selection or geometric candidate ranking
  -> reviewed translation + rotation overlay with synchronized canvas controls
  -> one AddObjectsCommand into the active project layer
```

Execution path:

```text
generated G-code
  -> MachineService validation and safety gates
  -> SimulatedTransport or platform serial transport
```

See `docs/ARCHITECTURE.md` for module ownership and persistence boundaries.

## Dated Windows verification snapshot (2026-08-06)

Audit environment:

- Python 3.14
- PySide6 6.11.1

Results:

- **307 tests passed and 2 POSIX-only tests skipped.**
- The complete suite collected, including app simulation and machine-service
  tests.
- Focused template/test-image runs passed their model, library, renderer,
  matcher, controller, widget, workspace, and desktop-integration checks.
- The browser simulator served a healthy API response and its HTML interface.
- The native desktop started with the synthetic camera and simulated controller
  under both Qt's offscreen backend and a native Windows 1600 x 900 visual
  render, ran its event loop, and shut down cleanly.
- An offscreen `E3MainWindow` smoke test saved and reloaded a template, created
  aligned objects as one history command, and undid the operation.
- A second offscreen `E3MainWindow` smoke test drove the modal grid designer,
  saved and edited a template in place, added four rectangles as one history
  command, and undid the entire grid insertion.
- Layout regression tests cover both Save and Update designer actions, compact
  600 x 430 logical screens, 360 px inspector viewports, and 13 pt text without
  hidden horizontal content.
- Generated corrected frames pass the real color/contrast detector and rigid
  matcher at known poses. The desktop controller path recovers a known pose,
  source switching rejects stale camera results, and the 500-feature renderer
  is structurally verified to use local pixel regions instead of full-bed work
  per feature.
- Trace regressions verify that rounded output previews a clean proposed vector
  matching its fitted width, height, rotation, and radius; the analyzed frame
  stays frozen during review; stale callbacks are rejected; and exact or
  simplified contours retain their previewed world placement when created.
  Corrected-image pixel centers are also registered to their OpenCV/BedMapper
  machine coordinates without a half-pixel overlay shift or a non-integral-
  extent scale drift, and ideal discrete rounded masks recover their radius
  without a center-span off-by-one.
- Transient canvas geometry now has a dynamic key: selected Trace results are
  solid green, aligned template cuts are solid cyan, and fixed camera evidence
  is dashed amber. It defaults to the upper-left viewport corner, remains fixed
  during canvas/workpiece interaction, and retains a position set by dragging
  the key directly. Alignment review uses the same smooth fitted camera
  boundary as Trace and keeps both lines visible when they overlap. These key
  positioning and drag paths are covered by offscreen widget tests.
- The native shell has been visually checked on Windows in safe simulation;
  extended manual interaction and real camera/controller use remain unverified.
- Ruff was not available in the current virtual environment.

The cutting-template coverage includes versioned persistence, resilient
catalog scans, compound imported paths, rigid matching, ambiguity and weak-match
rejection, frozen-frame review, cancellation of stale results, transient
overlays, direct-canvas rigid drag/rotation, object creation/undo, generated-job
revision invalidation, strict full-bed image validation, copy-isolated in-memory
source state, deterministic known-pose rendering, source/timer restoration, and
control/badge state. Rectangle width/height/radius edits, regular-grid
generation, editable authoring metadata, exact-ID replacement, gap/pitch
conversion, live preview, and work-area/object-count rejection remain covered.
Toolpath coverage also verifies that microscopic floating-point noise at an
exact work-area edge is accepted while a real overflow is still rejected.

The two skipped tests require POSIX pseudoterminals and `termios`. These results
describe the dated Windows snapshot, not the later Linux branch verification
recorded above.

## Historically verified on Linux

The 0.1.0 release documentation records:

- package compilation and automated tests;
- synthetic camera and automatic bed-map startup;
- a simulated HTTP workflow;
- guarded machine behavior;
- POSIX pseudoterminal serial framing and streamed jobs.

Those claims apply to the earlier release state. They are not evidence that the
consolidated desktop/object-trace branch passes unchanged on Linux.

## Implemented feature set

### Shared core

- Validated JSON configuration.
- Synthetic and OpenCV camera services.
- Linux V4L2 camera control application.
- Checkerboard lens calibration.
- RANSAC bed homography and perspective rectification.
- Workpiece, ArUco, and crosshair-grid detection.
- SVG shape/path parsing, curve flattening, and explicit physical-unit/viewBox
  mapping at the CSS 96 px/in reference conversion. Unsupported CSS
  stylesheets, clipping, masks, and geometry-changing presentation semantics
  are rejected rather than silently flattened incorrectly.
- Bounds-checked vector G-code and zero-power framing.
- Simulator and guarded machine service.

### Browser

- Camera, lens, and bed-calibration pages.
- Automatic and manual bed-point workflows.
- Single-SVG placement, sizing, rotation, and mirroring.
- Workpiece detection.
- G-code generation and download.
- Controller connection, diagnostics, arming, execution, and software stop.

### Desktop

- Native workspace with machine coordinates, grid, rulers, pan, zoom, snap,
  and a fully adjustable corrected-camera overlay whose control and renderer
  share a 70% default.
- LightBurn-inspired desktop hierarchy with original compact icons, a bright
  drafting bed, a non-hideable responsive runtime/safety strip, always-present
  numeric properties, a full-height design inspector, compact bottom G-code and
  runtime/material docks, and a fixed 30-color operation palette.
- Multiple objects and operation layers.
- Rectangle, rounded rectangle, ellipse, line, text, and SVG-path objects.
  Desktop SVG import applies parsed physical dimensions through transformed
  groups, preserves the requested placement center, and stops before object
  creation on any lossy parser warning. Absolute-unit and viewBox-only sizing,
  transformed placement, and fail-closed import behavior are parser- and
  offscreen-desktop-test covered to `0.01 mm`; interactive import review remains
  pending.
- Persistent press-drag-release rectangle drawing with a live active-layer
  outline, endpoint snapping, normalized drag direction, exact-size commit,
  immediate selection, and one-step undo/redo.
- Numeric width, height, and corner-radius editing for a selected rectangle,
  applied as one undoable validated shape change.
- Direct single-object corner resize and rotation handles with live preview,
  anchored-corner resizing, 15-degree Shift snapping, and undoable commits.
- Five-column operation summaries for mode, speed/power, output, and
  visibility, with inline toggles, operation-color editing, ordering controls,
  and scan interval, angle, and raster overscan controls.
- Transform, mirror, duplicate, delete, group, ungroup, align, distribute, and
  z-order commands.
- Undo/redo.
- `.e3laser` save/load, backup, autosave, and recovery.
- SQLite material presets.
- Multi-layer vector toolpaths, zero-power framing, previews, and estimates.
- Automatic invalidation of generated G-code and toolpath previews after any
  project revision changes.
- Camera focus controls and sharpness measurement.
- Guarded machine connection, park, diagnostics, run, and software stop.
- Native Machine Setup with camera control application, raw preview, synthetic
  scenes, checkerboard capture/solve, manual and CSV-assisted point entry,
  automatic 5×5 grid detection, bed-map solve/residuals, eight-point fine
  registration, workpiece detection, and fiducial inspection.
- Validated generated-G-code export.
- Simulation-only loading or deterministic generation of frozen corrected
  alignment frames, with camera-control gating and a persistent workspace badge.

### Camera-object tracing

- Automatic color/contrast detection.
- Click-to-sample hue.
- Direct and inferred regular-grid detections.
- Analytic fitted rounded rectangles plus simplified and exact pixel-derived
  contours.
- Separate observed and proposed-vector contours, with the workspace preview
  showing the geometry that object creation will consume.
- Border offsets.
- Review and selective conversion to editable project objects.
- One captured corrected frame held across detection review, with monotonic
  request cancellation and stale-result rejection.
- One-step undo for a created detection set.

The trace algorithms and native review lifecycle pass synthetic and offscreen
behavioral tests. The workflow has not been exercised end to end with the real
camera and calibration.

### Reusable cutting templates

- Versioned `.e3template` JSON with atomic, safe-filename library storage.
- Resilient catalog scans that keep valid unique templates available while
  reporting malformed files and excluding duplicate persistent IDs.
- Creation from visible, output-enabled project objects without mutating the
  source project.
- Dedicated regular-grid designer with a live preview, rows/columns, cut
  width/height/radius, and spacing entered as edge gap or center pitch.
- A 500-object grid limit and project-work-area validation before either saving
  a grid template or adding its editable rectangles to the current project.
- Versioned rectangle-grid authoring metadata, preserving template identity
  across parameter edits and distinguishing editable grids from arbitrary
  project-authored geometry.
- Direct creation of a grid in the active project layer as one undoable batch.
- Template-local normalization around the combined cut bounds.
- Per-outer-contour matching features for compound imported SVG paths, with
  contained holes excluded.
- Manual library selection plus synchronized numeric and direct-canvas
  center/rotation adjustment of the complete transient cut preview.
- A role-labeled overlay key and color-independent solid/dashed styling for
  distinguishing aligned cut geometry from camera-detected feature edges.
- Synthetic geometry-based template ranking, weak-match rejection, and
  template/pose ambiguity warnings.
- One corrected frame shared across all candidate trace settings and frozen
  while an accepted overlay is reviewed.
- Safe-simulation loading of corrected full-bed PNG/JPEG images with a strict
  uniform-scale contract and Unicode-safe paths.
- Deterministic corrected-frame generation from a selected template at known
  X/Y/rotation, with optional noise and missing labels; maximum-size grids use
  per-label rendering regions and exact discrete rounded silhouettes that do
  not introduce a detectable antialias fringe.
- One in-memory test frame shared by the workspace, tracer, and matcher, with
  stale-source rejection and explicit restoration of the synthetic camera.
- Rigid translation/rotation placement with scale differences reported but
  never applied.
- New object identities, active-layer assignment, and one-step batch undo.
- Optional `marker_id` schema metadata reserved for future identification.

Automatic matching requires at least three features; one- and two-cell grids
remain available for manual placement only. Matching compares feature centers,
dimensions, and orientation but not rounded-corner radius, so templates that
differ only in radius require manual selection and overlay review.

The portable model/library, generator, and matcher have focused synthetic tests,
and the native controls, review overlay, test-source lifecycle, application,
undo, stale-result handling, and generated-job invalidation have behavioral
offscreen coverage. The generated frame is intentionally idealized and the
workflow has not been verified with real corrected label-sheet images or
physical placement. No marker detector is implemented. See
[docs/CUT_TEMPLATES.md](docs/CUT_TEMPLATES.md).

## Known gaps

### Cross-platform

- No Windows serial backend, hardware camera discovery/control layer, or
  install/launch scripts exist.
- Selecting real serial hardware on Windows fails clearly and directs the user
  back to the simulator.
- Camera hardware handling assumes V4L2 and `/dev/video*`.
- Autosaves and material presets share an OS-native writable per-user data
  root (XDG/userbase on Linux and LocalAppData/AppData on Windows). Existing
  legacy-root data is copied forward without deleting the source, with fallback
  to the legacy file if migration cannot complete.
- CI covers the portable suite on Windows and Linux; Linux also runs the full
  supported Python-version matrix and repository-wide Ruff.

### Desktop and authoring

- Guarded jogging is implemented and automated-test covered. Direction and the
  selected X5..245/Y5..215 mechanical envelope were operator-exercised, but the
  controller/firmware identity and physical STOP/reconnect response were not
  recorded.
- No tested pause/resume behavior.
- No text-to-outline conversion.
- No DXF import. Raster image import currently stores an external absolute
  asset path; managed or embedded portable assets, selectable dither methods,
  and calibrated grayscale power modulation are not implemented. PNG, JPEG,
  and BMP sources use deterministic ordered dithering; TIFF is unsupported.
- Ellipse and line creation remain one-shot centered inserts; only rectangles
  currently have the persistent canvas drawing interaction.
- Single visible, unlocked objects have corner resize and rotation handles.
  Shared multi-selection transform boxes, node editing, proportional resize
  gestures, and smart guides are not implemented. The transient
  cutting-template preview retains its separate rigid-body drag and rotation
  controls.
- No full interactive end-to-end GUI automation.
- Cutting-template matching uses provisional software acceptance gates, but has
  no real-camera validation dataset or physically measured accuracy threshold.
- Object tracing has no sub-pixel edge estimator or real-camera accuracy
  dataset. At the default 4 pixels/mm, one corrected-image pixel is 0.25 mm;
  fitted dimensions and radii remain raster- and threshold-dependent.
- Loaded test images must already be corrected full-bed views; the loader does
  not infer bed corners or calibrate an ordinary photograph. Generated images
  reuse ideal template geometry and therefore cannot expose lens, homography,
  parallax, material-height, lighting, or mounting errors.
- Automatic template ranking cannot distinguish otherwise identical layouts
  whose only difference is rounded-corner radius.
- `marker_id` is stored but no QR/ArUco/marker identification path consumes it.
- Template placement intentionally supports translation and rotation only; it
  will not scale geometry to conceal calibration or material-height errors.

### Hardware

- GRBL is selected and powered output has been observed, but controller
  identity/firmware and the power scale remain unverified.
- The physical cut response confirmed the historical X-map reversal diagnosis;
  the later fresh keyed map records normal controller labels. Mechanical limits
  were manually probed, but firmware identity, repeatability, and photo-pose
  accuracy remain unverified.
- C920 control readbacks, the lens workflow, and real calibration residuals were
  exercised on the current rig. Repeatability after remounting, material-height
  sensitivity, and parallax remain unverified.
- Powered base, registration, validation, and label-placement output has been
  observed. The latest independent five-point, 4×4, and shifted checks passed,
  and the operator reported a close label-perimeter cut; no metrology-backed
  general placement specification or verified power scale has been established.

## Recommended next sequence

1. Manually exercise the safe native UI on Windows, including loaded and
   generated alignment images, and record usability issues.
2. Add PowerShell setup/launch scripts.
3. Keep the Windows/Linux CI matrix and Linux Python-version coverage green.
4. Separate portable OpenCV capture from Linux V4L2 discovery/control.
5. Keep the complete Linux suite and release-package smoke green.
6. Extend behavioral Qt coverage for the remaining project-editing and
   object-tracing workflows.
7. Validate template matching against curated corrected camera images at known
   material heights and define residual/confidence acceptance thresholds.
8. Verify that release archives continue to exclude local camera/trace output.
9. Only then proceed with documented physical camera/controller bring-up.

## Evidence terminology

- **Tested** — covered by a currently passing automated test.
- **Smoke-tested** — imported or constructed, but not exercised end to end.
- **Implemented, unverified** — source exists but lacks current execution
  evidence.
- **Historically verified** — recorded for an earlier commit or release.
- **Physically verified** — exercised on identified real hardware with recorded
  configuration and results.
