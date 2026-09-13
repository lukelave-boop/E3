# Console replies and laser-head fan diagnosis

Open Window > Console while connected and idle. Enter one read-only query in
Read-only diagnostic command and press Enter or Send. The response pane can
be selected and copied with Ctrl+A/Ctrl+C.

The source fix retains explicit command/reply and error history in the current
window across status polling. History is bounded to 1,000 displayed lines and
is not saved between application launches. Local controller-log snapshots are
still bounded and replaced. The Pi deliberately omits its raw controller log
from monitoring responses; this fix does not expose that log or widen the
manual-command allowlist. The selected E3 DEV TEST 0.7.67 build predates the fix:
it can show replies only in a temporary status-bar message.

## Read-only physical controller evidence, 2026-09-11

The operator entered $I and $$ in the Console and supplied a screenshot of an
empty output pane. The assistant then used the saved authenticated Pi endpoint
192.168.5.18:8765 and the existing machine.command API, checking connected,
disarmed and idle status before each query and binding each to the current
boot/session. The service reported version 0.7.54, revision 53386651, controller
session generation 1, and READY_MOTION. No serial connection, motion, laser
output, setting change, disarm, stop or service restart was requested.

- $I returned only ok; exact primary firmware identity remains unknown.
- $$ returned the settings list through $131 and final ok. It included $1=255,
  $30=1000.000, $31=0.000 and $32=1; it did not report $152.
- $G returned [GC:G1 G54 G17 G21 G90 G94 M5 M9 T0 F300 S0.000] and ok.

These are physical communication and commanded-state observations, not optical
output or fan-speed measurements. The operator reports continuous laser-head
fan operation. Its cause and native cooldown behavior remain unverified.
FAN1 is separately identified as the Pi cooler and FAN2 as air assist.

Creality's [Falcon A1 parameter documentation](https://wiki.creality.com/en/laser-engraver/falcon-a1/random-data/description-for-GRBL-configuration-parameters)
describes $152 standby timing, but applicability to this conversion controller
has not been established. Do not infer support or write that setting from the
other model's documentation. E3's continuous stepper hold protects coordinate
readiness; no change to that policy is part of the Console fix.

## Console verification

70 focused Windows tests passed across the Console, controller state,
control-surface and shutdown groups, including six new Console regressions.
Tests exercised offscreen Qt Enter/Send, reply selection across refreshes,
ordinary command rejection, empty acknowledgements, bounded history and actual
main-window signal wiring. Affected Ruff, compileall and diff checks passed.
No new frozen build or interactive GUI verification was performed. Timeout or
quarantine can still suppress a stale callback under the existing authority
rules; this patch does not change that behavior.
