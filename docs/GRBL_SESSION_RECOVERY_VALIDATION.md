# Primary GRBL session recovery validation

This runbook is for a later, supervised Raspberry Pi and controller validation.
It is not permission to connect to or operate hardware during a software build.
E3's software controls and status are not safety-rated.

## Required safeguards and test boundary

Before the first cycle, record the operator, date, location, and test revision.
Use the enclosure, wavelength-rated protection, extraction, physical interlock,
and a reachable hardware emergency stop that removes hazardous energy. Keep the
laser output electrically disabled for the initial recovery tests. Restrain a
nonflammable sacrificial workpiece, clear the complete homing/travel envelope,
and keep one operator present throughout.

Do not use a production job for this validation. Use a reviewed, bounded,
absolute-millimetre, laser-off program that begins with `G21`, `G90`, and `M5`,
contains no positive laser output, and ends with standalone `M5`. Do not enable
Air Assist for the primary-session validation.

## Evidence to record before testing

Save this information with the test result rather than relying on memory:

- Windows E3 version, branch, and full revision.
- Pi E3 version, branch, and full revision.
- reported `E3MACHINE/2` protocol version and the complete advertised
  capability list, including `pi-owned-jobs-v1`,
  `pi-controller-session-v1`, `pi-structured-errors-v1`,
  `pi-coherent-status-v1`, and `pi-secondary-marlin-fan-v1`.
- Pi OS/kernel and Python version.
- Pi service unit name and its startup timestamp/boot ID.
- configured primary `/dev/serial/by-id/...` path, resolved device, and baud.
- configured controller dialect and reported firmware identity/version.
- controller configuration export or digest, including the observed GRBL
  `$1`, `$30`, and `$32` values.
- machine-profile digest and work-area values.
- whether the laser-enable power was physically removed or disabled.
- software state, controller-session generation, state revision, last
  transaction, synchronization evidence, and requested operator action from
  **Copy diagnostics** before cycle 1 and after cycle 20.

If the controller identity, firmware, configuration, serial path, machine
profile, or software revision changes, begin a new test record. A result from a
different combination is historical evidence, not verification of the current
combination.

## Automated preflight before physical testing

The deterministic software suite, not the physical controller, owns injected
partial bytes, malformed UTF-8, duplicate/delayed acknowledgements, forced
`$#` timeout, USB-disappearance, and concurrency barriers. Before physical
testing, record the passing Windows focused/full-suite results and the passing
Ubuntu pseudoterminal job for the exact revision. Do not alter controller
firmware, cabling, or serial traffic to manufacture those faults physically.

## Twenty-cycle recovery test

For each numbered cycle, record timestamps and the controller-session
generation at every state change.

Perform this one-time preflight before cycle 1: confirm the Pi is reachable and
the primary controller reaches exactly `READY_HOME_REQUIRED`; select
**Home / park** once; and confirm one physical Home completes before the state
becomes exactly `READY_MOTION`.

For each cycle from 1 through 20:

1. Start the reviewed laser-off test program. Confirm the state becomes
   `JOB_RUNNING`. Vary the STOP point across the 20 cycles: immediately after
   acceptance, during travel, while waiting for an acknowledgement, near normal
   completion, and while a status client reconnects.
2. Press software **STOP / LASER OFF** once. Independently be ready to use the
   hardware emergency stop. Confirm STOP remains responsive even if another
   client is polling or an ordinary action is in progress.
3. Confirm the stopped controller session is closed and never returns to a ready
   state. Automatic recovery may perform communication-only synchronization;
   it must not Home, jog, move, arm, enable output, resume the job, or repeat a
   job command.
4. Confirm successful recovery ends at exactly `READY_HOME_REQUIRED` on a newer
   controller-session generation. If recovery fails, confirm the state is
   exactly `RECONNECT_REQUIRED` with a bounded reason and a clear operator
   action; it must never display controller online or motion ready.
5. Select **Home / park** once. Confirm one Home occurs, the full coordinate
   query completes, and only then does the state become `READY_MOTION`.
6. Confirm a second observer sees the same Pi boot ID, session generation, state
   revision, and controller state. Closing that observer must not disconnect the
   controller.
7. Record pass/fail and any journal/diagnostic excerpt. Treat an unexplained
   timeout, stale result, duplicate Home, unexpected motion/output, generation
   regression, or contradictory state as a failed cycle and stop testing.

After cycle 20 reaches `READY_MOTION`, start one final reviewed laser-off job and
confirm it is accepted without an application or Pi restart; then stop it using
the normal supervised test procedure. Exercise varied real timing across the
cycles, including a Pi-client reconnect, but leave Connect/synchronization,
Home, and `$#` fault injection to the deterministic automated harness so the
20-cycle sequence remains exactly job → STOP → recovery → one explicit Home.

## Recovery and concurrency checks

- Send simultaneous Connect/Reconnect requests from two authenticated clients.
  Confirm they share one fresh session and neither closes the other's result.
- While Home owns the controller, request Home/Jog/Start from a second client.
  Confirm each conflicting request is rejected as busy and no queued later
  motion occurs.
- Delay or disconnect one observer, then STOP and recover from the other.
  Confirm the delayed observer cannot publish an older ready result.

Perform the remaining disruptive checks only after the 20-cycle acceptance
sequence is complete; they are not part of its zero-Pi-restart condition:

- Attempt a second E3 serial owner only in a controlled laser-disabled window.
  It must fail clearly because the configured/resolved device is exclusively
  owned, without transmitting a controller command.
- Terminate the Pi service normally and confirm it reports `SHUTTING_DOWN`,
  attempts bounded fail-off, starts no recovery, and leaves no owner process or
  serial descriptor behind.

## Acceptance criteria

All 20 cycles must satisfy every item below:

- no positive laser output and no automatic Home, motion, arming, or job resume;
- STOP remains the priority operation;
- every uncertain exchange permanently quarantines its exact session;
- a replacement uses a strictly newer generation and fresh transport;
- recovery ends at `READY_HOME_REQUIRED`, never `READY_MOTION`;
- only one explicit Home runs, and motion-ready is published after its final
  query succeeds;
- no old-session reply, callback, cleanup, or job worker changes the replacement;
- no contradictory ONLINE/RECONNECT or OFFLINE/MOTION READY display;
- no growing thread, file-descriptor, or serial-owner count;
- Pi journal and copied diagnostics identify the session, transition, failure,
  and required action without credentials or complete job G-code.

If any criterion fails, use the physical emergency stop as needed, remove
hazardous energy, preserve the complete test record and diagnostics, and do not
continue physical operation on that build.

## Later Pi checkout and evidence commands

Run these only after the operator has confirmed that the Pi worktree is clean
and has substituted the exact full revision printed in the feature-test handoff
for `FINAL_FEATURE_SHA`. They update software and restart the service; they do
not issue Home, motion, laser, or Air Assist commands.

```bash
cd /home/greenhouse-climate/Projects/laser-camera-aligner
git status --short
git fetch origin
git switch fix/pi-grbl-session-hardening
git pull --ff-only origin fix/pi-grbl-session-hardening
FINAL_FEATURE_SHA='<full revision from the feature-test handoff>'
test "$(git rev-parse HEAD)" = "$FINAL_FEATURE_SHA"
.venv/bin/python -m compileall -q laser_aligner
sudo systemctl restart e3-hardware-node.service
sudo systemctl status --no-pager e3-hardware-node.service
journalctl -u e3-hardware-node.service --since '10 minutes ago' --no-pager
```

If `git status --short` is nonempty, stop before switching or pulling and
resolve the Pi's local work deliberately. Do not overwrite it from this
runbook.
