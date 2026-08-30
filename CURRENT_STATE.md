# Current repository state

This file records implementation and verification evidence. It is not an
operator procedure. Follow the canonical
[Permanent Camera Setup Runbook](laser_aligner/operator_docs/PERMANENT_CAMERA_SETUP.md)
for the current five-step calibration sequence and sixth read-only audit tab.

Snapshot: **2026-08-31**

## Active independent Camera Trace hole-area filters

Non-grid Camera Trace Contrast and Auto's dark/light raster attempts now separate
foreground-object review from enclosed-hole cleanup. The Trace Object filters are
ordered **Minimum area**, **Maximum area**, **Minimum hole area**, **Maximum hole
area**, **Minimum width**, and **Minimum height**, all area values use mm², and the
hole tooltips describe enclosed holes rather than object size. Hole controls are
available for non-grid Auto/Contrast and inactive for Grid and explicit Color.

Minimum object area first removes source-resolution connected foreground
components, including nested foreground islands. Maximum object area later
rejects complete post-vector root candidates above its inclusive limit.
Independently, enclosed background components below the minimum hole area or
above the optional maximum hole area are filled; holes exactly on either bound
or between them are preserved. `None` represents no maximum. The external
border-connected background is never filled. Background connected to hard-
ineligible pixels is equally protected and excluded from filterable-hole counts.
Cleanup changes the exact production Mask before 4× `RETR_TREE` extraction and
native fitting, so preserved holes retain
normal descendants and deliberately filled holes may absorb nested foreground
islands into the parent. Maximum hole area never acts as a root/object filter.

`geometry.foreground.clean_foreground_components` remains backward compatible
while accepting independent minimum and maximum hole areas. Its detailed companion
returns bounded counts for raw, preserved, below-minimum-filled, and above-maximum-
filled holes. `RasterVectorizationOptions`, mask previews, quick/exact pixel and
asset results, metadata, Camera Trace results, and Auto raster-attempt diagnostics
carry the effective physical range and aggregate counts without retaining a
per-hole list. Existing source-neutral callers that omit the new limits still use
minimum feature area as minimum hole area and no maximum, preserving imported-
raster defaults.

Legacy Trace options and QSettings with no hole fields migrate once: minimum hole
area copies the saved minimum object area and maximum hole area becomes **No
maximum**. Persisted values are explicit thereafter, so later object-area edits do
not recouple them. Validation rejects non-finite, negative, inverted, or out-of-
widget-bound ranges rather than swapping or clamping them.

Windows Python 3.12.13 verification on the synchronized integration worktree
passes **349 focused Trace tests** in **67.07 seconds** and **512 bounded-shutdown
tests** with **2 expected Windows skips** in **51.95 seconds**. The complete
four-worker repository suite passes **3,225 tests** with **15 expected
platform/privilege skips** in **193.41 seconds**. Repository Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` pass; the diff
check reports only Git's existing LF-to-CRLF notices. Focused regressions prove
that cancellation during source-mask hole cleanup or later native fitting
publishes no accepted Trace result, and that both the controller and main-window
guards prevent a late cancelled result from publishing or creating project
geometry. Frozen-build evidence remains pending on this integration branch. This
is deterministic Qt-free/offscreen automated verification, not an interactive
camera test.

This feature does not change primitive recovery, Straighten, camera
normalization, exposed-bed suppression, the Auto threshold-selection algorithm,
Grid/Color detection, project schema, planning, motion, Air Assist, Pi execution,
homing, arming, or laser-output authority. The synthetic mask and offscreen tests
do not constitute physical validation; the reflective wrench still requires a
fresh recorded camera run. The recommended first settings are **Minimum area 50
mm²**, **Maximum area 8,000 mm²**, **Minimum hole area 500 mm²**, and **Maximum
hole area No maximum**.
Exercise a finite maximum-hole value once before returning it to **No maximum**.

## Active bounded desktop shutdown correction

Accepted desktop Close now starts one monotonic four-second shutdown deadline.
The window arms a process-exit watchdog at the moment Close is accepted, before
state persistence or runtime teardown, so a non-cooperative Qt worker cannot keep
the E3 process alive past the deadline. This watchdog is an exit-only fallback;
the normal path cancels work, drains the desktop pool for at most one second,
stops the runtime under the same absolute deadline, and exits through Qt in the
ordinary way. `QThreadPool.waitForDone(-1)` is no longer used.

`RemoteCameraService` tracks every connecting or active request socket under a
lock. Address resolution runs through a daemon helper while the request worker
polls the shutdown generation, so even blocked platform DNS cannot retain a Qt
worker. Desktop shutdown latches cancellation and closes tracked sockets, waking
blocked fresh-frame, precision-burst, control/snapshot, send, and receive work.
The ordinary operation-grade camera timeouts are unchanged. Retained and labeled
`FunctionTask` wrappers remain alive until their runnables return, but their
success, failure, image, result, and busy-state publication is suppressed once
shutdown begins. Camera Trace, native fitting, raster conversion, and toolpath
generation receive cooperative cancellation checks through their CPU-heavy
loops.

Remote machine shutdown is separate from ordinary Disconnect. Only freshly
observed idle state permits one best-effort `machine.disconnect` attempt; an
empty or stale observer cache detaches without assuming the Pi is idle. Address
resolution, connect, authentication, capability check, and RPC share a maximum
0.75-second shutdown allowance; failure logs and detaches without retrying under
the normal 130-second operation timeout. An accepted or
ownership-uncertain Pi job detaches immediately and sends no RPC, STOP, `M5`,
reset, hold, Air Assist OFF, or controller Disconnect. The recent idle
Disconnect generation correction and all ordinary operation timeouts remain
unchanged.

Automated focused coverage reproduces blocked 36-second fresh-frame,
precision-burst, and control/snapshot calls; blocked address resolution and
socket creation/connect/send/receive races; an unreachable idle Pi, stale Pi
observer state, and slow-drip machine protocol; the combined blocked-camera plus unreachable-Pi
case under one shared deadline; stuck Qt-pool work in a subprocess; an updater
launch preceding blocked close preparation; late desktop callbacks; CPU
cancellation; stale/late Pi monitor races; and accepted/uncertain Pi-job
non-destructive detach. The final Windows Python suite passes **3,170 tests**
with **15 expected platform skips** in four-worker execution; repository Ruff,
`compileall -q laser_aligner`, and `git diff --check` pass.

Windows development packaging completed from shutdown implementation commit
`9687373aedee893c01d3cc6f9fb4efb15fa276a9` as version `0.6.156`. The final
windowed bundle and private machine-seeded development installer were rebuilt
with the unrelated Codex Poppler directory removed from the build process
`PATH`; the bundle contains no foreign `icu*.dll` files and launches
successfully. On 2026-08-31, accepted main-window `WM_CLOSE` to actual process
termination was measured on that frozen build with the configured hardware
profile as follows:

- ordinary disconnected state, with the camera and machine services offline:
  **279.9 ms**;
- 50 ms after invoking the exact **Refresh camera** control against the
  unreachable camera: **222.0 ms**;
- 50 ms after opening Trace and invoking its exact **Detect objects** control:
  **230.4 ms**;
- 50 ms after invoking **Connect machine** against the unreachable Pi service:
  **226.5 ms**; and
- combined camera Refresh plus unreachable-Pi Connect: **316.0 ms**.

Every performed frozen-build case was below 5 seconds and below the normal
two-second target. The Pi host answered ICMP in 7-8 ms and exposed its expected
Raspberry Pi MAC address, but TCP ports 8765 and 8766 remained closed throughout
the acceptance window. A live-camera-enabled close and an idle Pi-reachable
Disconnect therefore remain physically pending. The Trace control path was
started, but no live-image native-fit workload could be established while the
camera service was unavailable; its CPU cancellation evidence remains
automated. No physical controller motion, arming, laser output, accepted Pi job,
or Air Assist action was performed, and the software shutdown controls are not
safety-rated.

## Active Pi-owned secondary-controller Air Assist correction

Work on `feature/air-assist-output` retains the existing binary
`OperationLayer.air_assist` project setting, Cuts / Layers checkbox, persistence,
Undo/Redo, and Material Recipe authority. Imported LightBurn operations still
begin output-disabled. The project schema is unchanged, and all built-in machine
profiles remain Air Assist-disabled.

`MachineSettings.air_assist` is a constrained saved-machine mapping with
`mode`, `fan_index`, `port`, and `baudrate` fields. In addition to the existing
`disabled`, same-primary-controller `grbl_coolant`, and `marlin_fan` modes, this
branch adds `secondary_marlin_fan`. That E3 mapping keeps the primary laser and
motion controller explicitly GRBL while the Pi owns a separate persistent
Creality/Marlin serial connection. Its verified endpoint is
`/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` at 115200 baud, with
`fan_index = 0`. Windows stores that Pi-local endpoint as opaque configuration
and never opens it. The currently persisted Windows primary endpoint remains
the GRBL `e3bridge://192.168.5.18:8765` endpoint at 115200 baud; the exact
Pi-local primary-controller serial path has not been confirmed and must not be
inferred from the secondary path.

The secondary mapping is deliberately exact: ON is `M106 S255` and intended OFF
is `M106 S0`. It never adds a `P` parameter and never uses `M107`. Physical
bring-up has verified that exact ON command starts FAN2 on the identified
Creality/Marlin controller. Physical confirmation that `M106 S0` stops FAN2 is
still pending, as are full startup, transition, completion, STOP, restart, and
failure lifecycle checks. These software controls are not safety-rated.

Generated secondary-assist jobs encode the strict non-comment line
`E3AIRASSIST <mapping-sha256> ON|OFF` for each transition in the immutable
canonical program bytes. The SHA-256 binds the exact secondary mapping; changing
either that mapping or the schedule changes the finalized program digest. The Pi
validates those instructions and intercepts them before the primary stream, so
the GRBL controller never receives `E3AIRASSIST` or Marlin fan commands.
Malformed, forged, mismatched, or unsupported instructions fail closed. These
programs are E3-specific, are not portable controller G-code, and must not be
submitted through a non-E3 executor.

The layer planner enables assist immediately before the first powered Line,
Fill, or Raster output that requests it, holds it across paths, rapid travels,
passes, and adjacent requesting layers, and disables it before later powered
non-requesting work. Output-disabled, empty, and zero-power layers never enable
it. Preview and Start Here consume the same immutable instructions and preserve
the mapping digest and schedule.

After START ownership is accepted, the Pi owns both execution paths. One
persistent `CrealityControllerOwner` serializes secondary commands and validates
their acknowledgements and timeouts. The Pi-local reader latches passive USB
hangup/read failure, and execution checks that latch between primary program
lines so a lost secondary session fails before further work is streamed.
Secondary command failure fails the job;
the primary GRBL `M5`/STOP path remains authoritative. STOP acts on the primary
first, then runs bounded independent secondary-OFF cleanup so a secondary ACK or
timeout cannot delay primary STOP. Detaching the Windows client causes no fan
transition. Pi restart marks an in-progress job interrupted, never resumes it,
and attempts an acknowledged secondary OFF. This owner must later be shared with
the S1 Z-homing/CR Touch work rather than creating a second concurrent owner;
that separate branch is not merged or modified by this work.

Deterministic Air Assist/Pi/Windows focused verification passed **351 tests with
7 expected Windows POSIX skips**; an additional targeted run for serial-open
START rejection, changed/disabled-config restart recovery, and unresolved-
recovery START blocking passed **3 tests**. The complete Windows repository run
passed **3,118 tests with 15 expected platform skips** in **233.86 seconds**.
Repository Ruff passed with `--no-cache` after the ordinary cached invocation
encountered the worktree's known cache-directory ACL restriction. `compileall -q
laser_aligner` passed with `PYTHONPYCACHEPREFIX` directed to a temporary cache,
and `git diff --check` passed with only Git's existing LF-to-CRLF notices. These
are automated/simulated checks only. The only new physical evidence is the exact
FAN2 ON result above; intended OFF and end-to-end lifecycle behavior remain
pending physical verification.

## Active post-Create Camera Trace orientation review / Straighten

Straighten is now a normal project edit over selected, finished Camera Trace
artwork. Detect/review has returned to choosing which temporary outlines should
become project objects; it has no Straighten control, Reset control, temporary
rotation state, or rotated candidate overlay. Successful Cut creation selects the
new combined object or the complete separate-object batch and opens the normal
Shape inspector. The optional offer and muted no-offer diagnostic live there.

Eligibility is persistent, non-authoritative SceneObject metadata added only to
successful non-grid native Cut creation. `trace_orientation_eligible`,
`trace_output_mode`, `trace_artwork_id`, member index/count, and creation mode
extend the existing `trace_source` provenance. Stock boundaries, grid-normalized
results, rounded/simplified/exact non-native output, failed native fits, unrelated
project objects, and mixed eligible/ineligible selections do not enter the
estimator. The metadata uses the existing `.e3laser` metadata map and therefore
survives ordinary save/load without a schema change; it grants no planning,
G-code, motion, arming, controller, or laser authority.

`vision/trace_orientation.py` remains Qt-free, image-free, and bounded. Its public
adapter record is `TraceOrientationGeometry(object_id, artwork_id, geometry)`,
where `geometry` is already in current project/world coordinates. The desktop
adapter transforms each selected native path with its current width, height,
mirrors, rotation, and center translation. It neither reads nor reconstructs
pixels, masks, detection dictionaries, contours, or fitting diagnostics.

Each disconnected native subpath is analyzed as a component. Physical-length
lines, demonstrably near-linear cubics, anisotropic component axes, and meaningful
component-center alignment contribute capped evidence to one robust modulo-90
artwork consensus. All components of one combined object belong to one artwork;
all objects from one separate Create batch share the same artwork ID. Curved and
tiny local fragments can contribute little evidence but cannot independently veto
their own artwork. The conservative disagreement veto instead compares reliable
orientations from distinct selected artwork IDs, so two separately created labels
at incompatible angles remain conflicting. Total analysis remains capped at
20,000 native segments and 8,192 subpaths.

The existing confidence and angle gates remain conservative: under 0.4 degrees is
trivial, ordinary offers stop at 10 degrees, 10–15 degrees needs exceptional
confidence, and larger corrections are suppressed. Successful UI copy states the
detected direction and the opposite correction direction. Eligible no-offer
selections show a muted already-straight, insufficient-evidence,
conflicting-evidence, or out-of-range explanation; ineligible selections show no
Camera Trace-specific control.

The estimator pivot is the exact center of the union of selected world-native
bounds. Clicking **Straighten** recomputes from current geometry, rotates every
selected object center about that one pivot, adds the same correction to every
object rotation, and commits one standalone `UpdateTransformsCommand`. Create and
Straighten are separate history entries. Undo restores the command's exact saved
pre-Straighten transforms; Redo reapplies its exact saved corrected transforms.
Local native geometry is never rewritten, so line/cubic types, subpaths, holes,
islands, fill rule, topology, and relative spacing remain intact. Selection
changes, ordinary transforms, and Undo/Redo recompute only from current project
geometry, without camera, normalization, threshold, raster reconstruction, or
native-fitting work.

Focused post-Create estimator, panel, selection, creation, history, provenance,
and persistence verification passes **87 tests** in **1.67 seconds**. Broader
Camera Trace eligibility, raster threshold, native fitting/topology, grid,
source-control, and template regressions pass **169 tests** in **107.17 seconds**.
The complete Windows four-worker suite passes **2,917 tests** with **14 expected
platform/privilege skips** in **143.46 seconds**. Repository Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` pass. On this
Windows host, 200 warm end-to-end selection estimates (SceneObject adaptation
plus estimator) measured **0.9437 ms median / 0.9603 ms p95 / 1.0538 ms max**
for one combined four-component label, **10.9183 / 11.1325 / 12.0645 ms** for
one combined 92-subpath stencil, and **1.0141 / 1.0314 / 1.0814 ms** for one
four-object batch. These are synthetic software timings, not real-camera
performance evidence.

The implementation was prompted by newer operator-reported Coleman stencil
evidence: manual threshold 128 produced a dramatically cleaner production Mask
but later failed bounded native-topology validation, manual threshold about 150
produced usable geometry, and Auto selected 170 from the image and produced a good
trace. Controller, firmware, machine configuration, capture identity, and measured
placement results were not recorded in that report, so it does not satisfy the
repository's formal physical-acceptance record. Post-Create Straighten still needs
a recorded physical test of combined and separate creation selection, offer and
correction direction, shared-pivot placement, Undo/Redo, saved/reloaded provenance,
final Preview, and guarded generated output.

## Active Pi-owned desktop Disconnect generation correction

A physical Pi-owned desktop run reproduced **Disconnect** failing with
`Operation was cancelled by software STOP` before the controller-disconnect RPC
was sent. The exact cause was the interaction between request-time cancellation
and Disconnect's intentional revocation. `DesktopController._run()` captured and
thread-bound generation N; `RemoteMachineService.disconnect()` advanced the
STOP epoch to N+1 so queued and in-flight pre-START work would become stale;
`_machine_status_action()` then read the still-bound N and rejected Disconnect's
own cleanup before network transport.

Idle Disconnect now captures N+1 while performing that same revocation and
binds only its `machine.disconnect` cleanup RPC to N+1. Ordinary connect,
Home/park, jog, manual-command, upload/FINALIZE/START, and all other operation
paths retain their request-time generation. A later STOP or detach advances the
epoch again and still rejects the cleanup at its existing pre/post-RPC checks;
STOP remains an independent priority RPC. If upload/START preparation already
owns the operation lock, Disconnect retains the prior local detach behavior: it
cancels the stale preparation without sending controller Disconnect or STOP. An
accepted or ownership-uncertain Pi job likewise remains non-destructively
detached and continues under Pi authority.

The related lifecycle paths do not have the same inversion. Remote
`replace_connection()` is one Pi-side atomic ordinary action and does not advance
the Windows facade's generation. Direct `detach()` advances the generation but
has no cleanup RPC to self-cancel. Desktop shutdown calls detach first to revoke
workers; accepted/uncertain Pi execution stays detach-only, while later idle
`AppContext.stop()` uses the distinct short-budget remote shutdown path outside
the stale desktop worker scope.

A deterministic offscreen desktop regression exercises the exact
`DesktopController._run() → operation_scope() →
RemoteMachineService.disconnect()` path and observes one and only one
`machine.disconnect` RPC with no UI error. Remote-service regressions separately
cover blocked CHUNK and FINALIZE cancellation, accepted-job detach without an
RPC, idle disconnect, idle runtime shutdown, accepted-job runtime shutdown, and
a STOP overtaking an in-flight Disconnect RPC. The focused desktop, remote
facade, and Pi server batch passes **48 tests**. The complete combined-branch
Windows four-worker suite passes **2,851 tests** with **14 expected
platform/privilege skips**; repository Ruff, `python -m compileall -q
laser_aligner`, and `git diff --check` pass. The corrected Disconnect path has
automated Windows verification only; it has not yet been re-run against the
physical Pi/controller.

## Active Pi-owned parked-camera hold ordering correction

Physical reproduction on `main` commit `66a704653ddabee7f352b4c5eaa779d2bc3688ce`
showed Camera Trace enter Working while the controller was HOME REQUIRED, remain
motionless for about two minutes, then Home/park and complete normally. The
delay matched the Pi server's 120-second stepper-hold lease.

The exact cause was a cross-session ordinary-operation deadlock. Windows
`RemoteMachineService.temporary_stepper_hold()` establishes one long-lived
authenticated `E3MACHINE/2` session. The Pi server enters
`PiJobService.temporary_stepper_hold()`, which deliberately owns
`_ordinary_lock` across the complete yielded lease. `AppContext` then sent Home /
park through a second ordinary RPC while the first session still owned that
lock. Windows could not release the hold until Home / park returned, and Home /
park could not return until the hold released. Only the server's finite lease
timeout broke the cycle.

All six `AppContext` hold call sites have been audited and now keep the hold
capture-only. Object Trace and fresh base-bed mapping always complete Home /
park before hold acquisition. Dense calibration, accuracy validation, and fine
registration do so when `home_first=True`; their intentional no-home recaptures
acquire the hold directly and send no machine motion inside it. Coordinate audit
completes Home / park and its before-capture position sample before the hold,
captures the raw burst while held, releases, then takes its after-capture sample.
No ordinary machine RPC is nested in a remote hold. Raw-frame capture remains
inside the hold; sharpness scoring, lens correction, rectification, detection,
and analysis remain outside it. A failed Home / park never acquires a hold, and
a capture exception still exits the same-channel release path.

The Pi lock, same-channel hold/release authentication, finite 120-second lease,
idle requirement, Pi-owned job exclusion, controller authority checks, and STOP
bypass were not weakened or retimed. A direct server regression proves an
ordinary Home / park request remains serialized until another session releases
its hold. A second regression proves STOP still returns immediately during a
hold; the canceled held session reports failure rather than claiming a normal
release.

One deterministic full-stack loopback capture runs
`AppContext → RemoteMachineService → E3MACHINE/2 → PiMachineServer →
PiJobService → MachineService`. It proves prepare completion precedes hold
acquisition, the GRBL hold is active during the camera burst, and normal release
restores the finite idle delay. On the local simulated controller/camera sample,
prepare-photo took **0.0171 s**, hold acquisition **0.0138 s**, the stubbed raw
burst **0.000055 s**, the complete precision-capture scope **0.0316 s**, and the
already-connected end-to-end Trace capture **0.0321 s**. These are deterministic
software-loopback timings, not a physical camera-performance measurement. The
previous approximately 120-second value is the physical failure observation;
the corrected sequence has not yet been re-run on the Pi/controller/camera.

Focused Windows verification passes **149 tests** across RemoteMachineService,
PiMachineServer/PiJobService locking and STOP behavior, local MachineService
hold/Home behavior, Camera Trace precision capture, base mapping, coordinate
audit, fine registration, dense/accuracy workflows, and desktop Trace capture.
The complete Windows four-worker suite passes **2,848 tests** with **14 expected
platform/privilege skips**. Repository Ruff, `python -m compileall -q
laser_aligner`, and `git diff --check` pass. No timeout constant changed, and no
new physical motion, laser, camera, or accuracy verification is claimed.

The ordinary corrected live overlay was separately reported unavailable during
the physical reproduction, so the operator used Raw Live Monitor. That overlay
issue was not investigated or changed here and remains a separate follow-up.

## Active development-update publication continuity

The `Publish E3 development update` workflow no longer deletes and recreates
the live `e3-development` prerelease. Windows and Linux builds still complete
independently before publication, but the publish job now gives their outputs
immutable revision-specific names (`E3-Setup-<12-sha>.exe` and
`E3-<12-sha>-x86_64.AppImage`). A Qt-free Python publisher validates the
generated schema-1 manifest against the exact local files, uploads both
packages, and verifies GitHub's uploaded state, byte size, and server-reported
SHA-256 before it uploads the staged manifest.

The stable updater endpoint remains
`releases/download/e3-development/update-manifest.json`. GitHub provides no
in-place content replacement for a release asset: CLI clobber deletes before it
uploads, while the asset API can only rename metadata. The final switch is
therefore a recoverable near-atomic pair of renames from the old stable manifest
to an ID-specific backup and from the already-uploaded staged manifest to the
stable name. Cancellation signals are deferred across that critical pair, and
an always-run workflow recovery command restores the old verified manifest if
normal concurrency cancellation interrupts the publisher. Failures before the
switch retain the old manifest and packages; failures after it retain the
complete new packages and manifest. The old manifest backup alone is cleaned
up afterward on a best-effort basis. Prior binary assets are not removed, so a
client holding the prior manifest can still download and verify its referenced
package. Release tag, title, body, target, and prerelease metadata change only
after the new manifest is authoritative.

Desktop manifest retrieval now retries only transient HTTP 404, 408, 429, and
5xx responses with bounded 0.5, 1, and 2 second delays. Malformed URL and JSON
failures remain immediate; package downloads are not retried by this path, and
existing channel, revision, exact-size, SHA-256, and installer verification is
unchanged. Focused updater/workflow/publisher verification currently passes
**24 tests**; broader deployment, versioning, desktop handoff, and Windows
launcher regression verification passes **49 tests** with four xdist workers.
The complete Windows suite passes **2,656 tests** with **14 expected platform
skips** and four workers. Repository Ruff, Python compilation of `laser_aligner`
and `packaging`, workflow YAML parsing, and `git diff --check` pass. These are
local mocked-network and deterministic publisher tests; the actual Windows
installer and Linux AppImage for this change have not yet been built or
published, and no controller, motion, arming, laser-output, or physical test
was performed or is claimed.

## Active Camera Trace material eligibility, Auto orchestration, and raster parity

Ordinary non-grid Camera Trace now removes machine/background responsibility
from the raster vectorizer. The production order is corrected frame, hard
physical Trace ROI, trusted empty-honeycomb comparison, material eligibility,
eligibility-scoped illumination normalization, dark/light threshold, eligibility
gate, and the unchanged shared raster-vector geometry pipeline. The full frame
and its established pixel-center transform are retained.

In machine coordinates the hard ROI is the existing guarded output polygon, or
its existing guarded rectangle when no polygon is configured. In current
honeycomb-local coordinates it is the intersection of that guarded geometry
with the recorded support rectangle in the already established local frame.
This narrows vision evidence only. It does not expand support, output authority,
planning, G-code, motion, arming, STOP, or machine execution. A foreground root
clipped by that hard ROI is rejected before review and Auto scoring.

The controller supplies an empty-bed image only through the existing accepted
background path, after schema/kind, encoded-image SHA-256, complete bed-map
digest, support-frame digest, coordinate-frame, rectification, and final-image
dimensions validate. The comparison model is bounded to 800 pixels and at most
2 pixels/mm. It uses correlated locally detrended luminance, normalized patch
error, and compatible texture to propose structural bed evidence, but structure
alone no longer excludes pixels. Strong reference-like seeds with uncompensated
Lab luminance/chroma deltas no larger than 32/24 levels drive deterministic
Tukey-weighted compensation. Luminance uses a bounded 0.72–1.28 reference gain,
±48 offset, and ±32 whole-frame X/Y gradients; chroma uses ±24 offsets and ±16
gradients. The compensated point residual must meet 34/22/38
luminance/chroma/combined limits and its 1.5 mm patch mean must meet 26/18/30,
except that a strict 12/8/14 point match preserves true bed immediately beside a
changed boundary.

The 3 mm-radius exposed-evidence closing remains for real honeycomb continuity,
but now runs at bounded model resolution only from strong structural-plus-
appearance seeds. It may add only appearance-consistent pixels with loose
structural support. A single false seed cannot expand and closing cannot bridge
through clearly changed material. Broad brightness, white-balance-related drift,
gradient, mild blur, noise, and local reflections are covered by deterministic
fixtures. Changed or uncertain pixels remain eligible; the stock is eligibility,
not foreground.
Honeycomb-local Auto fails closed when no valid reference is available. Manual
Contrast and explicit Color may use clearly diagnosed hard-ROI-only fallback
when no reference exists; a supplied mismatched reference is rejected.

Camera Trace now has one detection-and-review workflow. The former seeded
**Cutout / silhouette** mode, prepared-frame state, Add-click lifecycle, quick
blue outline, and second asynchronous verification state have been removed.
Auto, By color, and By contrast produce the candidates. With **Use grid** off,
Auto is now an orchestrator over production tracing paths rather than another
independent detector: it prepares the corrected frame once, estimates one
  material eligibility, estimates one camera-raster background, derives
  symmetric dark- and light-feature rasters,
selects one bounded source-resolution Auto threshold for each immutable result,
conditionally tries
  Color only when eligible material contains bounded non-background chroma, and
  chooses a credible verified result or fails closed. With **Use grid** on, Auto
  deliberately retains the specialized
repeated-object detector, lattice fitting, cell normalization, missing-cell
inference, and damaged/open-cell review. A stored legacy
`cutout` preference is migrated once to `contrast`; the old value cannot leave
the color picker disabled. Desktop tests redirect organization/application
`QSettings` into a unique per-worker INI file, so parallel runs neither inherit
nor mutate the operator's real preferences.

Temporary candidates are real selectable canvas items over the frozen corrected
camera frame. Click selects one, Ctrl-click toggles, empty-space click clears,
and a rubber band selects multiple direct candidates. Inferred grid positions
remain explicit and are not silently promoted by a rubber band. The inspector
checkboxes and canvas use the same detection-ID set. Smaller overlapping
candidates receive deterministic hit priority; selection survives zoom and pan,
and a new detection, Clear, camera/calibration invalidation, or creation removes
the old temporary items. While review is active, normal project objects cannot
be selected or moved; their prior selection and flags are restored afterward.
Preview candidates never enter the project document, planning cache, G-code, or
execution paths.

Starting a new detection immediately removes the preceding temporary candidates
while leaving project objects untouched. Before native fitting, the Trace panel
can switch the frozen camera display among the corrected **Camera**, exact
source-resolution production **Exposed bed** mask, exact source-resolution
**Eligible** mask, normalized grayscale, and exact production **Mask**. Exposed
bed is the same immutable array inverted to form material eligibility, not a UI
approximation. Raster Mask is the immutable 4× binary workspace passed to `RETR_TREE`,
not a reconstructed UI approximation. Its display uses its actual 4× pixel scale
so all four images occupy the same physical area. Request
IDs and the camera-review signature reject stale preview, completion, and failure
callbacks. If fitting fails after mask preparation, the camera hold and diagnostic
views remain available until Clear or the next detection; a failure before a
deliverable preview returns to live camera state.

Physical build `26c5943` confirmed that Camera, Eligible, and Normalized looked
plausible but exposed a Mask-only presentation failure: the selector and status
changed while the workspace retained the corrected Camera pixmap. Subsequent
runtime tracing and the new automated pixel regression confirmed that the exact
4× QImage was present and byte-distinct in the Mask slot. The failure occurred
when the workspace independently re-rounded the fractional corrected-image area
at 4× pixels/mm: `4 × round(area × base_ppm)` is not always equal to
`round(area × 4 × base_ppm)`, so the exact production dimensions were rejected
before `setPixmap()`. The desktop now declares the integer source-resolution
multiplier explicitly, validates the 4× image against four times the already-
rounded source raster, and retains the actual 4× transform. No preview image is
resized and the immutable production mask is not copied back into or changed by
the display path. Temporary application-log diagnostics record dimensions,
format, byte count, and a padding-neutral pixel SHA-256 for every stored Camera,
Exposed bed, Eligible, Normalized, and Mask image.

Focused offscreen verification passes **93 tests** across the asynchronous
desktop camera/Trace path, real workspace rendering, and Trace panel behavior.
Repository Ruff, `python -m compileall -q laser_aligner`, and
`git diff --check` pass. This is automated display verification only. The fix
has not yet been exercised in a physical build after `26c5943`; no new live
camera, controller, motion, arming, laser-output, cutting, or physical-accuracy
verification is claimed.

Non-grid **By contrast** no longer enters the multi-hypothesis object detector.
The corrected BGR frame is first hard-gated and reference-suppressed, then
converted to eligibility-normalized raster artwork and sent through the same
production pipeline as an imported raster:
source-neutral immutable RGBA preparation, Otsu or manual thresholding with
explicit polarity, physical connected-component and pinhole cleanup, 4× mask
reconstruction, bounded `RETR_TREE` extraction, physical contour mapping,
source-edge refinement, and the authoritative native line/cubic fitter plus all
topology checks. Each root foreground contour and all descendants form one
review candidate. Minimum area remains the raster cleanup scale. A conservative
pre-fit root filter can omit a complete indivisible tree only when its threshold
bounds and the fitter's full displacement allowance prove that maximum area or
minimum width/height cannot pass; near-limit, smoothed, and ambiguous trees stay
for the unchanged authoritative post-fit review. The one final raster-local-to-
camera affine accounts for Y direction, pixel centers, work-area origin, exact
pixels/mm, and a possible fractional edge strip; there is no camera-side contour
extraction, second fit, or post-map refit.

The camera-specific normalization model is Qt-free and does not threshold or
repair output geometry. It converts the corrected image to uint8 grayscale,
fills ineligible pixels only in the temporary background-model input, derives
its robust response scale from eligible material, forces excluded response white,
and builds a temporary model bounded to 1 pixel/mm and 512 pixels on its long
axis. It normally computes 35 mm elliptical opening and closing envelopes and
smooths each with a 4 mm Gaussian. The closing supplies the dark-feature
background and the opening supplies the light-feature background; their
midpoint is retained only as signed diagnostic context. A narrowly gated clean/flat-
field path instead uses one constant robust border level only when four-level
histogram bins, a 2 mm border band, whole-model background dominance, and
far-versus-intermediate separation all pass their conservative gates. Continuous,
quantized, and machine-border shadow cases therefore fall back to the rank
envelope.

The one-sided float32 distances are closing-minus-image for dark features and
image-minus-opening for light features. Only the larger distance wins at a
pixel; ties stay blank. This retains exclusive polarity while avoiding the old
midpoint amplitude, which could treat half of a glyph as background and let a
darker surface mark cancel adjacent sound pixels. After a three-level noise
floor, one nearest-rank 99.5th-percentile magnitude clamped to 32–64 levels
supplies the shared response scale `R`. Dark and light uint8 artwork use the reciprocal
transfer `round(255R / (R + X))`: blank/opposite-polarity is 255, response `R`
is 128, and stronger response approaches black without hard clipping. Camera
Auto now generates at most 12 thresholds from that exact normalized raster:
stabilized Otsu, Triangle, Otsu-to-class-median interpolation, and 1/3/8/16/30%
foreground-occupancy quantiles. It scores source-resolution coherence, nearby
mask/component/hole stability, specks, occupancy, eligibility-border dominance,
retained coherent area, and narrow retained foreground before any 4× work or
native fitting. A credible non-Otsu winner must clear a baseline departure margin
that grows when it adds more than two components or worsens border occupancy.
No captured or physically successful threshold byte is a candidate constant.

The Otsu baseline can advance its lowest equally optimal plateau member by at
most two unused levels inside an empty histogram gap when the low class lacks
interpolation headroom. Normal polarity measures the selected foreground span;
inverted light polarity measures above the low background endpoint. Camera Auto
uses only eligible pixels; ineligible pixels are forced background before cleanup
and again by a nearest-neighbor gate at 4×. With no eligibility, ordinary
imported-raster Otsu and manual-threshold semantics remain unchanged.

The shared 4× mask path now constrains bicubic reconstruction to its proper
role: localizing a boundary inside the one-source-pixel transition band. Every
cleaned source pixel whose complete 3×3 neighborhood is foreground or
background is nearest-neighbor locked to that same classification at 4×. This
prevents cubic ringing from inventing a positive-area hole or island where the
source mask contains no boundary, without filling real holes, joining gaps,
changing component cleanup, or replacing subpixel edge localization.

The physical stencil failure `A retained raster contour has fewer than three
distinct points` was traced to the 4× reconstruction itself. Bicubic grayscale
interpolation can overshoot near a retained edge inside the deliberately dilated
one-source-pixel component gate; one nominal-background sample can therefore
cross the threshold and become a one- or two-point, zero-area `RETR_TREE`
contour even after base-resolution component cleanup. Shared source-neutral
contour pruning now removes only nodes with fewer than three distinct trace
points or exactly zero trace-pixel polygon area. Positive-area contours remain
eligible regardless of size. The complete `next`, `previous`, `first_child`,
and `parent` hierarchy is rebuilt in original sibling order. A degenerate node
whose subtree contains legitimate geometry causes its entire root tree to be
rejected rather than reparenting descendants and inventing a new even-odd
topology. Quick Preview, exact imported-raster vectorization, and camera raster
strategies all receive the same repair and diagnostics.

The normalization regression is demonstrated by four long dark glyphs on a
continuous camera gradient with within-glyph tone variation and darker surface
marks. The former midpoint path retained **20.6%** of the known solid cores at
manual threshold 128 and fragmented them into **97** foreground components; the
one-sided winner-gated responses retain **100%**, reject all tested clean
background, and produce the expected **four** components. A separate exact-mask
fixture starts with a solid source rectangle and a selected 2×2 intensity
plateau at level 127 under threshold 128. Unconstrained cubic reconstruction
reached level 145, created 24 background pixels, and produced a positive-area
child hole; the homogeneous-interior guard retains one root and no hole. A
camera-glyph integration fixture preserves four real roots and its one intended
hole, and the exact 1,170 × 444 Coleman source at threshold 122 remains 50
components, 50 roots, and zero descendants in a local read-only diagnostic.

The pixel-vectorization source, exact prepared-mask value, and result are source-
neutral and defensively immutable on bytes backing stores. Imported assets wrap
that contract with their real `RasterAssetIdentity` and exact encoded-byte
verification; live normalized camera pixels use a versioned content-derived key
and do not invent file metadata, paths, or SHA provenance. Non-grid contrast
exposes bounded-Auto/manual threshold and local light/dark response controls, visibly
disables the hue controls, and uses the native raster-vector output without a
border offset. With
**Use grid** enabled, By contrast deliberately retains the specialized
multi-mask object/grid detector, classification, normalization, and gap
  inference. By color remains the explicit operator-controlled color path and
  obeys the hard ROI.

For non-grid Auto, both raster polarities reuse one immutable normalization and
background estimate while each owns its exact immutable
`PixelVectorizationSource`, bounded Auto threshold selection, prepared mask, physical
minimum-feature cleanup, 4× reconstruction, hierarchy, source-edge refinement,
  and native validator. Color is attempted only when eligibility-scoped weighted
  HSV/Lab evidence covers enough material, at least 60% of the chroma weight lies
  in one ±14-hue window, and that window is at least 1.5× the strongest separated
  competitor. Its mask must cover 0.2–35% of eligible material and at most 25%
  of the eligibility boundary. A credible Color result must beat the best
  credible raster result by eight points. Auto ignores saved manual color
  samples and uses hue tolerance 14 and minimum saturation 45 for this bounded
  attempt; explicit **By color** keeps the operator's controls but obeys the hard
  ROI.

Completed strategies are scored deterministically without a positive candidate-
count term. The score is `40V + 20F + 15B + 10A + 10S + 5W - P`: `V` is the
valid independent-root ratio, `F` is useful foreground occupancy, `B` is
non-foreground border quality, `A` is useful retained physical area, `S` is the
fraction of retained roots at least four times the minimum feature area, `W`
is the in-frame candidate fraction, and `P` is a 35-point frame/background
penalty when both foreground and border occupancy reach 75%. A strategy with no
authoritative native candidate or a score below 70 is rejected, and one with at
least 95% foreground plus at least 75% border occupancy is rejected as
background-dominated. Only post-ROI, post-reference, within-output, verified
candidates contribute positive evidence. Stable
ties prefer dark raster, then light raster, then Color. The result message names
the selected strategy, exact Auto threshold or hue/tolerance, valid count, and omitted
invalid/filtered count; bounded per-attempt metrics and failure reasons remain
internal. Serialized Auto results retain `detection_mode=auto` while reporting
the effective selected native/Auto-or-Color options; the original request and
effective options are both preserved in diagnostics. Review-filtered roots count
as unavailable even when they did not reach topology fitting, and an all-pruned
raster attempt retains exact root count, bounds, stage, and failure reason.

A failed strategy does not abort Auto. Native fitting and every existing
frame/extrema, continuous-error, self/adjacent-arc, compound-clearance, even-odd,
and rasterized-hierarchy validator remain authoritative. Auto isolates a
failure only at an independent root-tree boundary: one root plus all holes and
islands is fitted and accepted or rejected as one indivisible unit. It never
separates a compound tree to escape validation. Other verified roots remain
available for review, while the rejected tree and its bounded reason stay in
diagnostics and are not selectable or creatable. The raster path first runs each
ordinary validator against the complete forest; only a non-complexity failure
triggers per-root diagnosis. Survivors are rebased and the unchanged global
validators run again. If all roots pass alone but fail together, the survivor
forest still fails, and every complexity-limit failure remains fatal to the
strategy.

In the Trace panel, non-grid Auto owns polarity, bounded threshold selection, and optional
color selection. Hue/sample and threshold/polarity controls are therefore
inactive, output is fixed to authoritative **Native lines / Béziers**, and border
offset is fixed at zero. Explicit **By color** alone enables hue/sample controls;
explicit non-grid **By contrast** alone enables manual threshold and polarity.
Auto with grid enabled preserves the specialized grid output and normalization
controls. Minimum feature area, review filters, native fit tolerance, output/work
authority, grid toggle, and selection controls remain available where applicable.
The read-only **Chosen threshold** value is `—` before detection, displays the
exact production byte after a successful non-grid Auto raster result, displays
`N/A` when Auto selects Color, updates on a new result, and returns to `—` on
Clear, failure, or settings staleness. Manual mode retains its editable byte and
does not receive the Auto value. The minimum area, width, and height defaults
remain 30 mm², 4 mm, and 3 mm.

Cut geometry offers two explicit commits. **Create separate vectors** creates
one editable object per selected candidate. **Create one combined vector**
creates one logical even-odd compound path containing all selected subpaths;
overlaps are deliberately preserved and are not unioned. Either operation is a
single undoable project edit. Stock-boundary creation remains a separate
single-outline, non-output path.

Focused Qt-free and offscreen regressions cover a reflective periodic honeycomb,
trusted empty-bed reference, machine surround, 20/50/84% stock coverage,
exposure/gradient/white-balance-related drift, blur/noise/highlight, empty bed,
blank stock, dark/light stencil artwork, a dark nearly vertical stencil with
reference-correlated normalized texture, appearance mismatch, closing
amplification and true-bed continuity, holes, narrow gaps, underline, hard-ROI
invariance, false warm Auto Color, real bounded Color, Auto fail-closed,
four-edge coordinate mapping, and exact Camera/Exposed-bed/Eligible/Normalized/4× Mask
switching, including complete stored-QImage and actual workspace-pixmap pixel
identity across a fractional-edge display case. Existing coverage also includes
the low-frequency background
model and its adversarial flat-field gates; dark/light reciprocal responses;
gradient, shadow, machine-border, gap, hole, dense-label, clean-raster, noisy-
solid, and true two-level cases; symmetric Otsu plateau stabilization; immutable
normalization/source/mask results; literal non-grid components; exact 4× mask
publication and reuse; degenerate-leaf pruning and hierarchy repair; independent
compound-tree isolation; conservative pre-fit review filtering; Auto's one-
background dark/light reuse and conditional Color route; native geometry and
imported-normalized-camera parity; request/signature staleness; retained failure
diagnostics; immediate old-candidate retirement; Trace panel controls; capture
and rectification timing; and the standalone raster diagnostic command.

Before integration, the broader eligibility, normalization, object-Trace,
native-raster, and desktop preview selection passed **231 tests** in **78.47
seconds**. After merging the Pi-owned execution mainline, one combined focused
Trace/Pi batch passed **383 tests** in **73.38 seconds**, and the complete local
Windows four-worker suite passed **2,843 tests** with **14 expected platform
skips** in **146.84 seconds**. Repository Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` pass. These are
deterministic Qt-free, source-level, loopback/simulator, and offscreen-widget
checks; no interactive GUI, live camera, or hardware validation is implied.

For the rank-envelope and homogeneous-interior corrections above, a fresh
Windows focused batch covering camera normalization and trace, eligibility,
shared raster vectorization, object Trace, native fit acceptance and curve
fidelity, desktop trace sources/layout/async integration, and the standalone
camera-raster diagnostic passed **328 tests** in **304.91 seconds**. Repository
Ruff, `python -m compileall -q laser_aligner`, and `git diff --check` also pass.

For the bounded Camera Auto threshold selection and exact chosen-threshold UI,
the final focused Windows four-worker batch passed **313 tests** in **235.31
seconds** across shared raster vectorization, camera normalization and Trace,
eligibility, Auto orchestration, Trace panel state, async desktop integration,
preview sources, and the standalone diagnostic. The complete Windows four-worker
suite passed **2,862 tests** with **14 expected platform/privilege skips** in
**238.22 seconds**. Repository Ruff, `python -m compileall -q laser_aligner`,
and `git diff --check` pass. This is deterministic Qt-free and offscreen-widget
verification only; no interactive GUI, live camera, controller, motion, arming,
laser-output, cutting, or physical-accuracy verification is claimed.

On the 640 × 480 correlated-texture stencil fixture at 2 pixels/mm, ten
post-warmup eligibility samples had median stage times of **25.081 ms** for
structural reference matching, **56.212 ms** for photometric compensation,
**8.871 ms** for the appearance veto, **2.258 ms** for guarded closing, and
**108.828 ms** total eligibility. Observed total eligibility ranged from
101.808–113.935 ms. The comparison remains bounded to 800 pixels, 2 pixels/mm,
and at most 50,000 deterministic photometric-fit samples. These timings are
development-machine samples, not performance guarantees or physical-camera
measurements.

At the time this correction was recorded, the reported Coleman stencil scene had
not been recaptured or replayed through a physical camera. The later 2026-08-30
operator report is recorded in the Straighten section above but lacks the details
required for formal physical acceptance. No controller, motion, arming,
laser-output, cutting, or physical-accuracy test is retroactively claimed for
this earlier correction.

## Active development-release trigger filtering

The `Publish E3 development update` workflow still runs automatically for
application/source, packaging, release-workflow, and runtime-dependency changes
on `main`, and it retains manual `workflow_dispatch`. Its push trigger now uses
an explicit `paths-ignore` list for normal CI workflows, repository templates
and instructions, non-installed documentation, tests, and the development-only
requirements file. GitHub applies `paths-ignore` only when every changed path
matches the list, so a mixed commit containing any unignored product-affecting
path still builds and publishes the Windows installer, Linux AppImage, update
manifest, and `e3-development` prerelease. Job definitions, main-branch guards,
and `cancel-in-progress: true` are unchanged.

Focused update/workflow verification passes **8 tests**. PyYAML independently
parses the workflow and verifies its trigger, concurrency, three-job structure,
and Windows/Linux-to-publish dependency. Ruff on the affected test,
`python -m compileall -q laser_aligner`, and `git diff --check` pass. No E3
application, controller, motion, arming, laser-output, packaging, manifest, or
publishing implementation changed; no package build or physical test was
required or performed.

## Active raster-vectorization source-edge localization

The adaptive local per-span tolerance experiment from commit `4039047` is
fully reverted. The 0.10 mm user-facing native fitting tolerance again supplies
the existing fixed 80% internal budget (0.08 mm at the default) to every span.
The earlier straight-edge recovery, curved-span distribution centering and
bounded Newton refinement, two-stage responsive preview, native line/cubic
persistence, continuous maximum-error proof, frame/extrema validation,
self/adjacent-arc and compound topology checks, hierarchy validation, project
path, and planning behavior remain in place.

Before an authoritative exact fit, each independent threshold contour now
retains its original topology while eligible samples are localized against the
original source raster. The local normal uses 1.25 source pixels of contour
support. The exact composited grayscale and alpha fields are bilinearly sampled
from -1.25 through +1.25 source pixels at 0.125-pixel intervals, using the same
manual/Otsu/alpha threshold, inversion, and alpha-gate semantics as
segmentation. An accepted sample must contain exactly one strong outward
foreground-to-background crossing, sufficient endpoint margin, contrast and
slope, bounded reverse variation, and a displacement no larger than 0.6 source
pixel. Profile work is bounded in 8,192-point chunks. Flat, noisy,
multiple-crossing, out-of-frame, and otherwise unsupported samples stay at the
threshold position.

The original threshold contour remains the classification authority. Detected
hard corners and their adjacent support points, persistent straight runs, and
straight spans promoted between hard anchors are never shifted. Nested parent
and hole contours are conservatively not refined. The source-edge maximum shift
is added to the ordinary smoothing/fitting/preview deviation envelope, and all
existing frame, continuous-error, topology, clearance, and 4× raster-hierarchy
validation still runs. Quick Preview remains the unchanged display-only
threshold outline; source-edge localization runs only in the exact worker.

On the exact 1,170 × 444 Coleman source (SHA-256 beginning `e72143e3`) at
80.0 × 30.358974 mm, manual threshold 122 and no smoothing, the critical P-bowl
span retains 147 samples and one cubic. The restored cubic-to-threshold
maximum/RMS/signed-mean errors are 0.066449/0.033027/-0.005640 mm. The
threshold-to-source displacement is 0.011856 mm maximum, 0.006583 mm RMS, and
0.005994 mm mean outward. This is only 20% of fit RMS, so source localization is
not the dominant total-error term, but it is comparable to the systematic
centering bias. Against the refined source edge, the restored cubic measured
0.068650/0.034286/-0.011571 mm; the refitted cubic measures
0.064342/0.035779/-0.004843 mm. Maximum error falls 6.3% and systematic inward
bias falls 58%. The complete P bowl changes from 15 to 14 segments rather than
adding pixel-scale pieces.

Exact Coleman A/S source-relative maximum and RMS errors improve from
0.073212/0.022554 to 0.059191/0.020840 mm and from 0.072049/0.025475 to
0.065321/0.022589 mm. Their segment totals fall 23→19 and 32→31. The E retains
72 segments and identical 0.013377/0.002304 mm source-relative maximum/RMS
because its straight and corner evidence is protected. The diagnostic image and
full method are recorded in
[`docs/diagnostics/COLEMAN_SUBPIXEL_SOURCE_EDGE.md`](docs/diagnostics/COLEMAN_SUBPIXEL_SOURCE_EDGE.md).
The complete real source changes from 930 to 913 native segments.
The complete real large Coleman `o` crop changes from 48 to 47 segments while
aggregate source-relative maximum/RMS error improves from
0.081235/0.025569 mm to 0.079563/0.022141 mm. A separate supersampled analytic
D-bowl known-geometry control also improves maximum and RMS error without
adding segments.

One prepared-source timing session measured useful Quick Preview at a 0.0358 s
warm median. It remains outside source-edge localization. Two warm exact-fit
runs measured 4.677 s at the restored threshold-only baseline and 5.697 s with
source-edge localization. Focused real Coleman P/A/E/S, analytic known geometry,
one-pixel phase translation, rotated/scaled resolution convergence, ambiguous
profile rejection, chunk equivalence, straight/corner preservation, fitter,
dialog, and desktop vectorization verification passes **118 tests** with four
xdist workers. Broader native-path, project/history, toolpath, planning/golden,
digest/cache, preflight, and desktop native-path verification passes **408
tests**, also with four workers. Repository Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` pass. This is
automated Qt-free/offscreen authoring analysis only;
no interactive GUI, camera, controller, motion, arming, laser-output, or
physical-accuracy test was performed or is claimed.

## Active raster-vectorization curved-span centering

The exact cubic fitter now checks the spatial distribution of a material
candidate's error before accepting an otherwise tolerance-compliant
chord-length correspondence. The additional gate uses physical arc-length
weights, RMS error, signed normal bias, and the fraction of signed error on one
side. A materially biased candidate receives up to three passes through the
existing bounded Newton reparameterization path. The conservative continuous
maximum-error proof remains authoritative for every accepted line and cubic;
the user-facing 0.10 mm default and 0.08 mm internal fit budget are unchanged.

The diagnosed Coleman `P` bowl is OpenCV outer contour 27 from the exact
1,170 × 444 source at 80.0 × 30.358974 mm, manual threshold 122, no smoothing,
and 0.10 mm fitting tolerance. Source pitch is 0.0683760684 mm horizontally and
0.0683760676 mm vertically. Its 392 samples span source pixels
`(176.125, 327.125)` through `(195.875, 361.875)`. The four hard corners are at
samples 49 `(186.875, 339.125)`, 219 `(193.625, 354.875)`, 367
`(178.625, 327.125)`, and 380 `(176.125, 329.625)`; the protected neighboring
hard anchors are 48-50, 218-220, 366-368, and 379-381. No persistent straight
run is classified on this contour.

At the centering-only baseline, the old and new native sequence was
`LLCCCCLLCLLCLLC`; recursive splits remained
three and verified merges remain zero. The defect was inside the single cubic
from sample 220 to 366, not at an anchor, split, or merge. That span covers 147
threshold-boundary points across the lower transition, outer right arc, and top
transition. Previously it passed on its first chord parameters, so Newton never
ran: conservative max error was 0.076471 mm, nearest-point RMS was 0.035119 mm,
and mean signed normal error was -0.012450 mm (62.6% of samples on the inward
side). One Newton pass now precedes acceptance: conservative max is 0.066740 mm,
nearest-point RMS is 0.033027 mm, signed mean is -0.005640 mm, and same-side
fraction is 55.8%. Maximum error remains at source pixel
`(195.875, 341.625)`, parameter approximately 0.343, showing that the change
corrects correspondence within the long curve rather than moving its anchors.
Across the full contour, conservative max/RMS changed from
0.076471/0.031228 mm to 0.066740/0.027719 mm; independently projected
nearest-point max/RMS changed from 0.076252/0.027764 mm to
0.066449/0.026714 mm.

On the same prepared payload and Python 3.14 interpreter, five measured runs
after warmup changed the exact-fit median from **7.270 seconds** on starting
`main` to **7.668 seconds** with centering, a 5.5% increase. Useful quick
preview remained approximately **0.05 seconds** (0.051-second median after the
change) because it does not execute cubic fitting. Focused Coleman/synthetic
`P`/`D`, translated phase, rotation, shallow-arc, straight-`E`, rounded-`C`/`O`,
and existing fitter verification passes **39 direct tests**. The complete
focused raster/fitter/dialog/desktop selection passes **154 tests**. Broader
native-path, project/history, toolpath, planning, golden, digest/cache, and
desktop native-path verification passes **400 tests**, all with four xdist
workers. Repository Ruff, `python -m compileall -q laser_aligner`, and
`git diff --check` pass. This is automated Qt-free/offscreen verification only;
no interactive GUI or physical hardware test is claimed.

## Active raster-vectorization straight-edge recovery

The exact native fitter now classifies persistent straight source runs before
anchor selection on every contour, including contours that already have hard
corners. This fixes the Coleman stencil `E`, whose single detected hard corner
previously caused `_fitting_anchors()` to return before straight-run discovery;
the older independent 10%-of-perimeter minimum also excluded its 3.46 mm top
edge. Classification is orientation-independent and scale-aware: a candidate
must have material physical/source-pixel/oversampled extent, bounded full-run
orthogonal residual at the stricter of the fit tolerance and source
quantization allowance, and bounded local plus full-run directional change.
Nearby raster-step fragments merge only when the complete combined span passes
the same evidence. A classified span is continuously revalidated and persisted
as a native line. Without that positive evidence, a shallow curve remains a
cubic even when its chord alone is within the 0.08 mm internal fit budget.

The exact 1,170 × 444 Coleman development stencil at 80.0 × 30.358974 mm,
manual threshold 122, no smoothing, and the default 0.10 mm native fitting
tolerance produced a 2,038-sample canonical outer `E` contour. Its bounding box
was `(-18.316239, -1.598291)` to `(-13.068376, 6.076923)` mm, with one hard
corner at sample 335. The source-supported bottom (samples 59-275), top
(1397-1599), and merged left (1684-2021) runs measured 3.692308, 3.460072, and
5.774845 mm, with maximum chord residuals 0, 0.017009, and 0.017094 mm. The
persisted `E` changed from `LLCCCCCCCCCCCCCCCC` to
`LCLLCLCCCCCCLCLCLC`: straight source arms are lines while corner transitions
remain cubic. Its maximum validated fit error changed from 0.075076 to 0.075472
mm and remains below 0.08 mm. Rounded Coleman `C`/`O` regions, analytic rounded
joins, rotated straight edges, quantized/noisy rotated edges, and a shallow
0.05 mm-sag curve are covered explicitly so short curve plateaus are not
promoted to lines.

On the same prepared payload and interpreter, two independent five-run sets
after one warmup each measured a combined exact-fit median of **3.998 seconds**
at starting commit `19aa9bab` and **1.211 seconds** with the repair. Focused
verification passes **107 raster/fitter/dialog/desktop tests**; broader native-path,
project, toolpath, planning, golden, digest/cache, and desktop-workspace
verification passes **366 tests**. Repository-wide Ruff,
`python -m compileall -q laser_aligner`, and
`git diff --check` pass. No quick-preview authority, fitting tolerance, Newton
requirement, continuous proof, frame/extrema check, topology/clearance rule,
hierarchy rule, native persistence, project/history, planning/cache, G-code,
machine, motion, arming, or output-safety contract changed. This is automated
Qt-free/offscreen verification only; no interactive GUI or physical hardware
test is claimed.

## Active raster-vectorization responsiveness recovery

**Trace image to vectors…** now uses two bounded background stages. The first
decodes and displays the exact source, production foreground mask, and a
preview-only approximation of the extracted contour tree. The second ignores
that approximation, reuses only immutable prepared grayscale/mask/raw-contour
data for identical options, and runs the complete authoritative native
line/cubic fitter plus the existing continuous-error, frame/extrema,
self/adjacent-arc topology, compound-clearance, preview-flattening, and 4×
raster-hierarchy checks. The quick geometry has no native subpath or project/
planning conversion and cannot enable **Create vectors**. It is replaced by the
verified result only after exact completion. Quick and exact workers each
coalesce to the newest pending settings; stale results and cancellation cannot
create or replace project geometry.

The portable vectorizer exposes opt-in, non-persistent timing with elapsed time
and call counts for decode/preparation, mask generation, contour extraction,
corner classification, cubic fitting, Newton reparameterization, continuous fit
validation, adjacent merging, authoritative topology, preview flattening, and
raster hierarchy validation. Prepared white-composited grayscale is cached with
the verified source. The exact fitter hoists immutable derivative differences
out of Newton's point loop and avoids scalar `np.clip` overhead while retaining
current-main reduction order. The five-million-step continuous-validation
budget and proof are unchanged: Coleman profiling measured that proof at about
0.10–0.14 seconds, not as the latency bottleneck.

On the 1,170 × 444 Coleman development stencil at 80.0 × 30.358974 mm and
threshold 122, authoritative current `main` took **8.16–8.47 seconds**
unprofiled for the first/final result. The new core path measured **0.020
seconds** to verify/decode, **0.046 seconds** to the useful quick mask/outline
(**0.067 seconds** cumulative), and **5.96 seconds** for the background exact
fit (**6.03 seconds** cumulative). The normal dialog's 160 ms debounce puts the
expected first visual near **0.23 seconds**. The final Coleman native-geometry
JSON and metadata JSON matched starting commit `32bd1ec` byte-for-byte. No
fitting tolerance, corner rule, Newton requirement, continuous proof, topology
rule, hierarchy rule, native persistence, transform, project/history,
planning/cache, or output-safety contract changed.

Focused verification currently passes **144 raster/fitter/dialog/desktop
tests** and **235 native-path, project/history, planning, digest, cache, and
toolpath tests**. This is automated Qt-free and offscreen-widget verification
only; no interactive GUI or physical camera/controller/motion/laser test is
claimed.

## Active desktop Import and status-message layout fixes

The native File menu now exposes one **Import** submenu containing **SVG…**,
**G-code…**, **LightBurn project…**, and **Raster image…**. The existing import
actions remain authoritative, including their callbacks, icons, enablement, and
`Ctrl+I` / `Ctrl+Shift+I` shortcuts; only their File-menu grouping and displayed
labels changed. The old direct File-menu import entries are absent.

The bottom status bar now responds to every `QStatusBar.messageChanged` signal.
While a temporary message is active, it constrains the permanent job-progress
widget to its readable minimum and hides editing details plus runtime and zoom
readouts only as required by the available width. Active preparation/execution
progress remains the highest-priority permanent widget. Clearing or timing out
the message automatically restores the normal responsive labels. Messages that
cannot fully fit beside progress at the narrowest supported width are clipped
before the permanent widgets and retained in the status-bar tooltip rather than
painting over other status text.

This is presentation-only desktop behavior. It changes no import parser or
project transaction, planning, controller, motion, homing, arming, laser output,
camera service, machine authority, or project geometry behavior. Verification
passed **58 focused tests** across the real menu/action construction, responsive
status geometry, control-surface source contract, reusable import review, and
all four desktop import integrations. Repository-wide Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` passed. This is
automated offscreen-widget coverage only; no interactive GUI or physical
hardware test is claimed.

## Active Objects layer-color swatch recovery

The Objects table again presents each assigned operation layer as a visible
24 px color button beside the layer name. The exact earlier implementation was
recovered from local branch/worktree commit `ebfac234`; the native Bézier branch
had been independently rebased onto merged raster-fit work and never included
that sibling UI commit, leaving the common ancestor's static 12 px icons in
place. Only the layer-color portion was restored; the recovered commit's
unrelated File-menu grouping was not copied.

The button sends its row's assigned layer ID to the existing `LayerPanel` color
chooser. Cancel emits no edit. A valid choice continues through the queued
`layerEdited` signal, `E3MainWindow._layer_edited()`, and `UpdateLayerCommand`,
so one undoable shared-layer change refreshes every Objects swatch, the
Cuts/Layers table and selected color control, the bottom palette, and workspace
vectors. Object selection and layer assignment remain independent, and speed,
power, passes, output state, visibility, scan settings, and power-correction
settings are preserved. Raster IMAGE and native cubic PATH rows use the same
assigned-layer control; raster-vectorization preview-overlay colors remain
separate and unchanged.

Focused Windows/offscreen coverage passed **64 tests** across Objects/Cuts,
layer-edit routing and history, native/raster workspace rendering, and raster-
vectorization UI. The complete Windows Python 3.14 suite passed **2,591 tests**
with **14 expected platform/capability skips**. Repository-wide Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` passed. This is
automated offscreen-widget verification only; no interactive GUI, camera,
controller, motion, homing, arming, laser-output, or physical test was performed
or is claimed.

## Active remove-simulation-mode milestone

Production simulation has been removed as a runtime and user capability. The
current configuration model and packaged default template accept only the real
serial-family transport boundary; first-run requires an explicitly saved real
machine and exits when canceled. `AppContext` constructs only local or remote
real camera services. Missing controllers and cameras remain offline and expose
their real errors rather than producing fake success or image state.

The former controller peer and transport live under `tests/fakes/` and are
injected only by tests. The product package no longer contains simulator camera
or generated-frame helpers, generated/frozen camera UI, or a camera test-frame
API. Legacy `simulation: false` configuration is normalized away. Before any
desktop runtime, credential, camera, or controller service is constructed,
`simulation: true`, a legacy simulator backend, an active saved simulator, or a
simulator-only registry opens an explicit recovery wizard. Recovery initially
selects nothing: the operator must select a configured physical machine or
create a new safe physical snapshot. Finish atomically replaces configuration,
retires simulator records with an exact one-time backup, and rolls back the
configuration, registry, credential, backup, and completion marker together on
failure. Cancel leaves those files untouched and exits without constructing a
runtime. Inactive simulator entries beside an active physical machine retain
their existing automatic atomic retirement behavior.

Normal recovery from a legacy configuration in the replaceable application
directory writes the repaired configuration into the upgrade-preserved user
state; an explicitly supplied `--config` is repaired in place. Both destinations
are stale-checked before the transaction writes. Both browser and desktop entry
points now construct `CoreRuntime`, whose active
saved-machine snapshot is the controller/work-area/laser and running-identity
authority passed to `AppContext`. The packaged and Python controller-port
defaults use the explicit `SELECT_CONTROLLER_PORT` sentinel. With no saved
registry, that sentinel and the former implicit `/dev/ttyUSB0` default are setup
errors; E3 does not create a plausible machine from either. An explicitly saved
physical `/dev/ttyUSB0` endpoint remains valid. Project, machine-registry, and
material schemas remain unchanged.

Every normal browser and desktop product entry point now grants process hardware
authority unconditionally. The browser parser no longer exposes `--hardware`,
and the normal shell/service entry points require no mode flag. The longstanding
`run-hardware.sh` and `run-desktop-hardware.sh` filenames remain only as exact
aliases to their normal launchers for compatibility; they cannot select a
different runtime. Desktop installation creates one normal application entry.
Hardware capability does not eagerly connect, move, Home, arm, or emit output;
`machine.allow_motion`, coordinate trust, preflight, exact-program authority,
temporary arming, bounds, STOP, and `M5` remain unchanged. Dedicated Pi
controller-owner services retain their separate explicit hardware gate.

Focused simulator-recovery, saved-machine authority, desktop startup, CLI,
camera-boundary, configuration, and first-run verification passed. The final
launch-authority follow-up passed **104 focused CLI/runtime/launcher tests** and
**284 focused machine-safety/runtime tests** across the internal hardware gate,
real disconnected-controller failures, strict GRBL/Marlin transcripts and
dialects, transport selection, and saved-machine authority. The complete
Windows Python 3.14 suite passed **2,352 tests** with **14 expected platform
skips**.
Repository-wide Ruff, `compileall -q laser_aligner`, and `git diff --check` also
passed. No physical controller, camera, motion, arming, or laser-output test was
performed or is claimed; unavailable hardware remains honestly offline while a
valid saved machine retains offline authoring and project editing.

The desktop startup-order follow-up inspects an explicitly supplied legacy
configuration named `default.json` before classifying any configuration as the
packaged first-run template. The actual packaged template still enters ordinary
first-run when no preserved configuration exists, and the configuration written
by first-run is inspected again before bridge credentials or `CoreRuntime` /
`AppContext` construction. Canceling either setup path exits before runtime
construction. Focused simulator-recovery and first-run verification passed **36
tests**. The complete Windows Python 3.14 suite passed **2,358 tests** with **14
expected platform skips**; repository-wide Ruff, `compileall -q laser_aligner`,
and `git diff --check` also passed. No physical hardware test was performed or
is claimed for this startup-only correction.

## Active native cubic Bézier path foundation

Project schema 3 persists PATH/POLYGON geometry as one validated, Qt-free native
representation containing line and cubic segments, open or closed subpaths,
compound paths, and an explicit `evenodd` or `nonzero` fill rule. Coordinates
remain normalized in object-local space while the existing `Transform` remains
the authority for size, both mirrors, rotation, and translation. Schema-1 and
schema-2 `geometry.polylines` load as equivalent native line-only subpaths in
memory; opening an old file does not rewrite it, and the next explicit save
writes only canonical schema-3 native geometry. Existing SVG, G-code, and
LightBurn polyline constructors pass through the same immediate compatibility
conversion and retain no second legacy geometry copy. Schema 3 is intentionally
forward-incompatible with older E3 builds that understand only schema 2; those
builds reject it rather than silently losing native path data.

The workspace builds `QPainterPath` line and cubic elements directly, applies
the persisted fill rule, and does not flatten native curves for display. Native
objects continue through project cloning, add/replace/delete, duplication,
grouping, layer assignment, object-level transforms, save/reopen, recovery, and
undo/redo as native geometry. Individual anchor/handle editing and native SVG
curve preservation remain follow-up work.

The native Objects panel exposes **Trace image to vectors…** only when exactly
one imported IMAGE object is selected. Its window-modal Raster Vectorization
dialog shows the original raster, generated foreground mask, and vector overlay.
Magenta, cyan, yellow, white, and black presets plus 0–100% opacity are
preview-only and repaint locally without rerunning fitting or changing geometry,
metadata, layer power, or output authority. Automatic Otsu, manual 0–255, and
usable-alpha detection are available with inversion, alpha cutoff, physical
minimum-feature area, smoothing, a millimetre fitting tolerance, outer-only or
full hierarchy output, and Replace or Keep/optionally-hide source handling.
Preview work is debounced and coalesced independently for one quick and one
exact worker plus the newest pending settings. The quick mask/outline remains
visible while exact verified fitting continues; only the verified result can
enable creation. Cancel performs no project mutation.

Imported-raster vectorization retains its fitted straight/cubic result instead
of destroying curves at the former `_fit_and_flatten_contour()` seam. The stages
canonicalize the complete closed-contour cycle, classify only corners persistent
across physical scales, add generic straight-run anchors, fit bounded line/cubic
segments with shared tangents at non-corner joins, validate/convert that fit to
one authoritative native subpath, reject exact cubic extrema outside the
image-local frame, and derive bounded preview points from that native geometry
without clipping. Bounded exact cubic self/adjacent-arc checks and adaptive
physical-space clearance prove the authoritative contour forest before the
existing 4× rasterized hierarchy comparison. Source- and 4×-resolution budgets
reject pathological masks before full allocation. Parent/depth/hole state and
quality metrics remain attached to each native subpath; no fitted cubic is
replaced by persisted polyline samples.

The raster fitter now incorporates only the compatible quality ideas from
historical reference commit `4310769`; that commit was neither merged nor used
as a whole-file source. Current-main canonicalization, physical-distance/
tolerance-aware corner classification, three-sample hard-corner anchoring,
generic straight-run anchors, shared non-corner tangents, frame-constrained
handles, exact extrema checking, native self/adjacent-arc topology, compound
clearance, hierarchy validation, native persistence, preview flattening, and
planning/cache identities remain authoritative. Inside those boundaries,
positive tangent-constrained cubic handles are refined by bounded Newton
reparameterization. Every accepted line/cubic receives a conservative
continuous proof against its target polyline using the convex hull of the
corresponding difference cubic, so a candidate that agrees only at stored
samples but forms a between-sample lobe is split. Adjacent like-kind pieces
merge only after a fresh fit proof and the current adjacent-native-arc check
pass. A separate 5,000,000-step fit-validation budget bounds that work.

Contour and metadata diagnostics now record conservative maximum fit error,
sampled mean/RMS fit error, fit-validation sample count, current-classifier hard
corners, recursive splits, verified merges, and the longest smooth fitted span;
the review dialog exposes the most useful values. Historical index/span corner
classification, historical frame/topology substitutes, the implicit
source-pixel trace-cleanup target and retry loop, the historical 0.01 mm default,
and its alternate preview/fit-error boundary were rejected as obsolete or as
unproven against current compound-contour safeguards. The current 0.10 mm
default, explicit user smoothing, current preview contribution to maximum
estimated deviation, and all downstream safeguards remain unchanged.

One compound native path preserves all selected outers and holes in the original
image-local frame with the source Transform and SHA-256. Replace/Keep/hide,
vector insertion, and any safe-layer creation are one undoable command. The
active layer is reused only when it is a visible Line layer at 0% power with
output disabled; otherwise E3 creates a visible `<image name> trace` Line layer
with the same safe defaults. The new vector and layer are selected, a retained
source remains below the vector, and its Transform is preserved. The ordinary
editable layer color remains independent of preview-overlay styling.

`object_polylines()` is the single native-curve-to-planning boundary. It applies
the complete object transform to anchors and controls first, then performs
deterministic recursive de Casteljau flattening in physical project millimetres
at **0.025 mm** tolerance. The normalized-geometry planning stage is version 2;
its dependency identity includes the tolerance and flattening algorithm version
so a stage-1 artifact cannot be reused. Downstream placement, containment,
preview, preflight, and G0/G1 generation continue to consume ordinary
`Polyline` values. Generation applies one aggregate 250,000-point normalization
budget across fresh LINE/FILL/RASTER vectors and normalized-cache hits before
publishing downstream artifacts. Closed compound subpaths must be separated by
more than the sum of their per-subpath curve envelopes plus a scale-aware numeric
margin; all-line subpaths carry no curve envelope. No controller spline or arc
command is introduced.

Before output acceptance, exact derivative-root extrema bound complete cubics in
rectangular work areas. Convex guarded polygons use recursive Bézier subdivision
and the convex-hull property, including the 0.025 mm flattening envelope. Those
checks are applied in project-local, placed machine, and controller-offset
domains in addition to the existing flattened-path checks. Compound fill
planning honors even-odd parity or deliberate nonzero winding; containment cut
ordering remains winding-independent and deepest-first. Cubic bounds are applied
per cubic, so an exact boundary line is not spuriously expanded merely because
another subpath contains a curve.

Native-path production caps are 8 levels of JSON nesting, 8,192 subpaths per
object, 100,000 segments per object, 250,000 segments per project, 250,000
flattened output points, 18 recursive subdivisions, and coordinate magnitude
1,000,000. Shape-history execute/undo/redo validates replacement-aware project
segment totals before mutation, and failed commands preserve both document and
history state. Existing raster caps remain 67,108,864 pixels in the 4× workspace,
4,096 retained connected components, 8,192 contours, 1,000,000 raw
pre-simplification points, 100,000 fitted segments, 5,000,000 bounded continuous
fit-validation steps, and 250,000 transient preview-flattened points. Limit
failures recommend simplifying or cleaning the source artwork.

This remains offline authoring and guarded planning behavior. It does not
connect, Home, move, arm, enable output, generate a job automatically, or start
execution. `machine.allow_motion`, coordinate/reference trust, exact program
authorization, temporary arming, Preview, preflight, START JOB, STOP/immediate
`M5`, controller dialects, and offline editing remain unchanged. Verification is
automated Qt-free and offscreen-widget geometry coverage only. The requested
post-rebase focused integration run passed **457 tests**. The Coleman stencil
diagnostic used the production automatic threshold (**Otsu 122**), 80.0 mm
width, 30.358974 mm height, no inversion, alpha cutoff 1, 0.05 mm² minimum
feature area, no smoothing, 0.10 mm fitting tolerance, and all contours. Its two
target rounded bars each retained a `CLCCLC` six-segment native sequence; their
maximum fit errors were **0.033948 mm** and **0.039448 mm**, their worst
non-corner join-angle discontinuities were **0 degrees**, and their centered
geometric difference was **0.038244 mm**. The complete Windows Python 3.14.4
suite passed **2,588 tests** with **14 expected platform skips**; repository-wide
Ruff, `compileall -q laser_aligner`, `git diff --check`, and
`git diff --check origin/main..HEAD` also passed. No controller, camera, motion,
homing, arming, laser-output, physical tracing-quality, or physical accuracy
verification is performed or claimed.

The historical-fitter consolidation passed **93 focused tests** across raster,
fitter, dialog, and desktop integration plus **311 native-path, topology, frame,
project/history, preflight, planning-stage, digest, cache, and golden tests**.
The complete Windows Python 3.14.4 suite passed **2,599 tests** with **14 expected
platform/capability skips**. Repository-wide Ruff,
`python -m compileall -q laser_aligner`, and `git diff --check` passed. This is
automated Qt-free and offscreen-widget verification only; no interactive GUI,
camera, controller, motion, homing, arming, laser-output, physical tracing-
quality, or physical accuracy test was performed or is claimed.

## Active Windows updater hardening

The packaged Windows updater now crosses an explicit external-process boundary
before launching the verified Inno Setup executable. E3 temporarily restores
standard Win32 DLL resolution, filters only `sys._MEIPASS`-rooted entries from a
copied child `PATH`, creates a detached process with the existing explicit Inno
arguments and installer-directory working directory, and then restores its own
DLL search state. Successful process creation is authoritative: if restoring
the dying parent's DLL state fails after the child exists, E3 logs that parent
cleanup failure and completes the handoff instead of claiming the installer did
not start. After the normal unsaved-project approval, the desktop starts the
same bounded shutdown used by an ordinary Close. Once close preparation is
accepted, the verified installer is spawned before synchronous runtime teardown
rather than waiting indefinitely for worker ownership to drain; late task
publication is already suppressed and the shared four-second process deadline is
active. A
process-creation failure after accepted terminal shutdown shows
a standalone error containing the verified installer path for manual launch,
then exits rather than presenting the stopped desktop as usable.

After rebasing this updater work onto the completed simulation-removal main,
focused updater, installer, offscreen Qt handoff, and real MainWindow drain
coverage passed **29 tests**. Focused no-simulation/runtime-authority,
`MachineService`, and exact controller-transcript regressions passed **61
tests**. The complete Windows Python 3.14 suite passed **2,368 tests** with **14
expected platform skips**; repository-wide Ruff, `compileall -q laser_aligner`,
and `git diff --check` also passed. The original installed PyInstaller
`--windowed --onedir` E3-to-visible-installer scenario has not been repeated and
is not package-verified. No physical controller, camera, motion, arming, or laser
verification was performed or is claimed.

## Active desktop layout v7 refactor

The desktop's default layout now keeps the canvas open to the status bar instead
of reserving a lower G-code/job area. Cuts, Camera, Objects, Shape, Templates,
Trace, Machine, and Material Recipes share one right-hand tabbed sidebar.
Template and Trace generation controls live beside their respective creation
workflows, while the persistent runtime strip owns Connect/Reconnect,
Disconnect, the deliberately disabled Pause control, and software STOP. Job
preparation, execution, and finishing progress is always represented in the
global status bar. At compact widths the status bar preserves progress first,
then exposes runtime, zoom, and edit details only as space allows; omitted
runtime detail remains available in its tooltip. Saved desktop geometry/state
is version 7 so older opaque dock layouts cannot recreate the removed panels.

The corrected workspace Live Overlay now offers nominal 0.5, 1, 2, 4, 5, 10,
and 15 fps selections and defaults to 2 fps. Its controller timer accepts the
nearest integer 15 fps period of 67 ms. Only one corrected-frame task may be in
flight: periodic ticks are dropped while it runs, and repeated explicit refresh
requests coalesce into at most one pending replacement. Slow correction or
network delivery therefore reduces the displayed frame rate instead of stacking
work. This is separate from the raw Live Monitor and camera capture rates.

The active UI work is rebased directly on `origin/main` commit
`e079f37b7b3249ff30b95a99f30fc7199921ea8a`, including PR #36's rule that an
offline camera reports stale bed-map status without opening the Bed Mapping
Required modal. Remote camera status parsing also tolerates the obsolete field
from a legacy physical Pi node only when it is exact boolean `synthetic: false`.
The returned mapping is copied before that field is removed; boolean `true`,
integer `0`, and every other value fail closed. Current fields are passed to
`CameraStatus` unchanged, the source mapping is not mutated, and no simulation
capability is restored.

The layout portion is presentation/action routing only, and the overlay-rate
change is camera scheduling only. The existing Preview, preflight, exact-program
authority, temporary arming, `MachineService`, guarded execution, STOP, and `M5`
boundaries are unchanged. Offscreen production-theme
layout checks covered 1600, 1080, and 900 px windows. A genuine 13 pt font audit
at 900 and 1080 px covered preparation, execution, every finishing state, status
containment, and mapped runtime-toolbar containment; Connect, Disconnect, Pause,
and STOP remained ordered and fully visible. Focused corrected-overlay,
controller scheduling, desktop layout, and async coverage passed **193 tests**.
Focused offline-camera coverage passed **9 tests**; remote-camera/status coverage
passed **65 tests** with **2 expected Linux-only V4L2 skips**; and PR #36 camera
provenance coverage passed **64 tests**. The complete Windows Python 3.14 suite
passed **2,402 tests** with **14 expected platform/privilege skips**.
Repository-wide Ruff, `compileall -q laser_aligner`, and `git diff --check`
passed. No interactive GUI, real camera, physical controller, motion, arming, or
laser-output test was performed or is claimed.

## Active Pi-owned remote job execution

Normal `e3bridge://` machine jobs now use the explicit high-level
`E3MACHINE/2` protocol and never silently fall back to the incompatible legacy
`E3BRIDGE/1` raw serial bridge. Windows `RemoteMachineService` owns exact program
preflight, bounded 64 KiB chunk upload, FINALIZE, operator START authorization,
monitoring, reconnect, and explicit STOP. The combined Pi node owns a persistent
`PiJobStore`, one `PiJobService`, and exactly one local `MachineService`/serial
session. Direct local serial continues through the original desktop
`MachineService` path.

Upload and START are separate. The Pi maps a client-generated canonical UUID to
a server-owned `.part` path, fsyncs bounded chunks, checks the declared
size/SHA-256 and strict UTF-8, runs independent local program validation,
journals the commit, and atomically renames only a verified job to `.gcode`.
FINALIZE binds the exact program digest, guarded output polygon, motion/power
flags, explicit GRBL/Marlin dialect, and current execution-policy digest. START
hashes and preflights the committed bytes again, writes `starting`, then performs
Pi-local connect/Home/park/arm/start. The later durable
`ownership_accepted = true` plus `start_accepted_at` transfers execution
ownership; the subsequent START response only reports that fact. After START is
sent, a lost or failed response is ownership-uncertain and is recovered by
querying that UUID; START is never retried blindly.

After acceptance, loss of Windows, Wi-Fi, TCP, or a monitoring client has no
controller effect and no heartbeat is required. The Pi locally streams one
command and waits for its acknowledgement before persisting progress; normal
powered completion still performs `M5`, planner barrier, Home, park, step-idle
restoration, and motor release. Explicit STOP halts further streaming and
attempts the configured controller stop plus `M5`. Detected controller
error/alarm, serial write/read failure, ACK timeout, corrupt stored data, or
runner/completion failure halts further streaming, attempts a best-effort `M5`,
invalidates controller trust as applicable, and records a terminal result while
the Pi service remains alive. STOP bypasses ordinary command/store serialization.
While active, another START, connect/reconnect/disconnect, Home/park, jog, manual
command, calibration motion, realtime sample, and stepper hold are rejected;
status and STOP remain allowed.

The durable states are receiving, prepared, starting, running, stopping,
complete, failed, stopped, and interrupted. A Pi process restart converts any
persisted active state to interrupted and never restores execution authority or
auto-resumes. This does not guarantee that a sudden process/power failure can
deliver software cleanup to an independently powered controller; physical
E-stop/interlock authority and operator attendance remain mandatory. Retention
is bounded to eight metadata records and the latest two terminal G-code files,
with receiving/prepared/active artifacts protected and stale `.part` files
cleaned after 24 hours. Job size is capped at 64 MiB, frame payload at 128 KiB,
and client paths are never accepted.

Protocol/store/server/desktop tests cover authentication and counted HMAC frames,
incompatible versions, traversal/malformed metadata, atomic crash recovery,
complete/partial/wrong uploads, duplicate FINALIZE/START, accepted-client
disconnect through complete remaining execution, prepared/upload disconnect,
reattach with exact persisted in-flight ACK progress, completed-offline discovery,
ordinary remote desktop shutdown with an empty observer cache, priority STOP
during ACK wait, all specified controller/serial failures, reboot interruption,
active-operation blocking, camera-client independence, local execution
preservation, and one real
`RemoteMachineService → E3MACHINE/2 → PiMachineServer → PiJobService →
MachineService` socket stack. After merging the complete Camera Trace
appearance-veto and exact Exposed-bed/4× Mask display work, the combined focused
Trace/Pi batch passed **383 tests** and the complete Windows Python 3.14
four-worker suite passed **2,843 tests** with **14 expected platform/privilege
skips**. Repository Ruff, bytecode compilation, and diff checking passed. No
interactive desktop, physical Pi, physical controller, motion, laser output,
process-kill recovery, or real network-disconnect test was performed; those
remain required before physical deployment and hardware acceptance.

## Current repository validation and deferred package check

Fast Development CI runs Windows Python 3.12 Ruff, desktop dependency/bytecode
validation, and the complete desktop-enabled suite with four bounded workers for
`fix/**`, `feature/**`, `agent/**`, `cleanup/**`, and `architecture/**` pushes.
Compatibility CI runs serial pytest on Windows Python 3.10 without desktop
extras and Windows Python 3.12 with desktop extras, plus repository Ruff, for
`main` pushes, pull requests targeting `main`, and manual dispatch. Direct
Linux/Pi components retain focused verification when changed; there is no
standing Ubuntu compatibility matrix.

The installed frozen PyInstaller `--windowed --onedir` E3-to-visible-Inno
handoff remains intentionally package-unverified because no disposable
interactive Windows environment was available. No lab installer or certificate
was applied to the development host, and the public update channel was not used
for the deferred exercise. This does not change the automated updater evidence
or any hardware, motion, arming, coordinate, bounds, preflight, STOP, or `M5`
authority.

## Historical verification record

The entries below record earlier milestones and may mention product simulation
that existed at the time. Those statements are historical evidence, not current
operator guidance or current functionality.

The desktop machine-configuration workflow is now generic across the existing
simulator, GRBL, and Marlin profiles without adding a controller dialect or a
second profile model. `MachineProfile` remains reusable motion-platform and
controller defaults, `ToolHeadProfile` remains reusable laser/tool defaults,
and each `MachineInstance` remains the operator's complete validated saved
snapshot. Machine Manager exposes the existing backend, protocol, endpoint,
timing, work-area, feed, Home/release/photo, laser, framing, offset, and guarded-
output values. Serial fields and GRBL-only step-idle settings are conditional.
Creating from profiles is distinct from editing a saved instance; neither
operation connects, Homes, jogs, arms, moves, emits, or executes work.

Machines created or duplicated through Machine Manager begin without another
machine's camera, calibration, or honeycomb-span binding, and profile-created
machines retain the existing safe-off motion/default-power/frame defaults.
Machine Setup names the immutable running machine and its machine/tool profiles,
reports current versus saved optical/calibration binding state, and offers an
explicit persistence-only action to bind the active optical profile to that
saved machine for a future launch. This action does not mutate the current
`CoreRuntime` identity or grant calibration, coordinate, motion, or output
authority. Until a matching bound profile is actually running, foreign
honeycomb support is not used as the current authoring/execution frame.

First-run onboarding now defaults to the software simulator and can instead
create a concrete saved machine from any existing physical machine profile plus
an existing physical tool-head profile. The selected schema-1 registry snapshot
is used on launch, while motion, default/frame power, and low-power framing stay
off and camera/calibration bindings stay empty. The optional network test checks
reachability only; saving performs no controller or camera action and is not
physical verification.

Normal new projects now resolve operation defaults from the immutable **running**
machine/tool profile identity, never the next-launch registry selection or
arbitrary user SQLite recipes. The curated priority is exact machine+tool, then
future explicit tool-only, then future universal records, with no tier mixing.
The existing Ender-3 S1 Pro plus generic 10 W identity retains all 13 historical
operations exactly. Other current combinations receive one visible Line layer
at `min(1000, running max work feed)` with 0% power, one pass, output disabled,
zero corrections, no air-assist assumption, and a visible instruction to
configure or apply a compatible recipe. `default_operation_layers()` and the
project/material/machine registry schemas remain unchanged. Project bounds and
machine-versus-honeycomb coordinate selection still come only from the actual
running work area and currently bound support.

Focused verification for this milestone passed **886 tests** across saved
profiles and runtime resolution, Machine Manager and Machine Setup, first-run,
material recipes, historical and resolved project defaults, coordinate/support
authority, structured preflight, `MachineService`, strict controller
transcripts, dialect/transport selection, deterministic planning goldens,
desktop async lifecycle, toolpath generation, and job planning. Repository-wide
Ruff, `compileall -q laser_aligner`, and `git diff --check` also passed. This was
automated Windows Python 3.13 verification only; no physical machine,
controller, camera, motion, arming, or laser behavior was re-verified or newly
claimed by this configuration/authoring change.

The machine core now has explicit transport and controller-dialect boundaries
without changing controller support or execution authority. The neutral
`MachineTransport` protocol remains limited to open, close, raw/line writes,
line reads, and drain. One construction-only `create_machine_transport()`
factory maps the existing saved `backend`, `port`, and `baudrate` values to a
fresh simulator, local POSIX serial, or authenticated `e3bridge://` network
transport. Bridge URI selection still happens before the local-platform gate,
so the network transport remains available on Windows while unsupported local
POSIX serial continues to fail clearly. Concrete network and POSIX imports stay
lazy, and the former `machine.serial_backend` protocol and factory imports remain
available for compatibility. No `machines.json` or configuration schema changed.

Current GRBL and Marlin command/parsing policy now lives in immutable,
Qt-neutral `ControllerDialect` values and a deterministic registry. Dialects
describe pure semantics: stable IDs, identity recognition and probes, response
classification, query/home/barrier/release/stop command policy, and existing
GRBL status, position, coordinate, and session parsing. They cannot open or
write a transport, acquire service locks, authorize output, or start work.
`protocol = auto` retains the existing startup delay and drain, GRBL-banner
recognition, ordered `$I` then `M115` probes, 1.0/1.5-second probe timeouts,
accepted responses, and fail-closed result; it sends no additional commands.
The simulator transport now delegates the same state and response behavior to a
separate in-process simulated-controller peer, leaving its transport surface as
communication mechanics while preserving its import path and observable test
state.

`MachineService` remains the sole normal safety, authorization, and orchestration
authority. It still decides when probing and writes are allowed and retains the
hardware gate, motion gate, temporary arming, program-digest authority, guarded
stream validation, command/ACK ownership, STOP epochs, cancellation, uncertain-
state/reconnect handling, job lifecycle, and all best-effort `M5` paths. A saved
machine profile continues to describe the physical motion platform and its
transport/controller settings; a tool-head profile continues to describe the
laser/tool configuration. Neither profile, a dialect, nor a transport grants
execution authority, and no stable profile IDs changed.

Focused machine-boundary verification passed 507 tests with the six expected
Windows skips for POSIX pseudoterminal/`termios` cases. That selection covered
MachineService, immutable dialects, strict GRBL/Marlin transcripts, the
simulator peer, transport selection, local/network/bridge behavior, saved
machine profiles and runtime resolution, structured preflight, deterministic
planner goldens, reconnect/runtime-strip behavior, Machine Setup/Manager, and
desktop async job lifecycle. After final parity-audit additions, the complete
MachineService/dialect/factory/transcript subset passed 269 tests. Repository-
wide Ruff, `compileall -q laser_aligner`, and `git diff --check` also passed.

An identical synthetic immediate-ACK loop measured a median 540.32 ns per ACK
before and 578.28 ns after the refactor: about 37.96 ns of policy-dispatch cost
per acknowledged line, with no registry lookup in the stream loop. That
CPU-only difference is negligible beside controller I/O and does not introduce
a generic execution framework. This refactor has automated verification only;
no physical GRBL or Marlin controller behavior was re-verified, and no new
machine/controller compatibility is claimed.

The existing SQLite `MaterialPreset` / `MaterialDatabase` system is now a
machine-aware material-recipe authoring library rather than a parallel preset
model. Recipes include the complete controlled `OperationLayer` settings,
optional stable motion-platform/tool-head profile scope, and an optional
recommended color. Compatibility is Qt-neutral and deterministic: exact
machine/tool, tool-only, universal, or incompatible by exact IDs only. The
desktop prioritizes compatible recipes, keeps incompatible rows visible for
custom CRUD, disables their Apply action, and refreshes against the immutable
running profile identity when the surrounding machine/profile UI changes.

Applying a compatible recipe performs one undoable `UpdateLayerCommand`. It
does no power/speed scaling, creates no geometry, and preserves the operation's
ID, authoring name, visibility, priority, and current output-enabled state; an
optional recommended color is the only authoring identity it may replace.
Recipes are not stored in `.e3laser` projects and grant no output authority.
Structured preflight and exact planning continue to inspect the resulting
ordinary layer values, and hand-edited layers remain supported. No controller,
motion, arming, stop, laser, G-code, JobPlan, project-schema, or execution path
changed.

The material database now uses an explicit in-place schema migration. Existing
row IDs and values are copied transactionally, old rows become universal,
new fields receive deterministic defaults, scope-aware rows with the same
material/name/thickness can coexist, and reopening is idempotent. Default
seeding is insert-only and cannot overwrite newer user data. The 13 existing
operator-supplied E3 10 W new-project operations and their exact-profile
built-in recipes derive from one curated value source while preserving every
historical layer field, order, color, correction, visibility, and output value.

Focused verification for this authoring/database increment passed 480 tests:
material/database migration and CRUD, complete default-layer parity, recipe
authority, structured preflight, deterministic planner goldens, toolpaths,
MachineService regressions, desktop material/layer integration, the real
startup-drained no-hardware action gate, dock wiring, and the full asynchronous
job-preflight desktop file. Repository Ruff, compileall, and diff checks also
pass. This increment requires no physical controller, motion, camera, arming,
or laser test.

The desktop project pipeline now has a Qt-neutral **structured job preflight**
before exact toolpath generation. `PreflightSeverity`, `PreflightFinding`,
`PreflightCounts`, and `JobPreflightReport` expose deterministic dotted finding
codes, structured context, severity counts, and one derived ready/blocker result.
`build_job_preflight_report()` inspects a detached `ProjectDocument` snapshot
against a detached `JobPreflightContext`; it does not flatten vector geometry,
decode raster pixels, build G-code, create Qt objects, contact a controller, or
change machine authority.

The report projects existing preparation rules rather than defining a second
planner. It covers project-versus-machine work-area agreement, machine versus
honeycomb-local coordinate authority, execution-grade support/frame and output-
polygon binding, calibration-profile identity, read-only bed-calibration
validity and honeycomb-support CURRENT state, layer/object/output eligibility,
operation-setting validity, configured machine work/travel feed ceilings,
bounded raster headers and aggregate resource limits, and known execution-
readiness facts. A stale bed calibration or support state blocks honeycomb-local
generation. Only provably exact local bounds for unrounded rectangles and valid
two-point lines become structured bounds blockers. Rounded rectangles, ellipses,
images, paths, and other complex geometry remain deferred with vector
flattening, fill/raster construction, placement, laser-spot correction, final
bounds, exact raster identity/decode, stream construction, and command
validation to the authoritative planner and guarded machine path.

Desktop Generate now runs snapshot, structured preflight, exact planning, and
Preview preparation under the existing owner-tokened asynchronous lifecycle. A
blocking report stops before `generate_project_gcode()` and opens a reusable,
non-modal structured findings dialog after releasing preparation ownership.
Ready and warning-only reports continue into exact planning and are embedded in
the window-modal exact Preview; warnings remain visible and non-blocking. The
view bounds rendered findings and structured context without truncating the
immutable report. STOP, project replacement, revision changes, and authority
changes retain their existing cancellation/stale-result behavior.

Focused Windows Python 3.13 verification passed **57 tests** across the core
report, reusable offscreen Qt view, embedded Preview, and planning-cache
contract; **64 tests** across the full desktop asynchronous job lifecycle;
**159 tests** across toolpath, JobPlan, staged planning, dependency digests,
cache behavior, and deterministic planning goldens; and **251 tests** across
raster assets, coordinate audit, calibration provenance/profiles, and
`MachineService` safety. Repository-wide Ruff, `compileall`, and final diff
checks are clean. This increment has no interactive GUI or physical controller,
motion, camera, arming, or laser verification and does not change their
authority or behavior.

The planner now has a behavior-preserving typed-stage spine for LINE output:
`SceneRevision -> NormalizedGeometryArtifact -> OperationArtifact ->
PlacedGeometryArtifact -> ControllerGeometryArtifact -> EncodedProgramArtifact
-> immutable JobPlan`. `SceneRevision` carries a canonical project-content
digest and the normalized, placed, and controller LINE artifacts carry
versioned dependency digests separate from their run-oriented artifact IDs.
`E3MainWindow` owns one bounded, lock-protected, in-memory `PlanningCache`
across exact background planning requests. Unchanged normalized geometry,
machine-beam placement, and controller geometry can therefore be reused while
fresh artifact metadata and the normal local/placed/controller safety
validation still run for every request. Operation wrapping, raster/fill
planning, encoded-program generation, and immutable `JobPlan` construction
remain uncached. The deterministic planning goldens remain unchanged.

Focused cache/planning verification passed **150 tests** with Ruff, compileall,
and diff checks clean before the benchmark-only addition. A repeatable Windows
benchmark using 8 LINE layers x 32 objects (256 objects), seven measured runs
per scenario, initially reported 325.598 ms median uncached generation and
317.806 ms warm identical regeneration. Cache hit/miss counts matched the
dependency model exactly, but warm identical planning improved only about
**2.4%**, showing that LINE geometry recomputation was not the dominant cost.
Profiling then identified repeated JobPlan G-code parsing as the actual hot
path. Two behavior-preserving follow-up changes first reduced each executable
JobPlan line from three word parses to one and then added a low-allocation scan
path that keeps the shared G-code regex and numeric semantics while avoiding
temporary `GcodeWord` objects and duplicate comment stripping in
`build_job_plan()`. The same 256-object benchmark improved to 221.688 ms median
uncached after the single-parse change and then to 199.619 ms after the
low-allocation scanner, with warm identical regeneration improving from
317.806 ms to 215.142 ms and then 195.586 ms. A further experiment that moved
JobPlan summary aggregates into the per-move loop produced no measurable
improvement and was rejected rather than merged. Routine optimization of this
roughly 0.20-second synthetic planning case is therefore paused; future
performance work should be driven by a user-visible slow workload such as a
large imported vector/raster job, not by chasing smaller benchmark-only gains.
These internal planning changes are automated-test and local-benchmark verified
only; no physical controller, motion, camera, or laser test was required.

The project layer now has a Qt-neutral **Importer Manifest / Registry**
foundation for foreign-file discovery before source content is committed into
an E3 project. `ImporterSpec`, `ImportCapability`, `ImportLayerManifest`,
`ImportScanManifest`, and `ImporterRegistry` provide immutable importer identity,
case-insensitive suffix lookup, file-size limits, capability declarations,
natural-size/layer scan facts, review warnings and approximations, and explicit
blocking errors or unsupported features. File manifests also carry the SHA-256
of the exact scanned source bytes. The default registry describes SVG, raster
images, LightBurn, and bounded foreign G-code using shared supported-suffix,
dialog-filter, and byte-limit constants.

LightBurn implements a bounded **scan -> strict parse** path on top of that
contract. `scan_lightburn_file()` and `scan_lightburn_project()` inspect bounded
XML structure without invoking the existing `_parse_shape()` geometry vectorizer.
The manifest reports the LightBurn format version, referenced cut layers, layer
names/mode hints/object counts, coordinate-processing facts, review warnings,
known approximations such as ellipse/rounded-rectangle/Bezier flattening and
vector-backup text, and explicit unsupported/blocking content. Embedded bitmaps,
text without a usable vector backup, unsupported shape/path types, malformed XML,
wrong extensions, and size-limit violations are surfaced fail-closed before
strict vector parsing. `load_lightburn_project()` reads the file once, performs
this scan, and enters the unchanged strict parser only when the manifest has no
blocking errors or unsupported features; the strict parser remains authoritative
for geometry conversion and detailed validation. Focused LightBurn scan/import,
manifest, and desktop-import tests reported **43 passed, 1 skipped**; the skip
was the PySide6-dependent desktop widget test in the local environment.

Foreign G-code now has the matching bounded **scan -> strict translate** path.
`scan_gcode_file()` and `scan_gcode_project()` follow modal units, absolute/
relative positioning, feed, power, laser mode, plane selection, and supported
G/M command state without calling `_motion_points()` or `_append_move()` and
therefore without sampling arcs or assembling E3 geometry during discovery.
The manifest reports source line/powered/travel counts, reconstructed feed/
power/M3-M4 operation combinations, coordinate-mode facts, stated or inferred
S-scale review information, arc-sampling approximations, and omitted controller
or work-coordinate behavior. Unsupported axes, words, G/M codes, block-delete
or checksum syntax, invalid/missing feed, conflicting or exceeded S-scale hints,
non-XY arc planes, and other known untranslatable constructs are reported
fail-closed before the existing strict translator runs. `load_gcode_project()`
reads the source once, scans it, and calls the unchanged strict translator only
when the manifest is ready. `ImportScanManifest` now also has immutable
`source_facts` for non-warning source statistics. Focused G-code/LightBurn scan
and importer plus manifest coverage reported **70 passed, 1 skipped**; the skip
was the PySide6-dependent G-code desktop widget test in the local environment.
Ruff, compileall, and diff checks were clean.

SVG now has the matching bounded scan and exact-source strict adapter.
`scan_svg_file()` hashes the exact bounded bytes and delegates to the capped SVG
parser only to produce detached geometry facts; it never constructs a
`SceneObject`. Natural physical dimensions, path/point counts, viewBox and
coordinate mapping, flattening approximations, parser errors, and incomplete
content are represented in the manifest. Existing fail-closed SVG semantics are
preserved: parser warnings become review blockers, and `load_svg_project()`
verifies the approved digest before the authoritative strict parse and native
object conversion.

Raster image discovery now reuses the bounded stable encoded-payload and header
metadata contract. `scan_raster_file()` reports exact encoded-byte SHA-256,
format and pixel dimensions, bit depth/channels/orientation/decode budget, and
the desktop's existing fitted-size and grayscale/dither facts without decoding
pixels or creating a project object. The post-review
`read_raster_asset_payload()` call requires the approved digest before any layer,
active-layer, history, selection, or object mutation. Newly created raster
layers are explicitly output-disabled; encoded-size, dimension, header,
decode-budget, display sizing, and one-/two-command undo behavior otherwise
remain unchanged.

All four formats now use that shared discovery contract in one native
desktop pre-import review flow. After file selection, the desktop runs the
format's bounded file scan and presents a reusable window-modal
`ImportReviewDialog` before invoking the existing strict loader or changing the
active authoring tool. The dialog shows source identity/size/format/capability
information, discovered layers or reconstructed operations, source and
coordinate facts, warnings, approximations, unsupported features, and errors,
including explicit empty states for facts a format does not report. Errors or
unsupported features disable **Import**; valid and warning-only manifests still
require an explicit **Import** action. Rendering is presentation-bounded to the
first 200 layer/operation rows and first 200 entries in each repeated fact or
message section; exact omitted counts remain visible and the immutable manifest
itself is not truncated. Cancel and blocked review return before
project layers, objects, history, selection, active layer, or creation/point-
pick authoring state changes. The existing strict SVG, raster, LightBurn, and
G-code paths remain authoritative and verify that newly read bytes match the
reviewed manifest's SHA-256; a changed source aborts without project/history/
selection/authoring mutation. Each format retains its existing undo/redo
transaction granularity. No project schema, controller path, motion, arming,
execution, or laser behavior changed. Focused Windows Python 3.13 verification
passed **204 tests** across the importer manifest, all four bounded scanners and
strict paths, the reusable offscreen Qt dialog, all four desktop integrations,
and their source/documentation contract. An additional raster-focused toolpath
run passed **32 tests** with 39 deselected.

Coverage includes explicit approval, warning-only acceptance, independent error
and unsupported-feature blockers, Cancel preservation of document/history/
selection/authoring state, deterministic UI truncation with exact omitted counts,
raw-byte digest generation and propagation, same-size source replacement after
approval, scan-before-strict ordering, strict-parser/probe rejection, and each
format's existing undo/redo behavior. Repository-wide Ruff, package compileall,
and diff checks also pass.

This flow is automated-test and offscreen-widget verified only; it required no
physical controller, motion, camera, or laser test.

Machine Setup now has a sixth **Coordinate Audit** tab after Accuracy
validation. Its refresh, JSON report copy, and clicked-point inspector are
read-only; only the explicit **Home / park and capture audit view** action uses
the existing laser-off parked precision-capture path. The audit reports the
detached running saved-machine/profile identity, expected and actually active
calibration profiles, controller/GRBL coordinate state, work and guarded beam/
carriage authorities, camera/lens/bed-map state, accepted support geometry, and
only the explicitly configured `machine.honeycomb_span_mm`. A missing physical
span or expected/active calibration mismatch is an explicit readiness blocker.
Bed Mapping now displays that same saved-machine span read-only and supplies it
unchanged to automatic four-edge detection and the three-hint fallback. An
unset span displays **Not configured** and blocks both detector workflows before
detection and without controller work, directing the operator to Machine
Manager instead of inventing a 190/191 mm value.
Captured evidence retains Home/park result, MPos/WPos/WCO, workspace/G92,
commanded-versus-reported error, before/after stability, timing, and bed-map
identity after normal motor-release cleanup clears current coordinate trust.
`MachineService` obtains these diagnostic samples with only the GRBL realtime
`?` byte through its existing transport, including `e3bridge://`; malformed or
missing frames fail the sample without granting authority or changing
controller state. Sampling refuses a running streamed job under the shared
command lock before transmitting the realtime byte and preserves job,
transport, coordinate, session, reconnect, authorization, and log state. The
audit overlay adds machine/work, guarded output, accepted support, and positive-
axis references; the shared Bed Mapping overlay remains axis-arrow-free by
default. A clicked audit point is bound to the published image, bed-map digest,
and accepted-support state/frame identity. It clears when a new audit capture
starts or any of that evidence changes, and copied reports recheck staleness so
they cannot retain an obsolete point. Follow-up review coverage now proves the
complete Home/park, before-sample, raw-burst, after-sample, motor-release, and
deferred-processing order; rejects capture-evidence publication when the image
write fails; directly exercises the optional Home-position snapshot through
`MachineService`; and verifies malformed realtime samples preserve existing
coordinate, reconnect, session, and authorization state. Permanent-fixture
reach editing and bounds proposals remain absent for the next increment. The
requested five-file focused Windows suite passes 276 tests, and the complete
11-file Increment 2 focused suite passes 434 tests; the precision-capture file
accounts for 19 tests. Repository-wide Ruff, compileall, and diff checks also
pass. This increment is automated-test and offscreen-widget verified only; no
physical controller, motion, camera, or laser test was performed.

The multi-machine foundation now carries an optional, validated physical
honeycomb ruler span on each saved machine. It remains unset for every generic
and Ender-3 S1 Pro starting profile until an operator explicitly configures it
in Machine Manager. `CoreRuntime` also passes a detached snapshot of the stable
running-machine, profile, camera, and calibration identities into `AppContext`.
The legacy Coordinate Audit fixture-reach evidence model is restored as
diagnostic-only state under
`<data_dir>/machine_state/<stable-machine-id>/fixture_reach.json`; renames retain
that path and duplicates begin without evidence. Preserved machine-state
directories permanently reserve their IDs even after registry deletion, so a
new or duplicated machine cannot inherit orphaned physical evidence. Only the
physical `legacy-config` machine can claim the old global `fixture_reach.json`,
using strict, no-clobber copy metadata; profile-created and duplicated machines
cannot win by launching first. The legacy source is never changed, malformed
evidence or claim metadata is ignored fail-safe, and a second machine cannot
inherit the claim. Explicitly saving valid scoped evidence clears a stale
in-process migration error because that scoped evidence is then authoritative.
Focused configuration, registry, runtime-identity, offscreen Machine Manager,
evidence isolation/restart, rename/duplicate, and migration tests pass. That
foundation increment changed no controller, motion, G-code, bounds, arming, or
laser behavior and was not physically tested.

The native desktop now has a bounded foreign G-code design importer for `.gc`,
`.gcode`, `.nc`, and `.tap`. It translates supported 2-D G0/G1/G2/G3 motion into
ordinary E3 path objects and reconstructs Line layers from modal feed/power
combinations. Imported layers are always output-disabled and foreign programs
are never streamed directly; subsequent execution still requires E3 generation,
exact Preview, and the guarded START JOB path. Unsupported coordinate-changing
or non-2-D commands fail import instead of being guessed. Focused parser and
offscreen desktop tests are included; this importer is not physically verified.

The native desktop now requires the final execution sequence **Generate -> exact
Preview -> START JOB** for both project programs and prepared Machine Setup
programs. The Preview is window-modal while it is open, so project authoring and
other main-window controls cannot mutate the reviewed source. Its distinct
bottom-right **START JOB** control closes the Preview and synchronously delegates
to the unchanged guarded `run_current_job()` path, leaving software STOP
accessible while preserving every stale-revision, support/map binding, raster
identity, connection, Home, motion, arming, bounds, and streamed-program check.
The main Job panel and Laser Tools menu can only open Preview; they no longer
offer a direct execution bypass. **Prepare Start Here…** still replaces rather
than executes a program, and the replacement must complete its own exact
Preview. Focused offscreen Qt acceptance and rejection tests verify modality,
blocked parent interaction, guarded handoff, Preview dismissal, STOP access,
stale rejection, bypass removal, Start Here non-execution, deferred rendering,
and unfinished-close invalidation. The mandatory exact-Preview execution gate
and its **START JOB** handoff were physically verified on 2026-08-16 through the
real Windows-laptop -> Raspberry-Pi -> controller/laser path described below.
Focused verification passed 333 tests:
15 exact-Preview tests, 57 desktop job tests, 49 compact-panel/layout/template/
runbook tests, 44 Machine Setup tests, and 168 core machine safety tests.

Earlier on 2026-08-16, a real powered job completed its streamed cutting
G-code, but automatic post-job Home / park did not finish and E3 remained in
the running state. A subsequent manual Home / park request was correctly
rejected with
`Cannot move to the photography position while a job is running` because the
completion worker still owned the machine. Ordinary Home / park already handled
this controller's missing terminal `$H` acknowledgement by requiring realtime
evidence of active `Home`, `Homing`, or the observed `Run` state followed by
`Idle`; automatic completion was still using the generic acknowledgement-only
running-job command path. Both paths now share the same fail-closed GRBL homing
acceptance state machine while retaining their distinct command-lock and
running-job/STOP ownership. Focused tests verify normal `ok`, each accepted
active-to-Idle transition, idle-only and malformed evidence, alarm/error,
disconnect, timeout, STOP cancellation, park/release ordering, and terminal job
publication. After that correction, the operator completed a physical acceptance
run on the same Windows-laptop -> Raspberry-Pi -> real-controller/laser rig:
**Generate -> modal exact Preview -> START JOB -> powered cut -> automatic Home
-> configured photography-position park -> motor release -> E3 Complete**. The
previous stuck `job.running=True` result did not recur. This physically verifies
the mandatory exact-Preview execution gate, the post-job GRBL homing correction,
and successful powered-job progression through Home, park, motor release, and
terminal Complete on this rig. It is not a safety certification and does not
physically verify STOP, alarm, error, timeout, disconnect, or other failure
paths; those remain automated-test evidence unless separately recorded.

Physical Pi 3 B+/C920 validation showed that OpenCV raw mode is not viable:
V4L2 negotiated MJPG at 1920×1080/30 fps, but `CAP_PROP_FORMAT=-1` was rejected
and `VideoCapture.read()` returned decoded 6,220,800-byte BGR frames. The
OpenCV raw-mode probe has therefore been removed. `CameraService` now first
attempts a narrow Linux V4L2 MMAP backend for persistent device paths. It
negotiates MJPG and the configured dimensions/rate, retains each bounded JPEG
packet unchanged, and decodes that same packet once for all ordinary camera and
precision consumers. Both representations share sequence, generation, and
capture timestamp. Native-size monitor requests forward the exact packet with
no resize or encode; unavailable native capture closes fully before the normal
decoded OpenCV fallback opens. That fallback is 1280×720/10 fps/quality 78,
while direct mode may deliver 1920×1080/10 fps. The V4L2 ABI abstraction,
buffer lifecycle, exact-byte path, fallback, camera lifecycle, precision, and
monitor behavior are automated-test verified without hardware. Physical native
V4L2 operation on the Pi 3 B+/C920 measured approximately 28.6% total CPU busy
(70.5% idle), roughly 116 MB RSS, and about 47.11 Mbps TX while the Raw Live
Monitor reported `DIRECT MJPEG` at 1920×1080 with a 10 fps monitor target. The
earlier approximately 1088 fps camera status was an accounting defect: the
sample was taken after V4L2 dequeue but before source-JPEG validation and decode,
so it did not represent a physical camera rate. Publication FPS now includes
validation and decode time; that correction is automated-test verified but has
not yet been physically rechecked. The physically measured prior transcoded
720p/10 fps baseline was about 18.11 Mbps TX, 2.2–2.4 CPU cores of active work,
and 146 MB RSS; each result is one observed configuration, not a universal
expectation.

The Raspberry Pi camera bridge now offers a bounded authenticated raw-monitor
mode on its existing `e3camera://` socket. One persistent connection carries
JPEG frames from the sole Pi-owned `CameraService`; the server and desktop both
use latest-frame replacement semantics, with two monitor clients maximum and a
4 MiB per-frame ceiling. The desktop now prefers 1920×1080 at 10 fps, offers
5/10/15 fps, and separately reports Pi-side usable-frame **Capture** FPS,
desktop socket **Network** receive FPS, Qt **Display** FPS after latest-frame
replacement, and source-frame **Age**, alongside the direct/transcoded mode.
It remains independent of machine connection and calibration authority. Focused
loopback, camera lifecycle, transport-timestamp, receive-accounting, and
offscreen desktop tests pass. The performance observation above is physical;
the corrected Capture / Network / Display / Age values are automated-test
verified only. Direct-path latency, controller responsiveness, precision-capture
coexistence, and go2rtc service impact remain physically unmeasured and must not
be inferred from desktop CI.

On 2026-08-16, physical Windows-laptop monitoring at 1920×1080 `DIRECT MJPEG`
measured Capture 17.4 fps, Network 10.1 fps, Display 7.7 fps, and source Age
7 ms with a 10 fps target. With a 15 fps target it measured Capture 17.2 fps,
Network 14.9 fps, Display 8.3 fps, and source Age 29 ms. The nearly proportional
increase in Network delivery without a corresponding Display increase localized
an approximately 8 fps ceiling to the Windows desktop presentation path while
Pi capture and socket delivery remained faster. The Raw Live Monitor now takes
the same bounded encoded JPEG packets through a narrow remote-camera API, keeps
only the latest received packet while a separate presentation worker is busy,
and uses Qt JPEG decoder-assisted scaling toward a thread-safe snapshot of the
current display size. The GUI thread now only constructs the `QPixmap`, presents
it, and updates diagnostics; ordinary decoded `monitor_frames()` callers remain
compatible. A repeatable local synthetic 1920×1080-to-899×506 comparison reduced
measured GUI-thread conversion/presentation work from 4.07 ms to 0.15 ms per
frame and total decode/preparation plus presentation from 8.49 ms to 4.82 ms;
the new 4.66 ms preparation occurs off the GUI thread and avoids full-resolution
BGR and QImage intermediates in this monitor path. The architecture, bounds,
dimensions, two-stage latest-frame replacement, resize behavior, diagnostics,
and lifecycle are automated-test and local-benchmark verified. The improvement
is **PHYSICALLY UNVERIFIED** until the real Windows-laptop 15 fps monitor test is
repeated; no 15 fps Display-performance conclusion may yet be inferred.

Explicit controller replacement now performs disconnect/laser-off cleanup
under the UI action's original STOP generation, then captures and binds the
post-cleanup generation for the replacement connection. The single generation
advance caused by disconnect is required; an additional concurrent STOP before
connect or a STOP during connect still cancels replacement. Both the native
Machine panel and Machine Setup use this shared service operation. Successful
replacement remains HOME REQUIRED with coordinate and jog references invalid;
there is no automatic Home, motion, job resume, arming, or laser authority.
This correction is automated-test verified and awaits physical STOP/reconnect
validation.

The desktop now presents an explicit **Reconnect** action when an established
controller session is marked RECONNECT REQUIRED. This operator action performs
one disconnect followed by the ordinary connection path; it never retries in
the background, homes, moves, resumes, or arms. A successful replacement
session remains HOME REQUIRED with coordinate and jog references invalid. A
failed replacement remains safely disconnected. Native modal message boxes
also receive one queued polish/update/repaint immediately after their first
show event. This application-wide, non-blocking workaround addresses the
observed Linux compositor/backing-store first-exposure failure without changing
modality or the dark theme; focused offscreen Qt coverage verifies visible
content, queued repaint execution, modal results, and parent usability. Both UI
corrections are automated-test verified and await physical Linux validation.

CI now validates the desktop on Windows only. Fast Development CI runs for
development pushes under `fix/**`, `feature/**`, `agent/**`, and `cleanup/**`;
it uses Windows Python 3.12 for Ruff, dependency/bytecode validation, and the
complete desktop-enabled pytest suite with four bounded xdist workers. Branches
outside those patterns, including `architecture/**`, receive the full automated
gate when opened as a pull request to `main`. The main compatibility workflow
runs Windows Python 3.10 without desktop extras, Windows Python 3.12 with desktop
extras, and a separate Windows Python 3.12 Ruff job for pushes/PRs targeting
`main` or `desktop-v1` and for manual dispatch. Linux desktop CI is no longer a
supported compatibility gate. Existing Linux/Pi runtime components and any
legacy Linux packaging path are separate concerns and require focused
verification when changed.

Physical reconnect after the software STOP test found the controller alive but
alarm-locked: settings queries succeeded while connection normalization's `M5`
was rejected with GRBL `error:9`. Connect now shares Home / park's narrow
pre-home recovery: only this exact consumed rejection, and only with mandatory
Home / park configured, permits `$X` followed by a second acknowledged `M5`.
The connection remains HOME REQUIRED with no coordinate or jog reference, and
Connect never homes or moves automatically. Other errors, alarms, unlock or
second-`M5` failures, disconnects, and timeouts fail closed. This correction is
automated-test verified but awaits physical reconnect and Home / park
validation on the controller.

Physical jog validation found that the requested feed was emitted on `G0`, so
GRBL used its configured rapid positioning rate and the Jog panel's speed field
did not control the observed motion. Guarded jogging now retains its trusted
absolute-target architecture but emits laser-off feed-controlled `G1` motion;
the requested feed and existing travel-feed ceiling therefore apply to the
controller move. This correction is automated-test verified but awaits a
repeat physical jog at both low and normal feed settings.

On 2026-08-15 the Raspberry Pi hardware-node candidate was physically exercised
with the installed controller and camera. The remote camera delivered the
configured 1920 x 1080 MJPG stream at 30 fps, and the authenticated controller
bridge connected and delivered commands to the physical GRBL-derived
controller. A `$H` command completed the physical double-touch homing motion,
but no terminal `ok` reached E3, so the prior acknowledgement-only Home / park
path timed out and correctly refused to issue the park move. The application
now prefers the normal `$H` acknowledgement but can verify this controller from
a realtime active-homing (`Home`/`Run`) to `Idle` transition; an immediately
idle, alarmed, disconnected, stopped, or otherwise ambiguous exchange fails
closed and requires reconnect. The bridge source and loopback coverage show
that complete serial lines, including `ok`, are forwarded without filtering,
but the physical session did not capture Pi-side raw serial traffic. Therefore
the available evidence cannot distinguish a controller-omitted acknowledgement
from a lower-level serial/bridge loss. The later 2026-08-16 powered-job
acceptance run described above physically verified the corrected shared homing
exchange and configured park pose in that successful completion path.

Initial transport opening now receives one bounded retry only before this
`MachineService` instance has established any trusted controller session.
Configuration, protocol, and bridge-authentication errors are not retried, and
an established session that becomes uncertain is never automatically
reconnected or resumed.

This historical entry describes the superseded `E3BRIDGE/1` raw-serial
candidate, not the current `E3MACHINE/2` ownership semantics. That earlier
Windows-to-Raspberry-Pi candidate placed authenticated controller and camera
transports underneath the existing guarded services. Its controller path kept
the desktop `MachineService` as the command owner and used a Pi-local realtime
stop/reset plus `M5` after client loss. The camera path
keeps V4L2 ownership and precision acquisition on the Pi, transfers retained
frames to the desktop, preserves sequence/generation/control diagnostics, and
rejects mismatched Pi/desktop capture profiles before startup. Twelve focused
loopback tests and Python bytecode compilation pass in an isolated harness. The
restored GitHub Actions matrix passes on Ubuntu with Python 3.10, 3.11, and
3.12, and on Windows with Python 3.10 and 3.12; the Python 3.12 jobs include the
desktop extras and offscreen Qt suite. Ruff, dependency checks, and bytecode
compilation pass in that same run. Local Linux verification also includes 1,758
repository-wide tests, 1,371 portable tests without PySide6, and the focused
network tests. The camera mode, bridge authentication, and controller command
delivery are now physically bring-up verified as described above. Network-loss
cleanup and calibration repeatability remain physically unverified. Corrected
post-job homing, photography parking, motor release, and powered completion were
physically exercised in the specific successful 2026-08-16 acceptance run; the
unexercised failure paths retain automated-test evidence only.

Fine-registration reset now immediately re-evaluates the retained eight-mark
capture against the restored base map instead of discarding the review and
leaving **Apply reviewed full-bed map** disabled. The full-map axis-span gate
also allows the support-contained pattern's measured 69.9% Y span (68% minimum,
with the independent 35% hull-coverage gate retained). The saved 2026-08-14
capture then qualifies with seven inliers, 0.084 mm RMS, 36.7% hull coverage,
and 0.695 mm maximum modeled correction. Focused reset and homography-gate tests
pass; applying this refinement has not yet been physically tested.

After applying that full-bed map, the first automatic honeycomb re-teach fell
back to unseeded segmentation because the accepted teaching metadata correctly
carried the superseded bed-map digest. It selected the outer right ruler edge
and failed the nominal-size gate by 14.38 mm. Setup re-teaching now permits the
integrity-checked prior teaching image to seed pixel registration even when its
map digest is stale; four fresh edges are still independently fitted and gated
through the new map before acceptance. The exact 2026-08-14 capture now resolves
the cutting surface with mapped side disagreement below the 2.85 mm teaching
limit. Execution continues to reject stale map/support bindings. Focused stale-
map and legacy-upgrade tests pass; the corrected button flow awaits live retry.

The powered dense 5×5 fit now places its five machine-axis nodes across an
exact 180 × 180 mm center span on the current support, retaining 5 mm crosses.
Every cross endpoint must remain inside both the freshly taught support and the
explicit configured honeycomb-output polygon. That exact polygon is stored with
the calibration session, used for generation and MachineService preflight, and
passed unchanged at Start. Profiles without an explicit polygon retain the
legacy conservative support/machine-rectangle layout. Focused 180 mm generation,
session-binding, and desktop calibration-job tests pass; the expanded pattern
has not yet been physically run.

New projects now contain the operator-supplied E3 10 W starting profiles in
Cuts / Layers slots 00–12: seven line-cut profiles (paper, two plywood types,
MDF, opaque black acrylic, vegetable-tanned leather, and cardboard/chipboard)
followed by six raster profiles. Speed, power, passes, raster interval, scan
angle, overscan, semantic colors, and zero power-correction values match the
2026-08-14 workbook except for the operator's corrected slot-00 paper cut:
1500 mm/min at 100% power. Existing saved projects are not rewritten. These are
unverified starting values for a machine with no air assist, not guaranteed
material settings; no profile has been physically acceptance-tested here.

Machine Setup jobs remain absolute machine-coordinate programs, but when the
active authoring canvas is honeycomb-local their workspace and popup previews
now use the current rigid support transform. This corrects a display-only bug
that drew valid fine-registration targets outside the visible support by
treating machine coordinates as local coordinates. Generated G-code and
machine preflight are unchanged.

The desktop now models an automatically detected honeycomb as a real movable
job coordinate system instead of conflating it with the persisted machine
rectangle. New projects created with a current, execution-verifiable schema-2
support use explicit `honeycomb_local` coordinates from X0/Y0 to the physical
span configured for the running saved machine; legacy schema-1 projects migrate
explicitly as `machine`. The four independently fitted and mapped corners are
reduced to a closest-fit right-handed rigid frame so small edge disagreement
cannot shear project geometry. Camera rectification, the authoring grid, Trace,
and toolpath preview can share that local frame.

Vector, fill, image-raster, and frame output is planned in local coordinates,
then rigidly placed in machine coordinates before laser-spot correction.
Generation independently checks local support bounds, placed beam geometry,
spot-corrected controller paths, and the selected execution authority. A prepared
honeycomb-local job binds the support pose, a digest of the complete bed map,
and the exact configured output polygon reviewed with the job; Preview, export,
Start Here, and Start reject stale bindings. Every powered segment in a
post-map Machine Setup pattern is likewise contained in the measured support,
the complete program remains machine-bounded, and the session is bound to that
exact support/map identity. Start first runs static program and machine-bound
preflight, rechecks the immutable support/map/output-polygon binding without a
camera capture, performs one laser-off Home, and begins the validated program
without parking at the photography pose. A missing, legacy, corrupt, or stale
support binding fails closed. The
powered base-map pattern is the intentional bootstrap exception because that
map is required to express a support in machine coordinates; it remains bounded
by the configured machine area and requires a restrained sacrificial sheet over
the exact reviewed pattern.
The complete 190 mm surface is available for authoring. On 2026-08-13 the
operator explicitly confirmed that the physical output authority covers a
210 Ã— 210 mm square centered on the detected support. The local hardware
configuration records that exact fixed machine-coordinate polygon, in support
order, as `(18.218005, 29.679375)`, `(228.217364, 30.198421)`,
`(227.698319, 240.197779)`, and `(17.698960, 239.678734)` mm. Its
machine-axis-aligned bounds are X17.698960..228.217364 and
Y29.679375..240.197779 mm. It is not inferred from later camera detections and
does not alter camera calibration or Home/park bounds. Unreachable geometry is
rejected.
Core coordinate, rectification, persistence, toolpath, Trace, and offscreen
desktop tests pass. This workflow has not yet been physically acceptance-tested,
and physical stops/controller max-travel values remain operator verification
items. An automatic schema-2 teaching reference was accepted on 2026-08-13,
but it has not yet passed a repeated fresh-capture or powered-job physical
acceptance test; do not treat its presence as proof of output accuracy.

The desktop live camera overlay and Trace review now rectify directly into a
current honeycomb's X0..width, Y0..height frame. Without a current frame they
retain the machine-coordinate camera-area fallback, which can expand beyond the
configured rectangle solely to avoid cropping visible evidence. The active
saved schema-2 support spans about machine X27.0..218.7 and Y38.7..231.1 and is
execution-verifiable in software, but is not physically verified.
A separate green polygon maps the explicit guarded-output square into
honeycomb coordinates. Its local bounds are Xâˆ’10..200, Yâˆ’10..200: exactly
10 mm beyond each edge of the 190 mm support. Trace, template review, project
generation, and `MachineService` preflight use the same polygon. Only a job
bound to the current honeycomb signature may opt into it; ordinary jobs retain
the legacy rectangular policy, and the immutable preflight result binds the
exact polygon so a config change invalidates it. Focused rectification, display,
detection-boundary, color-sampling, live-overlay routing, cache-isolation, and
authority-separation tests pass. Direct honeycomb-local live-overlay framing was
interactively confirmed on 2026-08-13. A follow-on Trace attempt exposed a stale
in-memory 192 × 192 mm empty project after accepting the 190 × 190 mm
support: camera framing had updated, but the project-frame callback ignored an
already-local document. Clean, empty, unsaved projects now reconcile
local-to-local dimensions when the support changes and again immediately before
Trace or color sampling. Saved, dirty, and nonempty projects retain the strict
mismatch rejection. Focused offscreen lifecycle tests pass; the corrected Trace
button flow has not yet been repeated on hardware.

On 2026-08-12, a live powered fine-registration run exposed that its historical
fixed work-area fractions were independent of the newly detected honeycomb.
The saved support began near machine Y37.3 mm while the generated cross extended
to Y32.5 mm, and the operator reported a mark beyond the honeycomb. Fine
registration now derives its targets in the current detected support frame,
clips them to the guarded machine rectangle, verifies the complete cross extents
inside both regions during generation, binds the prepared session to that exact
support reference, and repeats polygon containment on the exact powered G-code
immediately before job preflight. The same support/map binding, containment,
and Start-time revalidation now applies to accuracy validation, dense 5×5 fit,
4×4 mesh validation, and shifted confirmation jobs. Dense target rectangles
remain machine-axis aligned inside the shrunken support-contained region so the
Cartesian mesh remains valid. Focused generation/start acceptance and rejection
tests pass; this correction has not yet been physically run.

Base-grid detection now rejects duplicate, irregularly spaced, and
edge-contaminated 25-point OpenCV lattices and continues through its remaining
candidate thresholds. The saved 2026-08-12 C920 capture that previously chose
a duplicate lattice containing a false bottom-edge hardware point now resolves
the correct keyed 25-point grid. Honeycomb-ruler periodicity now refines the
integer autocorrelation peak to a fractional-pixel tick pitch, avoiding the
observed 5 px quantization that reported 206.8 ticks across a physical 190 mm
ruler. Focused synthetic detector tests pass; the honeycomb change has not yet
been repeated with a fresh physical three-hint detection.

Honeycomb hints no longer act as ruler endpoints. They select the X ruler,
approximate shared zero/intersection, and Y ruler.
Vision fits both baselines, measures fractional-pixel 1 mm pitch, uses their
intersection as the detected zero, and projects the configured physical span;
the active bed map still independently checks that projected span before the
optional reference can be saved. Focused unit, application, and offscreen UI
tests pass; this redesigned interaction awaits a live C920 retry.

Honeycomb reference detection is now automatic-first. Edge-density segmentation
locates one dominant rectangular honeycomb, fits all four cutting-surface edges,
and retains their four mapped intersections as measured evidence. The active
bed map orders those raw corners as origin, +X, opposite, and +Y; the configured
physical span defines the nominal honeycomb-local dimensions but does not
fabricate the observed edge lengths or corners. Printed tick recognition is not
required. Accepting the reviewed result creates an execution-verifiable schema-2
reference. The three-click path is explicitly a last-resort display/diagnostic
fallback for failed or ambiguous automatic results and cannot authorize
honeycomb-local or powered post-map execution. Synthetic segmentation and
focused application/UI tests pass; the full automatic pipeline awaits a live
C920 capture.

Accepting automatic detection stores the exact reviewed homed-bed teaching PNG
and metadata as atomic files bound by its four image corners, image digest,
bed-map digest, and support-frame digest. Automatic re-detection can register
that image from support-local matches with bounded count and spatial coverage.
Job Start deliberately does not recapture or register it after the operator has
traced and reviewed the current image.
The older annotated `1920x1080-manual-focus-010` template and schema-1 support
artifacts remain diagnostic only and cannot pass the execution predicate.
Synthetic support-local registration, moved-support, scale-mismatch, and
coverage-rejection tests pass. A 2026-08-13 live Start capture exposed that the
former automatic pose check both falsely rejected interrupted ruler edges and
caused a redundant Home/park/capture/release/Home sequence. At the operator's
direction, Start now uses one Home with no intervening camera pose check. This
correction has not yet completed a powered physical job.

Native Machine Setup camera views now support a validated clockwise quarter-turn
presentation transform. The local C920 hardware profile is set to 90 degrees so
its sideways mount displays machine X toward screen-right and machine Y toward
screen-top. Overlays are rotated as image pixels and pointer selections are
inverted back to the original sensor coordinates; lens images, bed points,
homographies, saved captures, controller axes, and output bounds remain
unchanged. Configuration and offscreen picker-coordinate tests pass. The rotated
view has not yet been interactively inspected in the live hardware process.

The cutting-template designer now authors twelve semantic shapes through one
Qt-independent geometry vocabulary: rectangle, rounded rectangle, circle,
ellipse, capsule, triangle, diamond, regular polygon, star, one-flat circle,
two-flat circle, and washer. New `shape_grid` recipes retain row-major identity
and bounding-box spacing; legacy `rectangle_grid` version-1 templates migrate
as rounded rectangles. Washer OD/ID is stored as one logical compound object,
and containment-aware vector ordering cuts nested closed contours deepest-first
before nearest-travel optimization. Every nested contour now completes all of
its configured passes before its containing parent begins, independent of
winding, while unrelated contours retain pass-major ordering. Template features
carry semantic shape and
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
texture fallback remains conservative. When the current schema-2 support has
an integrity-checked accepted empty-honeycomb photograph, Trace additionally
rectifies that image through the same support-local frame and marks cells whose
interiors strongly match the exposed honeycomb. Image hash, complete bed-map,
and support-frame digests must all match; stale evidence is ignored. Synthetic,
application, and controller wiring tests cover both paths.

The saved 2026-08-14 07:37 C920 recovery frame was replayed read-only through
the current post-mesh map. Trace finds 14 direct rounded rectangles as a coherent
2 x 7 grid at 100% confidence. The accepted honeycomb background leaves the four
already-open cells unchecked and selects the ten remaining labels. At zero
border offset, its shared 83.24 x 22.45 mm fit differs from the intact printed
borders by about 0.02 mm in width and 0.04 mm in height. The operator's visible
`-0.20 mm` uniform offset instead trims every proposed edge by 0.20 mm, so it is
not an alignment-preserving cleanup setting. No powered recovery cut was run as
part of this software verification.
Automated geometry, migration, UI, matching, Trace, and toolpath tests cover
the implementation, including three-pass washers, reversed contours, multiple
washers, imported compound paths, and three nesting depths. No generated shape
or containment ordering has been run on physical hardware.

Per-operation and material-recipe Power Correction is implemented as a bounded,
material-specific commanded-power bias layered over GRBL `M4`. Vector paths use
turn-angle severity and the configured acceleration model to add at most three
collinear ramp blocks on each side of a real junction. Raster correction first
credits laser-off overscan and changes image-area power only when that overscan
is shorter than the modeled braking distance. Zero correction retains the prior
program shape; raw G-code keeps `M4`, laser-off rapid travel, guarded inline `S`
on `G1`, and final `M5`. Projects and migrated material databases default both
new values to zero. Focused model, mapping, geometry, raster, exact-Preview, UI,
material, and guarded-stream tests pass. No corrected powered job has been run
on hardware. The platform-neutral suite passes 1,596 tests with the 103
loopback-server security tests run separately outside the socket-restricted
sandbox; all 1,699 tests pass. Repository-wide Ruff checks also pass.

On 2026-08-14, toggling an object's **Visible** checkbox exposed a native Qt
re-entrancy crash: the synchronous history listener rebuilt the object tree
while its `itemChanged` callback still owned the emitting row. Object edits now
use a queued connection so the native callback returns before the undoable
document refresh. The focused object-list, history, and workspace suites pass
64 offscreen tests; this fix has not yet been interactively retried.

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

Repeated-grid Trace gap suggestions now optionally inspect only the expected
cell ROI for grayscale boundary evidence after the direct cells establish a
shared rounded-rectangle geometry. Evidence can make a bounded center
refinement without changing the shared dimensions or angle; unsupported gaps
remain explicitly inferred/review-required. Synthetic Trace tests cover
lower-side/side-edge recovery beneath simulated glare, no-evidence fallback,
internal-text resistance, and disabled gap inference. Real C920 glare recovery
on the 2 × 4 label scene remains to be physically validated.

Powered Machine Setup calibration jobs now hand off automatically to their
matching Home / park precision capture and scoring operation after successful
completion. The handoff is bound to the exact prepared filename and is cleared
when a job fails, stops, or is replaced; ordinary project jobs are unchanged.
Preview's **START JOB** does not show a powered-job warning or typed arming
phrase; the guarded run path creates the exact program's one-use temporary
authorization internally and submits the prepared job immediately.

Trace review uses fixed-screen-size, high-contrast numbered badges so object
IDs remain readable over detailed camera imagery. A tri-state **Select /
deselect all** checkbox reflects none, mixed, and complete selection and can
change the whole detection list in one action. The focused desktop panel and
workspace widget suites pass 45 tests for the current implementation. Loose
identical-cell grids also repair only the affected center axis when a missed
edge makes one observed cell materially narrower or shorter than the repeated
size; unaffected placement and rotation remain independently observed.

Historical desktop-foundation branch marker: **`desktop-v1`**

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
superseded. The fixed polygon now explicitly requested by the operator reaches
Y240.197779, beyond that earlier laser-off Y215 jog evidence. The operator's
2026-08-13 assertion authorizes the configured software polygon for this
installation, but neither controller travel nor physical beam reach at those
extremes has been acceptance-tested. These are measured mechanical-jog reaches
only. They are not the
camera/calibration rectangle or authorization for laser output. The local
machine work area remains the configured and previously calibrated X10..210,
Y10..210 rectangle; the operator has since clarified that it was never intended
to define the physical honeycomb. It therefore remains conservative execution
authority pending documented laser-spot reach verification, not evidence of the
support size;
with the 5 mm laser boundary margin and zero spot offset, ordinary machine-frame
jobs retain guarded output X15..205, Y15..205. Honeycomb-bound jobs instead use
the separately configured fixed four-corner output polygon. Jogging intentionally
permits travel beyond the camera/calibration rectangle so the mechanical envelope
can be measured without changing calibration provenance. Honeycomb-local display
alone does not grant motion authority.

One packaged, versioned Permanent Camera Setup Guide is now the canonical
operator sequence. It is available from the Machine Setup footer and the main
Help menu, opens modelessly at the current numbered tab, and explicitly covers
the exact-Preview **START JOB** handoff. It warns that timeline Play and
**⏮ Start** only animate and that main **Generate** replaces a prepared
calibration job.
Automated UI/content/package tests keep its five tab headings and exact action
labels synchronized with the application.

Step 3 records a vision-detected pose for the movable honeycomb after a
ruler-overlay capture. Automatic square detection fits four independent edges;
reviewing and saving it records the raw mapped corners, their semantic topology,
and the exact accepted teaching image as an execution-verifiable schema-2
reference. This is separate from camera calibration but establishes the rigid
local job frame. Trace rectifies directly into it while a green polygon shows
guarded machine output.
Three rough clicks remain a last-resort corridor hint. Their fitted
baselines/shared zero can be saved as a legacy visual/diagnostic reference, but
they do not contain four-corner evidence and cannot authorize a honeycomb-local
or powered post-map job.
While those three hints are being placed, the picker shows the clean captured
frame rather than the diagnostic coordinate overlay. Cursor-centered wheel zoom,
middle/right-button panning, and double-click-to-fit improve placement without
changing the source-image coordinates supplied to detection.
For the hinted fallback, each ruler's bed-map-measured span must agree with the
configured physical span within 2 mm (or 1 percent for larger rulers); a poorer fit
is rejected before it can replace the saved visual reference. Recording or
clearing either reference does not mutate the laser-burned keyed bed map,
configured machine area, or guarded laser limits. An accepted automatic
reference does determine whether support-bound execution is available, and
prepared jobs are bound to its digest and invalidated when it changes.
Automated tests compare calibration and output-review state across detection
and recording and cover the execution-grade/legacy distinction.

The desktop Machine panel now exposes separately tested incremental XY jogging.
Home / park establishes the only accepted starting pose; each request is
translated to an absolute feed-controlled `G1` bracketed by an initial `M5`,
explicit `G21` and `G90`, a configured travel-feed ceiling, and a
planner-completion barrier.
The configured work-area rectangle is intentionally not applied to jogs because
jogging is the operator control used to measure the physical travel envelope.
STOP, disconnect, jobs, controller uncertainty, and motor release invalidate
the tracked jog position, and jogging is unavailable while armed or busy.
Automated tests cover repeated and beyond-configured-area moves, numeric/feed
rejection, UI gating, and a STOP/ACK race. Direction and the selected mechanical
endpoints were exercised on the active GRBL machine; STOP/reconnect behavior
was not physically measured during that session.

The native default workspace uses layout `v7`: Cuts/Layers, Camera, Objects,
Shape, Templates, Trace, Machine, and Material Recipes share one full-height
right column. The lower raw-G-code and Laser/job docks no longer exist, so the
bed/camera workspace reaches the bottom palette and global status area. The
optional Console remains a separate hidden bottom dock. Compatible older window
geometry and active right-tab choices may migrate, but opaque `v6` dock state is
not restored. Templates and Trace expose the shared Generate action below their
Create controls. Preparation and controller execution progress occupy the
bottom status bar, while Connect/Reconnect, Disconnect, deliberately disabled
Pause, and always-available software STOP occupy the non-hideable runtime strip.
The strip reflows at compact widths, and Reset workspace layout recomputes that
responsive row rather than restoring a clipped toolbar. Offscreen production-
theme tests cover `1600x900`, `1080x780`, and `900x680`, including status/progress
containment, compact STOP reachability, zero horizontal inspector overflow, and
a 420-pixel default sidebar. Operation numeric fields still commit once on edit
completion. Interactive desktop review remains pending.

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
positioning, arming, and run routes. A prepared job therefore keeps **START JOB**
available inside its exact Preview. Connection failure is reported as the
operation failure and prevents the requested action; STOP generation
invalidation prevents queued work from reconnecting and continuing. An
untrusted connection after STOP or an uncertain acknowledgement remains blocked
until the explicit reconnect path replaces it.

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
and explicit motor release. At that historical verification snapshot the
default-disabled system did not change fan/coolant state; the active typed Air
Assist implementation recorded above now adds only its explicitly configured
OFF/ON/OFF program behavior and laser-first cleanup. Zero-power jobs and every
stop, failure, emergency, and disconnect path skip the additional homing
and parking motion. The Laser panel distinguishes drain, home, park, and release
phases after stream progress reaches 100%; a terminal background-job error now
raises one desktop alert and is also copied into the in-app machine log.
Every GRBL connection explicitly releases the motors; connection and camera
cleanup repair a persisted camera-only `$1=255` to configured
`machine.grbl_step_idle_delay_ms` (250 ms by default), preventing a stale camera
hold from becoming normal power-on behavior. Tests cover delayed final `M5`
acknowledgement, planner-busy homing rejection until the new barrier, successful
parking, home and park failures, failed powered streams, FluidNC `$MD`, and the
`$SLP`/reset fallback without issuing fan/coolant commands when Air Assist is
disabled. On 2026-08-08 the
operator twice observed the head remain at the last powered point without
homing or parking. Process start time, source timestamps, the loaded profile,
and the generated `M4 S100` job rule out the previously suspected stale process
or disabled completion setting. The prior 3-second job acknowledgement timeout
could expire while a synchronized final `M5` drained motion, and `$H` had no
explicit pre-home planner barrier; test doubles reproduce both paths and their
fixes. The exact 2026-08-08 controller error was not captured. The later
2026-08-16 end-to-end powered acceptance run physically verified post-job
homing, photography parking, explicit release, and terminal Complete on this
rig, while failure-path behavior remains automated-test evidence only.

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
fitting with 0.30 mm RMS and 0.60 mm maximum software gates. Powered 5×5,
4×4, and shifted-confirmation sessions require the current automatic four-corner
support, are generated within both it and the configured machine area, and are
rechecked against the exact support/map binding at Start. These new containment
and Start-verification paths have automated coverage but no physical run.

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

Fine-registration, accuracy-validation, dense-fit, dense-validation, and
dense-confirmation sessions persist the exact bed homography/residual-mesh
revision active when their mark job is prepared. Powered sessions also persist
the execution-verifiable support signature, four taught corners, and complete
bed-map digest. Capture and offline analysis reject a legacy unidentified
session or any map change; Start additionally rechecks the exact program's
powered segments and current immutable support binding before arming. This prevents an old
powered session from being accepted after a fresh base map, translation,
full-map refinement, mesh change, or support change.

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
overlay alert. When the camera is offline, a stale map remains visible as a
status-bar requirement without opening the recovery modal; the prompt is
online-only. Visible overlays are invalidated immediately by focus or mapping
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
movable honeycomb support separate from the configured calibration/output
rectangles and verified laser reach. The repaired trace and ruler overlay have been reviewed against
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
sampled at the configured exact physical pitch and scan angle in the active
project frame, and converted with deterministic 8x8 grayscale dithering. Source
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
newer renderer's busy state. Application shutdown retains worker ownership and
suppresses callbacks, but drains for at most one second before bounded runtime
teardown under the shared four-second process deadline. Generation no longer
writes an implicit G-code artifact;
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
  and visually inspected offscreen at 1120 × 760. The mandatory exact-Preview
  -> **START JOB** execution gate was physically verified on the real hardware
  path on 2026-08-16; broader interactive Preview controls and rejection paths
  retain their existing automated or offscreen evidence unless separately noted.
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
- Stock-layout verification covers schema-preserving Stock boundary
  persistence, exclusion from generated G-code and zero-power frame bounds,
  horizontal/vertical centering, jagged-edge simplification, rotation snapping,
  and fit-with-margin on rectangular and concave stock outlines.
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
  -> configured machine coordinates or the current rigid honeycomb-local frame
  -> optional memory-only corrected-frame override in safe simulation
  -> workpiece / fiducial / object-trace detection
  -> geometry in the active project coordinate domain
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

- Native workspace with explicit machine or honeycomb-local coordinates, grid,
  rulers, pan, zoom, snap,
  and a fully adjustable corrected-camera overlay whose control and renderer
  share a 70% default.
- LightBurn-inspired desktop hierarchy with original compact icons, a bright
  drafting bed, a non-hideable responsive runtime/safety strip, always-present
  numeric properties, a full-height design inspector, compact bottom G-code and
  runtime/material docks, and a fixed 30-color operation palette.
- Multiple objects and operation layers.
- Rectangle, rounded rectangle, ellipse, line, vector outline text,
  stencil-safe bridged text, and SVG-path objects.
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
- Single-selected-image raster vectorization with original/mask/overlay review,
  physical cleanup and fit controls, hierarchy-preserving compound PATH output,
  one-step Replace or Keep/hide undo/redo, and an automatic visible 0%-power,
  output-disabled Line layer when the active layer is not already appropriate.
- Transform, mirror, duplicate, delete, group, ungroup, align, distribute, and
  z-order commands.
- Undo/redo.
- `.e3laser` save/load, backup, autosave, and recovery.
- SQLite material recipes.
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
- Optional post-Create geometric Straighten review for selected, finished,
  non-grid native Cut artwork. The combined object or complete separate-object
  batch is selected automatically, analyzed in current world coordinates, and
  rotated about one shared native-bounds pivot through normal undoable project
  history. Ambiguous evidence, grids, failed native fits, and unrelated objects
  receive no offer; eligible suppressed selections receive a muted reason.
- One captured corrected frame held across detection review, with monotonic
  request cancellation and stale-result rejection.
- One-step undo for a created detection set.
- A **Stock boundary (layout only)** Trace purpose that creates one locked,
  camera-aligned construction outline. The boundary persists in the normal
  project model but is excluded from all laser-output and framing paths.
- A contextual Stock layout toolbar for horizontal/vertical centering, rotation
  parallel to the nearest or named meaningful stock edge, and fit-to-stock with
  an uncut margin. Irregular traced contours are simplified only for edge
  selection; the original stock outline remains authoritative for fit checks.

The trace algorithms and native review lifecycle pass synthetic and offscreen
behavioral tests. Operator-reported Coleman runs now cover successful manual and
Auto native Trace generation, but the controller/firmware/configuration and
measured result were not recorded as formal physical acceptance. Straighten has
not been exercised end to end with the real camera and calibration.

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
- Autosaves and material recipes share an OS-native writable per-user data
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
- New text is converted immediately to PATH geometry for output. The source
  text, font, mode, height, bridge width, and bridge count are retained as
  metadata, but reopening the text-creation dialog to edit an existing vector
  text object is not implemented yet.
- No DXF import. Raster image import currently stores an external absolute
  asset path; managed or embedded portable assets, selectable dither methods,
  and calibrated grayscale power modulation are not implemented. PNG, JPEG,
  and BMP sources use deterministic ordered dithering; TIFF is unsupported.
  Raster vectorization is single-foreground only, not full-color or multi-layer;
  threshold, source resolution, and smoothing affect its estimated fit quality,
  and final projects retain fitted native line/cubic PATH segments. Preview and
  planning point sequences are bounded transient derivatives.
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
  fitted dimensions and radii remain raster- and threshold-dependent. Highly
  pixel-constrained `A` and `S` glyphs can still vary at narrow counters and
  curved shoulders; requiring a cleaner or higher-resolution source for those
  cases is an accepted first-release quality limitation.
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
