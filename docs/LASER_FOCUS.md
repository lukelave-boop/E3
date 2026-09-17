# Surface height and gauge-taught laser focus

## Recognizing existing gauge calibration

Use the setup wizard's **Check progress** to refresh current controller evidence.
A compatible saved gauge calibration remains complete when the bed survey changes.
If current status is unavailable, the wizard asks for a status refresh rather
than gauge teaching. An explicit compatibility problem is shown separately for
inspection. Neither message erases or rewrites the saved gauge calibration.

## Raster surface focus (source update)

Raster/image layers now use a fixed 7 mm gap above the measured material surface.
Cut/vector and Fill retain the existing thickness-derived gap and calibration.
The gap is relative to the measured surface, not absolute Z7: raising the material
or its supports raises the raster target by the same amount. Measure again after
changing the material or supports.

Raster target Z is raw probe contact Z plus the saved 7 mm taught focus offset.
A mixed job turns output off, drains XY, lifts to verified clearance, approaches
the next operation and focuses before its output. Start Here preserves the mode.
A target at or above clearance, above the configured maximum, or lacking current
measurement authority is rejected. Limits are not raised automatically.

The matching `pi-operation-focus-v1` companion is installed and verified on the
Pi, paired with E3 DEV TEST 0.7.181. All 322 isolated Linux/Pi fake-controller
tests passed, and all 20 installed source files match their tested bytes. Saved
settings and calibration were preserved. Reconnect and measure again after the
service restart. Windows offscreen and frozen-startup checks passed; interactive
operation and physical focus verification remain pending. See CURRENT_STATE.md
for the exact deployment record.

## Precision setup additions

The guided setup checklist now links to a four-corner-plus-center survey. Measure
a known-thickness rigid target at each position, review the inferred honeycomb
heights, then explicitly save the chosen datum. Survey evidence records distinct
contact/reference identities and actual XY; it cannot silently authorize a new
reference. Measure surface and Return laser to measured spot are grouped directly
before gauge teaching. Existing guarded actions and clearance checks remain.

The camera's height correction uses top elevation including supports, whereas
automatic focus uses the separately derived material thickness. The precision
workflow requires the matching `pi-material-surface-v1` companion. At tall surfaces
the existing contact/clearance bounds still apply; select physically adequate
clearance using the current setup controls. No higher Z limit is installed by
this feature. See [precision placement](PRECISION_PLACEMENT.md) and CURRENT_STATE.md
for the current build and pending physical qualification. Earlier handoffs below
remain the historical focus verification record.

## Home / park timeout correction

Pi-only companion **e3-pi-thickness-focus-1ccd58e0** (`de6cdc1`) is installed and
hash-verified. The restarted service responds, and all four fake-controller
concurrency regressions pass against its installed code. Existing **E3 DEV TEST
0.7.150** and saved calibration remain unchanged; physical retesting is pending.

A directly captured Pi deadlock after Home blocked status and software STOP:
the Home focus-preservation guard and CPU cooling acquired shared locks in
opposite orders. The correction covers Home preservation and completed-job
focus retention and remains compatible with E3 DEV TEST 0.7.150. It changes no
saved height, gauge teaching, movement sequence or firmware. Physical retesting
is required after installation; automated concurrency tests do not qualify
hardware stopping. See CURRENT_STATE.md for deployment status.


## Saved honeycomb Z height

Current handoff: **E3 DEV TEST 0.7.150**, paired with installed Pi companion
**e3-pi-thickness-focus-1ccd58e0**, revision `de6cdc1`, superseding the earlier
saved-height companion `c8f0fe1d`. Automated Windows/Pi
checks pass; dimensional and physical operator validation is pending.

Open **Machine Setup → 7 · Z / laser focus → Honeycomb height**. Enter
**Honeycomb Z relative to border**, then choose **Save honeycomb height**.
Negative values mean below the border; the accepted setting range is -10 to
+10 mm. Use the measured bed height. This changes a datum, not bed flatness
compensation or a tolerance. The default remains -1.500 mm until explicitly
saved. The controller stores it across app/Pi restarts, separately from gauge
teaching, and the Machine panel displays its current value.

Saving is an idle, disarmed, confirmed no-motion action. It clears the previous
surface, preview and job focus. Measure the workpiece again before running a
job. Gauge teaching and the valid border reference are retained. Both the new
desktop and matching Pi companion (pi-thickness-focus-v2) are required.
Material thickness = measured elevation - saved honeycomb height - spacers.
For example, elevation -1.532 mm with datum -1.600 mm and no spacers gives
0.068 mm material thickness. Negative thickness still blocks; nothing is clamped.


## Invalid thickness after measurement (2026-09-13)

After the probe returns to verified clearance, an invalid calculated thickness
blocks job focus and discards the surface. The connection and border reference
remain available. The panel and Pi journal report the measured elevation,
honeycomb datum, spacers and rejected thickness; correct the setup and measure
again. No negative thickness is clamped into a valid sheet. Probe, clearance or
communication failures still stop the machine. Pi companion
`e3-pi-thickness-focus-c9917851` is installed and hash-verified with existing
E3 DEV TEST 0.7.133. Physical validation is pending.

## Teaching hang correction (2026-09-13)

A Pi-side correction gives focus and CPU cooling a consistent lock order. Their
previous lock inversion could block a teaching request before any Z movement.
It changes no firmware, gauge offset or thickness policy and remains compatible
with E3 DEV TEST 0.7.133. A guarded companion accepts the original thickness-focus
source set as a predecessor. Operator verification is pending; a reported loss
of SSH during a freeze is not explained by the simulated application deadlock.

## Automatic thickness rule (2026-09-13)

Earlier handoff: **E3 DEV TEST 0.7.133** with Pi companion
**e3-pi-thickness-focus-2528c9d0**. Follow its INSTALL.md before testing.
The earlier 0.7.132 combined handoff below is retained as historical context.

The daily Workpiece focus selector is replaced by a calculated gap:

```text
material thickness = measured elevation above border - saved honeycomb height - spacers
laser gap = max(3 mm, 7 mm - material thickness * 2/3)
target Z = raw probe contact Z + taught 7 mm offset + laser gap - 7 mm
```

| Material thickness | Calculated gap |
| --- | --- |
| 0 mm | 7 mm |
| 0.2 mm | 6.867 mm |
| 1.5 mm | 6 mm |
| 3 mm | 5 mm |
| 4 mm | 4.333 mm |
| 6 mm and above | 3 mm |

This is the operator-selected straight line, superseding discrete thickness bands.
It uses the saved honeycomb height relative to the black border (initially −1.5 mm).
Set **Spacers** to zero for material resting directly on the honeycomb, or enter
the total thickness of all supports beneath the sheet before **Measure surface**.
The material must present one flat top across the job. Unknown supports must not
be counted as sheet thickness. A negative derived thickness rejects and blocks
powered jobs; it is never silently clamped to paper thickness.

The panel shows material thickness, calculated gap and target Z. Clear measurement
before changing spacer thickness, then measure again. Multiple jobs reuse the
accepted datum and calculated gap. Setup's manual preview and gauge controls are
for calibration; they cannot override an automatic workpiece's selected gap.

Use a matching desktop and Pi companion advertising `pi-thickness-focus-v1`.
The new action has no additional firmware requirement beyond the selected setup
speed firmware. Existing gauge compatibility and reference checks still apply.
The reported support datum and this focus rule have not been physically validated
for focus accuracy or cutting performance. Confirm the actual setup before use.

## Keep known Z across normal shutdowns

With the matching retained-Z desktop, Pi and firmware, a clean idle Pi
controller disconnect/shutdown can save the border datum and verified Z at or
above the selected clearance. On the next connection E3 restores that knowledge
without travel or probing, then requires fresh XY Home / park. The restored
border datum survives that Home; choose a new point and measure the workpiece
again to establish the focus used by subsequent jobs. Closing Windows alone is
not evidence that the Pi completed a clean shutdown.

**Machine → Z axis · Ender** shows whether Z was restored or why a new
reference is needed. If the Z axis drifted or was moved by hand, or the probe
mount or border/support changed while off, choose **Forget saved Z**, then **Reference
border** again. The taught gauge offset is kept. Unsupported firmware, a dirty
previous exit, changed configuration or failed readback cannot restore Z.

This retained-Z candidate needs a firmware update with the new restore
capability; earlier focus-positioning features below retain their stated
compatibility. Saved gauge calibration remains bound to its exact firmware and
may require explicit re-teaching once after that update. No retained-Z hardware
installation or physical power-cycle test has been performed yet. See the
[saved-Z workflow and qualification limits](Z_RETENTION.md).

Historical operator verification on 2026-09-12: Windows 0.7.93 plus Pi correction 59c31d7
completed the requested measured-job focus, clearance approach, automatic
clearance before Home/park and selection retention sequence. See CURRENT_STATE.md
for controller, firmware and calibration context. This observation does not
establish dimensional accuracy, qualify fault handling on physical hardware,
or verify the automatic reusable workpiece-focus change below.

## Daily focus and machine calibration

Use **Machine → Z axis · Ender → Reference and measure** for routine
referencing and surface measurement. Ender status/recovery, saved-Z controls
and related actions are embedded beside the manual Z controls. Position probe
uses the main calibrated view on the left; the duplicate live bed view in the
right-hand controls is removed.
Set the clearance and check the physical headroom and full XY/Z path before
choosing **Home / park + reference**. This single action homes and parks XY,
verifies completion and unchanged controller sessions, then references the
border and returns to clearance. A failed Home, STOP or changed session prevents
the reference stage. After an accepted saved-Z restore the button becomes
**Home / park XY** and parks without re-probing. Recovery keeps its separate
reference-only action. No failed action retries automatically.

After the gauge has been taught, **Measure surface** automatically calculates
and selects focus for the workpiece. The daily **Workpiece focus** group shows
the gap, calculated focus Z and whether that focus is ready for jobs. Start
jobs from this Machine-tab workflow; opening Machine Setup, previewing a target
or pressing a separate selection button is unnecessary. Later jobs reuse the
measured focus while the workpiece and verified reference remain valid.
The daily gap is calculated from thickness by the rule above. Clear the
measurement before editing spacers, then measure again. Status polling never
changes the selected gap or spacer thickness.

Open **Tools → Machine Setup… → 7 · Z / laser focus** for manual
**Preview and position**, **Probe / laser XY offset** and **Teach once with
the 7 mm gauge**, including teaching jogs, gauge-fit confirmation, Save and
Forget. Its **Use measured focus for next job** button remains for compatibility
with the manual preview workflow; it also selects reusable workpiece focus.
The page retains reference, measurement, recovery, saved-Z and raw live camera
helpers so calibration can be completed there. Both pages use the same machine
state and saved offsets.

The separate **Surface / laser focus…** button/dialog and focus workspace's
**Position XY** jog row are removed. Use the camera-positioning and probe/laser
transfer actions here; ordinary XY jogging remains in Machine under its existing
guards. Check the clear path, remove the gauge before XY travel and inspect the
solid target before probing.

The measured surface must be flat across the whole job, the gauge removed, and
the complete Z/XY travel paths clear at the selected clearance. Read and check
the physical conditions beside the controls. Headroom/Z-path, manual-Z
probe-stowed/path, recovery and gauge-fit confirmations remain. Manual
**Move to focus** in Setup confirms the adjacent gauge-removal and target-path
conditions. Bounds, clearance, session and STOP checks remain in force.
Automatic reusable focus alone added no new firmware requirement. The combined
faster-Z companion below requires speed firmware and fresh gauge teaching.
The revised workflow has no new physical verification; see
[CURRENT_STATE.md](../CURRENT_STATE.md) for test evidence.

## Matching desktop and Pi support

For the coordinated feature build, use E3 DEV TEST 0.7.132 with the combined
Z-speed/connection-priority Pi companion and matching speed firmware. The
desktop sends existing typed focus actions; the Pi selects the faster feeds.
Direct/local-controller operation in that frozen desktop retains the old feeds.
The Pi advertises `pi-workpiece-focus-v1`; the desktop rejects an older Pi for
this workflow instead of sending a powered job that omits focus. Earlier
automatic-focus-only releases worked with surface-height V2 firmware, but the
new normal Z motion requires `Cap:E3_Z_SETUP_SPEED_V1:1`. Live numeric Z and
saved-Z restoration retain their separate firmware-capability requirements.

At the confirmed border, Reference border brings a freshly verified, homed Z
position to the native cycle's Z20 starting point, including after teaching
below Z20. It checks the active maximum, stowed probe and acknowledged position
before the homing cycle. The native initial lift needs an active maximum of at
least 25 mm. Unknown coordinates still require the existing reset-start checks.

## Numeric Z during movement

With the matching live-Z firmware and Pi companion, the number refreshes at up
to five times per second while Z moves. It comes from the controller's executed
step counter, not the requested destination or a time-based animation. During
homing it is labelled **homing / unreferenced**, because homing changes the
coordinate origin. It is not yet a height above the border and is not an encoder
measurement. Stale reports disappear; unsupported firmware shows live unavailable
during movement. The final acknowledged idle readback still governs the next
operation. These display updates never enable a button or alter a motion limit.

## Configured focus positioning area

Focus uses the existing machine rectangle together with an explicitly configured
fixed honeycomb polygon, when one is saved. The smaller calibration rectangle
is not the physical honeycomb boundary. The selected point, probe carriage and
later laser carriage must fit that combined area; the complete move and return
paths must also remain inside it. It does not fill gaps between the shapes or
expand to their bounding box. The polygon is fixed configuration, never inferred
from the camera, live detection or the workpiece. Changed bounds invalidate the
selection. Ordinary job limits and camera calibration are unchanged.

## Teaching approach and travel limits

The updated surface-height V2 firmware searches for contact down to −10 mm
relative to the border. The matching Pi companion validates contact against the
minimum actually reported by that firmware; previous V2 firmware retains its
−2 mm limit. The desktop displays this reported minimum, and shows the range as
unavailable until controller geometry is known. The 0.7.90 client can operate
with the companion but its old range label is fixed at −2; use the updated
E3 DEV TEST build for the correct display. Changing the firmware geometry
invalidates the existing reference and requires gauge calibration to be taught
again for that firmware. This contact-search limit does not lower the ordinary
Z jogging or calculated-focus floor below zero.

Use 1, 2 or 5 mm steps for approach only while the complete step has physical
clearance. Switch to 0.1 mm near the gauge fit and remove the gauge before each
adjustment. Steps of at least 1 mm use 300 mm/min; 0.1/0.5 mm use 60 mm/min.
The matching Pi companion is required for 2/5 mm steps and the corrected range.
Each click is a separate move with completion and Z readback before the next;
holding or repeated clicking does not queue a run of moves. Idle polling waits
from the latest completed readback so it does not immediately take another turn.

The displayed Z is the controller coordinate, not the gap under the laser.
Probe contact does not establish the laser-face collision plane: a valid gauge
setting can be below the probe-contact coordinate and give a negative taught
offset. Teaching and calculated focus retain Z0 as their minimum and the active
configured maximum. No move below zero is permitted. If the gauge cannot fit
within that travel interval, the mounting/reference setup needs examination.
The operator still checks the actual laser, probe housing and workpiece path.

## Ender readiness and reconnect

Successful XY Home / park does not establish the Ender's Z connection. The
reference workspace shows that connection's fault above its actions and retains
saved limits and offsets as configuration, separately from a live Z reading. With
the primary connected, idle and disarmed, **Reconnect Ender** explicitly retries
the shared connection, checks identity and acknowledges outputs off. It never
homes, measures a surface or repeats an interrupted move. A firmware restart
may initialize and move the CR Touch pin; keep the pin path clear. A fresh border reference is
required before positioning; existing clearance restrictions remain in force.

The matching Pi companion adds `pi-laser-focus-recovery-v1`. Supported new
compact firmware can also answer while emergency-halted and accept an explicit
restart. The existing 9518b83f firmware supports normal focus operations below,
but cannot be restarted by this protocol if it is already halted. It must be
made responsive before the recovery firmware can be installed. See
[Ender recovery](ENDER_RECOVERY.md) for the protocol and verification limits.

## Recover XY after a failed probe

The probe-failure candidate firmware propagates CR Touch deploy/stow failures
before further descent and records which native touch failed. Its matching Pi
companion includes received `E3PD:1` detail in the focus error, exposing FAST/SLOW
and NO_TRIGGER/CONTACT_RANGE evidence in the workspace and saved log. Older
firmware still reports the original generic error. This is diagnostic evidence, not a
valid measurement; failures still stop and invalidate the reference, and never
retry automatically. Native alarms may end the exchange before detailed evidence
arrives. See the firmware's
[failure record](../firmware/marlin_material/README.md#probe-failure-evidence).
The original paper-probing cause has not yet been physically established.

A failed native surface probe deliberately stops and disconnects the controllers.
The retained clearance restriction can then leave ordinary Home waiting for Z
clearance while Z clearance and border reference wait for Home. A fake-backed
MachineService sequence reproduced this deadlock. The separate **Recover XY at
current height** action resolves that recovery path for split GRBL XY / Ender Z
machines; it does not diagnose or repeat the failed probe.

The clearance restriction and pending-reference state survive STOP and
controller reconnects within the same Pi service process. They are not persisted
across a Pi service restart. Restarting must not be used to clear this incident's
restriction or interpreted as physical clearance. The current incident still
requires an operator-confirmed recovery and installation plan; the new action
does not reconstruct an earlier process's physical state.

1. Restore the primary connection. Use **Reconnect Ender** separately if needed;
   a firmware restart can initialize the probe pin, so first check its physical
   path. Reconnection only restores communications and acknowledges outputs off.
2. With both connections ready, freshly inspect and confirm that the probe is
   physically retracted and the **entire XY homing, search and parking path** is
   clear at the actual current height. A triggered M119 input does not prove
   physical stow. Earlier clearance confirmations are not reused.
3. Choose **Recover XY at current height**. This homes and parks the primary XY
   controller with the laser off and issues no Ender Z travel. It preflights
   fresh Ender state, checks both controller sessions and STOP throughout the
   operation, and verifies the final XY position and unchanged Ender Z state.
   The action does not reset the Ender or establish a Z reference.
4. At the border, separately confirm the reference setup, including physical
   headroom for the initial 5 mm lift, and choose **Reference border**. The
   combined reference button uses this label in recovery and performs no
   additional XY Home. Only a successful reference and acknowledged clearance
   lift release the restriction.
   Ordinary Home, XY jogging, jobs and arming remain blocked until then.

Recovery preflight accepts a known Z within the active bounds, or the existing
explicit unknown reset state near Z0. Unknown nonzero Z, inconsistent known-axis
reports, unsupported firmware, an unstowed probe input or insufficient configured
reference travel are rejected before XY motion. Reset Z0 is not physical height:
headroom for the later reference lift relies on the fresh physical confirmation,
not a proven absolute ceiling. This recovery requires the matching Pi companion
with `pi-laser-focus-xy-recovery-v1`; it has no new firmware requirement beyond the
existing focus/reference contracts.

The reported `G39 C30.000 H15.000` / `Error:E3MH:2 PROBE_FAILED` incident does not
establish whether either probe touch happened. The observed service stayed in
the same process and the kernel recorded no USB disconnect/reset. The generic
firmware error does not identify deployment, trigger, contact, stow or Z-trust
failure, and the lack of a hole in the paper does not establish its cause. No
physical cause or physical recovery success is established by this software fix.

## Choose a probe point in the camera image

After referencing the border and reaching the selected clearance, choose
**Position probe** in **Machine → Z axis · Ender**, then click a solid spot
in the main calibrated view on the left. In Machine Setup tab 7, use that tab's
raw live preview instead. A crosshair and the proposed probe/head coordinates
appear in the selected workspace. The same button changes to **Move probe
here** after a valid selection. Check the XY path and
remove the gauge, then press that button again to move at clearance. The camera
click only previews the target; it does not move, deploy the pin or descend.
Check the actual probe over the intended solid patch, then use **Measure
surface**. A rejected camera click stays visible as a red crosshair; it is
not a movement target. Work-area rejection reports the calculated machine XY,
the exceeded limits and source-image pixel. Select a new point to replace it;
source, machine or calibration changes still invalidate the marker. **Return laser to measured spot** brings the laser to that same
physical point for manual gauge teaching or focus positioning; this return is
not required before an automatically focused job. The physical path and solid-target
instructions appear beside these actions. The gauge-fit confirmation remains
in Machine Setup. There, pressing **Move to focus** confirms the adjacent
conditions: gauge removed and path to the target clear.

XY positioning uses the configured travel speed, limited to 1,200 mm/min and
the machine's travel/work feed ceilings. Its completion wait accounts for the
move duration within the bounded focus operation; it does not use the short
ordinary-command acknowledgement timeout. STOP, lost connections and failed
position verification still invalidate the reference and stop the operation.

The main view is already corrected using the installed lens and active
registered/meshed bed calibration. Its selection uses the coordinates displayed
for the machine or honeycomb workspace and requires fresh image metadata that
still matches the camera, calibration and displayed area. An unavailable,
stale or unverified image cannot become a positioning target. Zooming, panning
and resizing do not change the selected physical point.

Machine Setup's preview remains raw. Its clicks pass through the same lens
and bed correction; smaller full-frame Pi previews are first converted to
original camera coordinates. Unknown preview transformations and changed source
settings remain blocked. Both views retain the configured laser-center
correction and saved probe-to-laser offset.
The selected point, probe carriage and later laser-return carriage must all
be in the configured focus positioning area. Missing/stale calibration, stale/offline frames,
changed source settings or dimensions, unreferenced Z, insufficient clearance,
or a missing probe offset prevents positioning. A changed calibration or
controller session cancels a pending selection. Clicking the black margins
outside the image does nothing.

The original calibration grid is not the edge of the usable bed. Points beyond
that grid use the current bed map, including later registration and local
corrections, within the configured focus area. Their preview says **Outside
original calibration grid**; this is informational and does not disable the
separate move. Check the actual placement before probing. A target that would
put the probe carriage or later laser-return carriage outside the configured focus area
is still rejected.

This is a bed-plane camera estimate. Raised surfaces are not height-corrected;
verify the probe is over a solid patch before the separate contact cycle.
Normal viewing grants no motion action unless Position probe is selected
and the separate move is requested. Moving daily selection to the main view
was a desktop change; the automatic job-focus workflow now requires the matching
workpiece-focus Pi companion. Surface-height V2 firmware supports positioning
without live telemetry.

## Live view and the probe-to-laser transfer

The daily Machine Z workspace uses the main calibrated view on the left and
shows its next-step prompt beside the controls. Machine Setup tab 7 retains a
raw live camera preview alongside its calibration controls: it preserves aspect
ratio and reports frame age, stale frames and a lost connection. Ordinary
viewing is for visual alignment; explicit Position probe mode adds selection
as above. Setup owns observation while its modal workspace is open. Ending
observation does not stop the shared camera.

For a narrower teaching surface, open **Machine Setup → 7 · Z / laser focus**
and enter the measured vector in **Probe / laser XY offset**, from laser center
to probe in machine axes. Positive X is right and positive Y is toward the
back for the current operator's machine.
The operator supplied X **+3.302 mm**, Y **+38.608 mm** from CAD and confirmed
the physical directions. These are this machine's proposed calibration values,
not global defaults or physically verified transfer accuracy. Save the measured
offset explicitly; an unset offset never silently becomes zero.

1. Choose **Home / park + reference**, then remain at the selected clearance.
2. Use the camera view and ordinary Machine XY controls to align the laser over
   the chosen flat spot at clearance. The focus workspace has no separate XY
   jog row. For direct camera placement of the probe instead, use the separate
   Position probe sequence above.
3. Check the XY path is clear and remove the gauge, then choose **Put probe
   over laser spot**. This shifts the carriage by minus the measured offset.
4. Inspect the solid flat target under the probe and choose **Measure surface**.
5. At clearance, check the transfer path and choose **Return laser to measured
   spot**. It reverses the offset and retains only that point's measurement.
6. Put the 7 mm gauge **on top of the surface that was just measured**, so it
   spaces the laser face 7 mm above that surface. Use the small Z teaching steps
   and save the fit as described below. Remove the gauge before focus moves.

Each movement is separately requested; no button silently continues into probing
or lowering Z. Both endpoints and the entire transfer path must be inside the
configured focus positioning area.
The Pi checks Z clearance, controller session and acknowledged XY before keeping
the measurement. Ordinary XY jogging discards this point-specific teaching
measurement while retaining valid workpiece focus for jobs. Manual teaching
and focus positioning require the laser to return from the probe position;
automatic job focus does not. Changing the measured XY offset clears the
measurement, workpiece focus and transfer sequence.

This requires the matching Pi companion; surface-height V2 mainboard
firmware supports it without another flash. The wide flat-patch method below
continues to work when no XY offset is set. A flat 3–5 mm piece is a convenient
teaching surface within the default Z30 contact envelope; its exact thickness
is not used in the focus calculation because its top is probed directly.

This feature positions the Ender Z axis from a probed top surface and a saved
7 mm gauge setting. The primary controller retains XY and laser control.
The focus setup actions do not fire the laser or apply camera height correction.
Once the gauge is taught, **Measure surface** enables the coordinated job
sequence below automatically. Calibration must be physically taught and checked
on this machine.

Material thickness and surface elevation are different. A 3 mm sheet on a
30 mm support presents a top surface 33 mm above that support's base, but is
still a 3 mm sheet for choosing cutting settings. Focus Z uses the measured top surface. The daily gap uses sheet thickness
after subtracting the explicitly selected −1.5 mm honeycomb datum and entered
spacers. The manual calibration workflow continues to use the probed top directly.

## Prepare the machine

Open **E3 DEV TEST**, connect, and use **Machine → Z axis · Ender** for daily
reference and measurement. Use **Tools → Machine Setup… → 7 · Z / laser focus**
for manual preview/position, probe offsets and gauge calibration.
Both show the reported Z, configured maximum, selected clearance, reference,
calibration and measurement. A reported position is not an encoder measurement.

This workflow requires the existing surface-height V2 mainboard firmware and the
matching Pi companion. Older firmware continues to support its existing
controls; the new workflow reports the missing capability rather than using a
different probe command as a fallback. Installing the Pi files does not flash
the board, and automatic reusable workpiece focus requires no additional
firmware update. Any separately selected firmware feature keeps its own
installation and verification procedure.

The existing configured Z maximum remains authoritative and cannot exceed
Z80. Clearance is an absolute Z coordinate in the border frame, not an amount
to lift from the current position. Choose a clearance within that maximum and
physically above the work, probe deployment, fixtures and cables. The default
is Z30. The displayed measurable contact ceiling is clearance minus 15 mm:
Z30 allows contact up to Z15; Z50 allows contact up to Z35. These conservative
limits account for native probe offset/retract space. Firmware independently
checks its actual runtime geometry before deployment.

Only the operator initiates hardware actions. Keep the laser unable to emit
while fitting the gauge. The Ender's XY motors remain disconnected. Use a
flat, solid patch large enough for both the probe and the laser's gauge-contact
edge; their physical XY positions differ. A single probe point does not measure
a tilted or curved surface over an entire job.

## Teach the gauge once

Open **Machine Setup → 7 · Z / laser focus**. All the controls in this sequence
are embedded in that tab; the Machine Z workspace uses the saved result and
contains no teaching or Preview and position section.

The workpiece surface and the gauge have different jobs. Probe the flat
workpiece's top **without the gauge present**. After measurement, the gauge
sits on that top and establishes a 7 mm gap from the measured surface to the
laser's reference face. The workpiece's own thickness is not that gap.

1. Check the stowed probe, full XY path, solid black border at the parking
   position and space for the initial 5 mm lift. Choose **Home / park +
   reference**. After verified XY parking, the reference stage uses the native
   homing cycle, checks a border contact, and returns to the chosen clearance.
   If Z is already known above Z20, the reference stage first lowers to Z20
   over the confirmed bare border. It needs an active maximum of at least Z25.
   Do not home over a workpiece or into a honeycomb cell.
2. Position over a solid, flat teaching workpiece at clearance, with the gauge
   removed. **Measure surface** probes the workpiece's exposed top. Observe the
   native fast/slow contacts and final retract. E3 stores that top's contact
   coordinate and the current machine/session identity. With the offset-transfer
   workflow, check the clear XY path and choose **Return laser to measured
   spot** before teaching. Leave the workpiece in place.
3. Place the gauge's bottom flat on that same measured workpiece surface, with
   its **7 mm step** under the laser's usual gauge-contact face. This step must
   be 7 mm above the surface that the probe measured. Remove the gauge before
   each Z jog, then reinsert it to check the fit. Switch to 0.1 mm steps near
   the fit; stop when it fits gently between the workpiece and laser face,
   without pressing on or moving the workpiece. Keep both mountings fixed.
   These dedicated teaching jogs retain the measured surface; unrelated Z
   commands invalidate it.
4. Check **7 mm gauge fits at this Z** only after that physical fit is correct.
   **Save current Z as 7 mm gap** saves the difference between the acknowledged
   focused Z and the raw probe contact. Teaching reads the position and saves
   calibration; it does not move or enable the laser. Remove the gauge and
   return to clearance before changing workpieces or jogging XY.

Do not probe the gauge's 7 mm top, then touch the laser face to that same top
and save it as a 7 mm gap. That teaches a zero gap as 7 mm. Backing off a small
amount before saving teaches only that small gap; the step's thickness below
the already measured top does not count. If that happened, use **Forget taught
offset** and repeat the workpiece-then-spacer sequence. An approximate remembered
backoff is not an exact correction for a saved calibration.

The relation saved is:

```text
7 mm gauge offset = taught controller Z - raw probe contact Z
target Z = new raw probe contact Z + saved offset + (selected gap - 7 mm)
```

The offset is not the optical focal length and need not equal 7 mm. It includes
the installed probe-to-laser mounting relationship. The 5 mm and 3 mm selections
place the laser 2 mm and 4 mm lower than the taught 7 mm baseline respectively.
Those selections reproduce the operator's gauge; they do not certify cutting
parameters or cut quality for a material.

A spacer measured at 7.01 mm, saved with the current nominal 7 mm setting,
introduces a 0.01 mm increase in the reproduced nominal gaps. That difference
is within the operator's requested 0.6 mm acceptance; it does not replace the
physical fit checks. The measured workpiece top may be below the border zero:
the updated probe firmware accepts contact from Z-10 up to clearance minus 15 mm
(previous V2 firmware retains Z-2). It must still be
a solid patch, and all commanded laser Z positions remain between Z0 and the
active maximum.

Calibration is stored in a strict, versioned JSON sidecar beside the active
machine configuration, bound to the Ender connection and firmware/probe
geometry. A damaged or mismatched file is reported. It never silently becomes
a default offset. Reteach after changing the head/probe mounting, lens, focus
adjustment or relevant firmware/probe settings. Persisted calibration contains
no live homing, surface or motion authority.

## Position above work, including raised surfaces

The normal job workflow uses **Machine → Z axis · Ender → Measure surface**
and the automatically calculated **Workpiece focus** gap. The following manual positioning sequence
is optional: open **Machine Setup → 7 · Z / laser focus → Preview and position**
to inspect or move to a calculated Z outside a job. Switching between pages
does not itself replace the shared measured surface or saved-Z reference.

**Preview target** calculates and displays a destination; it does not move Z.
The Windows client refreshes the Pi's completed-operation status before
displaying the preview. This prevents the preceding busy snapshot from clearing
the target immediately after a successful calculation. Existing saved gauge
teaching is retained.
When **Move to focus** is disabled, the line immediately above it explains the
missing prerequisite. A brief camera interruption does not discard a Z preview
for an already measured surface. Camera point selection still requires a fresh
view, and changed focus parameters or machine state still invalidate previews.

1. Choose **Home / park + reference** to establish the border reference in the
   current controller session. Raise to sufficient clearance **before** placing
   tall work under the head or moving XY over it. A maximum setting alone does not make a path clear.
2. Position over the intended flat surface and **Measure surface**. Supports
   and spacers naturally contribute to this reading. Clear the surface result
   whenever the work changes; the machine cannot detect a manually moved piece.
3. In Machine Setup tab 7's **Preview and position**, select the 7, 5 or
   3 mm gauge gap. **Preview target** shows the calculated Z against the active
   maximum without moving. Changing the gap, clearance, measurement or
   calibration invalidates the previous preview.
4. Remove the gauge and check that the path to the target is clear. Pressing
   **Move to focus** confirms those conditions; there is no separate checkbox.
   The action requires the current preview and rechecks controller position
   and session before movement. Observe the completed position. An out-of-range
   target is rejected rather than clamped to a different focus.
5. **Return to clearance** before ordinary XY jogging. A focused position is
   close to the work and is not a fixture-clearance height. If clearance was
   changed while lowered, the return must reach at least the earlier clearance.
   E3 displays this minimum and does not clear the restriction just because a
   measurement was forgotten.

Home and ordinary XY jogging discard point-specific measurement/preview
authority but can preserve the separate workpiece focus described below.
STOP, controller disconnect, session changes, ordinary Z commands or other
probe operations can invalidate that focus as well. No movement is retried
automatically after failure. The existing manual Machine-tab Z jog
range remains Z20 through the configured maximum. The dedicated teaching and
focus path may work below Z20, but never below Z0 or beyond the configured
ceiling. The probe-contact coordinate is not the laser-face collision plane.

Ordinary XY jogging remains blocked while Z requires a return to clearance.
**Home / park** automatically lifts to the retained clearance and verifies that
lift before moving XY. Unknown Z, recovery state, STOP or a failed lift blocks
continuation. Clearing a measurement does not remove the clearance restriction.

## Use the measured focus for jobs

1. In **Machine → Z axis · Ender**, establish the reference and selected
   clearance, position the probe over a solid spot on the actual workpiece,
   enter total **Spacers**, and choose **Measure surface**. Supports contribute
   to top elevation but are subtracted when calculating sheet thickness.
   Measurement automatically selects focus using the taught gauge offset and
   thickness-derived gap, including when the probe was placed
   through the main view. No separate laser-return, preview or job-selection
   action is needed.
2. Check **Workpiece focus** for material thickness, automatic gap, calculated
   focus Z and ready status. Clear measurement before changing spacer thickness,
   then measure the new workpiece again.
   The same flat surface must span the whole job; remove the gauge and check
   the Z travel and entire XY path at clearance.
3. Start the intended job with its normal preview and START authorization.
   E3 establishes verified clearance, approaches the first XY position with
   output off, waits for XY completion, moves to the measured focus Z and
   verifies that position before the first positive laser command. A missing
   or invalid focus plan, unknown reference or failed Z check blocks the job;
   it does not fall back to an XY-only powered run.
4. Successful completion acknowledges laser-off, drains queued XY motion and
   verifies a clearance lift before the normal Home / park. It still lifts if
   automatic post-job parking is disabled. The next job reuses the same
   workpiece focus and repeats the clearance/approach/focus checks. Failed or
   stopped jobs never trigger an automatic lift, Home or retry.

Successful Home / park, an unpowered frame and ordinary guarded XY jogging
retain the workpiece focus only through verified clearance, unchanged controller
sessions and fresh Ender firmware, known-Z, position and stowed-probe checks.
The next job approaches from its newly verified XY position. This retention
does not make the parked or jogged location a new probe measurement or manual
preview target.

Measure again when the workpiece thickness, material height or supports change;
E3 cannot detect a manually replaced piece. A new measurement replaces the old
workpiece focus. Clearing the surface, changing its calibration or clearance,
STOP, failed motion, loss of the Z reference, or a Pi/controller restart revokes
the plan and blocks powered jobs until the reference is valid and a fresh
measurement succeeds. Saved gauge calibration is kept separately. Even a valid
saved-Z restore requires measuring the workpiece again; session focus is not
saved in project files or restored as a shutdown checkpoint.

The Pi owns the complete accepted sequence even if Windows monitoring detaches.
The workpiece identifier is bound into each immutable job's bytes and never
reaches GRBL. Reusing the measured height does not reuse START authorization:
each job keeps its own upload, arming and authorization checks. This workflow
supports one flat surface and one gap per job; layer refocusing and camera
correction for raised work remain separate. Machines that have never entered
the focus workflow and have no taught focus calibration retain their existing
job behavior; zero-power programs do not request laser focus.

## Setup travel speeds

**Move probe here**, **Align probe** and **Align laser** now use the configured
laser travel feed, limited by both machine feed ceilings. There is no separate
1,200 mm/min setup cap. At the default 3,000 mm/min travel feed this is 2.5 times
the previous requested speed; lower configured limits still apply.

With the matching Pi companion and Ender firmware, all host-controlled Z lifts
use 1,200 mm/min (20 mm/s), including initial reference lift, post-measurement
return, clearance/Home return, manual Z+ and job completion. Normal lowering
uses 600 mm/min (10 mm/s); sub-millimetre downward gauge fitting uses 120 mm/min
(2 mm/s). Upward gauge steps use the lift feed. Step distances, bounds,
stowed-probe checks, completion waits, position readbacks and STOP handling are
unchanged. These are requested feeds; acceleration and move length affect time.

The previous firmware capped Z at 300 mm/min, so the host update alone cannot
provide the increased speed. Normal Z moves require the new firmware's exact
speed capability and reject older firmware before motion. The new firmware
sets the Z travel ceiling to 20 mm/s after settings load, leaves acceleration
settings unchanged (default Z: 100 mm/s²), and preserves the native homing/contact cycle's previous effective
5 mm/s ceiling. Legacy stock-firmware diagnostic commands retain their old
300 mm/min lift; they are separate from the normal setup workflow.

Install the matching Pi package and firmware kit using their generated
**INSTALL.md** instructions. The firmware identity change invalidates old
reference/retention authority and requires a fresh border reference and 7 mm
gauge teaching. Then validate laser-off positioning, small up/down steps and
longer unobstructed clearance lifts before measuring and checking gauge fit.
Record controller, firmware, configuration and results; automated checks do
not establish that these faster rates avoid missed steps or preserve accuracy.

## Physical acceptance

First teach using the 7 mm step, then check that a commanded return to that
setting reproduces the gauge fit. Check the 5 and 3 mm settings against their
actual steps. Repeat on a second known surface height and then with thin
material on a measured support, choosing sufficient clearance before adding
the support. The operator's requested dimensional acceptance is 0.6 mm.
Controller readback alone does not establish that accuracy or optical focus.

The earlier observed native probe, fan, manual Z and explicitly selected job
focus tests are historical evidence for their respective builds. Automatic
selection from Machine-tab measurement, reuse on subsequent jobs and their
failure handling still require operator validation on the matching desktop/Pi
pair. Automated tests use simulated transports and offscreen Qt widgets.

## Pi companion installation

Build the matching speed companion from the exact feature checkout with
`python scripts/package_z_setup_speed.py`. It creates a
`dist/e3-pi-z-setup-speed-…` folder. Open that folder's **INSTALL.md** for the
exact Windows copy command and Pi dry-run/apply commands using its package name.
The bundled `install_z_setup_speed.py` defaults to read-only.

The installer accepts the recorded installed `568b1cc9` workpiece-focus payload
(application revision `671b235f272b8a0e910ff5039006c0d431bb9cd8`) and already-current
files. It validates all 17 payload paths together, including the priority protocol and new shared
motion module, rejects unknown edits before replacement, and backs up changed
source bytes. Configuration, gauge calibration files, Z maximum, retained-Z
data and cooling settings are preserved. Firmware compatibility and fresh
reference/teaching still must be established as described above.

Apply only with the machine idle, E3 disconnected and the hardware-node service
inactive. The installer queries service state but neither stops nor starts it;
**INSTALL.md** gives those separate operator commands. It flashes no firmware.
Install the separately supplied matching compact firmware kit by its **README**
procedure before testing normal Z motion. Old firmware cannot deliver the new
travel speeds and is rejected by the updated Pi's normal Z controls.
