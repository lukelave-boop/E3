# Job Pause / Resume

During a supported toolpath, click **Pause**, wait for **JOB PAUSED**, then click
**Resume** to continue from the controller's retained queue. No job lines are
rewound, re-uploaded or skipped. Deceleration takes time; **Pausing…** is not a
claim that motion or laser energy has stopped. A separate Air Assist controller
keeps its current state. Primary GRBL coolant follows firmware suspension and
restoration, so its air output can stop while paused. Keep the workpiece and
axes stationary.

The matching Pi companion must advertise `pi-job-pause-v1`. Support requires
a GRBL 1.1 identity, an observed `$32=1` laser-mode setting, a current homed
coordinate reference and the existing verified `$1=255` motor-hold policy.
The application never changes `$32` to enable Pause. Other controllers and
older Pi nodes leave Pause unavailable. Pause is available while streaming
or draining the toolpath, and unavailable during Z focus, Home and park.

Pause sends GRBL's realtime safety-door suspend byte (0x84), which retains its
planner. In laser mode this avoids parking travel. The software waits for
`Door:0` and rejects a reported active spindle before offering Resume. Resume
sends cycle-start (`~`) only for that same paused job, session and pause revision,
then waits for Run/Idle. This behavior follows the upstream
[GRBL suspend implementation](https://github.com/gnea/grbl/blob/master/grbl/protocol.c)
and [motor idle implementation](https://github.com/gnea/grbl/blob/master/grbl/stepper.c).
Firmware forks still require physical qualification.

The receive owner continues dispatching telemetry and retaining FIFO ACKs during
pause. Ordinary ACK deadlines exclude the suspended interval; transition and
telemetry watchdogs remain bounded. Lost telemetry, unexpected restart, alarm,
unsolicited resume or revoked authority fails the job through the existing
reset/M5/session-quarantine path. A guarded serial write checks authority again
after taking the physical TX lock, so a Resume queued behind STOP cannot later
write cycle-start. STOP is independent of the pause gate and remains available.

Pi-owned execution and its durable record remain active while paused. Closing
Windows or losing its monitoring connection does not resume the job. Reconnect
and explicitly Resume. A Pi restart marks the job interrupted; it cannot resume
the lost controller session. A failed or timed-out pause/resume RPC is never
automatically retried. Observe the refreshed status before another action.

The original job-scoped laser authorization remains bound to this immutable job.
Pause grants no new arming or motion authority. STOP, disarm and disconnect
continue to revoke it. Browser jobs share the runner guards but this change adds
Pause / Resume controls only to the desktop; the browser UI is unchanged.

## Operator acceptance

Software tests use fake controllers, loopback RPC, offscreen widgets and POSIX
pseudoterminals. They do not verify physical laser-off, motor holding, position
retention or surface quality. These software controls are not safety-rated.

Record the exact Windows build, Pi bundle, controller identity, firmware and
configuration with the result. Start with a supervised zero-power path, pause
mid-move, confirm that it settles and retains position, then resume and check
the remainder against the preview. Repeat with a dense raster and at a row
boundary. Qualify powered behavior separately on a small sacrificial workpiece
using established enclosure, extraction and physical emergency controls: verify
laser output during suspension, continuation alignment and any pause mark.
Test STOP while paused and during resume; neither may permit later continuation.
Do not use the screen's PAUSED label as proof that the area is safe to enter.
