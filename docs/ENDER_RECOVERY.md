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
