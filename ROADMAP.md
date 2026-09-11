# Roadmap

## Gauge focus calibration

Implemented for operator testing: native border reference and surface probing,
7 mm gauge teaching, 5/3 mm derived gaps, persistent mounting offset, explicit
preview/position/clearance, and raised-surface V2 firmware. Next: verify actual
gauge fit at multiple elevations, then bind the accepted surface/calibration to
job preparation and coordinate a final Z lift before post-job XY homing. Camera
projection correction for raised work remains an independent calibration task.

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

Compact firmware's native Z cycle and approximately 7 mm material measurement
have operator evidence. Automatic FAN1 CPU cooling is implemented; thermal
switching and installed-board USB updates require physical qualification.
Next Z work compares caliper-measured samples, physical travel and support
variation. See docs/PI_CPU_COOLING.md.

The stock F401 baseline is restored and its Pi USB identity works. Next,
operator-test the [compact F401 kit](firmware/marlin_mainboard_compact/README.md):
confirm live MCU/capacity and all capabilities, cold start with normal Pi/webcam
wiring, then explicit USB update/boot and attached fan/Z/probe qualification.
The initial installer already includes USB updating. The headless profile
intentionally omits the stock menu; future display support is separate.
Earlier milestones below are historical.

Connected startup now has updater 0.2.0 and a bounded Pi Marlin-readiness
handshake. Ordinary traffic cannot hold the updater; incomplete images still
remain in recovery. A new SD install and companion Pi source update are required.
[Startup validation](firmware/marlin_mainboard/STARTUP.md) remains operator-pending; no physical
fix of the separate zero-response fault is claimed. Earlier release notes follow.

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

Material-height work now has a two-plane camera calibration study with a
separate intermediate-height check and an operator-positioned probe measurement
using the existing Creality/Air Assist owner. The first operator border reference
succeeded. Next: validate the corrected deployment sequence, known thicknesses,
tall-material behavior and interruption, then integrate
explicit material/support planes and provenance into tracing and execution.
See [height model and probe redesign](docs/MATERIAL_HEIGHT.md).

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
