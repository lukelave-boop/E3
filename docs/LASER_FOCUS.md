# Surface height and gauge-taught laser focus

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

## Choose a probe point in the camera image

After referencing the border and reaching the selected clearance, choose
**Position probe** on the left, then click a solid spot in the live image.
A crosshair and the proposed probe/head coordinates appear. Confirm the XY
path is clear and the gauge removed, then choose **Move probe here**. This
positions the probe at clearance; it does not deploy the pin or descend.
Check the actual probe over the intended solid patch, then use **Measure
surface**. A rejected camera click stays visible as a red crosshair; it is
not a movement target. Work-area rejection reports the calculated machine XY,
the exceeded limits and source-image pixel. Select a new point to replace it;
source, machine or calibration changes still invalidate the marker. **Return laser to measured spot** brings the laser to that same
physical point for gauge teaching or focus. The XY-path confirmation appears
above the positioning buttons; the gauge-fit and gauge-removal confirmations
also appear before the actions they enable.

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

For a narrower teaching surface, expand **Probe / laser XY offset…** and enter
the measured vector from laser center to probe, in machine axes. Positive X is
right and positive Y is toward the back for the current operator's machine.
The operator supplied X **+3.302 mm**, Y **+38.608 mm** from CAD and confirmed
the physical directions. These are this machine's proposed calibration values,
not global defaults or physically verified transfer accuracy. Save the measured
offset explicitly; an unset offset never silently becomes zero.

1. Home / park and reference the border, then remain at the selected clearance.
2. Use the live view and XY jogs to align the laser over the chosen flat spot.
3. Confirm the XY path is clear and the gauge removed, then choose **Put probe
   over laser spot**. This shifts the carriage by minus the measured offset.
4. Confirm a solid flat target under the probe and **Measure surface**.
5. At clearance, confirm the transfer path and choose **Return laser to measured
   spot**. It reverses the offset and retains only that point's measurement.
6. Fit the 7 mm gauge on that same spot, use the small Z teaching steps, and save
   the gauge setting as described below. Remove the gauge before focus moves.

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
The focus actions are laser-off setup actions; they do not fire the laser,
apply camera height correction, or automatically change focus when starting a
job. Calibration must be physically taught and checked on this machine.

Material thickness and surface elevation are different. A 3 mm sheet on a
30 mm support presents a top surface 33 mm above that support's base, but is
still a 3 mm sheet for choosing cutting settings. Focus uses the measured top
surface. It never infers sheet thickness by subtracting an assumed bed height.

## Prepare the machine

Open **E3 DEV TEST**, connect, and use **Machine → Z axis → Surface / laser
focus…**. The same workflow is available from Machine Setup. Its status shows
the reported Z, configured maximum, selected clearance, reference, calibration
and measurement. A reported position is not an encoder measurement.

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

1. Home / park the primary controller over the solid black border. Confirm the
   stowed probe and space for the initial 5 mm lift. **Reference border** uses
   the native homing cycle, checks a border contact, and returns to the chosen
   clearance. If Z is already known above Z20, this action first lowers to Z20
   over the confirmed bare border. It needs an active maximum of at least Z25.
   Do not home over a workpiece or into a honeycomb cell.
2. Position over the flat teaching workpiece while at clearance, then **Measure
   surface**. Observe the native fast/slow contacts and final retract. E3 stores
   the measured contact and the current machine/session identity.
3. Place the gauge on that same surface. Use the dedicated small Z teaching
   jogs to bring the laser's usual gauge-contact edge just onto the **7 mm**
   step. Keep the probe mounting and laser mounting fixed. These teaching jogs
   retain the measured surface; unrelated Z commands invalidate it.
4. **Save current Z as 7 mm gap** saves the difference between the acknowledged
   focused Z and the raw probe contact. Teaching reads the position and saves
   calibration; it does not move or enable the laser. Remove the gauge and
   return to clearance before changing workpieces or jogging XY.

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

Calibration is stored in a strict, versioned JSON sidecar beside the active
machine configuration, bound to the Ender connection and firmware/probe
geometry. A damaged or mismatched file is reported. It never silently becomes
a default offset. Reteach after changing the head/probe mounting, lens, focus
adjustment or relevant firmware/probe settings. Persisted calibration contains
no live homing, surface or motion authority.

## Position above work, including raised surfaces

1. Establish the border reference in the current controller session. Raise to
   sufficient clearance **before** placing tall work under the head or moving
   XY over it. A maximum setting alone does not make a path clear.
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

This release is explicit setup positioning and gauge validation. Ordinary XY
jogging, Home / park and job starts are blocked while Z still requires a return
to clearance. Clearing a measurement does not remove that restriction. It does
not yet bind a saved project or recipe to a focus measurement, automatically
refocus between layers, or coordinate a final Z lift with post-job XY homing.
Those job-integration steps follow physical calibration acceptance. Camera
correction for raised work is also separate.

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
