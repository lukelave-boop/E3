# Serial receive timing correction

## Finding and evidence

P1: the primary receive stack can starve command admission and delay queued
responses behind idle polling. The operator reported no visible speed change
between previewed 1500 and 3000 mm/min circle jobs. Generation of a 30 mm ellipse
correctly emits 72 segments at the two respective feeds. The Pi's retained
transcript shows short F1500 moves sent only a few times per second, and reports
XY maximum rates of 10000 mm/min and acceleration of 500 mm/s².

The operator verified deployed commit `3f3c25b`; hashes of MachineService,
PosixSerial and ControllerReceiver exactly match that commit. Its advertised
build revision differs from the checkout, so build metadata alone was not used
as source evidence. Windows remains 0.6.202 for this Pi-side correction.

Two code-derived causes interact:

1. `PosixSerial._reader_loop` holds `_receive_lock` over a 100 ms readiness
   wait. `read_line(timeout=0)` needs that lock to access the queue. Therefore
   its zero timeout does not prevent a long wait before examining queued data.
2. `ControllerReceiver._run` repeatedly acquires `_ingress` for blocking reads.
   Python locks do not promise fair acquisition; `sleep(0)` is not a reliable
   handoff to a waiting admission, transaction end, or quiet-boundary check.

Linux WSL pseudoterminal evidence with unchanged production transport measured
91.44 ms average zero-timeout reads. A combined receiver probe exceeded its
25-second diagnostic deadline while waiting through the nested locks. Moving
only the serial wait reduced queue-read latency but left about 1.2 ACK cycles/s
in that probe. Correcting both layers yielded about 44 cycles/s and 0.04 ms
queue reads with immediate fake acknowledgements. These measurements establish
software contention, not physical feed-rate acceptance on the Pi.

The newer continuous receive owner introduced additional admission/boundary
reads that exposed an older serial-lock design. Queue-only mocks omitted the
lower reader and its lock, so they did not model the physical transport.

## Correction and preserved dependencies

The raw reader waits for readiness outside `_receive_lock`. It then checks
retirement/synchronization and rechecks readiness without blocking under the
lock before reading bytes. Synchronization can have drained the original
readiness in the meantime; consuming stale readiness must not create a false
read failure or admit pre-synchronization input.

The receive owner registers waiting consumer operations before they acquire
ingress. The polling worker gives them the next turn after its current bounded
read. Consumer admission still drains idle input and writes under one gate;
fatal/unowned input is still latched before any new command can write. STOP
wakes the worker even while it is yielding to queued consumers.

The 10 ms post-ACK quiet interval, one outstanding transaction, strict framing,
exclusive serial ownership, synchronization purge, generation invalidation,
STOP/abort, Home, held-stepper policy, feed ceilings, and laser authorization
remain unchanged. No arbitrary timeout reduction or streaming pipeline is added.

## Regression and physical acceptance

Risk concentrates at readiness-versus-synchronization and receive-versus-command
admission boundaries. Deterministic tests pause the raw readiness wait while a
queue consumer runs, drain previously observed readiness during synchronization,
and queue admission before the worker polls. They exercise allowed admission,
unowned ACK rejection, and retirement. Existing PTY tests cover framing faults,
overflow, reopen, exclusive ownership, synchronization and write backpressure;
controller/session tests cover asynchronous faults and STOP.

The combined MachineService PTY fixture now models acknowledged `$1` writes
instead of always reporting 250 after accepting 255, and requires actual job
completion. It must not bypass held-reference validation merely to pass.
Three handshake/recovery fault fixtures also allow a 100 ms transaction budget instead of
10 ms so earlier valid transactions can finish their mandatory 10 ms quiet
interval before the intended failing stage is reached. Production timeouts
and assertions on the intended failure remain unchanged.

On the real Pi, compare the same regenerated circle at 1500 and 3000. Measure
cutting motion separately from Home/park. Requested speed is not a promise of
constant physical speed through acceleration and corners. Also repeat STOP,
Home recovery, and successive jobs, checking that no duplicate reply, reference,
or cleanup regression appears. Record results in CURRENT_STATE.md before
claiming physical speed recovery. Broader CI failures remain integration gates.
