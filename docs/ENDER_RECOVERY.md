# Ender connection and fault recovery

The XY/laser controller and Ender have separate connections. Successful XY
Home/park does not establish Ender readiness. Surface / laser focus now shows
the Ender's actual connection fault above its actions. Saved Z maximum and
probe offsets remain visible as configuration; they are not a live Z reading.

## Reconnect from E3

With the primary controller connected, idle and disarmed, choose **Reconnect
Ender** in Surface / laser focus. No XY/Z movement, homing cycle or laser arming
is commanded. A supported firmware reset runs native CR Touch initialization
and may move its pin; keep its deployment and retraction path clear. The service
invalidates the old surface, reference
and preview, opens its one shared Ender connection, requests a fresh identity
and verifies fan OFF. It does not replay the failed operation.

A failed attempt remains visible. Status polling does not automatically reset
or reconnect hardware. A temporary primary state change or Home operation no
longer hides the Ender error or permanently prevents a fresh status read.
Reconnect retains saved limits, offsets and calibration. A fresh border
reference is required before focus positioning; existing clearance restrictions
are not removed merely by reconnecting.

## Firmware recovery after an emergency halt

The new compact F401 firmware advertises `Cap:E3_RECOVERY_V1:1`. Its emergency
halt first performs the native output and stepper shutdown. It then remains
halted while a small polled serial handler answers M115. Each complete query
returns a new token in an `E3RECOVERY:1 STATE:HALTED BOARD:0401C013` response.
Only an explicit recovery command containing that token can request a reset.
Other commands, malformed input, old tokens and passive status queries cannot
resume execution. The host requires the complete fresh halted response and
acknowledgement, then verifies normal application identity after the reset.
It never uses an unrecognized response to authorize a reset or firmware write.

The earlier 9518b83f firmware cannot execute this protocol while halted: its
kill loop does not read commands. A USB adapter reset is not proof of a reset
of its STM32 processor. If that earlier firmware is already halted, a real
processor reset is still needed once before the new firmware can be installed.
This update cannot retroactively add commands to the halted image. No physical
unplugging, reset, firmware upload or motion is performed by building the kit.

## Which failures halt the Ender

An XY-only focus transfer does not mark the secondary Z axis as moving. A
failure still stops and invalidates the primary operation, but does not send
Ender M112 merely because the operation is named focus. A failed read-only
Ender status exchange loses connection trust without introducing a new halt.
Uncertain actual Z/probe motion retains its emergency-stop path. Genuine stop
handling and laser-OFF requirements remain enforced.

Shutdown attempts OFF on an existing usable connection; it does not reopen a
failed Ender just to run another 45-second readiness handshake. An explicit
reconnect remains bounded and cancellable by STOP or loss of its requesting
session. These software mechanisms are not safety-rated.

## Verification boundary

The September 12 operator log confirms the earlier XY barrier timed out after
three seconds, followed by STOP, an Ender readiness timeout and a service
shutdown timeout. The operator's subsequent USB reset succeeded at the adapter
level. A direct read-only Pi status check still reported primary READY_MOTION
and secondary ready=false with no M115 response. M112 delivery itself was not
captured; a halted MCU is consistent with, but not proven by, the silence.

Automated test, frozen-build and firmware-layout results are recorded in the
feature handoff. Tests with simulated serial devices, offscreen Qt and emulated
firmware do not establish physical recovery. Operator checks must confirm
connection recovery, retained settings and fault handling before this can be
called physically qualified. No job or motion automatically resumes on recovery.

## Real-board idle recovery verification — September 12

The operator power-cycled the Ender with USB connected. A fresh synchronized
M115 then identified MARLIN_F401. With the Pi service stopped, the audited
0c719c71 package uploaded successfully through the retained 0.3.0 updater:
fresh updater identity/HOLD preceded erase; all application blocks were
verified and committed, then an explicit BOOT was acknowledged. Fresh startup
and M115 identify Marlin 2.0.8.24F4 (Sep 12 2026 07:10:11), F401 0x423/256 KiB,
E3_RECOVERY_V1, SURFACE_HEIGHT_V2 and Z_LIMIT_80_V1 with expected geometry.
Application SHA256 is
fe9b02087d37c2019cfc54b16141f1f755e730f87837d535a51566898d6a5b6f.

The operator confirmed secure Z with motors unpowered and a clear probe pin
path before one controlled idle halt/reconnect test. The authenticated
job.stop emergency action was issued once. Restarting only the Pi service
then produced the explicit firmware HALTED diagnosis during ordinary M115
startup; it did not automatically recover. After a normal, unhomed primary
connection, explicit focus recover returned fresh normal Ender identity and
readback in 9.966 seconds with no further power cycle. The result was
available=true, Ender ready=true/fault=null, z_known=false, reference=false,
and no saved surface/preview. No homing, Z/XY travel or laser-fire command
was issued. M112 removes holding torque and boot may cycle the CR Touch pin.

Final real-board readback confirms active/persistent max40, firmware ceiling80,
FAN1=255 under CPU cooling, FAN2=0, and probe offset [3.302,38.608]. All four
configuration hashes remain unchanged; the Pi service is active. This verifies
one idle HALTED-to-guarded-recovery cycle. The token/reset wire exchange was
enforced by the audited reader but is not separately exposed in the RPC
transcript. Physical pin travel, stopping under motion, gauge accuracy and
raised-surface calibration are not newly qualified. Fresh Home/park and border
reference remain required. The selected Windows build is unchanged at 0.7.76.
See docs/ENDER_RECOVERY_HANDOFF.md for the installation and test record.
