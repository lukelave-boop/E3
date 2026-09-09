# Connected startup acceptance

Updater **0.2.0** plus the companion Pi code is the startup correction. This
requires one new combined SD installation: uploading only `application.e3fw`
cannot replace the updater. Preserve the prior packages and stock recovery.
No physical startup result for this revision is claimed yet.

## What changes

- A valid application starts after the updater's five-second window despite
  INFO/M115, ordinary runtime commands, partial/malformed lines or UART errors.
  Only exact `HOLD` or a validated `BEGIN` cancels automatic boot. `BOOT` remains
  an explicit maintenance action and still validates the image first.
- Invalid/incomplete applications remain in the updater indefinitely. Interrupted
  uploads do not become valid until the existing CRC/vector/commit checks pass.
- The Pi's existing serial owner sends only read-only M115 readiness queries
  during initialization, at most 15 attempts in a 45-second query budget. Native
  initialization and the display animation are allowed to finish. A Marlin
  identity followed by OK is required; an isolated OK is insufficient.
- Startup replies are synchronized away before the separate fan-OFF exchange.
  No automatic HOLD, BOOT, reset, firmware write, homing or motion is sent.
  Normal actuator commands are not retried by this readiness loop. Unknown
  firmware, a persistent updater, USB failure or timeout leaves the owner closed
  and unready. This is bounded startup readiness, not automatic job resumption.
- The existing initial settle delay and bounded serial synchronization add a
  small amount to the query budget. This is not a claim that the Ender always
  needs 20 seconds, or proof of the separate working-board zero-response cause.

## One-time installation

Follow INSTALL.md for SD replacement and the matching Pi source update. The
package's SOURCE_REVISION.txt pins that companion source. The service restart
there installs new software once; it is not required for each ordinary boot.
The first SD programming power cycle still follows the loader's installation
procedure. Subsequent normal power-up keeps Ender USB and the webcam attached.
No Windows-to-Ender USB connection is part of this procedure.

## Operator acceptance, no job or measurement

Keep the probe area clear because native startup may initialize the pin. Keep
laser emission disabled. With the SD installer removed and both USB devices
connected to the Pi, check these normal startup orders:

1. Ender powered before the Pi.
2. Pi powered before the Ender, bringing the Ender up within the readiness window.
3. Both powered together.

For each, allow the service to start, connect through E3 and use the existing
mainboard status control. Require the exact E3 mainboard/material capabilities,
unhomed Z after cold boot, and both fan command readbacks OFF. Observe the actual
outputs and webcam continuity separately. Record firmware/package digest, Pi
source revision, startup order, time to readiness and the complete result.
Do not run a job, home or probe as part of startup acceptance.

Also check a service restart with the already-running Ender left connected.
This is an additional acceptance case, not the normal startup procedure. If a
startup attempt fails, retain its error and USB metadata; do not treat repeated
restarts or a timed cable reconnection as a pass. Opening a missing USB device
or a transport fault can still fail; readiness does not repair a USB link.

The updater's exact-HOLD and abandoned-upload recovery checks are separate
spare-board maintenance tests, performed only after successful normal startup.
Use the updated host.py, with explicit hardware authorization, and restore the
same accepted application afterward. Never deliberately interrupt an upload
on the working machine to qualify spare recovery.
