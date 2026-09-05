# Primary GRBL session authority

This is implementation step 1 of the September 2026 whole-application audit.
It changes the primary controller lifecycle. Desktop/Pi snapshot reporting,
automatic Home on Start, secondary Air Assist recovery, and independent cooling
control remain separate work. This document records software behavior; it is
not evidence of physical verification or a safety-rated control.

## Receive ownership

The private connection candidate still completes the full existing handshake:
bounded input synchronization, dialect validation, acknowledged safe output,
stepper configuration, modal/offset queries, and realtime evidence. Only then
does it start a `ControllerReceiver`. Receiver startup and an authority check
must succeed before the exact transport/generation becomes publicly usable.

The receiver serializes all reads and command admission for that GRBL session.
It observes idle events without a Windows RPC or a `status()` call. Before a
new exchange writes, it drains queued idle input under the same ingress lock.
An unowned acknowledgement or payload retires the session. Command payload and
its terminal response remain owned by one transaction. Existing post-terminal
quiet checks, strict parsing, deadlines, and narrowly verified Home fallback
remain in force. A receive timeout checks the raw queue under ingress before
declaring a quiet boundary; thread scheduling alone cannot prove wire quiet.

ALARM, restart, malformed framing, transport failure, and realtime Alarm, Sleep,
Door, or Hold latch a fault before another write can enter. Valid ordinary
realtime status and recognized ESP-IDF diagnostic chatter remain allowed.
In particular, a `Run` report between line transactions is valid: the GRBL
planner can execute accepted motion while no line exchange is active. A missing
or incomplete diagnostic position sample alone does not reset the controller.

GRBL has no transaction IDs. This prevents input already observed or queued
while idle from acknowledging a future command. It cannot identify an old ACK
that first arrives only after a new exchange has actually begun. Ambiguous
exchanges therefore still require a fresh session; retry on the same stream is
not a repair strategy.

## Authority and terminal outcomes

Receive faults revoke the exact session, coordinate reference and temporary
authorization, cancel its active job, and initiate bounded abort/cleanup.
Arming and successful job-result publication share short receiver guards with
fault latching. A fault already observed cannot be converted into a new grant
or a successful job by a delayed service callback. A retired receiver/callback
cannot revoke or close a replacement transport or generation.

The offending frame is retained in the bounded session transcript. The first
quarantine owns the initiating failure; repeat cleanup and stale workers do not
replace it. Close failures remain separate transcript events. Job faults do
not manufacture an operator STOP timestamp. Primary fail-off/close has one
owner; the job unwinder no longer performs another primary M5 on the retired
transport. Existing secondary cleanup remains separate.

## Abort versus successful completion

Every STOP of a published GRBL session, including ordinary Stop/disarm/disconnect
paths, attempts bounded realtime hold/reset (`!`, Ctrl-X), followed by bounded
M5/close cleanup. Quarantine uses the same abort policy. An M5 line alone may
wait behind accepted planner motion. Neither a false job-running flag nor the
absence of a line transaction proves that motion has physically finished.

Communication recovery opens a fresh generation and returns only to
`READY_HOME_REQUIRED`. It never homes, moves, arms, or resumes a job. Successful
completion uses the existing motion barrier, optional Home/park, and verified
`$1=255` hold; it does not enter the abort path or discard its trusted reference.
Marlin's ordinary stop behavior and explicit emergency M112 policy are retained.

## Verification and physical acceptance

The deterministic coverage includes idle faults, malformed/duplicate replies,
admission and arming races, blocked powered writes, STOP/disarm abort ordering,
successive successful jobs, stale callbacks, and receiver retirement. The
existing 1,000-lifecycle soak also checks eventual thread retirement. See
`CURRENT_STATE.md` for the exact test run and known baseline failures.

Physical acceptance is still required on the actual Pi, primary firmware, and
serial configuration. Record those identities and results alongside the
[existing recovery campaign](GRBL_SESSION_RECOVERY_VALIDATION.md):

1. Connect, Home/park, and verify held XY at READY_MOTION. Run two prepared
   powered jobs; verify completion barriers, post-job parking, retained hold,
   and no unsolicited reset between successful jobs.
2. With laser energy safely isolated, exercise a controller alarm/reset and
   USB loss while idle. Confirm automatic trust revocation without polling
   causing it, fresh communication recovery, and Home required before motion.
3. Exercise software STOP during a long move and between accepted motion and
   its barrier; verify actual planner cancellation and output cessation, then
   recovery with no job resumption. Repeat for disarm during execution.
4. Repeat Home and jobs under normal camera/status monitoring. Compare command
   latency and streaming throughput; confirm harmless ESP-IDF chatter and
   realtime Run reports do not cause false quarantine.
5. Retain Pi journals through each fault: the original frame/reason, generation,
   abort attempt and cleanup outcome must remain distinguishable.

The software cannot establish physical output cessation if a transport or
controller stops responding. Existing physical emergency measures remain
necessary. No cooling-fan behavior is changed by this step.
