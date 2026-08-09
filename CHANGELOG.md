# Changelog

## Unreleased — `desktop-v1` / `0.2.0.dev0`

- Removed the desktop powered-job warning and typed arming-phrase dialogs.
  **Start** now submits the already reviewed prepared job immediately while
  retaining an internal one-use, time-limited authorization bound to that exact
  program.
- Automatically reopen the matching Machine Setup step and run its Home / park
  precision capture and scoring operation after a powered base-map,
  registration, dense-correction, or validation job completes successfully.
  The handoff is bound to the exact submitted program digest and execution
  receipt, including jobs that complete before the next Qt callback. Failed,
  stopped, replaced, stale same-name, and ordinary project jobs do not trigger
  capture, and an already-open Setup dialog is reused instead of nesting a
  second modal window.

- Hardened controller ownership and transport recovery. Concurrent reconnects
  cannot tear down a newer connection, queued arming cannot survive STOP or
  overlap Home/jog ownership, automatic protocol detection can fall back after
  a consumed GRBL probe rejection, and serial motion-only completion waits for
  the planner. Executable line length, receipt bounds, POSIX reopen state,
  receive-buffer growth, and unsupported baud handling now fail closed.
- Hardened versioned and external inputs across configuration, project,
  material, template, HTTP, SVG, image, calibration, and vision boundaries.
  Duplicate keys, nonstandard numeric constants, coerced scalar/container
  types, non-finite values, malformed topology, oversized image resources, and
  ambiguous HTTP framing are rejected before side effects. Calibration updates
  persist before live publication, and all generated/captured artifacts use
  collision-resistant exclusive publication.
- Release archives now derive their version from canonical package metadata and
  contain only tracked, in-repository regular files. Untracked operator data,
  symlinks (including symlinked parent directories), missing tracked files, and
  paths outside the checkout are rejected. CLI startup also stops the runtime
  after partial startup, bind, serving, or close failures.

- Enabled the Machine panel's guarded XY jog controls. Home / park establishes
  a tracked starting pose; each press sends laser-off absolute millimetre motion
  at a bounded travel feed. Jogging deliberately does not apply the configured
  work-area rectangle so it can be used to measure the real travel envelope.
  STOP, disconnect, jobs, motor release, or an uncertain exchange revoke the
  pose, and armed/busy controllers cannot jog.
- Fixed Auto Trace confidently outlining bright seams between repeated dark
  objects instead of the objects themselves. Trace now evaluates global,
  illumination-corrected, and adaptive filled-region hypotheses, ranks regular
  grids by coherent filled-body support, and keeps signed local contrast as a
  fallback. Grid numbering is stable row-major; out-of-work-area cells remain
  visible in red and unchecked instead of disappearing; direct selection skips
  them; trace/color-pick requests reject project/machine work-area mismatch; and
  corrected pixels retain the rectifier's exact scale even for non-integral
  work-area extents.
- Trace now distinguishes the configured camera/work crop from the smaller
  boundary-margin-reduced laser-output area, reports the exceeded side and
  distance, and fails closed on observations touching a raster edge. Shared
  grid sizing says when it, rather than the raw fit, caused an overrun. Machine
  Setup Step 3 adds a parked 10 mm coordinate/ruler overlay with separate
  configured and margin/spot-offset-aware guarded boundaries; it is diagnostic
  only and never rewrites machine limits from a movable honeycomb ruler.
  Step 3 can also persist an optional detected honeycomb-ruler outline for
  visual comparison. Three rough clicks only seed the ruler search; verified
  baselines and repeated tick marks define the saved reference. The hints and
  result cannot change calibration, detection
  selection, template matching, generated paths, or any machine/output safety
  boundary.
  Template alignment now excludes cropped/out-of-output evidence instead of
  allowing it to support a seemingly viable match, and held-capture errors show
  any secondary motor-release cleanup failure in the same operator message.
- Added one packaged, versioned **Permanent Camera Setup Guide** as the
  canonical five-tab operator sequence. It is available modelessly from the
  Machine Setup footer and the main Help menu, follows the current tab when
  opened, and spells out the calibration-job handoff: Preview Play is animation
  only; close Preview and use the main Laser **Start** without clicking
  **Generate**, which would replace the prepared calibration program.
  Automated contracts bind the runbook to the current tab and button
  labels and verify that it is included in installed packages.
- Bind newly prepared fine-registration and accuracy-validation sessions to the
  exact active bed homography and residual-mesh revision. Legacy sessions
  without that identity, or sessions made stale by any map change, now fail
  before Home / park or camera acquisition and must be prepared again.
- Honor the configured controller startup delay before the first command when
  GRBL or Marlin is selected explicitly, not only during automatic protocol
  detection. ESP32 USB serial devices can now finish bringing up their command
  channel before connection cleanup queries settings and sends `M5`.
- Accept V4L2's parenthesized menu labels when verifying numeric camera-control
  readbacks, such as `auto_exposure: 1 (Manual Mode)`, while still rejecting
  unrecognized trailing text. This keeps valid locked exposure from being
  reported as unverified on the physically inspected C920 control interface.
- Separated corrected-overlay calibration failures from camera-device faults.
  Legacy or stale bed maps now produce one **Bed mapping required** alert and
  route to the appropriate Lens or Bed mapping step without claiming another
  application owns a demonstrably online camera.
- Hardened the final machine boundary against programmatic settings bypasses:
  hardware and motion gates are exact booleans, backend selection is exact,
  feed/timeout/idle-delay limits are independently revalidated, and temporary
  arming is bounded to 1–600 seconds. Arm and Start now re-analyze every exact
  program line and recompute its digest, motion/power flags, and safety profile,
  so forged or mutated preflight tokens fail before transport output while
  cleanup `M5` remains available.
- Moved project cloning, toolpath/zero-power framing, Preview indexing, and Start
  Here rebuilding under owned cancellable desktop tasks. Worker and renderer
  authority is tokenized independently; unfinished Preview close, STOP, project
  replacement, renderer failure, and application shutdown fail closed without
  accepting stale output or writing automatic G-code artifacts. Raw G-code,
  workspace paths, Preview paths, and large backward timeline scrubs are
  time-sliced while STOP remains live.
- Unified raster import and workspace Preview with the bounded PNG/JPEG/BMP
  contract. Workspace pixels now decode from the exact stable byte payload used
  for their SHA-256 identity, and the cache budget is shared across current
  project sources so large multi-image documents do not repeatedly evict and
  decode every preview after an edit.
- Reduced the parked calibration burst from an infeasible 120 frames to 45
  frames while retaining a required 15-frame consensus. The bounded profile
  fits both the configured 15 fps camera deadline and the 10 fps simulator,
  lowers 1080p burst memory substantially, and performs image analysis only
  after the temporary motor-hold scope is released.
- Serialized camera bursts, control updates, scene changes, and restarts behind
  one cancellable owner while keeping copied live previews available. Capture
  deadlines now cover controls, settling, discards, and samples; diagnostics
  include negotiated/observed FPS and sequence gaps. Shutdown releases a
  blocked backend before joining its reader, manual-focus measurement uses a
  fresh post-settle frame, and parked workflows defer lens correction and
  clarity scoring until the final frame has ended the temporary motor hold.
- Composed lens correction, bed homography, and residual-mesh rectification
  into one cached raw-camera-to-bed remap. Cache identity checks reject stale
  or concurrently replaced calibration models, and strict resolution/finite
  coordinate checks fail closed.
- Moved default autosaves and the material database to one OS-native writable
  per-user data root and made project backup replacement atomic. On first use,
  legacy data is copied forward without deleting or overwriting its source;
  failed migration keeps the legacy recovery/preset file visible.
- Restricted the browser control surface to a validated IPv4 local bind, exact
  loopback clients, same-origin requests, JSON POST bodies, and a per-process
  request token. Remote-control configuration is rejected rather than exposing
  hazardous routes, and CLI host/port overrides are validated before startup.
- Keep Machine Setup responsive during Home / park, precision/stable captures,
  checkerboard capture and lens solve, and camera diagnostics. The dialog owns
  one worker at a time, shows progress, keeps a local **STOP / LASER OFF** usable,
  defers Close through cleanup, rejects queued motion after STOP, discards
  stopped results, and clears stale review/apply state before new work. Saved
  axis orientation is also described accurately when calibration provenance is
  stale without relaxing its edit gate.
- Expanded Machine Setup lens management with exact current-resolution capture
  groups, camera-readiness gating, per-capture sharpness/coverage/exposure
  evidence, confirmed evidence deletion, structured solve-quality reasons, and
  worst-view reprojection errors. Replacing or clearing a lens model now makes
  the dependent bed map visibly stale and disables registration and validation
  controls until remapping.
- Made cold legacy lens catalogs header-only and moved checkerboard evidence
  indexing to a visible background task with `640 x 360` detector inputs,
  coherent progress, mutation/close guards, and exact-resolution grouping. The
  bounded index is advisory: the final lens solve still re-decodes and detects
  every selected original at full resolution. Index and solve now digest and
  decode the same capped immutable file payload, reject decode-time evidence
  replacement without a stale commit, and provide an always-available
  **Re-index all captures** recovery action. Fresh lossless captures use that
  same persisted-byte preview-quality pipeline. Machine Setup now also displays
  both observed and camera-negotiated FPS.
- Added a dedicated fresh keyed 5×5 base-bed mapping workflow that requires no
  old homography or manual point entry. It prepares zero-power and normally guarded
  powered jobs through the existing Preview/run pipeline, resolves all grid
  rotations/reflections from two larger interior crosses, rejects zero-power/stale/
  altered/ambiguous sessions, and requires 25 inliers within `0.50 mm` RMS and
  `0.80 mm` maximum fit error. Reviewed application installs points and the new
  homography transactionally, clears corrections tied to the old base map, and
  records the generated keyed controller-coordinate labels as normal on both
  axes. A separate laser-off direction and bounds check remains required before
  normal production, but no longer masquerades as a hidden Step 3-to-Step 4
  calibration gate.
- Raise the corrected-camera overlay default from 18% to 70%, matching the
  clearer photographic workspace used during physical alignment while keeping
  the opacity control fully adjustable.
- Keep the canvas overlay key fixed at the upper-left during workpiece moves,
  scrolling, zooming, refits, and overlay refreshes. The key can now be dragged
  directly and retains its operator-selected viewport position; the frozen-test
  warning badge defaults to the opposite corner so both remain visible.
- Reorganized the native workspace into three resizable regions: a full-height
  right inspector for Cuts/Layers and its related design tabs, a compact G-code
  panel below the canvas, and a wider Laser/Machine/Material Library panel
  beside the G-code. Raw G-code is visible by default, Console remains optional
  in the same slot, and layout persistence is bumped to `v6` so obsolete saved
  dock topology cannot restore the cramped two-stack arrangement.
- Made that three-region shell responsive at `1080x780` and `900x680` with
  13 pt text. Dock minimums no longer force the window wider than the screen,
  operation and disabled-jog controls reflow without horizontal clipping, and
  numeric operation settings create one project edit when committed instead of
  one undo/rebuild cycle per typed digit.
- Prevent Connect and Home / park from sharing or stealing GRBL replies.
  Controller initialization, each command/ack transaction, complete Home / park
  sequences, and scoped camera captures now own the serial response stream
  exclusively. The desktop exposes a non-actionable **Connecting** state and
  disables overlapping machine actions while keeping software Stop available.
  `$1` parsing also accepts anchored spaced, annotated, and integral-decimal
  controller reports without accepting `$10` or fractional idle delays.
- After a successful powered serial job, keep the job active while the service
  confirms `M5`, drains previously accepted planner motion, homes, returns to
  the configured camera pose, waits behind the park move, restores the normal
  GRBL idle delay if necessary, and explicitly releases the motors. Job-stream
  acknowledgements now tolerate planner backpressure beyond the short
  interactive-command timeout. The Laser panel exposes drain/home/park/release
  phases, and asynchronous completion failures raise a one-time desktop error
  instead of resembling an ordinary 100% finish. It does not send fan/coolant
  commands. Stops, failures, emergency actions, disconnects, and zero-power jobs never
  initiate the additional homing and parking motion. Preview states that its
  cyan marker is the end of the exact stream, before these automatic actions.
- Treat GRBL `$1=255` as a scoped camera-hold state rather than a permanent
  preference. Every serial GRBL connection explicitly releases the motors;
  when it finds a stale `255` left across a crash or power cycle, it first
  restores configured `machine.grbl_step_idle_delay_ms` (250 ms by default).
  Camera cleanup performs the same restoration and explicit release.
- Keep GRBL steppers energized from immediately before Home / park through the
  parked-bed precision camera burst, then restore the controller's original
  `$1` idle-delay setting before image analysis. Because neither the restored
  delay nor a forced zero-delay planner event released this ESP32 controller,
  the app now uses FluidNC `$MD` when available and falls back to standard GRBL
  `$SLP` followed by its required soft reset. Releasing motors invalidates the
  coordinate reference and returns hardware to **HOME REQUIRED**. Restoration
  is attempted on failed captures; simulator behavior is unchanged.
- Clear the expected GRBL alarm after the `$SLP` fallback reset, and make the
  next Home / park recover defensively when its initial laser-off `M5` is
  rejected specifically with post-sleep `error:9`. The recovery unlocks only
  to issue `M5` and immediately perform mandatory homing; other errors remain
  blocking.
- Refine identical rounded-rectangle grid rotation from repeated lattice-center
  baselines instead of averaging noisy short-edge silhouette fits. Added a
  separate **Snap cells to fitted grid** option: disabling it keeps canonical
  dimensions and corner radius while allowing each direct cell to retain its
  observed center and rotation; inferred cells remain lattice-bound.
- Make desktop **Detect objects** establish its own hardware camera pose. It
  now performs guarded Home / park inside the temporary stepper-hold scope,
  captures a fresh stable frame set, restores the original motor idle behavior,
  and only then rectifies, detects, and renders traces. Frozen simulation test
  frames bypass machine activity.
- Changed parked-bed precision measurement from one sharpest surviving frame
  to the median coordinates of the 15 clearest all-mark inlier frames. The
  current 45-frame burst retains temporal screening while reducing sensitivity
  to one unusually sharp but geometrically shifted frame.
- Fixed shifted-confirmation preparation so it writes a confirmation-only
  session that its matching capture button accepts, rather than incorrectly
  marking the same session as both validation and confirmation.
- Tightened shifted-confirmation detection around each predicted position and
  reject large seed shifts, preventing older neighboring calibration crosses
  from being scored as the newly burned confirmation pattern.
- Reworded stale dense-grid capture errors to explain which physical marks are
  obsolete, why marks used to fit a refinement cannot confirm that refinement,
  and the exact shifted-confirmation action the operator should use next.

- Corrected dense-fit review labeling: an invalid result with multiple
  unreliable cells now marks them red as **REJECTED**, never amber as safely
  inferred. **INFERRED** is reserved for the single-cell, application-eligible
  case.
- Split interactive stable stills from parked-bed statistical capture. Trace,
  template matching, color sampling, ordinary saved stills, and similar UI
  operations now choose the sharpest of five fresh frames after a short discard
  instead of waiting for the 45-frame calibration burst. Parked calibration
  and validation retain the full statistical analysis.
- Separated the 5×5 dense-fit, 4×4 interstitial-validation, and shifted
  confirmation sessions. Preparing or capturing one grid can no longer make a
  different grid's capture button reuse its target metadata. Machine Setup now
  provides an explicit shifted-confirmation capture action as well.
- Parked-bed analysis retains configurable robust-median and single-sharpest
  comparison strategies, while the default 45-frame profile uses the stable
  15-frame clarity consensus described above.

- Added a reviewed, rollbackable 5×5 local residual correction for
  position-dependent camera-to-laser error. The bounded mesh is used in both
  mapping directions and image rectification, with an independent 16-point
  interstitial validation workflow and 0.30 mm RMS / 0.60 mm maximum targets.
- Dense-map safety rejections now remain on the review screen. The captured
  image and all measurements are shown, and one camera-occluded grid cell may
  be excluded and conservatively inferred from the other 24. It is highlighted
  as **INFERRED** and still requires independent holdout validation; two bad
  cells or a fit outside the movement/smoothness gates cannot be applied.
- Dense 4×4 validation now overlays every numbered detected center, its cyan
  commanded-map position, and the error vector between them. Matching table
  rows use the same pass/warning/failure colors so detections can be audited
  against overlapping calibration marks.
- Added a one-time reviewed validation refinement for a coherent 16-point
  residual, with stale-map, confidence, update-size, total-displacement, and
  local-gradient gates. A separate checker-shifted 16-point confirmation job
  uses fresh positions and is required after refinement; the same validation
  measurements cannot be reused to claim accuracy.

- Added a GRBL coordinate-state preflight. Home/park records the active
  workspace plus its `G54`-`G59` and `G92` offsets through read-only `$G`/`$#`
  queries; absolute-motion jobs are blocked if that state changes before
  streaming, and the reference is exposed in status and controller logs.
- Fixed calibration analysis consuming a cached pre-park camera image. GRBL
  park completion now uses a positive `G4 P0.01` synchronization dwell, and
  registration/validation allow a six-second physical settling interval before
  waiting for three newly captured frames with a separate six-second freshness
  timeout. This also excludes fresh frames captured while the slow bed is still
  completing its queued park move.
- Added a persistent machine connection status and Connect/Disconnect button to
  the Machine Setup window so calibration recovery does not require closing the
  dialog.
- Added a shared precision camera-capture path for alignment and other
  clarity-sensitive operations. It waits for motion/camera settling, discards
  genuinely newer buffered frames, collects a configurable unique-frame burst,
  reapplies and reads back supported camera controls, and selects a sharp still
  where a single image is required.
- Fine registration and holdout validation now detect each mark across the
  burst, combine centers with median/MAD outlier rejection, reject excessive
  temporal jitter, and persist frame, control, inlier, outlier, and jitter
  diagnostics. Machine Setup can repeat the measurement without another home
  cycle to distinguish capture variation from homing repeatability.
- Stopped recurring camera-refresh failures from opening a modal dialog every
  refresh cycle. One camera fault is acknowledged once, subsequent failures are
  suppressed until recovery or an explicit Refresh camera retry, and successful
  recovery is reported without stealing focus.
- Made explicit Refresh camera perform a real device reconnect when the camera
  is offline, has no frames, or reports a read fault. Reopening runs outside the
  GUI thread and a successful reopen immediately refreshes camera status and the
  corrected workspace image.

- Made Machine Setup axis orientation explicit and persistent: X/Y now show
  NORMAL/OFF or REVERSED/ON, legacy maps are visibly inferred rather than
  falsely shown off, and confirmation can record an inferred state without
  changing calibration points. Setup also restores its window, tab, simulation
  scene, cross sizes, and marking speeds while intentionally resetting marking
  power to zero.
- Replaced ambiguous checkbox indicators throughout the desktop with a
  consistent compact gray-OFF/green-ON switch treatment, including the saved
  X/Y mapping-orientation controls.

### Committed desktop foundation

- Added a native PySide6 workspace alongside the browser application.
- Added multi-object, multi-layer `.e3laser` projects with undo/redo, grouping,
  alignment, distribution, z-ordering, atomic saves, backups, and autosaves.
- Added SQLite material presets and multi-layer vector toolpath generation.
- Added corrected-camera overlay, toolpath preview, camera focus controls, and
  guarded machine panels.
- Kept jogging and pause/resume visibly disabled pending dedicated core APIs and
  physical controller verification.

### Desktop control surface

- Fixed Home/park on GRBL-derived controllers that acknowledge `$H` after the
  endstop sequence but do not return the expected realtime `<Idle...>` report.
  The procedure now continues from that acknowledgement, and the subsequent
  park move uses a zero-duration
  `G4 P0` planner barrier with the existing 120-second completion limit.
- Made controller acknowledgement failures identify the exact command, so a
  setup timeout distinguishes `M5`, `$H`, `G21`, `G90`, the park move, and its
  completion barrier.

- Added a dedicated exact-job graphical Preview opened by generation and zero-power
  framing. It provides time scrubbing, animated playback from 0.1× to 40×,
  cut/travel display, optional controller-power shading and inversion, current
  feed/power/X/Y/layer/pass details, timing and distance statistics, warnings,
  fit/zoom/pan, and PNG export.
- Built Preview from an immutable parse of the exact finalized G-code submitted
  to the existing guarded execution path, including controller-ignored
  layer/pass/source metadata and physical laser-spot offset recovery.
- Separated prepared-job maximum power from live controller execution status so
  idle polling no longer replaces a generated `20% / S200` summary with
  `no active controller job / 0%`.
- Added an original monitor/toolpath glyph for the Preview toolbar action so
  the icon-only Job toolbar no longer falls back to showing the action words.
- Added per-operation Preview visibility, dynamic generated-layer legends, and
  cut/time/maximum-power statistics.
- Added selectable source-order or nearest-path planning and records the exact
  planner in controller-ignored job metadata and the Preview heading.
- Added real bounded closed-vector fill, binary vector raster, and imported
  50%-threshold image raster G-code. Scan interval/angle are editable; raster
  overscan is laser-off, timed, and controller-space bounds checked.
- Replaced per-dark-run image planning with alpha-aware, physical-pitch
  resampling and deterministic 8x8 grayscale dithering. Raster rows now remain
  contiguous and serpentine, with full-row lead-in, white gaps, and lead-out at
  engraving feed while the laser is off. Excessive sample or controller-command
  counts fail closed before a job can be prepared.
- Corrected image raster orientation to match the canvas through source-top,
  mirror, and object rotation transforms. Image rows now honor the operation's
  absolute machine-coordinate scan angle and retain exact pitch for
  non-integral dimensions. Transformed images participate in zero-power framing,
  zero-power cut metrics match the exact plan, and encoded dimensions plus
  row/sample/vector-edge workloads are rejected before expensive decode or
  iteration.
- Added acceleration and command-latency-aware Preview timing configuration.
- Added guarded **Prepare Start Here** at reviewed move boundaries. Replacement
  programs restore absolute millimetres, laser-off positioning, layer/pass
  context and power, record the configured photography pose, and preview the
  exact spot-offset approach from Home/park before the unchanged normal
  execution gates.

- Added a build-first native title-bar identity containing the application name,
  release version, and short source revision before the current project name.
  Source launches fingerprint the installed application files, while packaged
  builds can supply `E3_POSITIONING_SYSTEM_REVISION` explicitly.
- Suppressed Qt/X11's automatic application-name title suffix because the
  build-first caption already contains the product name.
- Added native **Machine Setup** tabs for camera controls, synthetic scenes,
  checkerboard lens calibration, manual/CSV/automatic bed mapping, residual
  review, workpiece detection, and ArUco inspection.
- Routed the Camera inspector's calibration buttons into the native workflow
  and paused its live overlay while setup owns capture actions.
- Added validated G-code export and documented desktop parity with every
  operator capability in the legacy browser workflow.

- Reorganized the native window around a LightBurn-inspired information
  hierarchy while retaining E3 terminology, behavior, and safety boundaries.
- Replaced the large text-button shell with original icon-only command bars, a
  compact drawing rail, a bright gridded drafting bed, thin rulers, and hidden
  canvas scroll bars while retaining direct middle-button/Space panning.
- Added a non-hideable runtime strip showing simulation/hardware authority,
  controller connection, motion permission, and a persistent software-stop
  action with the physical emergency-stop disclaimer. It shares the command row
  on wide screens and moves to a guaranteed-visible row on narrow screens.
- Added a selection-aware numeric property bar for position, size, rotation,
  percentage scaling, aspect locking, millimetre/inch display, mirroring, and
  rectangle corner radius.
- Added direct single-object corner resize and rotation handles with live
  preview, anchored resizing, Shift rotation snapping, stale-model protection,
  and atomic undo/redo commits.
- Replaced fixed center insertion for rectangles with a persistent canvas draw
  tool: press-drag-release shows a live active-operation-colored outline,
  creates the exact snapped dimensions as one undoable command, selects the new
  object, and remains active for consecutive rectangles.
- Made Select and Rectangle exclusive visible tool modes. Select or a canvas
  right-click exits rectangle drawing; middle-button and Space-drag panning
  retain priority, and Escape remains the software-stop shortcut.
- Reworked Operations/Layers into an orderable five-column operation summary
  with inline Output/Show controls, color editing, and explicit unsupported
  fill/raster toolpath status.
- Added a fixed 30-color numbered operation palette; existing colors assign
  selected objects and unused swatches create a matching operation. Added split
  design and laser inspector tab groups, a dedicated Window menu, and a
  resettable workspace layout.
- Fixed the native view paint path so the light bed and adaptive grid remain
  visible without a camera frame; the corrected image remains adjustable over
  the drafting surface.

### Camera object tracing

- Added multi-object camera tracing with color/contrast modes, regular-grid
  inference, reviewed selection, border offsets, and vector-object creation.
- Made regular-grid tracing fit a dominant repeated-shape family and one shared
  lattice. Rounded output can now repair malformed direct cells, reject
  duplicate/noisy candidates, synthesize gaps with identical geometry, and
  select the reviewed complete grid in one action.
- Added one-step undo for a set of traced objects.
- Added synthetic object-tracing tests and an offline trace inspection tool.
- Made camera color sampling visibly enter a canvas-pick state, report sampling
  failures in the Trace inspector, and carry the sampled BGR color through the
  detector instead of reducing every sample to a saturated hue.
- Added neutral-color segmentation and signed large-scale contrast masks for
  real-image-like textured backgrounds. Filled rounded rectangles now compete
  as silhouettes instead of being represented only by an expanded local edge
  halo.
- Made fitted rounded-rectangle results preview the same clean proposed vector
  that will be created instead of the simplified camera-pixel contour.
- Renamed the contour control to **Simplify tolerance**, limited it to
  **Simplified contours**, and exposed fitted dimensions and corner radius in
  the results table.
- Preserved exact preview placement when irregular contours become project
  paths by centering them from their actual bounds.
- Froze the analyzed camera frame throughout trace review and rejected stale
  asynchronous results after a new request, clear, source change, or shutdown.
- Registered Qt camera pixels to the OpenCV/BedMapper pixel-center convention,
  removing the half-pixel overlay shift that looked outside on top/left edges
  and inside on bottom/right edges at high zoom.
- Corrected the rounded-fit raster extent from a pixel-center span to a pixel
  count, removing a one-pixel radius underestimate on ideal rounded masks.
- Added a dynamic on-canvas overlay key with explicit color roles and distinct
  line styles: selected Trace results are solid green, aligned template cuts
  are solid cyan, and camera-detected label edges are dashed amber.

### Laser spot coordinates

- Added zero-default X/Y laser-spot offsets using the convention `spot =
  controller + offset`, with shared desktop/browser generation.
- Validate both the desired physical spot geometry and the offset-corrected
  controller path. Generated programs record both bounds, and the desktop
  preview removes the controller correction so it remains aligned with the
  corrected camera image.
- Rejected and removed the provisional local X −28 mm, Y −8 mm correction
  after a second physical cut moved farther from the target. The laser-burned
  bed map already references the spot; session homing and axis orientation now
  require laser-off verification before another powered test.
- Added per-connection coordinate-reference tracking. Serial motion and arming
  are rejected until homing/parking succeeds; reconnect, reset, emergency stop,
  and job failure invalidate the reference.
- Made desktop hardware Start run `M5`, home, park, and wait idle before arming
  and execution, removing the need for a separate manual Home / park action.
- Increased Home/park setup-command acknowledgements to at least six seconds
  for slower controllers. Job streaming now uses a cancellation-aware extended
  acknowledgement timeout because planner backpressure and synchronized laser
  state changes can delay `ok`; interactive commands retain their configured
  timeout.
- Added explicit X/Y bed-point reversal controls because a symmetric cross grid
  cannot determine controller-axis sign from image geometry alone.
- Added a native eight-point fine-registration workflow. It prepares zero-power or
  normally guarded powered cross jobs, captures fresh marks at the homed camera
  pose, reports commanded/observed residuals, distinguishes global translation
  from position-dependent error, and refuses unsafe or excessive corrections.
- Persisted an explicit, resettable bed-map translation separately from the
  zero-default laser-head offset. Application is confirmation-gated, limited to
  5 mm cumulative magnitude, and allowed only for a consistent multi-point
  result; a full bed solve clears the fine translation.
- Added reviewed inclusion checkboxes for fine-registration measurements. Up to
  two clearly obstructed or false detections may be excluded while remaining
  visible; at least six marks and every original confidence/scatter gate remain
  mandatory. The future lower target is moved away from the photo-pose head
  obstruction observed in the first physical run.
- Added a separate reviewed full-bed homography refinement for position-dependent
  fine-registration results. It requires seven RANSAC inliers, broad coverage,
  bounded residual/warp/scale checks, explicit confirmation, and preserves the
  prior solved map for reset; it never weakens the translation thresholds.
- Added guided independent accuracy validation with five holdout crosses. Zero-power
  and powered jobs use the normal guarded pipeline; automatic capture reports
  per-point, RMS, maximum, and mean error against fixed limits, rejects missing,
  low-confidence, zero-power-only, or stale-map sessions, and cannot change calibration.

### Cutting templates

- Added versioned `.e3template` files and an atomic reusable template library.
- Added a dedicated rounded-rectangle grid designer with live preview,
  rows/columns, cut width/height/radius, edge-gap or center-pitch entry, and
  footprint/pitch/count feedback.
- Limited generated grids to 500 objects, rejected footprints outside the
  project work area, and marked grids with fewer than three cuts as manual-only
  for alignment.
- Applied a `1e-6` mm numerical tolerance consistently at work-area and
  G-code bounds checks so exact-fit layouts survive floating-point noise while
  meaningful overflow remains blocked.
- Persisted versioned grid-authoring metadata so saved grids can be edited while
  retaining their template identity; arbitrary project-authored templates are
  not guessed to be parameter grids.
- Added direct grid creation in the active project layer as one undoable batch.
- Added numeric rectangle corner-radius editing alongside width and height in
  the Transform inspector, applied as one validated undoable shape change.
- Added resilient library scans: malformed entries are reported without hiding
  valid templates, and duplicate persistent IDs are excluded safely.
- Added project-to-template normalization for visible cut objects and rigid
  translation/rotation instantiation with new object IDs.
- Added per-outer-contour matching features for compound imported SVG paths
  while excluding contained holes.
- Added manual template selection, geometry-based candidate ranking with
  synthetic test coverage, ambiguity and scale-mismatch warnings, and reviewed
  overlay adjustment.
- Added one-step batch undo when aligned template objects are created on the
  active project layer.
- Reused one frozen corrected frame across candidate settings, rejected weak or
  unresolved ambiguous matches, and canceled results invalidated by focus or
  library changes.
- Added a safe-simulation alignment source that can load a corrected full-bed
  PNG/JPEG or deterministically generate a selected template at known X/Y and
  rotation with optional noise and missing labels. The frozen frame feeds the
  normal tracer and matcher, remains memory-only, and can be cleared to restore
  the synthetic camera.
- Added source-generation guards, a persistent workspace warning, and camera
  control gating so stale or misleading live-camera state cannot replace a
  frozen test image during review.
- Made maximum-size synthetic grids render through per-label pixel regions
  instead of repeated full-bed buffers.
- Made alignment review reuse the smooth fitted camera boundary shown by Trace,
  and draw its amber dashes over the cyan cut line so both remain identifiable
  when the fit is exact.
- Replaced the generated test image's chromatic antialiased silhouette with an
  exact discrete rounded mask. This removes the one-pixel false size expansion
  while preserving antialiasing for printed detail inside each label.
- Invalidated generated G-code and toolpath previews after project revisions so
  aligned geometry cannot be followed by execution of a stale job.
- Reserved `marker_id` as schema metadata; marker-based identification is not
  implemented.
- Documented that automatic matching does not compare corner radius, so
  otherwise identical templates that differ only by radius require manual
  selection.
- Added focused synthetic and offscreen behavioral tests for schema round trips,
  library behavior, rigid placement, alignment/ranking, review controls,
  object creation/undo, and stale-state rejection. Real-camera and physical
  alignment remain unverified.

### Windows simulation

- Made POSIX serial transport selection lazy so simulator imports remain
  portable.
- Enabled safe browser and native desktop simulator startup on Windows.
- Made POSIX pseudoterminal tests skip explicitly on unsupported platforms.
- Fixed Windows text clipping in the grid designer's primary action and made
  its form, preview, status card, and footer fit compact logical screens.
- Made the Templates, Camera, Trace, and Transform inspectors tolerate narrow
  docks and larger text without hiding controls; concise responsive labels keep
  their full meaning in tooltips.
- Made an explicit choice of the already-selected template activate placement,
  so a one-template library can always open its workspace preview.
- Kept manual template placement visible until the operator explicitly starts
  **Align selected template** or automatic camera identification.
- Added direct manipulation of the transient cyan template preview: dragging a
  cut moves the complete layout and dragging its round handle rotates the
  layout about the reviewed center, with numeric placement controls kept in
  sync and camera detections left fixed for comparison.

### Documentation

- Added repository-wide development instructions and a current-state snapshot.
- Reconciled architecture, platform support, test evidence, and roadmap status.
- Documented the cutting-template workflow, rigid-only placement contract, and
  real-camera verification boundary.

Unreleased items must not be treated as part of version 0.1.0 until they are
committed, verified, and included in a release.

## 0.1.0 — 2026-08-04

- Initial GitHub-ready source tree
- Simulation-first browser application
- C920/OpenCV camera capture and V4L2 controls
- Lens and bed calibration workflows
- SVG parser, placement engine, vector G-code generation, and zero-power framing
- POSIX serial controller abstraction with GRBL/Marlin probing
- Fixed photography-position homing/parking workflow with controller-idle wait
- Conservative streamed G-code allowlist, compact-word parsing, coordinate checks, and rapid-with-laser rejection
- Zero-power framing with no `M3`/`M4` command
- Software safety gates, 20 automated tests, end-to-end simulated API validation, documentation, and Linux installation scripts
