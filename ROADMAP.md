# Roadmap

## Base-map grid containment (2026-09-17)

Base-map recalibration now fits the complete keyed 5 x 5 pattern, with boundary clearance, inside the current saved four-corner honeycomb and machine area. Setup coordinates and generated marks use the same grid. Prepared recalibration jobs bind the saved support/map and reject changed geometry at Start or capture. First-time mapping without a current support retains the machine-bounded bootstrap pattern.


## Raster surface focus acceptance

Raster/image jobs now request a 7 mm measured surface gap while cut/vector and
Fill retain the current rule. Mixed-operation and Start Here source coverage
uses fake controllers. E3 DEV TEST 0.7.181 is built and selected; the matching
Pi companion is installed and hash-verified with 322 passing Linux/Pi checks.
Physical qualification remains pending; installed firmware and calibration are preserved.


## Daily Machine panel cleanup and setup Z maximum

The daily panel omits the two spot-transfer buttons and the complete marked
maximum/duplicate focus status/Ender reconnect/Saved Z section. Manual Z jog,
reference/measurement, workpiece focus and actionable errors remain. Machine Setup
-> 7 - Z / laser focus -> Maximum Z height provides the existing guarded host-side
maximum editor; setup retains recovery and saved-Z controls. Backend limits,
controller routes and persistence are unchanged. The setup editor observes modal,
busy, suspension and shutdown gates. 455 focused Windows offscreen desktop/backend
and remote tests passed in the shared tree; operator validation is pending.


### Follow-up: keep the clicked button stationary during status layout changes

The 0.7.175 operator recording still shows movement as live Z text wraps and a
telemetry note appears. StableScrollArea now anchors the clicked button's viewport
position across asynchronous layout changes, including disabled controls. Wheel,
scrollbar, Tab and explicit reveal navigation release the anchor. Content remains
fully readable and controller behavior is unchanged. Regression coverage includes
the actual Home/reference layout, narrow/wide forms, disappearing controls, and
user navigation; the recording is UI evidence, not physical hardware qualification.


Desktop scroll stability: all form scroll areas preserve their position when
action buttons disable and Qt transfers focus, including Home/reference.
User scrolling, Tab/Shift+Tab and explicit setup-guide navigation are retained.
Windows offscreen regression tests cover the real focus panel busy transition.
Interactive operator validation and compatibility CI/integration remain pending.


Desktop confirmation and profile feedback: Yes is green, No red and Cancel
gray regardless of the default choice. Profile Save is green when the current
ordered layer settings differ from the selected saved profile, and clears after
saving, loading or reverting. Cancelled/failed saves retain the highlight.
Existing defaults and hardware behavior are unchanged.

Source verified by 42 focused Windows offscreen tests, affected-code Ruff and
compileall; frozen build verification and interactive review are pending.


## Saved-profile update acceptance

Save with overwrite confirmation is implemented and verified by focused Windows
tests and offscreen layout/popup inspection. Confirm the workflow interactively
in the next DEV TEST build; profiles continue to save only on explicit request.


## Distribution area operator review

Desktop source supports full-bed, chosen-rectangle and selection-span distribution.
Focused Windows geometry and offscreen widget checks cover both directions,
rejections, cancel and undo. E3 DEV TEST 0.7.169 is selected; interactive review remains
pending; compatibility CI is required before integration.

## Cut/layer profile review

The 98 focused profile, menu, desktop layer and launcher tests, repository Ruff
and compileall pass on Windows. Compact layouts were inspected offscreen.
Complete operator testing of saved layer sets in the frozen feature build.
Compatibility CI remains required before integration.


## Setup wizard operator feedback

The operator found the progress wording and Step 4/5 handoff unclear. Plain
step names, ordered instructions and direct control links address this feedback.
Retest the complete setup path with the operator; software checks alone do not
verify usability or physical calibration.


## Precision placement qualification

The measured-surface placement path and step-by-step Machine Setup wizard are
implemented. The wizard reuses the existing controls and UI-neutral evidence
workflow; its operator walkthrough and physical qualification remain pending.
Complete independent one-height assessment, then lower/middle/upper physical
trials through 20 mm above the honeycomb including spacers. Qualify only points
where radial XY error plus measurement uncertainty is at most 0.10 mm. Publish
tested area and interval, identify limiting contributions on failure, and qualify
arbitrary tracing separately. Keep the current camera. See
[precision workflow](docs/PRECISION_PLACEMENT.md) and CURRENT_STATE.md for current
verification and build status.

## STOP Air Assist recovery

Rechecking terminal-job OFF recovery at Start is implemented and focused tests
pass. Pi installation and operator STOP/recover/new-job validation are tracked
in CURRENT_STATE.md. Unconfirmed OFF and mismatched bindings remain blocked.

## Home/cooling concurrency correction

The lock inversion captured on the Pi is corrected across Home and job-focus
completion. Deterministic concurrency coverage includes STOP cancellation;
broader verification and deployment are tracked in CURRENT_STATE.md. Physical
Home/park and job-completion testing remains operator qualification.


## Saved honeycomb datum follow-up

An explicit saved height editor in Step 7 is implemented for the paired
desktop/Pi feature build; automated checks pass and the Pi companion is installed; operator physical
verification remains pending in CURRENT_STATE.md. Guided four-corner/center surveying, manual leveling guidance
and measurement/return-button regrouping are implemented for precision setup. Continuous bed mesh
compensation is separate scope. Small-negative thickness tolerance was canceled.


## Invalid thickness recovery (2026-09-13)

Separate invalid derived measurements at verified clearance from motion failures.
Keep jobs blocked and expose numeric diagnostics while preserving the connection
and reference for corrected measurement. Physical validation remains pending.

## Focus/cooling hang correction (2026-09-13)

A simulated regression reproduces and corrects a focus/cooling lock inversion
before teaching motion. Focused Windows/Linux tests pass; hand off the guarded
Pi-only update for controlled operator validation. Compatibility CI passed. The reported loss of SSH is
not established as a consequence of this application deadlock.

## Thickness-derived workpiece focus (2026-09-13)

Implemented the operator-selected linear 7-to-3 mm gap over 0-to-6 mm thickness,
with explicit spacer subtraction and the reported −1.5 mm honeycomb datum.
Daily focus selection is automatic; gauge teaching remains a calibration action.
Acceptance/rejection and fake-controller sequence tests pass on Windows and Linux.
Physical focus accuracy and material cutting results remain pending.

## Faster Z setup qualification

Remove the private XY positioning cap and supply direction-dependent Z feeds
with a matching 20 mm/s travel firmware ceiling. Preserve effective native
probe/homing feeds and all existing admission, bounds, clearance and STOP
guards. Qualify the matched Windows/Pi/firmware build physically, including
up/down travel without missed steps, renewed gauge teaching, repeated surface
measurement and job focus/clearance. See CURRENT_STATE.md for software evidence.

## First-connected Pi app priority

Implement server-enforced client ownership, negotiated 30-second reservations,
observer-only desktop controls and non-owner cleanup rejection. Verify competing
clients, legacy priority, owner handoff, expired/in-flight reservations and global
STOP through authenticated loopback and Windows offscreen checks. Package the
combined speed/priority Pi update against the recorded 568b1cc9 installed baseline. Physical
two-app testing and installation remain pending; no connection-stability claim
extends to Wi-Fi, Pi power, USB, firmware or hardware faults.

## Windows update handoff reliability

Separate installer creation from the shutdown deadline and preserve launch
diagnostics. Cover slow and failed launches, bounded teardown, and frozen
Windows process creation. Original operator failure timing remains unknown.

## Workpiece-focus companion compatibility

Recognize the exact recorded Pi combination of the e57adbb5 base and targeted
12dbb3d/59c31d7 updates. Test its full predecessor hashes and upgrade/import
path, including rejection of unknown edits. Keep this packaging correction
separate from the verified 0.7.120 executable and unchanged application payload.
Physical installation and focus qualification remain operator tasks.

## Automatic workpiece focus

Machine-tab measurement supplies reusable job focus directly. The Pi must
finish the clearance XY approach and verify the calculated Z before positive
output. Successful jobs, framing, XY jogging and Home / park retain the flat
workpiece datum under unchanged authority. New measurement or gap selection
replaces the binding; lost reference/failures require a new measurement and
block powered jobs. Reuse across Pi/controller restart remains excluded.
Matching Pi deployment and repeated-job physical qualification are pending;
see CURRENT_STATE.md for exact automated/build evidence.

## Retained Z qualification

Implement clean idle Pi-owned Z checkpoints, consume-before-connect storage,
guarded firmware coordinate restoration, and explicit Forget saved Z controls.
Keep border-reference retention separate from surface measurements and reusable
job focus. Matching desktop/Pi/firmware deployment and a recorded physical
power-cycle acceptance test remain pending; automated acceptance does not
establish unpowered axis stability. Preserve exact-firmware gauge compatibility
and require explicit re-teaching when that identity changes. See
[the retention workflow](docs/Z_RETENTION.md) and CURRENT_STATE.md for verification.

## Machine Z layout

Place daily reference, measurement, recovery and saved-Z controls in
**Machine → Z axis · Ender**. Position probe selects a spot in the main
calibrated view on the left, previews a crosshair and requires a separate
**Move probe here** press; remove the duplicated right-hand live view. Require
fresh calibrated image metadata and preserve machine/honeycomb coordinate
mapping. Show calculated reusable **Workpiece focus** in the daily controls;
keep manual **Preview and position** controls in
**Machine Setup → 7 · Z / laser focus** with probe offsets, gauge teaching and
its raw live camera preview. Remove the separate Surface / laser focus dialog
and its Position XY jog row. Move and next-job selection explicitly confirm the physical conditions
printed beside their buttons; headroom, manual-Z, recovery and gauge-fit checks
remain. Preserve saved-Z restoration, retained Home / park XY and Forget saved Z
without project persistence changes. Automatic workpiece focus requires its
matching Pi companion; the layout alone adds no firmware requirement. Verification
and build status belong in CURRENT_STATE; revised-layout operator testing is
separate from historical physical results.

## Focus selection across camera parking

Implemented and regression-tested retention of selected flat job height through
successful clearance/Home/park. Pi correction 59c31d7 is installed and hash-
verified; 228 isolated Linux/Pi tests pass. Windows 0.7.93 and firmware are
unchanged. Full Windows development CI passed. The operator reports successful
physical execution of the requested focus/job/clearance/parking sequence on
2026-09-12; dimensional accuracy and failure-injection qualification remain separate.

## Measured job focus integration

Implemented explicit one-use selection and Pi-owned clearance/approach/focus/
cut/lift sequencing, including automatic clearance before manual Home / park.
Windows 0.7.93 and the matching Pi companion are delivered. Automated acceptance,
216 isolated Linux/Pi tests and full Windows development CI pass. Operator
physical sequence verification remains pending. Multi-height/layer focus and raised-surface camera
correction remain separate work.

## Completed focus preview handoff

Windows 0.7.92 is packaged, verified, selected and open through E3 DEV TEST.
The corrected Preview retains its target through status polling in the live
read-only/offscreen test. The saved 7 mm teaching is retained. Physical focus
movement and gauge-fit verification remain separate; full Windows CI is still
incomplete because its job was cancelled.

## Operator-requested lower surface limit

The matching V2 -10 mm firmware and Pi validation are installed. The requested
single paper retry after recovery/reference passed at -2.072 mm and returned to
Z30. Windows 0.7.91 is selected and launched with the truthful range display.
Qualify repeatability and gauge focus separately; ordinary travel limits are
preserved.

## Native probe failure qualification

The candidate now propagates deployment/stow errors and records first-failure
evidence from the actual native descent path. Complete operator-confirmed
firmware installation and a single observed reference/measurement before claiming
the original paper-probing issue resolved. Do not widen limits or retry blindly.

## Probe-failure recovery verification

The explicit current-height XY recovery path repairs the reproduced software
deadlock while retaining clearance and a separate border-reference requirement.
Automated cancellation and invalid-state tests do not establish probe deployment,
contact, stow, physical headroom or the cause of PROBE_FAILED. Matching desktop
and Pi deployment plus an operator-observed recovery/reference remain required.

## Focus preview usability

Separate camera-selection freshness from measured-surface Z preview validity,
and display the reason for unavailable focus movement. Keep explicit preview
and movement requests plus the existing session, bounds and confirmation gates.
Physical gauge reproduction and raised-surface accuracy remain operator checks.

## Z workflow repair verification

The current focus repair keeps the existing surface-V2 firmware for positioning
and larger teaching jogs. The known-Z re-reference path and full non-live-firmware
focus sequence have regression coverage. Matched deployment and real movement
checks are recorded in CURRENT_STATE before declaring the repair ready; gauge
fit and quantitative raised-surface calibration remain operator measurements.

## Live Z and focus-area follow-up

Executed-step Z telemetry and fixed-honeycomb focus bounds are implemented with
focused automated tests. Install and physically observe the matching firmware,
Pi and desktop together before declaring live motion reporting verified. The
operator's actual retained click is covered by a complete simulated focus
sequence. Gauge accuracy and raised-surface qualification remain pending.

## Camera probe selection follow-up

Rejected-click diagnostics are implemented for operator investigation. Resolve
the reported same-spot discrepancy using the actual retained click; the cause
is not established by the screenshot estimate. Machine travel limits remain.

## Gauge teaching travel qualification

Corrected the probe-contact lower-bound assumption and added bounded coarse/fine
teaching steps with responsive idle polling. Focused simulated-controller and
Windows widget tests cover negative taught offsets and travel-limit rejection.
Operator gauge fit, resulting focus accuracy and observed response time remain
physical qualification steps; raised-surface camera correction is separate.

## Reduced-preview probe positioning

Implemented calibrated click mapping for the Pi's full-frame monitor resize.
Acceptance/rejection and offscreen resize/DPI coverage accompany the correction;
physical camera-selected placement remains an operator check before probing.
Height-dependent camera correction remains outside this bed-plane workflow.

## Ender recovery qualification

Implemented explicit secondary reconnect, persistent fault visibility, bounded
shutdown and separation of XY/read-only failures from Ender emergency halt.
Added a restricted compact-firmware halt/restart protocol. Offline verification
does not establish physical recovery: install matching host/firmware support
after restoring processor responsiveness, then qualify reconnect, retained
settings and fresh Z referencing before resuming focus calibration.

## Probe positioning recovery correction

Implemented a travel-duration completion wait and reordered focus prerequisites.
Simulated acceptance/cancellation and offscreen UI checks cover the fix. Install
the matching Pi companion and frozen desktop build, then qualify actual camera
placement before continuing surface measurement and gauge teaching.

## Camera probe selection correction

The inset calibration-grid restriction is corrected; selection uses the
configured bed with current registration and mesh. Physical placement and
gauge-focus acceptance remain the next operator checks.

## Console replies and laser-head idle fan

Desktop Console reply persistence is implemented in source; frozen delivery
and operator verification remain pending. The physical primary controller
reports M5/S0 and no $152 setting in its settings listing. Its exact firmware
identity and the cause of continuous laser-head fan operation remain unknown;
no fan behavior, standby setting or motor-hold policy has been changed.

## Gauge focus calibration

Implemented for operator testing: camera-selected probe positioning with
crosshair/coordinate preview, calibration and frame checks, offset-aware
clearance transfer, and same-point return to the laser. Physical click-placement
accuracy and raised-surface camera correction remain separate validation/work.

Added for operator testing: a live observational bed view and explicit offset
transfers to probe and then position the laser over one point. Physical transfer
alignment and gauge fit remain next; camera-click positioning on raised work is
separate from this raw live view.

Implemented for operator testing: native border reference and surface probing,
7 mm gauge teaching, 5/3 mm derived gaps, persistent mounting offset, explicit
preview/position/clearance, and raised-surface V2 firmware. Next: verify actual
gauge fit at multiple elevations, then bind the accepted surface/calibration to
job preparation and coordinate a final Z lift before post-job XY homing. Camera
projection correction for raised work remains an independent calibration task.


Next for the installed Ender: collect the [independent Pi identity report](docs/ENDER_STARTUP_DIAGNOSIS.md),
then use the existing stock recovery baseline if silent. Confirm its actual
F401 memory capacity before selecting another custom image. USB updates remain
a requirement of the final custom installation.

The [F103RET6 USB-update candidate](firmware/ender_aux_f103/README.md) is built
and offline-tested, but target identification and physical qualification remain
pending. The current Ender has historical F4 firmware evidence; resume diagnosis
of its F401 startup/serial failure rather than selecting another processor image
by trial. No F103 deployment or integration is claimed.

Connected startup now has updater 0.2.0 and a bounded Pi Marlin-readiness
handshake. Ordinary traffic cannot hold the updater; incomplete images still
remain in recovery. A new SD install and companion Pi source update are required.
[Startup validation](firmware/marlin_mainboard/STARTUP.md) remains operator-pending; no physical
fix of the separate zero-response fault is claimed. Earlier release notes follow.

## Numeric editing consistency

Shared numeric controls cover desktop tabs and dialogs, including Z maximum
editing and live refresh. E3 DEV TEST 0.7.63 is selected at revision 11db1bc;
full Windows and focused POSIX CI passed. Automated keyboard/focus/action
checks cover accepted and rejected edits, Save/Generate and layer selection.
Operator verification remains pending; no Pi or firmware change is needed.

## Desktop Ender Z controls

Machine-tab jogging, live idle readback and persistent host maximum are built
in E3 DEV TEST 0.7.61. Full Windows and focused POSIX CI passed. Pi-companion
installation and physical operator verification remain pending. The 30 mm
material probing extension remains separate and unimplemented.

## Compact Z travel qualification

The operator selected an absolute Z80 ceiling from the current border-frame
Z20 plus 60 mm remaining travel. Compact firmware enforcement is being prepared
for operator installation. Extending G39 and clearance for the 30 mm reference
remains separate work; the current G39 range is unchanged.

Pi FAN1 cooling is implemented and tested offline; operator installation and
thermal on/off observations remain pending. Next Z calibration work separates
measured travel scale, independently measured sample thickness/support offset,
and position-dependent honeycomb variation. See docs/PI_CPU_COOLING.md.

Compact F401 qualification: boot, Pi service integration, both fan on/off
mappings and probe deploy/stow have operator evidence. The Pi Z0/Z5 endpoint
mismatch is corrected offline; final Z20 clearance, material accuracy and
installed-board USB uploads still require physical qualification.

The stock F401 baseline is restored and its Pi USB identity works. Next,
operator-test the [compact F401 kit](firmware/marlin_mainboard_compact/README.md):
confirm live MCU/capacity and all capabilities, cold start with normal Pi/webcam
wiring, then explicit USB update/boot and attached fan/Z/probe qualification.
The initial installer already includes USB updating. The headless profile
intentionally omits the stock menu; future display support is separate.
Earlier milestones below are historical.

Mainboard firmware now has a complete SD installation/recovery package for
both fans, probe and Z. The next stage is operator physical validation of the
attached loads and known-thickness measurement using
[the supplied procedure](firmware/marlin_mainboard/VALIDATE.md). Firmware and
guarded source controls are implemented; full-machine installation has not
occurred. Webcam continuity remains required. Prior stage notes follow.

Native material-height firmware now has a bounded Marlin prototype and guarded
host integration. Spare-board communications, unhomed rejection and restoration
to BENCH passed. Remaining work is physical native-cycle/contact qualification
with attached hardware, repeatability/known-thickness evidence, then desktop and
camera-plane integration. See [the prototype](firmware/marlin_material/README.md).
No operator validation is requested as part of this software/board-only task.

Spare-controller firmware: SD installation, USB update and cold startup of the communications-only retained-loader updater have operator verification on the spare. Controlled incomplete-image recovery has also passed, including USB restoration and cold startup. The next application adds raw input reporting and FAN1/FAN2/probe/Z simulation for a board-only bench setup. Physical probe, Z and fan control remain later stages after bench acceptance. Broader fault recovery, physical outputs and exact target compatibility remain to be verified before using the working machine board. See [the isolated firmware project](firmware/ender_aux/README.md).

Material-height next step: operator-test the single-contact border check and a known-thickness material, then wire the accepted path into desktop controls and camera correction. The native homing cycle has operator acceptance.

Probe priority: verify the new single native fast/slow homing test with a 5 mm initial
lift and final Z 20 clearance, following successful operator-observed deployment/stow.
Reference/measurement admission remains held during this correction; the earlier
repeated G30 procedure is withdrawn.
The supplied terminal history confirms a completed native test with final Z 20;
verify the 5 mm version and the corrected repeat/pre-check handling next.

Material-height work now includes production material/support-plane separation,
measured-height selection and bound tracing/placement/jobs. Physical acceptance
of the 0.10 mm XY target through the intended height range remains pending.
See [precision placement](docs/PRECISION_PLACEMENT.md); the earlier
[height/probe study](docs/MATERIAL_HEIGHT.md) is retained as history.

Development release publication now includes package retention: current plus
two recent versions, followed by a seven-day retirement grace for older packages.
Observe the first live retirement/deletion cycle; see [update policy](docs/UPDATES.md#package-retention).

Desktop readability now includes resizable columns throughout the app, relative
speed controls, and balanced Preview defaults. Validate the new layout and
percentage entry in E3 DEV TEST. A future hybrid speed display can build on the
existing physical feed values; no project migration is required for this display.

Upload/start monitoring now avoids the durable job store's disk transaction lock,
and the desktop shows submission progress separately from machine authority.
Validate the combined feature on the Pi, including long uploads, slow verification,
actual communication loss, STOP during preparation, and the next successful job.

Active reliability investigation: capture primary ACK timeout evidence before
cleanup, distinguish raw serial polling/framing from receiver contention, and
identify the intermittent 0.7.0 streaming stall before changing lifecycle or
timing policy. Instrumentation is implemented; root cause and physical
verification remain open. See CURRENT_STATE.md for automated coverage.

This roadmap describes work after the foundational runtime, machine-profile,
import, planning, and updater-hardening milestones. Detailed implementation and
verification evidence belongs in [CURRENT_STATE.md](CURRENT_STATE.md); the dated
Windows portability snapshot in [PROJECT_STATUS.md](PROJECT_STATUS.md) remains
historical evidence.

Production simulation is no longer an E3 runtime or product capability. Normal
browser and desktop launches are hardware-capable, but unavailable controllers
and cameras remain honestly offline. `MachineService`, motion permission,
coordinate trust, bounds, exact preflight/program authority, temporary arming,
STOP, and `M5` remain the execution boundary. GRBL and Marlin are the only
implemented controller dialects; additional compatibility is not implied.

The physically relevant target remains the Ender-3 S1 Pro, generic 10 W diode
tool head, overhead C920, and the current direct-or-authenticated-Pi-bridged
controller/camera path. Current software verification is not physical
acceptance. Additional named-machine support should wait until matching hardware
is available for the same recorded acceptance process.

## Whole-application audit sequence

0.7.0 consolidates the completed lifecycle corrections. The operator reports
speed recovery and a successful STOP/Home/next-job retest after c016e19.
That closes the specific retest below, not the full fault-injection campaign.
The separate S1 Pro Z-homing feature is archived for later review. Exact
prepared-job automatic Home/START and consistent object layer assignment are
implemented on `codex/auto-home-object-layers`, awaiting operator validation.
The remaining secondary lifecycle and cooling capability investigations follow.

Step 1 implements continuous primary GRBL receive ownership, fault-driven trust
revocation, reply admission boundaries, and explicit abort versus successful
completion. Physical acceptance remains pending; see
[primary session authority](docs/PRIMARY_SESSION_AUTHORITY.md) and
[CURRENT_STATE.md](CURRENT_STATE.md) for test evidence. Subsequent audit steps
now include the implemented Pi/desktop snapshot correction and explicit repeat
Home, pending physical acceptance; see [Pi status authority](docs/REMOTE_STATUS_AUTHORITY.md).
The STOP/Home/next-job follow-up separates pending START, delayed job observations,
and terminal notifications; Windows verification precedes operator retesting.
The subsequent speed report exposed nested serial/receiver lock contention;
the [timing correction](docs/SERIAL_RECEIVE_TIMING.md) retains response ownership
and requires Linux transport tests and a physical speed/STOP retest.
Remaining work includes exact prepared-job auto-Home/Start, secondary lifecycle corrections, cooling capability validation,
and broader physical-sequence acceptance.

## Completed foundation

The following foundations are implemented and automated-test covered:

- deterministic planner behavior with checked-in planning goldens;
- typed planning stages, stable dependency digests, and bounded selective
  recomputation;
- importer registry and immutable bounded-scan manifests;
- shared desktop pre-import review with blocking findings, bounded rendering,
  explicit approval, and exact-source binding;
- imported-raster tracing with seam-invariant bounded fitting, persistent
  physical corner classification, conservative source-neutral recovery of
  arbitrary-angle lines and conceptual circular arcs, editable safe Line-layer
  output, and high-contrast preview comparison controls;
- unified Camera Trace review with physical material eligibility, bounded Auto
  threshold selection, independent physical object/hole area filtering for
  non-grid raster strategies, explicit full-hierarchy or exterior-only Trace
  detail, compound native line/cubic fitting, and an
  optional post-Create geometric Straighten command over selected finished
  project artwork, while retaining the independent repeated-object/grid
  workflow;
- SVG, raster-image, LightBurn, and foreign-G-code preflight;
- structured job readiness preflight before authoritative exact planning,
  including immutable numbered remediation and allowlisted UI-only navigation;
- numbered Machine Setup guidance with an explicit capture-then-save honeycomb
  frame workflow and independent ruler-overlay/frame status;
- machine-aware material recipes that remain authoring aids rather than
  execution authority;
- neutral machine transports separated from immutable GRBL and Marlin dialect
  policy;
- explicit generation-bound primary-controller sessions with private synchronized
  candidates, exclusive Pi serial ownership, command transactions, permanent
  uncertainty quarantine, communication-only post-STOP recovery, structured Pi
  causality metadata, and fail-closed desktop action projection;
- profile-driven real-machine first-run and Machine Manager flows with multiple
  saved physical machine instances;
- immutable running-versus-next-launch machine identity;
- machine/tool-aware new-project defaults, including a visible 0%-power,
  output-disabled fallback for unmatched physical profiles;
- shared operation-color editing from Cuts/Layers and clickable Objects-row
  swatches through one undoable layer command;
- guarded exact Preview and the existing `MachineService` execution boundary;
- capability-gated Pi execution-policy mismatch diagnostics whose authenticated
  profile remains subordinate to the unchanged opaque digest and whose logs
  contain fixed field labels only;
- bounded desktop process shutdown with active remote-camera cancellation,
  finite worker draining, shutdown-only idle-Pi Disconnect, and non-destructive
  accepted-job detach;
- Windows frozen packaging and automatic-update source boundaries, including
  the hardened external Inno process handoff.

These items have different automated, offscreen, interactive, and historical
evidence levels. Completion here means the architecture exists; it does not
upgrade software verification into a hardware or safety claim.

## Near-term hardening

### Automated and software verification

- Keep Fast Development and Compatibility CI aligned with the supported Windows
  test boundary and the standing focused Ubuntu pseudo-terminal/controller-session
  job, without treating that job as general Linux desktop support.
- Keep repository, dependency, workflow, packaging, launcher, and documentation
  drift checks clean.
- Complete the installed frozen PyInstaller E3 -> HTTPS download/verification ->
  close -> visible Inno -> install -> final-page launch exercise in a disposable
  interactive Windows environment. This remains intentionally deferred and is
  not package-verified.
- Continue focused rejection-path coverage for stale authority, source changes,
  controller uncertainty, STOP, reconnect, persistence failures, and updater
  process-creation failures.
- Exercise remaining desktop authoring and review interactions that currently
  have only offscreen or source-level coverage.
- Curate corrected-camera template/Trace fixtures only when they are deliberate,
  reviewed test evidence rather than personal captures.
- Preserve deterministic planning goldens; do not regenerate them merely to hide
  behavior changes.

### Future physical acceptance

When the actual target hardware is available, record the controller, firmware,
configuration, environment, and result for each step:

1. Confirm controller identity, firmware, protocol, relevant settings, and
   usable power scale.
2. Exercise the intended direct or authenticated Pi-bridged connection.
   Record process-exit timing while idle/live, immediately after camera refresh
   and Trace start, with the Pi reachable and unreachable, and while ordinarily
   disconnected; every accepted Close must terminate E3 in under five seconds.
3. Confirm Home/reference establishment and reset/reconnect behavior.
4. Confirm coordinate origin, X/Y directions, and active offsets.
5. Confirm the configured photography park pose and repeatability.
6. Exercise small conservative laser-off jogs in both directions.
7. Complete the documented
   [20-cycle laser-disabled recovery sequence](docs/GRBL_SESSION_RECOVERY_VALIDATION.md)
   (STOP → fresh communication recovery → explicit Home) while retaining the
   physical emergency-stop boundary; record every session generation and
   contradictory/stale result.
8. Run and review a small centrally located zero-power job.
9. Only with the required physical safeguards and attending operator, run one
   supervised low-risk armed test on suitable sacrificial material.
10. Repeat calibration/alignment measurements to establish repeatability for the
    accepted camera, work plane, and support.

This sequence is not a safety certification. Enclosure, extraction, interlocks,
hardware emergency stop, fire controls, and an attending operator remain
physical requirements.

## Product-feature backlog

### Authoring and import

- Shared multi-selection transform boxes, proportional resize, node editing,
  and smart snap guides.
- Persistent on-canvas creation for shapes beyond rectangles.
- DXF import and improved SVG `<use>`, stylesheet, clipping, and text-outline
  support.
- Managed or embedded raster assets instead of external absolute paths.
- Editable regeneration of existing vector text.
- Optional fiducial/marker identification after selecting and testing a real
  marker format.

### Camera, calibration, and material height

- Curated real-camera template and Trace datasets with measured geometry.
- Investigate the separately reported ordinary corrected live-overlay failure;
  the Raw Live Monitor remained usable during the Pi-owned Trace reproduction.
- Repeatability and coverage visualization after camera remounting.
- Material-thickness input connected to calibration profiles.
- Camera-height and optical-center modeling, multiple calibration planes, or
  bounded parallax compensation.
- Optional distance-sensor integration only after its accuracy and failure
  behavior are characterized.

### Raster and production

- Selectable dither algorithms and calibrated grayscale power curves.
- Further fill/hatch optimization without changing guarded motion semantics.
- Improved duration estimates and reproducible job manifests.
- Job history and recovery workflows.
- Controlled pause/resume only for specifically tested controller behavior.
- Stable update rollback and clean-machine recovery verification.

## Release direction

A production-oriented release requires:

- documented repeatable physical alignment across the supported work area;
- a recorded controller/firmware profile and verified power scale;
- installation, update, rollback, and recovery checks on supported systems;
- no known path-generation or guarded-bounds defect in supported inputs;
- physical safeguards and interlock expectations documented without describing
  software controls as safety-rated;
- documentation that clearly separates automated, offscreen, interactive,
  historical, package-level, and physical evidence.

Pi Start now synchronizes idle secondary RX before a fresh acknowledged
`M106 S0` and uses the existing bounded framing-rejection reopen policy.
Startup/restart OFF and exact typed mappings remain unchanged. Failed Start
preserves bounded secondary diagnostics; cleanup STOP no longer manufactures an
operator STOP. Desktop rejection returns promptly and reports the error once,
including when controller cleanup invalidates its session. Physical retesting
remains required; the original physical exception was not retained, so idle RX
contamination or a framing rejection cannot be confirmed from that log alone.

## Pre-start secondary OFF recovery

Pre-start secondary OFF permits exactly one fresh-session recovery after a
persistent-session synchronization, write, acknowledgement, or framing failure.
The existing owner closes the uncertain session, reopens, settles, synchronizes,
and requires a new acknowledged `M106 S0` before primary streaming. Failure of
that sole retry preserves both bounded diagnostics and rejects Start. Air Assist
ON is never automatically replayed. Startup/restart, STOP, mapping validation,
and primary GRBL readiness/stepper-hold behavior are unchanged.

## Continuous raster and fixed coordinate boxes (2026-09-16)

Implemented inline raster power switching and matching preview/Start Here behavior, plus fixed-size X/Y/Z boxes near the Machine controls. Operator motion/engraving validation remains pending; acceleration and row reversals still limit achieved speed.
