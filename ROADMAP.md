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

## Completed foundation

The following foundations are implemented and automated-test covered:

- deterministic planner behavior with checked-in planning goldens;
- typed planning stages, stable dependency digests, and bounded selective
  recomputation;
- importer registry and immutable bounded-scan manifests;
- shared desktop pre-import review with blocking findings, bounded rendering,
  explicit approval, and exact-source binding;
- SVG, raster-image, LightBurn, and foreign-G-code preflight;
- structured job readiness preflight before authoritative exact planning;
- machine-aware material recipes that remain authoring aids rather than
  execution authority;
- neutral machine transports separated from immutable GRBL and Marlin dialect
  policy;
- profile-driven real-machine first-run and Machine Manager flows with multiple
  saved physical machine instances;
- immutable running-versus-next-launch machine identity;
- machine/tool-aware new-project defaults, including a visible 0%-power,
  output-disabled fallback for unmatched physical profiles;
- guarded exact Preview and the existing `MachineService` execution boundary;
- Windows frozen packaging and automatic-update source boundaries, including
  the hardened external Inno process handoff.

These items have different automated, offscreen, interactive, and historical
evidence levels. Completion here means the architecture exists; it does not
upgrade software verification into a hardware or safety claim.

## Near-term hardening

### Automated and software verification

- Keep Fast Development and Compatibility CI aligned with the supported Windows
  test boundary, while running focused Linux/Pi checks when those components
  change.
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
3. Confirm Home/reference establishment and reset/reconnect behavior.
4. Confirm coordinate origin, X/Y directions, and active offsets.
5. Confirm the configured photography park pose and repeatability.
6. Exercise small conservative laser-off jogs in both directions.
7. Exercise software STOP while retaining the physical emergency-stop boundary.
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
