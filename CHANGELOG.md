# Changelog

## 0.6.196 - bounded pre-start OFF recovery

Pre-start secondary OFF permits exactly one fresh-session recovery after a
persistent-session synchronization, write, acknowledgement, or framing failure.
The existing owner closes the uncertain session, reopens, settles, synchronizes,
and requires a new acknowledged `M106 S0` before primary streaming. Failure of
that sole retry preserves both bounded diagnostics and rejects Start. Air Assist
ON is never automatically replayed. Startup/restart, STOP, mapping validation,
and primary GRBL readiness/stepper-hold behavior are unchanged.

## Unreleased

Pi Start now synchronizes idle secondary RX before a fresh acknowledged
`M106 S0` and uses the existing bounded framing-rejection reopen policy.
Startup/restart OFF and exact typed mappings remain unchanged. Failed Start
preserves bounded secondary diagnostics; cleanup STOP no longer manufactures an
operator STOP. Desktop rejection returns promptly and reports the error once,
including when controller cleanup invalidates its session. Physical retesting
remains required; the original physical exception was not retained, so idle RX
contamination or a framing rejection cannot be confirmed from that log alone.

Entries in this section are chronological. Simulator references in earlier
entries describe behavior that existed before the removal entries below and are
not current product capability.

- Corrected GRBL motion readiness so `READY_MOTION` is published only after Home
  establishes the current generation's coordinate reference and the controller
  verifies continuous stepper hold (`$1=255`). Successful jobs retain that held
  reference, including post-job Home / park, so another job may start without a
  redundant Home. Intentional release, STOP, fault, reconnect, restart, or serial
  uncertainty still invalidates readiness. No `$SLP` or `$MD` is emitted.

- Added a bounded, conservative classifier for ESP-IDF firmware diagnostics
  multiplexed onto a GRBL serial stream. Valid `E`/`W`/`I`/`D`/`V` frames are
  retained in generation- and transaction-labelled diagnostics without stealing
  payload or acknowledgements; ANSI control framing is removed from operator
  presentation. These frames provide no homing evidence and do not extend
  deadlines. Arbitrary text and malformed protocol still fail closed, while
  `error:x`, `ALARM:x`, and realtime Home/Idle frames remain authoritative.

- Corrected the hardened GRBL connection handshake for controllers that cleanly
  acknowledge `$I` without returning its optional identity payload. An explicit
  `machine.protocol = grbl` now continues into the complete fail-off, settings,
  `$1`, modal, workspace/offset, and realtime capability verification; `$I`'s
  acknowledgement alone never establishes trust and auto protocol does not infer
  GRBL from it. Optional identity payload remains exposed when present,
  contradictory positive identity still fails closed, and later incompatibility
  now has a distinct diagnostic. Generation quarantine, fresh-session retry,
  transaction ownership, synchronization, STOP, and READY publication gates are
  unchanged.

- Rebuilt the primary Raspberry-Pi-to-GRBL connection boundary around explicit,
  generation-bound controller sessions. Private candidates obtain exclusive
  POSIX ownership, purge complete/partial/kernel RX state, prove a quiet window,
  and complete `$I`/`M5`/`$$`/`$G`/`$#`/`?` alignment before publishing HOME
  REQUIRED. Ordinary transactions and jobs retain their exact transport and
  command sequence; uncertain writes, reads, acknowledgements, framing, STOP,
  or replacement permanently quarantine that generation. STOP remains the
  immediate primary-first path, then asynchronously establishes communication
  only on a fresh session without Home, motion, arming, output, Air Assist, or
  job resume. Pi RPC responses now carry boot/build/state/session metadata and
  structured errors, stale client actions/results fail closed, conflicting
  physical operations are rejected instead of queued, and lifecycle recovery is
  single-flight. The desktop presents Pi reachability separately from the 11
  controller states, gates every motion/arming/start/manual surface, suppresses
  stale callbacks, and exposes bounded sanitized diagnostics. Focused Ubuntu
  pseudo-terminal/session/Pi regression CI and a deterministic recovery fault
  matrix were added. These controls are not safety-rated and require the
  documented 20-cycle physical validation before hardware behavior is verified.

- Added conservative geometric primitive recovery after the shared native
  raster contour fit. Imported rasters and Camera Trace's Contrast, Auto,
  Color, and direct native grid adapters use the same source-neutral stage.
  Compatible baseline spans may become robust arbitrary-angle
  total-least-squares lines or conceptual circular arcs when source-pixel and
  fitting-tolerance maximum, RMS, endpoint, join, frame, and topology gates all
  pass. This is geometric model fitting, not OCR, glyph, logo, or template
  recognition. Hard-corner partitions remain protected, and a recovered join may move
  an observed corner only to a nearby model intersection inside both endpoint
  allowances. Any rejected hypothesis or invalid composition falls back to the
  original fitted line/cubic pieces. Conceptual arcs are stored as bounded
  canonical cubic Bézier spans; no native path schema or controller-arc command
  was added. Compact diagnostics report recovered and rejected primitive counts,
  lengths, residuals, and endpoint adjustment; the existing bounded timing
  snapshot reports elapsed recovery time. Raster rotation, thresholding,
  source-edge output localization, smoothing, G-code planning, and post-Create
  Straighten behavior are unchanged.

- Added an explicit Camera Trace **Trace detail** choice above Purpose.
  **Full detail** remains the default and preserves the existing exterior,
  hole, island, and nested contour behavior. **Outer silhouette** sends only
  each disconnected foreground root's true exterior boundary to native fitting
  and vector creation. Its source-neutral `OUTER_ONLY` route now uses bounded
  external-contour extraction before hierarchy limits or fitting, so ignored
  photographic interior topology cannot veto or exhaust the exterior; open
  notches and concavities remain part of that exterior and disconnected roots
  remain separate. The cleaned Mask and independent hole filters remain exact
  evidence rather than being filled for display. Manual and automatic Contrast,
  top-level Auto, and Color honor the selection; Grid retains its specialized
  Full-detail behavior and disables the choice. This changes no threshold
  scoring, eligibility, calibration, planning, motion, laser, Air Assist, Pi,
  Straighten, or primitive-recovery behavior.

- Separated Camera Trace foreground-object area review from enclosed-hole
  cleanup for non-grid Contrast and Auto's dark/light raster strategies. The
  Trace panel now exposes minimum/maximum object area and minimum/maximum hole
  area in mm². Holes inside the inclusive hole range are preserved; holes below
  its minimum or above its optional maximum are filled in the exact production
  Mask before contour extraction and native fitting. Existing preferences
  migrate once by inheriting their former minimum-area coupling and defaulting
  to no maximum-hole filter. The shared source-neutral vectorizer supports the
  same explicit limits while omitted imported-raster settings retain their old
  minimum-pinhole and unbounded-maximum behavior. Grid and Color paths, the Auto
  threshold-selection algorithm, normalization, exposed-bed eligibility,
  Straighten, planning, motion, Air Assist, Pi execution, homing, and laser
  authority are unchanged.

- Reworked Machine Setup calibration guidance and recovery without changing
  calibration math or execution authority. Every numbered tab now presents a
  compact Goal / Do this now / Done when guide. Bed Mapping groups the normal
  honeycomb workflow into **1. Home, park & capture ruler overlay** followed by
  **2. Detect & save honeycomb frame**, with independent MISSING/CURRENT/STALE
  status, visible prerequisite guidance, full-width scalable actions, a
  preview-only clear control, and diagnostic-only three-hint fallback under
  Advanced / troubleshooting. Automatic review now requires the literal **Save
  honeycomb frame** action; Try again and Cancel save nothing. Structured
  preflight findings now carry immutable ordered remediation and stable UI
  navigation IDs. Blocked Job Preflight shows numbered recovery, preserves the
  exact technical reason, and can open the precise Machine Setup tab or Machine
  Manager field through a fixed presentation-only route that performs no
  capture, motion, calibration write, arming, or laser action.

- Added capability-gated, fail-closed diagnostics for Windows/Pi execution-policy
  mismatches. FINALIZE and START can carry a bounded authenticated copy of the
  fixed 25-field policy profile; the Pi first proves it hashes to the existing
  opaque policy digest, then logs only fixed differing field labels. Values,
  credentials, arbitrary client labels, authorization phrases, and G-code are
  never logged by this diagnostic. Older nodes receive no new field, the policy
  digest algorithm and independent Pi preflight remain unchanged, malformed or
  unbound diagnostics are rejected, and the desktop now directs the operator to
  Machine Setup / Machine Manager or node diagnostics.

- Added the permanent pointer-driven Windows **E3 DEV TEST** launcher workflow.
  A separate one-file, windowed executable with an orange DEV icon validates an
  exact bounded `current-feature.json`, matching adjacent packaged build
  metadata, and starts only that frozen E3 EXE through a sanitized PyInstaller/
  Win32 process boundary. Explicit environment activation gives feature builds
  a DEV title, window icon, and `E3.DevTest` AppUserModelID while the production
  E3 title, icon, implicit taskbar identity, settings, project data, and machine
  behavior remain unchanged. The matching Desktop shortcut points permanently
  to the launcher; selecting a later feature requires only an atomic pointer
  update and never automatic taskbar pinning.
- Replaced unbounded desktop Close with a single four-second monotonic shutdown
  deadline. Remote camera shutdown now makes blocked address resolution
  cancellable and tracks and closes active sockets so blocked fresh-frame,
  precision-burst, and control/snapshot work wakes without shortening ordinary
  operation timeouts. Desktop tasks remain strongly owned
  and labeled, drain for at most one second, suppress all late UI publication,
  and cooperatively cancel Trace/native fitting, raster conversion, and toolpath
  planning. Freshly observed idle Pi shutdown gets one best-effort
  `machine.disconnect` attempt within a shared 0.75-second end-to-end shutdown
  allowance, while stale/empty state detaches without an RPC; accepted or
  ownership-uncertain Pi jobs detach immediately without STOP, `M5`, reset,
  hold, Air Assist OFF, or controller Disconnect. A process watchdog guarantees
  termination before the hard deadline if a non-cooperative Qt-pool worker
  survives normal teardown. Ordinary machine/camera timeouts and the corrected
  interactive Disconnect generation behavior are unchanged.

- Added Pi-owned secondary-controller Air Assist execution using the existing
  persisted and undoable `OperationLayer.air_assist` field. The typed machine
  mapping now includes `secondary_marlin_fan` alongside disabled and the existing
  same-primary GRBL coolant and indexed Marlin modes. Its configuration carries
  `mode`, fixed `fan_index = 0`, Pi-local `port`, and `baudrate`; the identified
  Creality/Marlin endpoint is
  `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0` at 115200 baud. Its only
  commands are `M106 S255` and `M106 S0`, never a `P` parameter or `M107`.
  Generated jobs preserve strict non-comment
  `E3AIRASSIST <mapping-sha256> ON|OFF` instructions in immutable program bytes,
  so any mapping or schedule change changes the program digest. The Pi validates
  and intercepts those instructions before streaming the separate primary GRBL
  program, owns execution after
  START, checks secondary acknowledgements/timeouts, and fails the job closed on
  secondary failure. Windows detach causes no fan transition; restart marks an
  active job interrupted without resume and recovers the exact typed secondary
  binding accepted with that job before serving new work, retaining and blocking
  START on an unacknowledged cleanup. STOP acts
  on primary GRBL first, then performs bounded independent secondary cleanup.
  One persistent `CrealityControllerOwner` is reserved for later sharing with
  the separate S1 Z-homing/CR Touch work. Built-in mappings remain disabled, and
  LightBurn imports remain output-disabled while retaining their Air Assist bit.
- Redesigned Camera Trace **Straighten** as a post-Create project edit. Temporary
  Trace candidates now serve only outline review; they have no Straighten,
  rotated preview, or Reset state. Successful non-grid native Cut creation
  immediately selects the new combined object or complete separate-object batch
  and opens the normal Shape inspector. A bounded, Qt-free adapter analyzes the
  selected objects' current world-space native geometry, including existing
  scale, mirrors, rotation, and placement. It combines physical lines,
  demonstrably near-linear cubic segments, anisotropic component axes, and
  component alignment modulo 90 degrees. Disconnected subpaths in one compound
  object and members of one separate-vector creation batch are one artwork
  conflict boundary; independently created, reliably disagreeing artworks still
  suppress the offer. Clicking Straighten rotates every selected object around
  one exact combined-native-bounds pivot through `UpdateTransformsCommand`, so
  Create and Straighten are distinct Undo/Redo entries and local lines, cubics,
  subpaths, holes, islands, fill rules, and spacing remain unchanged. Trace
  eligibility and creation-batch identity persist as non-authoritative object
  metadata. Failed native fits, Stock boundaries, unrelated project objects,
  source pixels, thresholding, normalization, topology validation, planning,
  motion, and laser authority are unchanged.

- Replaced Camera Trace's one-shot automatic Otsu choice with a bounded,
  image-derived threshold selector over the corrected normalized raster. Otsu
  remains the baseline alongside Triangle, class-interpolation, and foreground-
  occupancy candidates. Cheap source-resolution scoring favors stable coherent
  and narrow strokes while penalizing speck growth, unreasonable occupancy, and
  border/background dominance before the winning byte enters the unchanged 4×
  reconstruction and native fitter. Dark and light polarity share the same
  evidence contract; no physical threshold byte is embedded. The Trace panel
  now shows the exact production byte for a successful Auto raster result,
  `N/A` for an Auto Color winner, and clears the value on a new request, Clear,
  failure, or staleness. Manual threshold editing and the 30 mm² / 4 mm / 3 mm
  minimum filter defaults are unchanged.

- Fixed two independent Camera Trace defects that produced missing glyph pieces
  and unjustified internal loops. The photographic rank-envelope path now uses
  the full smoothed closing-minus-image response for dark features and the full
  image-minus-opening response for light features, with an exclusive
  larger-distance polarity gate; the opening/closing midpoint remains
  diagnostic only and can no longer halve a glyph's useful contrast or let a
  darker surface mark erase neighboring pixels. The shared 4× pixel pipeline
  also locks homogeneous cleaned-mask 3×3 interiors to their source
  classification, while retaining bicubic localization in the real boundary
  band, so interpolation ringing cannot invent a positive-area hole or island
  where no source boundary exists. Variable-tone glyphs, exact threshold-128
  camera glyphs, imported/camera parity, legitimate holes and gaps, broad
  shadows, and machine-background rejection have focused regressions. This
  changes vision/vector preparation only; guarded output authority, project
  creation, planning, G-code, motion, arming, and laser controls are unchanged.
  The reported physical Coleman-camera scene still requires a fresh run.

- Fixed Pi-owned desktop **Disconnect** self-cancellation. The remote facade
  still advances its STOP generation to revoke queued and in-flight pre-START
  work, but binds only its own idle `machine.disconnect` cleanup RPC to that
  newly created generation instead of reusing the desktop worker's stale one. A
  later STOP still supersedes the cleanup, blocked upload/finalize work remains
  cancelled, and accepted or ownership-uncertain Pi jobs retain non-destructive
  monitoring-client detach. Remote replace, shutdown, and detach paths were
  audited for the same generation inversion.

- Fixed a Pi-owned parked-camera deadlock that delayed Camera Trace and other
  Home-first precision captures until the 120-second remote stepper-hold lease
  expired. Trace, base-bed mapping, coordinate audit, dense calibration,
  accuracy validation, and fine registration now complete Home / park and any
  ordinary position RPC before acquiring the capture-only hold. No-home
  recaptures still hold the machine for their raw burst. The Pi ordinary lock,
  same-channel authenticated release, finite lease, job exclusion, and priority
  STOP remain unchanged. Trace diagnostics now report prepare-photo,
  hold-acquisition, raw-burst, and complete precision-capture timing separately.

- Fixed Camera Trace false empty-bed suppression when dark or differently
  colored material happens to correlate with the locally normalized honeycomb
  texture. The bounded reference model now derives deterministic robust Lab
  exposure, white-balance, and planar-gradient compensation only from strong
  reference-like seeds, then requires both the existing structural match and a
  compensated luminance/chroma appearance match before excluding a pixel. The
  existing 3 mm-radius closing is retained at model resolution but now starts
  only from strong combined seeds and may bridge only appearance-consistent,
  structurally supported pixels, so it cannot cross clearly changed material.
  The Trace selector adds **Exposed bed** between Camera and Eligible and shows
  the exact immutable production suppression mask. Correlated-texture dark/light
  stencil, appearance mismatch, closing amplification/continuity, blank stock,
  photometric drift, exact 4× mask, imported-raster, and grid regressions cover
  the change. This is authority-free vision preprocessing; physical Coleman
  validation is still required.

- Fixed the desktop Camera Trace **Mask** display for corrected camera areas
  with a fractional final pixel strip. The source frame is rounded to integer
  dimensions before the immutable production mask is reconstructed at exactly
  4×, so validating that mask by independently rounding `area × 4 × pixels/mm`
  could reject its correct dimensions after the selector and status had already
  changed, leaving the prior Camera pixmap visible. The workspace now validates
  an explicit 4× display against four times the already-rounded source raster
  and retains the true 4× pixels/mm transform without resizing or mutating the
  mask. A real-workspace regression checks the complete selected pixel arrays
  for Camera, Eligible, Normalized, and Mask, including the fractional-edge
  case. Temporary per-slot dimensions, format, byte count, and pixel SHA-256
  diagnostics are written to the application log for physical verification.
  Segmentation, thresholding, contour extraction, and native fitting are
  unchanged.

- Rebuilt ordinary non-grid Camera Trace around physical material eligibility.
  The full corrected frame keeps its established pixel-to-millimetre transform,
  but a hard Trace ROI is now created from existing guarded-output geometry; a
  honeycomb-local project additionally intersects that geometry with its
  recorded support rectangle. The already validated, rectified empty-honeycomb
  reference is compared with the current frame using bounded locally detrended
  luminance correlation, normalized patch error, and compatible texture. Only
  strong exposed-bed evidence is excluded; changed or uncertain pixels remain
  material-eligible and do not become foreground. Normalization derives its
  model and response scale from eligible material. A new optional immutable
  `PixelVectorizationSource.eligibility_mask` excludes impossible pixels from
  Otsu and gates both source and exact 4× masks, while absent eligibility retains
  imported-raster keys, thresholding, masks, hierarchy, and geometry. All
  non-grid Auto paths and explicit Color obey the same hard ROI. Implicit Color
  is bounded to at most 35% of eligible material and 25% of its boundary and
  must beat a credible raster result by eight points; every Auto strategy now
  has a 70-point absolute quality floor. The Trace display adds the exact
  **Eligible** mask, displays the immutable 4× production **Mask** over the same
  physical area, and distinguishes provisional Auto masks from the selected
  strategy. Synthetic reflective-honeycomb, lighting-drift, empty-bed,
  blank-sheet, stencil, dark/light, warm-false-Color, real-Color, hard-ROI,
  coordinate, import-parity, and display-size regressions cover the new
  boundary. This is vision preprocessing only and adds no laser authority;
  physical Coleman-camera acceptance testing remains required.

- Moved normal `e3bridge://` job execution ownership from Windows raw-serial
  streaming to the Raspberry Pi. The explicit authenticated `E3MACHINE/2`
  protocol separates bounded upload/finalize from START, persists canonical
  G-code and atomic metadata in a Pi-owned store, independently repeats
  `MachineService` preflight and safety-policy binding, and acknowledges START
  only after durable Pi ownership exists. The one Pi-local `MachineService`
  retains command/ACK streaming, priority STOP, failure cleanup, powered-job
  Home/park/release completion, and the sole controller serial session. A
  START-accepted job now continues through Windows, Wi-Fi, TCP, or monitoring-
  client loss; the network is not a run-enable heartbeat. Reconnect discovers
  the same UUID/digest/progress or a completed-offline result. Pi restart marks
  unfinished execution interrupted and never auto-resumes it.

- Added bounded HMAC-authenticated/counted JSON frames, canonical UUID/path and
  SHA protections, 64 KiB upload chunks, a 64 MiB job limit, deterministic
  eight-record/two-terminal-program retention, stale-part cleanup, idempotent
  retry behavior, and explicit incompatibility with legacy `E3BRIDGE/1` rather
  than unsafe fallback. The desktop now shows Pi upload/verification/START and
  stale-monitoring states, preserves direct local serial execution, and records
  upload throughput, finalization time, and START latency. Ordinary desktop
  shutdown always detaches the remote observer—even before its first status
  refresh—and does not substitute for an explicit STOP. Automated protocol,
  atomicity, disconnect, reconnect with persisted in-flight progress, STOP,
  failure, restart, completion, interference, combined camera-node, and real
  socket-stack tests were added; no physical Pi/controller/laser verification is
  claimed.

- Corrected the camera-photo-to-raster boundary for non-grid Camera Trace. The
  earlier shared-raster reuse converged one stage too early and treated a
  rectified photograph as finished artwork, allowing global Otsu to promote
  broad illumination, shadows, vignetting, or a dark machine edge. The new
  Qt-free camera normalization adapter estimates one symmetric low-frequency
  background on a model bounded to about 1 pixel/mm and 512 pixels on its long
  axis. It takes the `float32` midpoint of grayscale opening and closing by a
  35 mm elliptical rank envelope, then applies 4 mm Gaussian smoothing. Those
  rank operations affect only the background estimate, never the normalized
  raster or production mask. A conservative flat-field guard instead uses one
  robust constant border background only when eight four-level histogram bins
  cover 99.5%, a 2 mm border is 99.5% coherent, that border tone covers at least
  half the bounded model, and far tones satisfy an 80% separation test; this
  retains low-noise 40 x 40 mm clean interiors while rejecting realistic and
  quantized shadows plus a machine-colored border. `float32` signed residuals,
  a three-level noise floor, and one nearest-rank 99.5th-percentile response
  scale clamped to 32–64 levels feed the monotonic reciprocal transfer
  `round(255R / (R + X))`, producing symmetric dark/light uint8 rasters without
  clipped-black endpoint loss or geometry repair. Automatic Otsu advances its
  lowest equally optimal plateau member by at most two unused levels only when
  the low class lacks interpolation headroom; normal and inverted polarity use
  the foreground span and background endpoint respectively. Source-mask
  classification and the established bicubic 4× path remain unchanged.
  Manual non-grid Contrast now applies Otsu or its 0–255 threshold to the
  selected normalized polarity; Auto captures, rectifies, estimates the
  background, and normalizes once, then reuses the result for both raster
  attempts. Explicit Color, Auto's conditional Color strategy, and the
  specialized grid detector are unchanged. Deterministic gradient/shadow,
  edge-background, noise, 21.5 mm dense-label, 40 mm clean-solid, two-tone Otsu
  endpoint, gap, hole, clean-raster parity, immutability, and dark/light tests
  cover the adapter, but the physical Coleman scene has not yet been rerun and
  remains unvalidated.

- Added an exact **Camera / Normalized / Mask** diagnostic selector for the
  frozen Trace request. The exact 4× production contour mask is published from
  the worker immediately after mask preparation and before contour extraction
  or native fitting, while queued delivery, request IDs, and review signatures
  prevent stale GUI updates; starting a new Detect also clears prior temporary
  candidates. Capture/rectification, grayscale preparation, background
  estimation, normalization, mask/component/4× preparation, contour extraction,
  root review, native fitting, topology validation, detection, and request-total
  timing remain non-persistent diagnostics. Manual Contrast now uses the same
  root-isolated forest behavior as Auto, and complete roots already outside the
  selected maximum-area or minimum-dimension review limits are rejected before
  expensive fitting without changing masks, splitting hole/island trees, or
  weakening the native fitter and its validators.

- Reworked non-grid Camera Trace **Auto detect** from a separate legacy mask
  detector into a deterministic orchestrator over production tracing paths. One
  immutable corrected frame now feeds shared-raster Otsu dark and light
  strategies plus a Color strategy only when coherent HSV/Lab evidence is not
  background- or border-dominated. Auto reports the selected threshold/polarity
  or hue, scores verified results from valid-root ratio, useful foreground and
  physical area, clean borders, non-microscopic roots, and in-frame geometry,
  and continues after a failed strategy or independent root tree. Compound
  root/hole/island trees remain indivisible and every native/topology validator
  remains authoritative. Grid Auto deliberately retains repeated-object/lattice
  detection, normalization, inference, and damaged/open-cell review. Auto-owned
  hue and threshold controls are disabled; non-grid Auto fixes output to native
  lines/Béziers with zero border offset while manual Color and Contrast remain
  operator overrides. The winning candidates remain temporary review-only scene
  data until the operator explicitly creates project geometry.

- Fixed shared imported-raster and camera pixel vectorization so a microscopic
  non-geometric 4× contour cannot abort valid large artwork. The reproduced root
  cause is bicubic interpolation overshoot inside the one-source-pixel component
  halo, which can create an isolated threshold fragment and a one- or two-point,
  zero-area OpenCV contour after base-resolution cleanup. The shared pipeline now
  prunes only contours with fewer than three distinct points or zero trace-pixel
  area, preserves positive-area features, rebuilds the complete `RETR_TREE`
  sibling/child/parent structure, and rejects a whole tree instead of reparenting
  legitimate descendants across a degenerate boundary.

- Corrected non-grid Camera Trace **By contrast** to use the complete
  imported-raster pixel-vectorization pipeline instead of selecting a mask with
  the specialized object finder and only sharing its final fitter. Corrected
  camera pixels now share grayscale/Otsu/manual/invert semantics, physical
  component and pinhole cleanup, 4× `RETR_TREE` extraction, source-edge
  refinement, native line/cubic fitting, hierarchy, and topology validation with
  imported images. Each root contour tree is one review candidate; grid Contrast
  deliberately retains classification, normalization, and gap inference. Added
  source-neutral pixel inputs/results beneath real imported-asset provenance,
  contrast threshold/polarity controls, and exact imported/camera
  mask-hierarchy-segment-geometry equivalence tests.

- Simplified Camera Trace to one candidate-review workflow. Removed the seeded
  Cutout/silhouette mode and its separate capture, Add-click, quick-outline, and
  verification state. Auto, Color, and Contrast candidates are now directly
  selectable on the frozen camera canvas with click, Ctrl-click, empty click,
  and rubber-band selection; the inspector checkboxes share the same selected-ID
  set. Native line/Bézier output now uses the authoritative physical contour
  fitter for global contrast candidates, including preserved holes. Creation is
  explicit: separate editable vectors or one even-odd compound vector whose
  overlaps are preserved rather than unioned. A legacy `cutout` preference
  migrates to `contrast`, and desktop tests isolate QSettings per xdist worker.

- Fixed Camera Trace **Cutout / silhouette** after the first interactive camera
  use exposed that per-click segmentation-hypothesis ranking could choose a
  merged neighboring-letter region. A corrected frame now produces one bounded,
  click-independent dark/light foreground consensus and a stable forest of
  discrete connected contour trees before selection. Add clicks only choose an
  existing outer/hole/island tree, duplicate clicks coalesce by candidate ID,
  and quick plus exact stages reuse the same immutable preparation. Shared
  source-neutral component cleanup and `RETR_TREE` decomposition now serve both
  camera cutouts and raster vectorization, while their mask construction and UI
  remain separate. The authoritative physical line/cubic fitter and all guarded
  output behavior are unchanged.

- Removed the development updater's release-wide delete/recreate outage. The
  `e3-development` prerelease now retains its live manifest and packages while
  revision-specific Windows and Linux assets upload and are checked against
  GitHub's recorded size and SHA-256. A verified staged manifest is the final
  authority switch through a recoverable name swap; cancellation recovery keeps
  either the old or new complete revision usable, and prior packages remain for
  clients that already fetched an older manifest. The stable manifest URL and
  development channel are unchanged. Desktop manifest retrieval now retries
  transient HTTP 404, 408, 429, and 5xx responses with bounded 0.5, 1, and 2
  second backoff, without weakening manifest parsing, channel/revision checks,
  package size/SHA-256 verification, or installer handoff.

- Added a seeded **Cutout / silhouette** Camera Trace mode alongside unchanged
  Auto, Color, Contrast, and repeated-grid detection. A frozen corrected frame
  accepts multiple inside-object clicks and retains only each clicked connected
  contour tree, so disconnected text and artwork are not globally promoted.
  Outer boundaries, holes, and stencil islands survive into one even-odd path.
  Blue quick segmentation is replaced asynchronously by verified native
  line/cubic geometry before Create is enabled. Camera-specific segmentation
  remains separate while physical contours share the raster vectorizer's one
  hard-corner/straight-run/cubic fitter, continuous error proof, frame and
  topology validation. Analytic rectangles, circles, ellipses, and washers
  retain their fast paths; washer rings now persist as native cubics. The
  corrected camera pixel pitch supplies a conservative physical resolution
  floor and bounded intensity-edge centering. Existing grid normalization,
  missing-cell inference, template consumers, and output-boundary review are
  unchanged.

- Reverted the adaptive per-span fitting-tolerance experiment from `4039047`.
  The displayed 0.10 mm tolerance again supplies the existing fixed 0.08 mm
  internal budget for every span. Straight-run recovery, bounded Newton curve
  centering, responsive Quick Preview, native line/cubic persistence, and all
  continuous-error, frame, topology, clearance, and hierarchy validation remain
  in place. The exact stage now instead localizes eligible independent curve
  samples against the original grayscale/alpha threshold transition. Updates
  are normal-only, capped at 0.6 source pixel, and rejected for flat, noisy,
  multiple-crossing, or out-of-frame profiles. Existing hard-corner support,
  classified straight runs, and all nested contours remain on the extracted
  threshold contour. The real Coleman P bowl's source-relative inward bias fell
  from 0.0116 to 0.0049 mm without an added segment; A and S improved against
  source evidence and E's protected straight geometry was unchanged.

- Improved curved raster-vector fidelity without changing the 0.10 mm user
  default or the conservative maximum-error proof. Material cubic candidates
  now inspect arc-length-weighted RMS error, signed normal bias, and one-sided
  error distribution before accepting the initial chord-length correspondence.
  A visibly biased candidate receives up to three bounded Newton
  reparameterizations before ordinary acceptance continues. This removes the
  inward bow on the outer Coleman stencil `P` curve while preserving its native
  segment sequence, hard corners, recursive splits, straight-run recovery,
  frame/topology/hierarchy validation, verified-only Create gate, and quick
  preview path.

- Fixed the exact raster fitter so material straight source edges remain native
  lines even on contours that also contain hard corners. The previous anchor
  path returned as soon as it had corner support, and its separate
  10%-of-perimeter straight-run cutoff excluded the Coleman stencil `E` top and
  split left edge. The replacement uses rotation-independent, scale-aware
  evidence from physical tolerance, source and oversampled pixel pitch,
  full-run chord residual, and directional change; nearby raster-step fragments
  merge only after combined revalidation. Classified spans still pass the
  authoritative continuous fit proof. Shallow sub-tolerance arcs, rounded
  corners, adjoining transitions, and rounded `C`/`O` glyph regions remain cubic
  without positive line evidence. The default 0.10 mm control, responsive
  quick/exact workers, verified-only Create gate, Newton fitting,
  topology/clearance, hierarchy,
  native persistence, project/history, planning/cache, and output-safety
  contracts are unchanged.

- Restored sub-second perceived responsiveness to **Trace image to vectors…**.
  The dialog now decodes and displays the source, mask, and a bounded
  preview-only contour overlay first, then refines the unchanged authoritative
  native line/cubic result in a separate worker. **Create vectors** remains
  disabled until the exact fit and all existing frame, continuous-error,
  topology, clearance, and hierarchy checks pass. Newer settings invalidate
  both stages, coalesce to the latest request, and cannot be overwritten by
  stale worker results. Immutable prepared grayscale, mask, and raw-contour data
  are reused without giving quick geometry persistence or planning authority.
  Opt-in per-stage timing covers decode, masks, contours, corners, cubic fitting,
  Newton refinement, continuous validation, merging, topology, preview
  flattening, and raster hierarchy checks. That responsiveness-only revision
  reached a useful Coleman core preview in about 0.07 seconds while retaining
  byte-identical native geometry and metadata to its prior authoritative result.

- Grouped the four native desktop import commands under one **File > Import**
  submenu with concise **SVG…**, **G-code…**, **LightBurn project…**, and
  **Raster image…** child labels while preserving their existing actions,
  shortcuts, icons, enablement, callbacks, review flow, and import behavior.
  Temporary bottom-status messages now reserve their own horizontal space,
  retain active job progress first, hide lower-priority readouts responsively,
  and restore them automatically when the message clears, preventing status
  text overlap at compact window widths.

- Restored the clickable operation-color swatch beside every Objects-row layer
  name. It opens the same current-color-initialized chooser as Cuts/Layers and
  uses the existing undoable layer-edit command, so all objects sharing the
  layer, the workspace, Cuts/Layers controls, and the bottom palette refresh
  together. Cancel makes no project or history change, and object layer
  assignments and all non-color operation settings remain unchanged.

- Added the schema-3 native path foundation. PATH and POLYGON objects now use
  one versioned canonical geometry containing line and cubic Bézier segments,
  multiple open or closed subpaths, and an explicit even-odd or nonzero fill
  rule. Schema-1 and schema-2 polyline projects migrate in memory to native
  line-only paths and are written as schema 3 only on an explicit later save;
  their source files are not rewritten by opening. Schema-3 files are
  intentionally forward-incompatible with older E3 builds that understand only
  schema 2, which reject them rather than dropping native data. Raster
  vectorization now retains its fitted straight and cubic segments rather than persisting its
  preview/topology samples, and the Qt workspace renders cubic segments
  directly. The existing Polyline planner remains authoritative after one
  deterministic project-to-planning flattening boundary at 0.025 mm in
  transformed physical coordinates. The normalized-geometry stage is version
  2 and includes the flattening contract in its dependency/cache identity.
  Exact cubic extrema and conservative convex-polygon subdivision protect the
  full curve in local, placed-beam, and spot-corrected controller coordinates;
  the existing flattened-path and final program checks still run. An adversarial
  follow-up rejects fitted raster curves whose exact extrema leave the reviewed
  source frame, derives preview points only from the authoritative native path,
  and proves contour topology with bounded native-arc checks plus adaptive
  flattening clearance. Compound planning paths must clear the sum of their
  per-subpath flattening envelopes, and one aggregate point budget covers fresh
  LINE/FILL/RASTER normalization and normalized-cache hits before downstream
  artifacts are published. Shape-history replacement now enforces the project
  segment cap atomically, and legacy polyline children reject unexpected native
  fields. No controller spline command, node editor, SVG native-curve import,
  machine authority, or physical verification is added.

- Consolidated the useful fitting behavior from historical reference commit
  `4310769` without merging or restoring that branch. Current-main physical
  corner classification, canonical anchors, frame/extrema checks, native arc
  topology, compound clearance, preview/planning boundaries, cache identities,
  and native path persistence remain authoritative. Cubic candidates now use
  constrained tangent handles with bounded Newton reparameterization, a
  conservative continuous control-hull error proof that rejects between-sample
  lobes, and topology-checked adjacent merging. A separate five-million-step
  validation budget and maximum/mean/RMS error, hard-corner, split, merge, and
  smooth-span diagnostics make quality and failure work explicit. The obsolete
  historical corner classifier, frame/topology substitutes, implicit trace
  cleanup target, 0.01 mm default, and historical preview/fit boundary were not
  restored.

- Added a dedicated imported-raster vectorization workflow without replacing
  raster import or engraving. Exactly one selected IMAGE exposes **Trace image
  to vectors…** in Objects; its modal, coalesced worker preview shows the exact
  identity-verified original, foreground mask, and vector overlay with automatic,
  manual, or usable-alpha detection, physical speck/smoothing/fit controls,
  contour/hole policy, high-contrast preview color presets and opacity, and
  Replace or Keep/hide handling. The Qt-free production pipeline uses a capped
  4× mask, hierarchy-aware contours, full-cycle seam canonicalization, physical
  multi-scale corner persistence, generic straight-run anchors, shared tangents
  across non-corner joins, and bounded line/cubic fitting into authoritative
  native PATH geometry. Exact source-frame and native-arc topology checks review
  that geometry before bounded transient flattening is used for overlay,
  diagnostics, and planning estimates. It records raw/fitted/preview counts and
  estimated deviation, preserves the source frame/transform and nested holes,
  and rejects excess connected components, contours, raw points, fitted
  segments, final points, or internal work pixels with actionable cleanup
  guidance. Source handling, vector insertion, selection, and any new visible
  0%-power, output-disabled ordinary Line layer are one undoable operation; a
  retained source stays beneath the vector in its unchanged transform. This first version is
  a single-foreground logo/line-art/silhouette tracer, not full-color or
  multi-layer tracing; it does not provide node-level curve editing. It does not
  generate G-code, authorize output, contact hardware,
  Home, move, arm, or start a job.

- Removed the reduced-capability `--hardware` product launch mode. Normal
  browser and desktop startup now always grants process hardware authority
  without eagerly connecting to a controller; disconnected controllers report
  their real connection failures. Historical hardware-named shell entry points
  are compatibility aliases to the same normal launch behavior, and desktop
  installation exposes one application entry. The internal `MachineService`
  hardware-authority guard, `--laser-lockout`, motion permission, coordinate
  trust, preflight, exact-program authorization, temporary arming, bounds,
  STOP, and `M5` behavior remain intact.

- Completed removal recovery for legacy simulator configuration and saved
  registries. Desktop startup now requires an explicit physical saved-machine
  choice before credentials or runtime construction, performs no writes on
  cancel, preserves raw physical records, and transactionally rolls back the
  replacement configuration, exact simulator backup, registry, credential, and
  completion marker on failure. Browser and desktop startup now share
  `CoreRuntime` saved-machine authority; unconfigured controller placeholders
  fail closed, while an explicitly saved physical `/dev/ttyUSB0` endpoint
  remains valid. Packaged legacy fallback recovery writes to upgrade-preserved
  user state, while explicit configs are repaired in place, and recovery exposes
  no pre-Finish reachability probe. Removed the remaining production camera
  test-frame helper and export. No controller, camera, motion, arming,
  laser-output, or physical verification behavior was added or claimed.

- Hardened the packaged Windows updater's external Inno Setup handoff. The
  launcher now gives the installer standard Windows DLL resolution, removes
  only PyInstaller-bundle entries from a copied child `PATH`, starts a detached
  process with explicit arguments and working directory, and restores E3's DLL
  search state after failed process creation. Once Windows creates the child,
  that installer handoff remains successful even if the exiting parent cannot
  restore its own DLL state. The desktop also performs the normal
  close approval once, stops periodic task producers, and waits/rechecks owned
  background work before the final close-and-launch sequence. A CreateProcess
  failure after terminal shutdown now presents a standalone error with the
  verified installer path for manual launch and then exits; it no longer
  re-shows a stopped E3 window.
  Focused Windows automation covers the real launcher boundary; an installed
  PyInstaller `E3.exe` to visible Inno wizard handoff still requires packaged
  verification.

- Made first-run, Machine Manager, and Machine Setup generic across the existing
  simulator, GRBL, and Marlin profiles without adding controller support or a
  second profile model. Profile-created machines are validated concrete
  snapshots with motion, default/frame power, and low-power framing off and no
  inherited camera/calibration/support binding. Running-now versus next-launch
  identity is explicit; profile/registry edits perform no controller action and
  cannot hot-swap the current runtime. Machine Setup reports binding state and
  can explicitly persist the active optical profile for a future launch without
  changing current calibration or execution authority. New projects now use
  only the highest compatible tier of curated built-in operations from the
  immutable running identity: Ender-3 S1 Pro / generic 10 W retains the exact
  historical 13 layers, while unmatched combinations get one 0%-power,
  output-disabled neutral Line layer capped by the running work-feed ceiling.
  User SQLite recipes are never automatic defaults. Machine, material, and
  project schemas and all MachineService, motion, arming, laser, STOP, G-code,
  and execution behavior remain unchanged.

- Extended the existing `MaterialPreset` / SQLite `MaterialDatabase` into a
  machine-aware material-recipe library without adding recipe identity to
  projects or execution. Recipes now carry the complete operation authoring
  settings, optional exact stable machine/tool-head profile scope, and an
  optional recommended operation color. Exact machine/tool, tool-only, and
  universal recipes are prioritized deterministically; incompatible recipes
  remain visible for CRUD but cannot be applied. Applying a compatible recipe
  is one undoable layer update, performs no scaling, preserves the layer ID,
  name, visibility, priority, and output-enabled state, and never contacts
  hardware. The legacy SQLite table migrates transactionally in place with old
  rows retained as universal recipes, while the existing 13 E3 10 W new-project
  operations and seeded built-ins now derive from one curated value source.
  Structured preflight, exact planning, `MachineService`, arming, motion, and
  project-schema authority remain unchanged.

- Added a Qt-neutral structured job-preflight report before native desktop
  project generation. Stable coded info, warning, and blocker findings summarize
  existing work-area, coordinate/calibration/support binding, output/settings,
  machine feed ceilings, and bounded raster-readiness rules without planning
  geometry or contacting hardware. Stale local calibration/support blocks, and
  only exact unrounded-rectangle or line local bounds become structured bounds
  blockers; rounded rectangles, ellipses, images, paths, and other complex
  geometry remain deferred.
  Blockers stop before exact generation and open a reusable non-modal report;
  ready and warning-only reports continue through the authoritative planner and
  appear in exact Preview. Existing async cancellation, exact planner rejection,
  guarded `MachineService` preflight, motion, arming, laser, execution, and
  project-schema behavior remain unchanged.

- Extended the reusable desktop pre-import review flow to SVG and raster images
  as well as LightBurn and foreign G-code. All four paths now run a bounded scan
  before strict import and show source, layer/operation, coordinate, warning,
  approximation, unsupported, and error facts. Blocked manifests disable
  Import; all other manifests require explicit approval. Cancel returns before
  project/history/selection/authoring mutation. The dialog renders no more than
  200 discovered rows or 200 entries per repeated text section and reports exact
  omitted counts without truncating the manifest. File scans now record the
  exact source-byte SHA-256, and the authoritative strict path rejects a changed
  source before native object or project mutation. SVG remains fail-closed, and
  raster scanning reuses the bounded encoded-payload metadata path without
  decoding pixels. Approved unchanged sources retain their existing undo/redo
  behavior. Newly created
  raster layers are explicitly output-disabled. This changes no project schema,
  controller, motion, arming, execution, or laser behavior.

- Restored the machine-aware read-only Coordinate Audit as the sixth Machine
  Setup tab. It reports running-machine/calibration identity, configured work
  and output authorities, machine-specific honeycomb span/support state, and
  clicked-point coordinate frames. Its explicit capture action reuses the
  existing laser-off Home / park capture path and retains immutable MPos/WPos/
  WCO, workspace/G92, stability, timing, and bed-map evidence after normal
  motor-release cleanup clears current coordinate trust. Diagnostic GRBL
  sampling sends only `?` through the existing local or `e3bridge://` transport.
  Sampling now refuses to compete with a running streamed job before any byte
  is transmitted. Bed Mapping displays the same saved-machine honeycomb span
  read-only, supplies it unchanged to automatic and three-hint detection, and
  blocks both when it is unconfigured. Clicked-point evidence is cleared when a
  replacement audit capture begins or its image, bed map, or support changes,
  so copied reports cannot combine a current audit with an older image point.
  Refresh, report copy, and point inspection command no hardware. Permanent
  fixture reach editing and bounds proposals remain deferred.

- Added the machine-aware physical-setup foundation for a future Coordinate
  Audit: saved machines now retain an optional explicitly configured physical
  honeycomb ruler span, `AppContext` receives detached running-machine identity,
  and diagnostic fixture-reach evidence is isolated by stable machine ID with a
  no-clobber migration reserved for the original `legacy-config` machine.
  Retained evidence scopes permanently reserve their IDs, preventing deleted
  machines' evidence from attaching to replacements. This does not change
  motion, bounds, G-code, arming, laser power, or controller behavior.

- Added the desktop Machine Manager with an always-visible machine selector,
  automatic preservation of the current configured machine, editable and
  duplicable saved machine instances, next-launch activation, camera/calibration
  binding visibility, and a private household installer path that seeds the exact
  config, registry, calibration profile, and bridge credential.

- Added the Phase 1 multi-machine foundation: a versioned saved-machine
  registry, reusable motion-platform and tool-head profiles, conservative
  one-time migration of the active machine/laser configuration, and safe-off
  defaults for newly profile-derived machines. The active runtime still uses
  the existing validated `Settings`; this increment does not switch,
  reconnect, home, move, arm, or run another controller.

- Made the desktop exact Preview the mandatory final execution gate. It is
  window-modal, owns the sole visible **START JOB** control, dismisses before
  delegating to the unchanged guarded run path, and leaves software STOP
  accessible. Main-window job controls now open Preview instead of starting a
  prepared program directly; Start Here remains a non-executing replacement
  workflow that produces another exact Preview.

- Corrected native-V4L2 camera FPS accounting to include source-JPEG validation
  and decode, and split Raw Live Monitor diagnostics into Pi Capture FPS,
  desktop Network receive FPS, Qt Display FPS, and source-frame Age.

- Replaced the physically unsuccessful OpenCV raw-MJPEG probe with a narrow
  single-owner Linux V4L2 MMAP backend. Validated source JPEG packets are
  forwarded unchanged at native resolution and decoded once for ordinary
  camera consumers; initialization failures use the decoded OpenCV camera and
  the 1280×720/10 fps/quality-78 transcoded monitor fallback.

- Fixed explicit controller replacement after Software STOP so disconnect
  cleanup is followed by a freshly scoped connection attempt. Concurrent STOP
  requests still cancel replacement, and successful replacement remains HOME
  REQUIRED without restoring coordinate, motion, or laser authority.

- Added an authenticated persistent raw Live Monitor for Raspberry Pi camera
  sources with bounded latest-frame delivery, 5/10/15 fps selection, and no
  calibration or machine-control authority.

- Added an explicit Reconnect action for untrusted controller sessions; the
  replacement connection remains HOME REQUIRED and never recovers motion or
  laser authority automatically.
- Added one centralized, queued first-show repaint for modal message boxes to
  avoid black client areas on affected Linux Qt compositor/backing-store paths.

- GitHub Actions now separates fast development feedback from final compatibility
  validation. `fix/**`, `feature/**`, `agent/**`, `cleanup/**`, and
  `architecture/**` pushes run the complete Windows Python 3.12 desktop suite
  with four bounded workers alongside parallel Ruff and dependency/bytecode
  jobs. Pushes and pull requests for `main`, plus manual dispatch, run serial
  Windows Python 3.10 core/non-desktop and Windows Python 3.12 desktop jobs with
  separate repository Ruff. Major phases publish elapsed timing summaries.

- GRBL connection normalization can now recover the exact post-reset alarm-lock
  rejection `error:9` with `$X` followed by an acknowledged `M5`, but only when
  mandatory Home / park is configured. Connect performs no motion and leaves
  coordinates untrusted until Home / park succeeds; every other rejection or
  ambiguous exchange still fails the connection.

- Desktop jog moves now use feed-controlled, laser-off `G1` motion so the Jog
  panel speed governs physical GRBL movement; absolute targeting, feed ceilings,
  Home / park trust, STOP handling, and M5-before-motion remain unchanged.

- Controller bring-up now retries one transient initial transport-open failure,
  while deterministic authentication/configuration failures and all uncertain
  established sessions remain manual-reconnect-only. GRBL Home / park can
  safely complete when `$H` omits `ok` only after controller-reported active
  homing followed by `Idle`; ambiguous, alarmed, stopped, or disconnected
  sequences never proceed to the park move.

- Nested closed vector contours now complete all configured passes deepest-first
  before a containing contour begins. The geometric rule is winding-independent
  and applies to washers, traced paths, and imported compound paths while
  retaining pass-major behavior for unrelated contours.

- Added an experimental authenticated Raspberry Pi hardware-node path for a
  Windows/Linux E3 desktop: `e3bridge://` carries the guarded controller
  transport and `e3camera://` preserves Pi-side V4L2/precision capture. Client
  loss triggers best-effort controller stop/reset plus laser-off cleanup and
  never auto-resumes. The network path remains physically unverified.

- Trace can now compare repeated-grid interiors with the integrity-bound empty
  honeycomb teaching photograph. Already-open cells are labeled and left
  unchecked even when exposed honeycomb is darker or less textured than intact
  labels; stale image, bed-map, or support evidence is ignored.
- Documented that a negative Trace border offset deliberately trims every edge.
  The latest saved recovery frame aligns at a zero offset, while its prior
  `-0.20 mm` setting necessarily produced an inset cleanup path.
- Fixed Fine registration reset leaving the reviewed full-bed map disabled:
  retained marks are re-reviewed after reset, and the broad-coverage gate now
  accommodates the support-contained eight-mark layout.
- Fixed automatic honeycomb re-teaching after a bed-map refinement: the prior
  integrity-checked image may seed fresh edge fitting without being reused as
  execution evidence, preventing blind selection of an outer ruler edge.
- Expanded the support-bound dense 5×5 correction grid from the legacy 70%
  interior span to an exact 180 × 180 mm center span, with complete 5 mm crosses
  checked against both the taught support and configured output polygon.

- New projects now populate Cuts / Layers slots 00–12 from the operator's E3
  10 W cut/raster profile chart, including paper cut and raster starting points;
  slot 00 uses the corrected 1500 mm/min and 100% paper-cut values, and existing
  saved projects remain unchanged.
- Powered Machine Setup jobs now transform their machine-coordinate paths into
  the active honeycomb-local canvas for both workspace and popup previews.

- Reworked the native desktop shell as layout `v7`. Cuts, Camera, Objects,
  Shape, Templates, Trace, Machine, and Material Recipes now share one
  full-height right sidebar; the dedicated lower raw-G-code and Laser/job docks
  are removed, so the bed/camera workspace extends to the bottom status area.
  Preparation and controller execution use one global bottom progress widget,
  while Templates and Trace expose the same existing Generate action directly
  below their Create controls. Connect/Reconnect and Disconnect now sit beside
  the deliberately disabled Pause and always-available software STOP in the
  persistent primary runtime strip. Compact windows give that strip its own
  toolbar row and a two-row status/control fallback. The optional Console stays
  hidden by default, and opaque `v6` dock state is not restored into the new
  topology. Exact Preview remains the only visible **START JOB** gate and still
  submits through the existing guarded main-window path; `MachineService`,
  arming, motion, authorization, STOP/`M5`, and all other safety semantics are
  unchanged.

- Expanded the corrected workspace Live Overlay selector to 0.5, 1, 2, 4, 5,
  10, and 15 fps, with 2 fps as the default. The desktop controller now permits
  the approximately 67 ms interval required for 15 fps while retaining one
  corrected-frame job in flight and at most one coalesced explicit pending
  refresh; periodic ticks are dropped when correction or network throughput is
  slower than the selected cadence, so no work backlog accumulates. The separate
  raw Live Monitor and camera-capture rates are unchanged.

- Restored wire compatibility with legacy physical Pi camera nodes whose status
  payload contains the retired exact boolean `synthetic: false` field. The
  desktop copies the received status mapping before removing only that value;
  `synthetic: true`, integer `0`, and every other value remain invalid, the
  caller's mapping is not mutated, and no simulation capability is restored.

## Historical `desktop-v1` development — `0.2.0.dev0`

- Trace can now create a locked **Stock boundary** instead of laser-output
  geometry. Stock boundaries remain visible in the camera-aligned project, are
  persisted through `.e3laser` save/load, and are explicitly excluded from
  vector, fill, raster, preview-frame, and generated-job output.
- Added a contextual **Stock layout** toolbar with one-click horizontal and
  vertical centering, meaningful-edge rotation snapping, and conservative
  fit-to-stock scaling with preset or custom uncut margins. The edge selector
  includes nearest, top, bottom, left, and right stock edges.
- Replaced the one-line display-only text prompt with vector text creation.
  **Outline cut** produces ordinary font contours; **Stencil-safe cut** detects
  enclosed counters and removes scaled material bridges so centers such as O,
  A, R, B, and 8 remain connected to the parent sheet.

- Projects now explicitly distinguish legacy machine coordinates from a
  movable honeycomb-local coordinate system. New projects use the current
  detected cutting surface from X0/Y0 to the saved machine's configured
  physical support span; schema-1 projects migrate as machine-coordinate
  projects and are never silently reinterpreted.
- Corrected camera images can now be rectified directly into the rigid
  honeycomb-local frame. This aligns camera pixels, rulers, the authoring grid,
  Trace results, and object coordinates while keeping the configured machine
  envelope independent. The rigid fit removes small ruler-detection shear.
- Honeycomb-local vector, fill, image-raster, and frame jobs are transformed to
  machine coordinates only at generation. Local geometry, transformed beam
  geometry, laser-spot-corrected controller motion, and the independent machine
  envelope are each validated. Jobs bind the exact support and complete bed-map
  digest and are rejected after either changes. **START JOB** rechecks that immutable
  binding without another camera capture, then performs one laser-off Home and
  begins the validated program without parking at the photography pose. The
  active hardware profile can bind these jobs to an explicit
  fixed convex output polygon independent of the camera-calibration rectangle.
  Its honeycomb-local coordinates depend on the accepted support pose and
  configured physical span; it is immutable across live detections and is
  rechecked by low-level preflight.
- Execution no longer repeats camera pose verification after the operator has
  traced, generated, reviewed, and selected Start. This removes the previous
  Home/park/capture/release/Home sequence. The prepared support, bed-map, and
  output-polygon signatures remain mandatory and stale software bindings are
  still rejected before the single job-start Home.
- Object-list Visible and Locked edits are now applied after Qt finishes the
  native tree-item signal. This prevents a synchronous history refresh from
  deleting the emitting row and terminating the desktop process.

- The desktop live camera overlay and Trace now display and analyze a direct
  honeycomb-local rectification containing the complete current support instead
  of cropping it to the configured machine rectangle. The canvas separately
  maps the configured guarded laser-output polygon into that local frame, and
  observations outside it remain red, unchecked, and blocked from output.
- Replacing a detected support now updates any clean, empty, unsaved
  honeycomb-local project to the replacement's exact dimensions. Trace and
  color sampling repeat that reconciliation before their strict frame check,
  fixing a stale 192 mm project left behind after accepting a 190 mm support.
  Saved, edited, or nonempty projects are still never reinterpreted.
- Fine-registration targets now follow the current detected honeycomb cutting
  surface instead of fixed machine-work-area fractions. Complete cross extents
  must fit both the honeycomb polygon and guarded machine rectangle, and Start
  rechecks the exact powered segments against the unchanged support reference.
- Added validated quarter-turn rotation for native Machine Setup camera views.
  All overlays rotate with the image, while picker clicks are mapped back into
  unchanged sensor coordinates so existing lens and bed calibration data stay
  valid. The active hardware profile uses a 90-degree clockwise view, making
  machine X screen-right and machine Y screen-up for its sideways camera mount.
- Base-grid detection now skips duplicate, irregular, and edge-contaminated
  OpenCV lattices instead of accepting the first nominal 25-point result.
  Honeycomb-ruler pitch measurement also refines its autocorrelation peak below
  one pixel, avoiding whole-pixel scale errors at the current camera resolution.
  Its three clicks now select the X ruler, Y ruler, and their approximate shared
  zero rather than acting as measured endpoints; detected baseline intersection
  and 1 mm pitch project the configured physical span for independent bed-map
  comparison.
  Automatic honeycomb detection is now the primary workflow: it segments the
  dominant dense rectangle, uses its outer frame only as a search envelope,
  refines inward to four continuous ruler-square borders, and orders those corners with
  the active bed map, and uses the configured physical span to construct the ideal
  square. The outer visual frame is not treated as a ruler measurement and printed
  tick recognition is not required.
  Three hints remain only as an
  explicitly labeled fallback for failed or ambiguous automatic detection.
  The active hardware calibration profile can also use its supplied annotated
  homed-bed photograph as a visual template: feature registration projects the
  explicitly taught green cutting-surface corners into a fresh capture, avoiding
  confusion with the red outer physical frame.
- Trace review now draws each detection number as a fixed-size, high-contrast
  badge over the camera image and provides a tri-state **Select / deselect all**
  checkbox above the detected-outline list.
- Identical-cell Trace normalization now repairs the center only along an axis
  whose observed size is materially malformed. This prevents a missed edge
  from shifting the shared-size cut while preserving genuine per-label
  placement on the unaffected axis.
- Added persistent optical calibration profiles keyed by configured camera
  resolution and locked focus. Lens captures/models, bed maps, fine and dense
  corrections, validation sessions, and honeycomb references no longer
  overwrite another focus value's stack. Existing unprofiled calibration is
  copied into the focus recorded in its bed-map provenance without deleting the
  original files, and **Save focus** names the profile activated after restart.
- Added a non-destructive **Test focus range…** workflow. It takes three fresh
  sharpness measurements at each requested manual-focus value, ranks median
  scores, restores the original autofocus/manual focus in all completion paths,
  and does not save settings or invalidate calibration merely for comparison.
- GRBL connection initialization no longer enters sleep and soft-resets the
  controller merely to release already-idle motors. It still sends `M5` and
  repairs a stale `$1=255` camera hold, avoiding the controller's audible reset
  announcement while preserving explicit release after jobs and held captures.
- Controller-required actions now connect automatically before continuing when
  the controller is offline. This shared behavior covers desktop jobs,
  Home / park, jogging, diagnostics, Trace detection, every parked Machine Setup capture, and
  the equivalent HTTP command, positioning, arming, and run routes. Connection
  failure prevents the requested action, while STOP generation invalidation
  prevents queued work from reconnecting and continuing afterward.
- Added optional per-edge Trace offsets for fitted rounded rectangles. The
  existing uniform border offset remains the default; Top, Right, Bottom, and
  Left can now independently expand or trim the rotated object and its
  adjoining corners. Uniform and per-edge arrow controls step by `0.1 mm`.
- Trace contrast detection now includes strong closed-outline evidence so pale
  labels with thin black borders and dense interior text are recognized as
  whole labels instead of reducing the result to unrelated solid bed objects.
- New Trace captures now replace earlier Trace-created project objects by
  default, preventing a completed workpiece from reappearing in the next
  generated job. Replacement preserves all non-Trace content, is undoable, and
  can be disabled when multiple captured batches should accumulate. The
  temporary-clear action is now explicitly labeled **Clear detection preview**.
- Removed the desktop powered-job warning and typed arming-phrase dialogs.
  Preview's **START JOB** submits the already reviewed prepared job immediately
  through the guarded path while retaining an internal one-use, time-limited
  authorization bound to that exact program.
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
  visual comparison and, in the new desktop workflow, the rigid local job
  frame. Three rough clicks identify both ruler corridors and their
  approximate shared zero; verified baselines, intersection, and repeated tick
  pitch define the saved reference. The hints and
  result cannot change the bed map or any machine/output safety boundary;
  honeycomb-local camera, Trace, project, and generated-path placement now bind
  to its exact digest.
  Template alignment now excludes cropped/out-of-output evidence instead of
  allowing it to support a seemingly viable match, and held-capture errors show
  any secondary motor-release cleanup failure in the same operator message.
- Added one packaged, versioned **Permanent Camera Setup Guide** as the
  canonical five-tab operator sequence. It is available modelessly from the
  Machine Setup footer and the main Help menu, follows the current tab when
  opened, and spells out the calibration-job handoff: timeline Preview controls
  are animation only; use the exact Preview's **START JOB** without clicking
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
- Add a **Detected top edge** identical-cell anchor for loose printed grids, so
  damaged or overprinted bottom contours cannot shift a clean observed top
  border when canonical cell heights are applied.
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
