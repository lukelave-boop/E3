# Continuous GRBL motion streaming

Consecutive canonical `G1 X… Y… F… [S…]` moves now share one receive owner.
The sender no longer waits through a 10 ms quiet interval and a background
reader handoff after every small raster segment. This removes an artificial
command-rate limit that made detailed areas substantially slower than long
uniform runs. Both desktop project and browser SVG jobs use MachineService;
their generation, immutable programs, preflight and output boundaries remain
unchanged. Marlin and noncanonical commands retain their existing exchanges.

## Ownership and limits

The first stream-size check uses the original ASCII line length as an upper
bound: the lossless compactor only removes characters. If all eligible original
lines already fit, a full encoding pass is unnecessary. Any longer eligible line
retains the complete stateful compaction scan, preserving acceptance and rejection
for padded numbers and changing controller capacity. Actual streaming still
checks every encoded line before admission.

Dense raster validation prepares fixed polygon edges once and checks each point
using the reference predicate's same arithmetic and tolerance. Inside travel
points take the direct rectangle check; rejection retains detailed clearance
diagnostics. Output, beam-offset, travel and policy checks remain authoritative.

Start checks every compacted stream line against the controller's byte capacity
before arming or motion. A successful check is retained for only the same
immutable line tuple and byte capacity, so the final Start check does not repeat
the entire scan while the temporary arming timer runs. New commands or a changed
capacity recheck; a failed or partial scan is never cached. Current authorization,
policy, session, coordinate and per-write checks remain independent.

For raster-envelope programs, validation skips legacy work-area predicates whose
results were unused. Travel-envelope and both powered endpoint checks remain.
An observed 729,269-command/22,099,480-byte job spent about 59 seconds uploading,
173 seconds in Pi validation and 105 seconds in Start before its grant expired.
Offline Windows comparison of those same bytes reduced validation from 10.9 to
8.3 seconds and the repeated stream check from 2.8 seconds to below 1 ms, with
identical validated output. These are diagnostic timings, not a promised Pi
startup duration; physical-machine timing and acceptance still need testing.

Only a strict, unambiguous `[OPT:flags,planner,rx]` response from the existing
session handshake permits multiple outstanding commands. The window is at
most 127 bytes including every newline, reserves one advertised RX slot, and
contains at most 16 commands or the smaller reported planner count. Missing,
malformed or conflicting capacity evidence permits only one outstanding line.
That conservative path still avoids the per-segment quiet interval and receive
thread handoff. No settings write, buffer-size guess or new controller query
is used to obtain more capacity.

The receive owner reserves FIFO acknowledgement ownership before the physical
write. Credit is released only when its acknowledgement is consumed. Every
admission checks already queued input first. Error, alarm, restart, malformed
input, unexpected payload, excess acknowledgement, timeout, partial write or
read failure fails the stream and retires its exact controller generation.
There is no retry or replay. Each pending command has a bounded deadline.

GRBL acknowledgements carry no identifiers. A duplicate first arriving while
another command is pending cannot be distinguished from that command's reply.
The protocol does not promise detection of every delayed duplicate. Already
queued/unowned replies and excess replies are rejected, and a quiet fence
separates the stream from subsequent operations. This limitation also existed
for sufficiently delayed replies in the prior send-response implementation.

## Safety boundaries

The background receiver yields raw reads to the stream owner only for the
contiguous motion run. The same framing classifier and fault latch remain
authoritative. No physical-write lock is held while waiting for an ACK.
Software Stop/disarm/disconnect retain independent bounded hold/reset, M5 and
close attempts; an old stream cannot continue on a replacement session.

Every physical write still checks the original full program line against live
motion permission, process hardware authority, laser authorization, focus,
secondary-controller state and current job/session identity. Complete program
validation still enforces absolute millimetres, feed/power ceilings, bounds,
laser-off rapid travel and initial/final standalone M5.

Every noneligible command ends the stream and receives the original strict
transaction handling. This includes G0, M3/M4/M5, focus/air-assist directives,
modal commands and completion barriers. All pending replies are consumed and
the original quiet interval is established before the next operation. ACK
acceptance is not physical motion completion; existing motion barriers remain
mandatory. Focus descent finishes before any eligible powered run begins.

## Lossless wire encoding

A fresh encoder for each run retains the first G1, X, Y, F and supplied S.
Later lines omit unchanged modal words and redundant decimal zeros/spaces.
It never rounds coordinates, changes power/feed, joins or drops moves, changes
dithering, or edits the uploaded/exported program. Coincident moves retain an
axis word; S0 transitions remain explicit. Every new run establishes its own
modal values, including after M5, row transfers and successive jobs.
Start checks every eligible run against the exact session byte window before
arming, Home or focus movement, rejecting oversized commands with their line
number. The runtime repeats this size check before admission.

## Verification and physical limits

Tests exercise semantic equivalence, receive/window accounting, delayed and
missing replies, faulty input, Stop during a blocked write or pending ACK,
policy revocation, completion fences and fresh sessions. POSIX PTY tests use
the actual serial backend with a simulated controller; they skip explicitly
on Windows. Exact results and deployment status belong in CURRENT_STATE.md.

Requested feed is still subject to controller acceleration, planner and serial
bandwidth. At 115200 baud with 8N1 framing, the nominal transmit ceiling is
11,520 bytes/s. A 0.1 mm segment at 6,000 mm/min lasts 1 ms, so some patterns
can exceed that ceiling even after lossless encoding. The sender does not
silently change feed, power, sampling, machine limits or baud rate to hide
that limit. Constant physical speed at every setting is not guaranteed.

For Pi-owned jobs the companion must be installed on the Pi; changing the
Windows preview alone cannot change execution. The current desktop can keep
its existing immutable-job protocol and image-editing features. Record the
actual controller, firmware, settings and supervised motion/Stop results
before claiming physical acceptance. These software controls are not
safety-rated.
