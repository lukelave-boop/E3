# Surface height and gauge-taught laser focus

## Keep known Z across normal shutdowns

With the matching retained-Z desktop, Pi and firmware, a clean idle Pi
controller disconnect/shutdown can save the border datum and verified Z at or
above the selected clearance. On the next connection E3 restores that knowledge
without travel or probing, then requires fresh XY Home / park. The restored
border datum survives that Home; choose a new point and measure the workpiece
again before previewing or selecting focus for a job. Closing Windows alone is
not evidence that the Pi completed a clean shutdown.

Surface / laser focus shows whether Z was restored or why a new reference is
needed. If the Z axis drifted or was moved by hand, or the probe mount or
border/support changed while off, choose **Forget saved Z**, then **Reference
border** again. The taught gauge offset is kept. Unsupported firmware, a dirty
previous exit, changed configuration or failed readback cannot restore Z.

This retained-Z candidate needs a firmware update with the new restore
capability; earlier focus-positioning features below retain their stated
compatibility. Saved gauge calibration remains bound to its exact firmware and
may require explicit re-teaching once after that update. No retained-Z hardware
installation or physical power-cycle test has been performed yet. See the
[saved-Z workflow and qualification limits](Z_RETENTION.md).

Operator verification on 2026-09-12: Windows 0.7.93 plus Pi correction 59c31d7
completed the requested measured-job focus, clearance approach, automatic
clearance before Home/park and selection retention sequence. See CURRENT_STATE.md
for controller, firmware and calibration context. This observation does not
establish dimensional accuracy or qualify fault handling on physical hardware.

## Daily focus and machine calibration

Use **Machine → Z axis → Surface / laser focus…** for routine referencing,
surface measurement, focus previews and next-job focus selection. Set the
clearance and check the physical headroom and full XY/Z path before choosing
**Home / park + reference**. This single action homes and parks XY, verifies
completion and unchanged controller sessions, then references the border and
returns to clearance. A failed Home, STOP or changed session prevents the
reference stage. It does not retry automatically.

Open **Tools → Machine Setup… → 7 · Z / laser focus** to edit **Probe / laser
XY offset** or **Teach once with the 7 mm gauge**. The whole teaching section,
including teaching jogs, gauge-fit confirmation, Save and Forget, lives only
in this tab. The embedded page includes the same reference, measurement and
live-camera helpers, so calibration can be completed there. The daily window
displays and uses the saved offsets.

The separate XY transfer-path and solid-flat-patch checkboxes have been removed.
Check the actual clear path, remove the gauge before XY travel, and inspect
the solid target before probing. The headroom/Z-path, gauge-fit, gauge-removal
and flat-job confirmations remain, as do the backend bounds, clearance, session
and STOP checks. These desktop changes do not require a Pi or firmware update
or invalidate saved gauge teaching. Earlier protocol requirements below still
apply to the installed services. The revised interface has no new physical
verification; see [CURRENT_STATE.md](../CURRENT_STATE.md) for test evidence.

## Matching desktop and Pi support

Install the matching Pi companion before selecting the desktop feature build.
Camera-selected honeycomb positioning and 2/5 mm teaching jogs work with the
existing surface-height V2 firmware. A firmware update is needed only for the
optional live numeric Z stream; it is not a prerequisite for these motion fixes.

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
focus window shows that connection's fault above its actions and retains saved
limits and offsets as configuration, separately from a live Z reading. With
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
and NO_TRIGGER/CONTACT_RANGE evidence in the dialog and saved log. Older firmware
still reports the original generic error. This is diagnostic evidence, not a
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
**Position probe** on the left, then click a solid spot in the live image.
A crosshair and the proposed probe/head coordinates appear. The same button
changes to **Move probe here** after a valid selection. Check the XY path and
remove the gauge, then press that button again to move at clearance. The camera
click only previews the target; it does not move, deploy the pin or descend.
Check the actual probe over the intended solid patch, then use **Measure
surface**. A rejected camera click stays visible as a red crosshair; it is
not a movement target. Work-area rejection reports the calculated machine XY,
the exceeded limits and source-image pixel. Select a new point to replace it;
source, machine or calibration changes still invalidate the marker. **Return laser to measured spot** brings the laser to that same
physical point for gauge teaching or focus. The physical path and solid-target
instructions appear beside these actions. The gauge-fit confirmation remains
in Machine Setup; gauge removal is still confirmed before Move to focus.

XY positioning uses the configured travel speed, limited to 1,200 mm/min and
the machine's travel/work feed ceilings. Its completion wait accounts for the
move duration within the bounded focus operation; it does not use the short
ordinary-command acknowledgement timeout. STOP, lost connections and failed
position verification still invalidate the reference and stop the operation.

The view remains raw, but click coordinates pass through the installed lens
correction and active registered/meshed bed calibration. The service applies
both the configured laser-center correction and saved probe-to-laser offset.
When the Pi sends a smaller full-frame preview, E3 first converts the click
back to the original camera coordinates. Resizing this window does not change
the selected bed point. The original source must still match the calibration;
unknown preview transformations and changed source settings remain blocked.
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
Normal live viewing grants no motion action unless Position probe is selected
and the separate move is requested. The matching updated Pi companion is
required; surface-height V2 firmware supports positioning without live telemetry.

## Live view and the probe-to-laser transfer

The focus window includes a live bed view alongside the controls and a next-step
prompt. The image is the raw camera feed: it preserves aspect ratio and reports
frame age, stale frames and a lost connection. Ordinary viewing is for visual
alignment; the explicit Position probe mode adds calibrated selection as above.
Closing the window ends this view's stream without stopping the shared camera.

For a narrower teaching surface, open **Machine Setup → 7 · Z / laser focus**
and enter the measured vector in **Probe / laser XY offset**, from laser center
to probe in machine axes. Positive X is right and positive Y is toward the
back for the current operator's machine.
The operator supplied X **+3.302 mm**, Y **+38.608 mm** from CAD and confirmed
the physical directions. These are this machine's proposed calibration values,
not global defaults or physically verified transfer accuracy. Save the measured
offset explicitly; an unset offset never silently becomes zero.

1. Choose **Home / park + reference**, then remain at the selected clearance.
2. Use the live view and XY jogs to align the laser over the chosen flat spot.
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
the measurement. Ordinary XY jogging invalidates it. Teaching and focus are
blocked while the laser has not yet returned from the probe position. Changing
the measured XY offset clears the current measurement and transfer sequence.

This requires the matching Pi companion; surface-height V2 mainboard
firmware supports it without another flash. The wide flat-patch method below
continues to work when no XY offset is set. A flat 3–5 mm piece is a convenient
teaching surface within the default Z30 contact envelope; its exact thickness
is not used in the focus calculation because its top is probed directly.

This feature positions the Ender Z axis from a probed top surface and a saved
7 mm gauge setting. The primary controller retains XY and laser control.
The focus setup actions do not fire the laser or apply camera height correction.
The separate **Use measured focus for next job** selection enables the coordinated
job sequence below. Calibration must be physically taught and checked on this machine.

Material thickness and surface elevation are different. A 3 mm sheet on a
30 mm support presents a top surface 33 mm above that support's base, but is
still a 3 mm sheet for choosing cutting settings. Focus uses the measured top
surface. It never infers sheet thickness by subtracting an assumed bed height.

## Prepare the machine

Open **E3 DEV TEST**, connect, and use **Machine → Z axis → Surface / laser
focus…** for daily use, or **Tools → Machine Setup… → 7 · Z / laser focus**
for probe-offset and gauge calibration. Both show the reported Z, configured
maximum, selected clearance, reference, calibration and measurement. A reported
position is not an encoder measurement.

This workflow requires the new surface-height V2 mainboard firmware and the
matching Pi companion. Older firmware continues to support its existing
controls; the new workflow reports the missing capability rather than using a
different probe command as a fallback. Installing the Pi files does not flash
the board. Use the application-only USB update instructions in the separately
prepared firmware package; the retained updater and factory SD loader are not
replaced by that application upload.

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
are embedded in that tab; the daily Surface / laser focus window uses the saved
result and contains no teaching controls.

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

**Preview target** calculates and displays a destination; it does not move Z.
The updated Windows client refreshes the Pi's completed-operation status before
displaying the preview. This prevents the preceding busy snapshot from clearing
the target immediately after a successful calculation. Existing saved gauge
teaching is retained; this client fix needs no firmware update or Pi restart.
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
3. Select the 7, 5 or 3 mm gauge gap. **Preview target** shows the calculated Z
   against the active maximum without moving. Changing the gap, clearance,
   measurement or calibration invalidates the previous preview.
4. **Move to focus** requires the current preview and rechecks the controller
   position and session before movement. Observe the completed position. An
   out-of-range target is rejected rather than clamped to a different focus.
5. **Return to clearance** before ordinary XY jogging. A focused position is
   close to the work and is not a fixture-clearance height. If clearance was
   changed while lowered, the return must reach at least the earlier clearance.
   E3 displays this minimum and does not clear the restriction just because a
   measurement was forgotten.

Home, STOP, disconnect, session changes, ordinary Z commands or other probe
operations invalidate current measurement/preview authority. No movement is
retried automatically after failure. The existing manual Machine-tab Z jog
range remains Z20 through the configured maximum. The dedicated teaching and
focus path may work below Z20, but never below Z0 or beyond the configured
ceiling. The probe-contact coordinate is not the laser-face collision plane.

Ordinary XY jogging remains blocked while Z requires a return to clearance.
**Home / park** automatically lifts to the retained clearance and verifies that
lift before moving XY. Unknown Z, recovery state, STOP or a failed lift blocks
continuation. Clearing a measurement does not remove the clearance restriction.

## Use the measured focus for a job

1. Probe the actual work surface and return the laser to the measured spot.
   Select the desired gap and choose **Preview target**.
2. Confirm that the same flat surface spans the whole job, the gauge is removed,
   and Z travel and the entire XY path are clear at the selected clearance.
   Choose **Use measured focus for next job**. Its readout shows gap, focus Z
   and clearance. This selects a one-use job plan; it does not move or emit.
3. Close the focus window and start the intended job using its normal preview
   and START authorization. If lowered, E3 first lifts before arming. It moves
   XY with output off at clearance, waits for completion, lowers to the measured
   focus Z, verifies it, and only then begins the powered toolpath.
4. Successful completion acknowledges laser-off, drains queued XY motion, and
   verifies a clearance lift before the normal Home / park. It still lifts if
   automatic post-job parking is disabled. Failed or stopped jobs never trigger
   an automatic lift, Home, or retry.

The Pi owns the complete accepted sequence even if Windows monitoring detaches.
A controller/Pi reset, changed measurement/calibration/clearance, ordinary XY
jog or failed Home invalidates selection; select again after establishing a fresh
measurement. Successful Home / park retains the selected flat job height only
after a verified clearance lift, unchanged controller sessions and fresh Ender
firmware, known-Z, position and stowed-probe checks. The job approaches from the
newly verified parked XY position. This does not restore probing/reference/preview
authority at the parked location. The UUID binding travels in the exact immutable job bytes and never
reaches GRBL. Selections are consumed once and are not saved in project files.
Jobs without a selected binding retain their existing behavior and do not focus
automatically. This sequence supports one flat surface and one gap per job;
layer refocusing and camera correction for raised work remain separate.

## Physical acceptance

First teach using the 7 mm step, then check that a commanded return to that
setting reproduces the gauge fit. Check the 5 and 3 mm settings against their
actual steps. Repeat on a second known surface height and then with thin
material on a measured support, choosing sufficient clearance before adding
the support. The operator's requested dimensional acceptance is 0.6 mm.
Controller readback alone does not establish that accuracy or optical focus.

The earlier observed native probe, fan and manual Z tests are historical
evidence for their respective builds. The new expanded probing, gauge teaching,
focus movement and obstruction/USB-failure behavior require their own operator
validation. Automated tests use simulated transports and offscreen Qt widgets.

## Pi companion installation

The companion checks every source hash before replacement, preserves unknown
edits and existing configuration/maximum/cooling settings, and makes backups.
It does not start services or flash firmware. Copy from **Windows PowerShell**:

```powershell
scp -r 'C:\Users\lukel\Documents\E3\dist\__PI_PACKAGE__' greenhouse-climate@192.168.5.18:/home/greenhouse-climate/
```

With the machine idle and E3 disconnected, run in **Pi Bash**:

```sh
sudo systemctl stop e3-hardware-node.service
sudo systemctl reset-failed e3-hardware-node.service
e3_project=/home/greenhouse-climate/Projects/laser-camera-aligner
e3_focus=/home/greenhouse-climate/__PI_PACKAGE__
"$e3_project/.venv/bin/python" "$e3_focus/install_laser_focus.py" --project "$e3_project" --apply &&
sudo systemctl start e3-hardware-node.service
```

An unknown-source rejection requires reviewing that difference; do not force
replacement. Keep the service stopped during any separate application USB
upload. Use the exact firmware package's readback/boot procedure before opening
the desktop workflow.
