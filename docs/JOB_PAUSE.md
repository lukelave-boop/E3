# Job Pause / Resume

## Taking control and stopping to edit settings

If the Pi keeps running after a desktop detaches or its control lease expires,
click **Take control**. With pi-job-controls-v1 this reattaches to the existing
running or paused job without reconnecting/resetting its serial controller. It
cannot take another desktop's live lease. Pause's tooltip explains ownership,
busy and stale-status blocks.

**Stop job** requests priority controller interruption and laser-off. On GRBL it
uses the existing feed-hold/reset path to discard queued work, bypassing ordinary
desktop tasks and pending acknowledgements. A paused job is stopped without
cycle-start or a confirmation to finish its buffered motion. The button remains
available during active-job transitions and desktop work while current status
and control ownership allow it. The red software STOP remains independently
available and always requests emergency secondary interruption.

The interrupted job gets no successful-completion receipt. XY and workpiece
authority are invalidated. With `pi-job-stop-retain-z-v1`, Stop job can keep the
independent Ender border reference when Z was stationary and fresh recovery checks
match its last verified job position. Use **Home / park XY**: it raises Z to the
previous clearance first if needed, then homes XY without another border probe.
Reapply material thickness/spacers (or measure), then generate/review and Start
anew. Changed/unknown Z, firmware, connection or setup requires referencing again.
Stopping during Z movement uses full interruption and discards Z. No automatic
Home, clearance lift or resumed motion follows Stop. A status message confirms
only a software request, not physical cessation. Use physical emergency controls
for an emergency; neither software control is safety-rated.

Retaining Z requires a matching Pi companion. Older nodes receive the full
emergency STOP and lose reference authority. Earlier desktop builds used
`job.cancel`, which drained accepted moves;
a whole short path could already be queued and thus finish despite that request.
That legacy RPC remains available to older clients but is not the current button
behavior. Test interruption during short vector paths, dense raster, paused jobs
and pending ACKs, followed by guarded recovery. Record exact builds/controller/
firmware/settings; fake-controller tests do not establish physical stopping.

Desktop Pause/Resume uses the existing job session without a preceding Connect
request. A changed controller session, boot or queued state invalidates the request;
the Pi also validates the run and pause revision. Rejection never causes a retry
or reconnection. This fixes the active-job Connect error observed in build 0.8.19.

During a supported toolpath, click **Pause**, wait for **JOB PAUSED**, then click
**Resume** to continue from the controller's retained queue. No job lines are
rewound, re-uploaded or skipped. Deceleration takes time; **Pausing…** is not a
claim that motion or laser energy has stopped. A separate Air Assist controller
keeps its current state. Primary GRBL coolant follows firmware suspension and
restoration, so its air output can stop while paused. Keep the workpiece and
axes stationary.

The matching Pi companion must advertise `pi-job-pause-v1`. Support requires
a successful GRBL handshake, an observed `$32=1` laser-mode setting, a current homed
coordinate reference and the existing verified `$1=255` motor-hold policy.
The application never changes `$32` to enable Pause. Other controllers and
older Pi nodes leave Pause unavailable. Optional `$I` identity may be absent,
as on the installed controller; a contradictory reported version rejects support.
Omitted identity is not evidence of a particular firmware version. Resume still
requires confirmed `Door:0`; an ignored suspend request times out and fails off.
Pause is available while streaming
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
