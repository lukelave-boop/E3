# Roadmap

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

Step 1 implements continuous primary GRBL receive ownership, fault-driven trust
revocation, reply admission boundaries, and explicit abort versus successful
completion. Physical acceptance remains pending; see
[primary session authority](docs/PRIMARY_SESSION_AUTHORITY.md) and
[CURRENT_STATE.md](CURRENT_STATE.md) for test evidence. Subsequent audit steps
now include the implemented Pi/desktop snapshot correction and explicit repeat
Home, pending physical acceptance; see [Pi status authority](docs/REMOTE_STATUS_AUTHORITY.md).
The STOP/Home/next-job follow-up separates pending START, delayed job observations,
and terminal notifications; Windows verification precedes operator retesting.
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
