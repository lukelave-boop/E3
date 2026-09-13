# Saved Z after a normal shutdown

This feature can reuse the known Z coordinate and border reference after a
clean shutdown, avoiding another border probe when the machine's physical
position and reference setup have stayed unchanged. It does not retain a
workpiece measurement or a selected job focus height.

The implementation requires matching desktop, Pi companion and restore-capable
Ender firmware. No retained-Z hardware installation or physical power-cycle
acceptance test has been recorded yet. Earlier focus-workflow observations do
not verify this feature. See [CURRENT_STATE.md](../CURRENT_STATE.md) for the
current automated and build verification record.

## Normal workflow

1. Finish the operation and wait for the machine to become idle and disarmed.
   Z must have a valid border reference and be at or above the saved clearance.
   If needed, separately use **Return to clearance** and wait for its verified
   completion before exiting. Saving a shutdown checkpoint does not lift Z.
2. Disconnect or shut down E3 normally. The Pi must complete its own clean idle
   controller disconnect/shutdown and verify the final state. Closing the
   Windows window, losing Wi-Fi or an old displayed Z value is insufficient.
3. On the next connection, E3 consumes the saved record and checks the connected
   machine before restoring its Z knowledge. It issues no travel or probing
   command as part of restoration; it enables Z motor hold after the coordinate
   check. Surface / laser focus shows whether restoration succeeded or why it
   was rejected. There is no routine startup confirmation dialog.
4. Complete fresh **Home / park XY**. This restores XY readiness while preserving
   the accepted border datum. Select and measure the current workpiece surface
   before a new preview or **Use measured focus for next job** selection.

A Windows exit while a Pi-owned job is still running is not a clean idle
checkpoint. The existing Pi-owned job lifecycle is unchanged: accepted jobs can
continue after the desktop disconnects. A saved-Z record never resumes a job.

## What E3 can retain

The checkpoint contains the verified Z coordinate, selected clearance, configured
Z maximum and the border datum with its firmware/geometry identity. It belongs
to the configured controller rig. E3 accepts it only if the saved Z is at or
above clearance and within the active maximum, the firmware still matches, the
probe reports stowed, and current controller checks succeed.

The workpiece surface, camera target, preview and next-job focus selection are
session-specific and discarded. Restoring the border datum does not mean the
material or supports under the next job are unchanged. Existing gauge teaching
is stored separately; ordinary restart does not by itself require teaching it
again.

## When a new border reference is required

E3 will not restore from a missing, malformed, already-consumed or dirty record.
An interrupted or failed shutdown, active job/probe/focus operation, arming,
unknown Z, a position below saved clearance, unresolved recovery state or failed
shutdown verification cannot create a clean checkpoint. STOP and failed
operations revoke retained reference authority.

Changed rig configuration, configured maximum, firmware or geometry, an
unstowed/inconsistent probe report, mismatching live known Z or a failed final
readback also prevent restoration. Unsupported firmware has no fallback.
Unknown nonzero controller Z is rejected. Unknown reset Z0 is accepted only with
the clean consumed checkpoint and the remaining checks; reset Z0 alone is not
physical position evidence.

A clean record is marked dirty before the next controller connection can use
it. If that new session crashes, its earlier clean record cannot be replayed on
the following start. A subsequent verified normal disconnect/shutdown is needed
to create another clean record. Failure to invalidate the record prevents
connection admission.

## Physical changes require Forget saved Z

E3 cannot detect movement by hand, unpowered Z drift or a changed physical
reference setup. If the Z axis, probe mount or border/support moved while E3 was
off, use **Forget saved Z**, then separately **Reference border** again before
positioning Z. Use the same action whenever the retained position is doubtful.
No software check proves that the axis stayed still with power removed.

**Forget saved Z** discards the saved coordinate and current border-reference
authority. It does not move the machine, probe, fire the laser or erase the taught
gauge calibration. It clears temporary positioning state and requires a fresh
border reference. A changed probe or laser mounting may also require re-teaching
the gauge fit; retaining its stored value does not verify that the old geometry
still applies.

The action appears only when the Pi reports retention support and is available
only with current idle, connected and disarmed state. It is separate from
**Forget taught offset**. With an older Pi companion, the retention controls are
hidden and the existing reference workflow remains in use.

## Firmware compatibility and qualification

The new firmware advertises `Cap:E3_Z_RESTORE_V1:1`. Through MachineService's
existing secondary-controller owner, the typed host uses guarded `M124` to
adopt saved Z from unknown reset Z0 or verify an already-known matching native Z
without rewriting it. Different live Z is never overwritten. The command sends
no travel or probing and has no `G92` fallback. The host separately sends
`M84 S0` and `M17 Z` to establish motor hold, then verifies final known Z and
probe state before restoring the border datum. These commands are unavailable
through ordinary manual-command entry.

Gauge calibration remains bound to its exact saved firmware identity and
geometry. The update that adds restoration can therefore require one explicit
new gauge teaching. E3 does not silently migrate, relabel or delete existing
calibration to make it compatible. Once a calibration is taught for the matching
firmware, normal unchanged restarts retain it.

Automated acceptance and rejection tests establish software behavior with
simulated controllers and fake firmware I/O. Physical acceptance still needs a
recorded clean shutdown and power-cycle test with the exact Windows build, Pi
revision, controller, firmware, configuration and observed result. That test
must verify actual retained height, restored border reference, subsequent XY
Home and a separately requested measurement/focus sequence. Unpowered axis
stability and physical accuracy remain unverified until recorded. These controls
are not safety-rated.
