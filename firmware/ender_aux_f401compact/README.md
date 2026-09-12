# Compact F401 retained updater

The maintenance host waits 35 seconds on the same open serial connection,
then sends a line separator and queries a fresh identity before updater entry.
It reports the current stage and partial timeout responses. Startup traffic
cannot authorize an erase; firmware errors stop the operation without retry.
Keep the Pi hardware service stopped throughout maintenance. Upload commits
the application but leaves boot as a separate operator command. This startup
handling has offline protocol coverage; physical upload qualification is pending.

For older Marlin builds whose M997 acknowledgement is truncated or garbled,
nonempty incomplete bytes permit one HOLD/identity check. Explicit refusals
and a completely silent timeout still stop the operation. Partial bytes never
permit an erase by themselves. An exact HOLD acknowledgement and a fresh
matching updater identity are required; M997 and flash transactions are not
retried. This covers the reported one-byte handoff failure in simulated tests;
qualification of this complete host sequence on the machine remains pending.

Updater 0.3.0, board/image ID 0401C013. This is a separate target; historical
RET6-only and F103 sources/packages remain unchanged. See
../marlin_mainboard_compact/README.md for the complete SD/USB kit.

Both exact silicon pairs (0x423,256 KiB) and (0x433,512 KiB) use the same layout:
factory loader [0x08000000,0x08010000), updater [0x08010000,0x08020000),
metadata [0x08020000,0x08020200), application [0x08020200,0x08040000).
RAM is capped at 64 KiB. USB erases only sector 5 and programs only this app
region, including metadata. Upper RE flash, loader, updater and option bytes
are never USB update targets. CRC/vector checks and commit-last recovery follow
the established protocol, with five-second automatic boot unaffected by ordinary
traffic. Mismatch mode answers INFO/M115/DIAG repeatedly and permits no writes.

Build with `python firmware/ender_aux_f401compact/build.py`. Run core,
platform and diagnostic tests with the corresponding run_*_tests.py scripts.
application.c and core_harness.c provide the inherited BENCH test harness only;
they are not the native application shipped in the mainboard installer.
The actual application is built separately under marlin_mainboard_compact.

Platform facts/mapping come from the existing ender_aux references to ST RM0368
and the published Creality/Klipper S1 configuration. No physical flash capacity,
electrical level or startup timing is established by emulator tests.
