# Pi status authority and explicit repeat Home

This revision follows the primary-session-authority change at `55741cc`.
Windows remains an observer of Pi-owned machine and job execution. An RPC socket
is disposable; its failure does not establish that the Pi process stopped or
that the controller lost its coordinate reference.

## Evidence and scope

On 2026-09-05 the operator reported successful Home/park, successive powered
circles, and immediate powered STOP with Home required afterward. Later Windows
flashed PI OFFLINE / STATE UNKNOWN while the Pi service remained active and
answered status requests. Disabling the live camera overlay did not eliminate
the flashes. Earlier kernel undervoltage and USB errors were also recorded;
these observations do not establish a single cause for every timeout.

Code review identified two freshness defects in the same refresh transaction:
a supplemental job query could discard an already successful machine snapshot,
and a job-only response could mark an older machine snapshot fresh. Coherent Pi
status already included the tracked terminal job, yet the client often made
another unnecessary job-status RPC. Generic Pi session-error logs omitted the
failed protocol stage and exception detail.

The correction does not increase serial acknowledgement timeouts or retry
ambiguous GRBL exchanges. It does not claim to fix Wi-Fi, power, thermal, USB,
or camera-server failures.

## Observation contract

- The authenticated `machine.status` body and its boot, generation, and revision
  metadata publish together, before optional job-detail requests. Coherent Pi
  job ownership publishes with them. Where job details need another RPC, the
  prior durable job identity/acceptance remains present, marked stale; a raw
  controller job without Pi acceptance cannot temporarily replace it.
- A job-detail failure marks job detail stale and records `job_status_error`.
  It cannot discard a successfully published machine snapshot. Coherent active
  and latest job records are reused when they identify the exact tracked job.
- A job-only response cannot refresh machine state. If its metadata advances
  controller state, machine data remains unavailable until a matching fresh
  machine snapshot arrives.
- Machine observations expire after three seconds without a new accepted
  snapshot. Authenticated Pi replies establish recent node contact for five
  seconds. These are desktop observation windows, not hardware heartbeats or
  coordinate-authority leases. They use the local monotonic clock.
- A failed machine poll marks its observation unavailable immediately. While
  recent Pi contact exists, Windows says STATUS UNAVAILABLE / STATE UNKNOWN.
  After node contact expires it says PI NOT RESPONDING / STATE UNKNOWN. Neither
  label asserts a controller reset. Ordinary motion controls require fresh
  machine status; STOP remains available and accepted Pi jobs remain Pi-owned.
- Superseded polling failures and delayed job detail cannot overwrite a newer
  machine observation. STOP epochs, monotonically increasing controller
  revisions/generations, and retired Pi boot identities reject old authority.
- Pi session-error logs include stage, authenticated status, bounded error
  detail, request prefix, peer, and elapsed time. Client RPC failures include
  action and timing. Request payloads and authentication credentials are not
  logged by these additions.

The existing `monitor_connected` field remains for compatibility and describes
machine-observation availability. It is not the Pi's process or USB state.
Camera status remains separate. A camera response cannot make machine state
fresh, and disabling the overlay is not a prerequisite for these semantics.

## Explicit Home/park

The operator may request Home/park in either READY_HOME_REQUIRED or READY_MOTION
when idle, disarmed, and motion-enabled. MachineService rejects concurrent
controller work rather than queuing a second Home behind the first. Pi physical
operation admission and exact session-generation checks remain in force.

An admitted repeat Home invalidates the prior reference before controller I/O
and returns the controller to READY_HOME_REQUIRED. Only the full laser-off,
verified continuous hold, Home, coordinate/modal validation, park, planner
completion, and final validation sequence may publish READY_MOTION. Failure,
STOP, or a replaced session cannot restore the old reference. Invalid or armed
requests rejected before admission do not move or invalidate a good reference.

The separate job-start preparation operation retains its one-Home contract.
Automatic Home on START, Air Assist session redesign, and independent laser
cooling-fan control remain later changes.

## Operator validation

Deploy matching Pi source and Windows feature build. Record the firmware,
configured primary by-id endpoint, build revisions, power/thermal readings,
service logs, and observed outcome. Use the actual GRBL device, not the unrelated
Espressif JTAG board previously found on the Pi.

1. Connect, Home/park, then observe idle READY_MOTION for several minutes with
   live overlay both enabled and disabled. If status becomes unavailable, retain
   matching desktop and Pi timestamps/logs; distinguish contact loss from a
   reported controller fault or generation change.
2. From idle/disarmed READY_MOTION, explicitly Home/park again. Confirm full
   Home and park, followed by READY_MOTION with held XY steppers.
3. Run the established small test job twice. Confirm completion, configured
   post-job Home/park, and readiness for the next job.
4. STOP a test job and verify output/motion stop, then Home required. Explicit
   Home/park must recover through the complete sequence. Repeat STOP during Home
   only under the existing controlled hardware test procedure.
5. Observe a disposable monitoring-connection failure: it must not issue an
   automatic controller disconnect, reset, or STOP. Real controller-session
   loss must still invalidate coordinates and require Home after recovery.

Automated tests use fake controller transcripts, deterministic callback ordering,
real loopback authenticated RPC sessions, and offscreen Windows widgets. They
cannot establish physical stepper hold or laser shutdown. See CURRENT_STATE.md
for the exact test/build evidence and remaining physical acceptance.
